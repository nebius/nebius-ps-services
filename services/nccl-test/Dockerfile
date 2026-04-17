# syntax=docker/dockerfile:1.7

ARG CUDA_IMAGE_TAG=13.2.0
ARG NCCL_TESTS_REF=v2.18.3
ARG NCCL_PACKAGE_VERSION=

FROM nvidia/cuda:${CUDA_IMAGE_TAG}-devel-ubuntu24.04 AS build

ARG DEBIAN_FRONTEND=noninteractive
ARG NCCL_TESTS_REF
ARG NCCL_PACKAGE_VERSION

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        git \
        libopenmpi-dev \
        openmpi-bin \
        pkg-config \
    && if [[ -n "${NCCL_PACKAGE_VERSION}" ]]; then \
        apt-get install -y --no-install-recommends \
          "libnccl2=${NCCL_PACKAGE_VERSION}" \
          "libnccl-dev=${NCCL_PACKAGE_VERSION}"; \
      else \
        apt-get install -y --no-install-recommends \
          libnccl2 \
          libnccl-dev; \
      fi \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src

RUN git clone --branch "${NCCL_TESTS_REF}" --depth 1 https://github.com/NVIDIA/nccl-tests.git

WORKDIR /src/nccl-tests

RUN mpi_home="/usr/lib/$(gcc -print-multiarch)/openmpi" \
    && make -j"$(nproc)" \
      MPI=1 \
      MPI_HOME="${mpi_home}" \
      CUDA_HOME=/usr/local/cuda \
      NCCL_HOME=/usr

FROM nvidia/cuda:${CUDA_IMAGE_TAG}-runtime-ubuntu24.04 AS runtime

ARG DEBIAN_FRONTEND=noninteractive
ARG CUDA_IMAGE_TAG
ARG NCCL_TESTS_REF
ARG NCCL_PACKAGE_VERSION

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

LABEL org.opencontainers.image.title="nccl-test" \
      org.opencontainers.image.description="Nebius first-party NCCL benchmark runtime for Kubeflow MPIJob validation" \
      org.opencontainers.image.source="https://github.com/nebius/nebius-ps-services" \
      org.opencontainers.image.documentation="https://github.com/NVIDIA/nccl-tests" \
      org.opencontainers.image.vendor="Nebius"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libnccl2 \
        libopenmpi3 \
        openmpi-bin \
        openssh-client \
        openssh-server \
        rdma-core \
        ibverbs-providers \
        libibverbs1 \
    && if [[ -n "${NCCL_PACKAGE_VERSION}" ]]; then \
        apt-get install -y --no-install-recommends "libnccl2=${NCCL_PACKAGE_VERSION}"; \
      fi \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /etc/ssh/ssh_config.d /etc/ssh/sshd_config.d /root/.ssh /run/sshd /var/run/sshd \
    && chmod 700 /root/.ssh \
    && printf '%s\n' \
        'Host *' \
        '  StrictHostKeyChecking no' \
        '  UserKnownHostsFile /dev/null' \
      > /etc/ssh/ssh_config.d/10-mpi-operator.conf \
    && printf '%s\n' \
        'PermitRootLogin yes' \
        'PasswordAuthentication no' \
        'StrictModes no' \
        'UsePAM no' \
      > /etc/ssh/sshd_config.d/10-mpi-operator.conf

COPY --from=build /src/nccl-tests/build /opt/nccl_tests/build
COPY --from=build /src/nccl-tests/LICENSE.txt /opt/nccl_tests/LICENSE.upstream.txt
COPY --from=build /src/nccl-tests/README.md /opt/nccl_tests/README.upstream.md

WORKDIR /opt/nccl_tests

RUN while IFS= read -r binary; do \
      ln -sf "${binary}" "/usr/local/bin/$(basename "${binary}")"; \
    done < <(find /opt/nccl_tests/build -maxdepth 1 -type f -name '*_perf' | sort)

ENV NCCL_TESTS_HOME=/opt/nccl_tests \
    CUDA_IMAGE_TAG=${CUDA_IMAGE_TAG} \
    NCCL_TESTS_REF=${NCCL_TESTS_REF} \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility \
    OMPI_ALLOW_RUN_AS_ROOT=1 \
    OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1 \
    PATH=/opt/nccl_tests/build:${PATH}

CMD ["/usr/sbin/sshd", "-De"]
