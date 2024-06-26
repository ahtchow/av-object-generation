import os
import random
from copy import copy
import torch
import pickle as pkl
from torch.utils.data import Dataset
import numpy as np
import h5py
from tqdm.auto import tqdm


synsetid_to_cate = {
    '02691156': 'airplane', '02773838': 'bag', '02801938': 'basket',
    '02808440': 'bathtub', '02818832': 'bed', '02828884': 'bench',
    '02876657': 'bottle', '02880940': 'bowl', '02924116': 'bus',
    '02933112': 'cabinet', '02747177': 'can', '02942699': 'camera',
    '02954340': 'cap', '02958343': 'car', '03001627': 'chair',
    '03046257': 'clock', '03207941': 'dishwasher', '03211117': 'monitor',
    '04379243': 'table', '04401088': 'telephone', '02946921': 'tin_can',
    '04460130': 'tower', '04468005': 'train', '03085013': 'keyboard',
    '03261776': 'earphone', '03325088': 'faucet', '03337140': 'file',
    '03467517': 'guitar', '03513137': 'helmet', '03593526': 'jar',
    '03624134': 'knife', '03636649': 'lamp', '03642806': 'laptop',
    '03691459': 'speaker', '03710193': 'mailbox', '03759954': 'microphone',
    '03761084': 'microwave', '03790512': 'motorcycle', '03797390': 'mug',
    '03928116': 'piano', '03938244': 'pillow', '03948459': 'pistol',
    '03991062': 'pot', '04004475': 'printer', '04074963': 'remote_control',
    '04090263': 'rifle', '04099429': 'rocket', '04225987': 'skateboard',
    '04256520': 'sofa', '04330267': 'stove', '04530566': 'vessel',
    '04554684': 'washer', '02992529': 'cellphone',
    '02843684': 'birdhouse', '02871439': 'bookshelf',
    # '02858304': 'boat', no boat in our dataset, merged into vessels
    # '02834778': 'bicycle', not in our taxonomy
}
cate_to_synsetid = {v: k for k, v in synsetid_to_cate.items()}


class ShapeNetCore(Dataset):

    GRAVITATIONAL_AXIS = 1
    
    def __init__(self, path, cates, split, scale_mode, transform=None):
        super().__init__()
        assert isinstance(cates, list), '`cates` must be a list of cate names.'
        assert split in ('train', 'val', 'test')
        assert scale_mode is None or scale_mode in ('global_unit', 'shape_unit', 'shape_bbox', 'shape_half', 'shape_34')
        self.path = path
        if 'all' in cates:
            cates = cate_to_synsetid.keys()
        self.cate_synsetids = [cate_to_synsetid[s] for s in cates]
        self.cate_synsetids.sort()
        self.split = split
        self.scale_mode = scale_mode
        self.transform = transform

        self.pointclouds = []
        self.stats = None

        self.get_statistics()
        self.load()

    def get_statistics(self):

        basename = os.path.basename(self.path)
        dsetname = basename[:basename.rfind('.')]
        stats_dir = os.path.join(os.path.dirname(self.path), dsetname + '_stats')
        os.makedirs(stats_dir, exist_ok=True)

        if len(self.cate_synsetids) == len(cate_to_synsetid):
            stats_save_path = os.path.join(stats_dir, 'stats_all.pt')
        else:
            stats_save_path = os.path.join(stats_dir, 'stats_' + '_'.join(self.cate_synsetids) + '.pt')
        if os.path.exists(stats_save_path):
            self.stats = torch.load(stats_save_path)
            return self.stats

        with h5py.File(self.path, 'r') as f:
            pointclouds = []
            for synsetid in self.cate_synsetids:
                for split in ('train', 'val', 'test'):
                    pointclouds.append(torch.from_numpy(f[synsetid][split][...]))

        all_points = torch.cat(pointclouds, dim=0) # (B, N, 3)
        B, N, _ = all_points.size()
        mean = all_points.view(B*N, -1).mean(dim=0) # (1, 3)
        std = all_points.view(-1).std(dim=0)        # (1, )

        self.stats = {'mean': mean, 'std': std}
        torch.save(self.stats, stats_save_path)
        return self.stats

    def load(self):

        def _enumerate_pointclouds(f):
            for synsetid in self.cate_synsetids:
                cate_name = synsetid_to_cate[synsetid]
                for j, pc in enumerate(f[synsetid][self.split]):
                    yield torch.from_numpy(pc), j, cate_name
        
        with h5py.File(self.path, mode='r') as f:
            for pc, pc_id, cate_name in _enumerate_pointclouds(f):

                if self.scale_mode == 'global_unit':
                    shift = pc.mean(dim=0).reshape(1, 3)
                    scale = self.stats['std'].reshape(1, 1)
                elif self.scale_mode == 'shape_unit':
                    shift = pc.mean(dim=0).reshape(1, 3)
                    scale = pc.flatten().std().reshape(1, 1)
                elif self.scale_mode == 'shape_half':
                    shift = pc.mean(dim=0).reshape(1, 3)
                    scale = pc.flatten().std().reshape(1, 1) / (0.5)
                elif self.scale_mode == 'shape_34':
                    shift = pc.mean(dim=0).reshape(1, 3)
                    scale = pc.flatten().std().reshape(1, 1) / (0.75)
                elif self.scale_mode == 'shape_bbox':
                    pc_max, _ = pc.max(dim=0, keepdim=True) # (1, 3)
                    pc_min, _ = pc.min(dim=0, keepdim=True) # (1, 3)
                    shift = ((pc_min + pc_max) / 2).view(1, 3)
                    scale = (pc_max - pc_min).max().reshape(1, 1) / 2
                else:
                    shift = torch.zeros([1, 3])
                    scale = torch.ones([1, 1])

                pc = (pc - shift) / scale

                self.pointclouds.append({
                    'pointcloud': pc,
                    'cate': cate_name,
                    'id': pc_id,
                    'shift': shift,
                    'scale': scale
                })

        # Deterministically shuffle the dataset
        self.pointclouds.sort(key=lambda data: data['id'], reverse=False)
        random.Random(2020).shuffle(self.pointclouds)

    def __len__(self):
        return len(self.pointclouds)

    def __getitem__(self, idx):
        data = {k:v.clone() if isinstance(v, torch.Tensor) else copy(v) for k, v in self.pointclouds[idx].items()}
        if self.transform is not None:
            data = self.transform(data)
        return data


class PandaSet(Dataset):

    GRAVITATIONAL_AXIS = 1
    
    def __init__(self, path, cls, split, scale_mode, transform=None, input_size=1024):
        super().__init__()
        assert split in ('train', 'val', 'test')
        assert scale_mode is None or scale_mode in ('global_unit', 'shape_unit', 'shape_bbox', 'shape_half', 'shape_34')
        self.path = path
        self.split = split
        self.scale_mode = scale_mode
        self.transform = transform
        self.cls = cls
        self.input_size = input_size

        self.pointclouds = []
        self.stats = None

        self.get_statistics()
        self.load()

    def normalize_point_cloud(self, pc, mode='shape_unit'):
        if mode == 'shape_unit':
            shift = pc.mean(dim=0).reshape(1, 3)
            scale = pc.flatten().std().reshape(1, 1)
        elif mode == 'shape_bbox':
            pc_max, _ = pc.max(dim=0, keepdim=True) # (1, 3)
            pc_min, _ = pc.min(dim=0, keepdim=True) # (1, 3)
            shift = ((pc_min + pc_max) / 2).view(1, 3)
            scale = (pc_max - pc_min).max().reshape(1, 1) / 2
        pc = (pc - shift) / scale
        return pc

    def get_statistics(self):

        basename = os.path.basename(self.path)
        dsetname = basename[:basename.rfind('.')]
        stats_dir = os.path.join(os.path.dirname(self.path), dsetname + '_stats')
        os.makedirs(stats_dir, exist_ok=True)

        stats_save_path = os.path.join(stats_dir, 'stats_' + '_'.join(self.cls) + '.pt')

        with open(self.path, 'rb') as f:
            data = pkl.load(f)
            pointclouds = []
            rel_distance = []
            for split in ('train', 'val', 'test'):
                for obj in data[self.cls][split]:
                    rel_distance.append(torch.norm(torch.from_numpy(obj['points'])- torch.Tensor(obj['box']['position']), dim=1).reshape(-1, 1))
                    pointclouds.append(self.normalize_point_cloud(torch.from_numpy(obj['points'])))

        all_points = torch.cat(pointclouds, dim=0) # (B, N, 3)
        rel_distance = torch.cat(rel_distance, dim=0)
        N, _ = all_points.size()
        mean = all_points.mean(dim=0) # (1, 3)
        std = all_points.view(-1).std(dim=0)   # (1, )
        self.stats = {'mean': mean, 'std': std, 'mean_dist_from_center': rel_distance.mean(), 'std_dist_from_center': rel_distance.std()}
        torch.save(self.stats, stats_save_path)
        return self.stats

    def get_features_from_pc(self, obj):
        # pc: (N, 3)
        pc = torch.from_numpy(obj['points'])
        pos = torch.Tensor(obj['box']['position'])
        rel_pc = pc - pos

        # # Compute point cloud as relative to the object center in spherical coordinates
        r = torch.norm(rel_pc, dim=1).reshape(-1, 1) # (N, 1)
        theta = torch.atan2(rel_pc[:, 1], rel_pc[:, 0]).reshape(-1, 1) # (N, 1)
        phi = torch.acos(rel_pc[:, 2] / r.flatten()).reshape(-1, 1) # (N, 1)

        # Normalize
        r = (r - self.stats['mean_dist_from_center']) / self.stats['std_dist_from_center']
        theta = (theta) / (np.pi/2)
        phi = (phi) / np.pi
        breakpoint()

        return torch.cat([r, theta, phi], dim=1)

    def load(self):

        with open(self.path, 'rb') as f:
            data = pkl.load(f)
  
        for pc_id, obj in enumerate(data[self.cls][self.split]):
            
            # pc = self.get_features_from_pc(obj)

            pc = torch.from_numpy(obj['points'])
            pos = torch.Tensor(obj['box']['position'])
            pc = pc - pos
                
            if self.scale_mode == 'global_unit':
                shift = pc.mean(dim=0).reshape(1, 3)
                scale = self.stats['std'].reshape(1, 1)
            elif self.scale_mode == 'shape_unit':
                shift = pc.mean(dim=0).reshape(1, 3)
                scale = pc.flatten().std().reshape(1, 1)
            elif self.scale_mode == 'shape_half':
                shift = pc.mean(dim=0).reshape(1, 3)
                scale = pc.flatten().std().reshape(1, 1) / (0.5)
            elif self.scale_mode == 'shape_34':
                shift = pc.mean(dim=0).reshape(1, 3)
                scale = pc.flatten().std().reshape(1, 1) / (0.75)
            elif self.scale_mode == 'shape_bbox':
                pc_max, _ = pc.max(dim=0, keepdim=True) # (1, 3)
                pc_min, _ = pc.min(dim=0, keepdim=True) # (1, 3)
                shift = ((pc_min + pc_max) / 2).view(1, 3)
                scale = (pc_max - pc_min).max().reshape(1, 1) / 2
            else:
                shift = torch.zeros([1, 3])
                scale = torch.ones([1, 1])

            pc = (pc - shift) / scale
            yaw = (torch.Tensor([obj['box']['yaw']]) - np.pi/2)

            #normalize yaw between -pi and pi
            def normalize_angle(angle):
                # Normalize angle to be within the range [-pi, pi]
                normalized_angle = (angle + np.pi) % (2 * np.pi) - np.pi
                return normalized_angle

            self.pointclouds.append({
                'pointcloud': pc,
                'view_angle': torch.Tensor([obj['box']['view_angle']]) / np.pi,
                'yaw': normalize_angle(yaw) / np.pi,
                'cate': self.cls,
                'id': pc_id,
                'shift': self.stats['mean_dist_from_center'],
                'scale': self.stats['std_dist_from_center']
            })

        # Deterministically shuffle the dataset
        self.pointclouds.sort(key=lambda data: data['id'], reverse=False)
        random.Random(2020).shuffle(self.pointclouds)

    def __len__(self):
        return len(self.pointclouds)

    def __getitem__(self, idx):
        data = {k:v.clone() if isinstance(v, torch.Tensor) else copy(v) for k, v in self.pointclouds[idx].items()}
        if self.transform is not None:
            data = self.transform(data)
        return data
