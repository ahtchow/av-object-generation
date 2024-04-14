PROJECT ?= av-object-generation

CURRENT_UID := $(shell id ${USER} -u)
CURRENT_GID := $(shell id ${USER} -g)

XSOCK=/tmp/.X11-unix
XAUTH=/tmp/.docker.xauth
DOCKER_OPTS := \
	--name ${PROJECT} \
	--rm -it \
	-u ${CURRENT_UID}:${CURRENT_GID} \
    -v /etc/passwd:/etc/passwd:ro \
    -v /etc/group:/etc/group:ro \
    -v /tmp/.X11-unix:/tmp/.X11-unix \
    -v ${HOME}/.Xauthority:${HOME}/.Xauthority \
    -v ${HOME}/.Xauthority:/root/.Xauthority \
    -e DISPLAY \
	--ipc=host \
    --net=host

DOCKER_OPTS_GPU := \
	--runtime nvidia \
	--gpus all \

NVIDIA_DOCKER :=$(shell dpkg -l | grep nvidia-container-toolkit 2>/dev/null)
DOCKER_IMAGE := ${PROJECT}

ifdef NVIDIA_DOCKER
	DOCKER_IMAGE :="${DOCKER_IMAGE}-nv"
	DOCKER_OPTS :=${DOCKER_OPTS_GPU} ${DOCKER_OPTS}
endif

build:
	docker build \
		-f docker/Dockerfile \
		-t ${USER}/av-object-generation .

exec:
	docker run \
		--runtime nvidia ${DOCKER_OPTS} \
		-v $${PWD}/docker_home:/home/${USER} \
		-v $${PWD}/diffusion-point-cloud:/work_dir/diffusion-point-cloud \
		${USER}/av-object-generation

docker-exec:
	docker exec -it ${PROJECT} bash
