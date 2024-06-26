import torch
from torch.nn import Module

from .common import *
from .encoders import *
from .diffusion import *
from .flow import *


class FlowVAESurfaceConditional(Module):

    def __init__(self, args):
        super().__init__()
        self.args = args
        self.encoder = PointNetEncoder(args.latent_dim)
        
        # Here we add an self-attention layer to the model to encode the view angle and yaw.
        self.FC1 = nn.Linear(args.latent_dim+2, int(args.latent_dim/2))
        self.BN1 = nn.BatchNorm1d(int(args.latent_dim/2))
        self.FC2 = nn.Linear(int(args.latent_dim/2), args.latent_dim)
        self.BN2 = nn.BatchNorm1d(args.latent_dim)
        self.MHSA = nn.MultiheadAttention(args.latent_dim, 2)

        self.flow = build_latent_flow(args)
        self.diffusion = DiffusionPoint(
            net = PointwiseNet(point_dim=3, context_dim=args.latent_dim, residual=args.residual),
            var_sched = VarianceSchedule(
                num_steps=args.num_steps,
                beta_1=args.beta_1,
                beta_T=args.beta_T,
                mode=args.sched_mode
            )
        )

    def load_partial_state_dict(self, state_dict):
        model_state_dict = self.state_dict()
        filtered_state_dict = {k: v for k, v in state_dict.items() if k in model_state_dict}

        # Update the model's state_dict with the filtered state_dict
        model_state_dict.update(filtered_state_dict)

        # Load the updated state_dict into your model
        self.load_state_dict(model_state_dict, strict=False)

    def get_loss(self, x, view_angle, yaw, kl_weight, writer=None, it=None):
        """
        Args:
            x:  Input point clouds, (B, N, d).
            view_angle:  View angle, (B, 1).
            yaw:  Yaw, (B, 1).
        """
        batch_size, _, _ = x.size()
        # print(x.size())

        z_mu, z_sigma = self.encoder(x)
        z = reparameterize_gaussian(mean=z_mu, logvar=z_sigma)  # (B, F)

        # Add MHSA layer to encode the view angle and yaw.
        z = torch.cat([z, view_angle, yaw], dim=1)
        z = self.BN1(self.FC1(z))
        z = self.BN2(self.FC2(z))
        z = z.unsqueeze(1)
        z = self.MHSA(z, z, z)
        z = z.squeeze(1)
                
        # H[Q(z|X)]
        entropy = gaussian_entropy(logvar=z_sigma)      # (B, )

        # P(z), Prior probability, parameterized by the flow: z -> w.
        w, delta_log_pw = self.flow(z, torch.zeros([batch_size, 1]).to(z), reverse=False)
        log_pw = standard_normal_logprob(w).view(batch_size, -1).sum(dim=1, keepdim=True)   # (B, 1)
        log_pz = log_pw - delta_log_pw.view(batch_size, 1)  # (B, 1)

        # Negative ELBO of P(X|z)
        neg_elbo = self.diffusion.get_loss(x, z)

        # Loss
        loss_entropy = -entropy.mean()
        loss_prior = -log_pz.mean()
        loss_recons = neg_elbo
        loss = kl_weight*(loss_entropy + loss_prior) + neg_elbo

        if writer is not None:
            writer.add_scalar('train/loss_entropy', loss_entropy, it)
            writer.add_scalar('train/loss_prior', loss_prior, it)
            writer.add_scalar('train/loss_recons', loss_recons, it)
            writer.add_scalar('train/z_mean', z_mu.mean(), it)
            writer.add_scalar('train/z_mag', z_mu.abs().max(), it)
            writer.add_scalar('train/z_var', (0.5*z_sigma).exp().mean(), it)

        return loss

    def sample(self, w, view_angle, yaw, num_points, flexibility, truncate_std=None):
        batch_size, _ = w.size()
        if truncate_std is not None:
            w = truncated_normal_(w, mean=0, std=1, trunc_std=truncate_std)
        # Reverse: z <- w.
        z = self.flow(w, reverse=True).view(batch_size, -1)
        z = torch.cat([z, view_angle, yaw], dim=1)
        z = self.BN1(self.FC1(z))
        z = self.BN2(self.FC2(z))
        z = z.unsqueeze(1)
        z = self.MHSA(z, z, z)
        z = z.squeeze(1)

        samples = self.diffusion.sample(num_points, context=z, flexibility=flexibility)
        return samples

    def forward(self, x, view_angle, yaw, kl_weight, writer=None, it=None):
        return self.get_loss(x, view_angle, yaw, kl_weight, writer=writer, it=it)
