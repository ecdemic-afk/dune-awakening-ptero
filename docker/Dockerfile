# syntax=docker/dockerfile:1.7
#
# Runtime image for the Dune: Awakening Pterodactyl egg (dune-awakening-ptero).
# Adapted from Sergentval/pelican-egg-dune-awakening's image (MIT).
#
# Provides what the battlegroup needs beyond a stock yolk:
#   - a writable Kubernetes service-account folder (the Funcom Director reads
#     its token there; the Kubernetes stand-in writes it),
#   - /mnt/server -> /home/container (the unpacked game binaries carry
#     install-time paths),
#   - Python 3.11 (tomllib) for the supervisor, 32-bit libs + patchelf for
#     in-place game updates, tini as PID 1.
#
# Published as ghcr.io/ecdemic-afk/dune-awakening-ptero by
# .github/workflows/image.yml (on every change here and weekly). To build by hand:
#   docker build -t ghcr.io/<you>/dune-awakening-ptero:latest docker/

FROM debian:bookworm-slim

LABEL org.opencontainers.image.source="https://github.com/ecdemic-afk/dune-awakening-ptero"
LABEL org.opencontainers.image.description="Pelican Wings runtime for Dune: Awakening dedicated server"
LABEL org.opencontainers.image.licenses="MIT"

ENV DEBIAN_FRONTEND=noninteractive \
    LC_ALL=C.UTF-8 \
    LANG=C.UTF-8

# Distro-stable packages — pinning them would break every Debian point release
# and add no real reproducibility (we don't ship deterministic builds anyway).
# hadolint ignore=DL3008
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        coreutils \
        curl \
        file \
        gawk \
        grep \
        gzip \
        iproute2 \
        jq \
        lib32gcc-s1 \
        lib32stdc++6 \
        openssl \
        patchelf \
        procps \
        python3 \
        sed \
        tar \
        tini \
        tzdata \
        util-linux \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# Pelican convention: runtime user is `container` at UID 988 with home at
# /home/container. Matches ghcr.io/parkervcp/yolks images so other Pelican
# tooling that hard-codes this UID continues to work.
RUN useradd --uid 988 --user-group --create-home --home-dir /home/container --shell /bin/bash container

# AMP-compat symlink. CubeCoders' install.sh runs `patchelf --set-interpreter`
# with absolute install-time paths (e.g. /mnt/server/extracted/postgres/lib/
# ld-musl-x86_64.so.1). AMP itself uses /mnt/server as both install AND
# runtime path — paths match, ELFs load. Pelican Wings installs into
# /mnt/server (via the install container) but mounts the runtime volume at
# /home/container, so the baked-in interpreter paths point at a directory
# that doesn't exist at runtime → 'env: <bin>: No such file or directory'
# from every musl-linked Funcom binary. Symlinking /mnt/server -> /home/
# container at image build time makes the patchelf'd paths resolve at
# runtime without re-running patchelf on every container start.
RUN ln -s /home/container /mnt/server

# K8s ServiceAccount mount — see header. The Funcom Director's IGWO
# component reads /var/run/secrets/kubernetes.io/serviceaccount/{token,ca.crt}
# at the standard path; mock-k8s-go writes them there as the container user.
#
# Pelican Wings starts containers with ReadonlyRootfs=true and only mounts
# /home/container and /tmp as writable. Image-built directories anywhere
# else are invisible — the runtime can't write to them. The fix is to
# declare this path as a Docker VOLUME: at container creation Docker mounts
# an anonymous writable volume here and copies the image's initial content
# (this empty 0777 dir) into it. The 0777 mode is necessary because Wings
# may resolve the runtime UID dynamically, and chown'ing in the image is
# moot once Docker is operating on a fresh anonymous volume.
RUN mkdir -p /var/run/secrets/kubernetes.io/serviceaccount \
 && chmod 0777 /var/run/secrets/kubernetes.io/serviceaccount
VOLUME ["/var/run/secrets/kubernetes.io/serviceaccount"]

USER container
WORKDIR /home/container
STOPSIGNAL SIGTERM

# tini as init: forwards signals to the whole process group (-g) so the
# console.sh trap actually fires across the 10+ background services it
# orchestrates, and reaps any zombies left behind by setsid'd children.
ENTRYPOINT ["/usr/bin/tini", "-g", "--"]
# Pelican Wings passes the egg's startup command as $STARTUP and starts the
# container with the image's default CMD. The CMD must therefore eval
# $STARTUP so Wings can actually launch the server. Falls back to a plain
# shell if STARTUP is unset (useful for SFTP-only / debugging sessions).
CMD ["/bin/bash", "-c", "exec ${STARTUP:-bash}"]
