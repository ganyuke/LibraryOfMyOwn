# LibraryOfMyOwn container image.
#
# The runtime stage is distroless (glibc + libstdc++, no shell or package
# manager). Python comes from uv's standalone builds, and pandoc and typst are
# static release binaries, so nothing from a distro package is needed at runtime.
# git is left out on purpose: repository cleanup falls back to dulwich.
#
# Build:  docker build -t libmyown .
# Run:    docker run -p 4033:4033 -v libmyown-data:/data libmyown

ARG UV_VERSION=0.12.20

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

FROM debian:trixie-slim AS build

ARG TARGETARCH
ARG TYPST_VERSION=0.15.1
ARG PANDOC_VERSION=3.12

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl xz-utils \
 && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /usr/local/bin/uv

# Same release archives and checksums as scripts/deploy-pi.sh. Bump together.
RUN set -eu; \
    case "$TARGETARCH" in \
      amd64) \
        typst_asset=typst-x86_64-unknown-linux-musl; \
        typst_sha=a6d077d0a95eed5a2eba715b2dae06be954f624ccbf85758a03f389ded33118c; \
        pandoc_sha=67d7d011fed8c8543306022b985b9b2499ab9b74818df91d8727c7e9ebc5ba06 ;; \
      arm64) \
        typst_asset=typst-aarch64-unknown-linux-musl; \
        typst_sha=5aa8d74a3d906e60ea12a66ac2f37f8eef1b14cbad7182a745e393a10c23dcee; \
        pandoc_sha=6cefcf7100e23a99447c26f89d1ff5b253f3407fcef99a9e27ae06f3ed16cb82 ;; \
      *) echo "unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    mkdir -p /opt/libmyown/bin /tmp/dl; cd /tmp/dl; \
    curl -fsSL -o typst.tar.xz \
      "https://github.com/typst/typst/releases/download/v${TYPST_VERSION}/${typst_asset}.tar.xz"; \
    echo "${typst_sha}  typst.tar.xz" | sha256sum -c -; \
    tar -xJf typst.tar.xz; \
    install -m 755 "${typst_asset}/typst" /opt/libmyown/bin/typst; \
    curl -fsSL -o pandoc.tar.gz \
      "https://github.com/jgm/pandoc/releases/download/${PANDOC_VERSION}/pandoc-${PANDOC_VERSION}-linux-${TARGETARCH}.tar.gz"; \
    echo "${pandoc_sha}  pandoc.tar.gz" | sha256sum -c -; \
    tar -xzf pandoc.tar.gz; \
    install -m 755 "pandoc-${PANDOC_VERSION}/bin/pandoc" /opt/libmyown/bin/pandoc; \
    rm -rf /tmp/dl

# The venv links to this interpreter by absolute path, so both stages must use
# the same /opt/libmyown/python location.
ENV UV_PYTHON_INSTALL_DIR=/opt/libmyown/python \
    UV_PYTHON_PREFERENCE=only-managed \
    UV_PROJECT_ENVIRONMENT=/opt/libmyown/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /opt/libmyown/app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --extra pdf --no-install-project

# Drop the parts of the interpreter a headless server never loads (Tk, the test
# suite, pip, headers).
RUN set -eu; cd /opt/libmyown/python/cpython-*.*.*-linux-*; \
    rm -rf include share lib/pkgconfig lib/itcl* lib/libtcl* lib/tcl* lib/thread* lib/tk* \
      lib/python3.*/test lib/python3.*/idlelib lib/python3.*/tkinter lib/python3.*/turtledemo \
      lib/python3.*/ensurepip lib/python3.*/site-packages/pip* lib/python3.*/lib-dynload/_tkinter*

COPY libmyown ./libmyown
COPY pdf-scripts ./pdf-scripts
RUN mkdir -p /data


FROM gcr.io/distroless/cc-debian13:nonroot

COPY --from=build /opt/libmyown /opt/libmyown
COPY --from=build --chown=nonroot:nonroot /data /data

ENV PATH=/opt/libmyown/bin:/opt/libmyown/venv/bin:/usr/bin:/bin \
    LANG=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATA_DIR=/data \
    PDF_SCRIPTS=/opt/libmyown/app/pdf-scripts \
    HOST=0.0.0.0 \
    PORT=4033

WORKDIR /opt/libmyown/app
USER nonroot
VOLUME /data
EXPOSE 4033

ENTRYPOINT ["/opt/libmyown/venv/bin/python", "-m", "libmyown.main"]
