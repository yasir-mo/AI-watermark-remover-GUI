# Core service. Stdlib-only Python plus the three optional CLI tools.
# No pip install of anything: the whole point is that the core has no deps.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    WATERMARKS_MAX_INPUT_BYTES=268435456

# exiftool and qpdf come from Debian. c2patool is a Rust binary fetched from
# the ContentAuth release page; if the download fails the build still succeeds
# and the service reports c2patool as missing via /capabilities.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libimage-exiftool-perl \
        qpdf \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

ARG C2PATOOL_VERSION=v0.9.12
RUN set -eux; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) target="x86_64-unknown-linux-gnu" ;; \
      arm64) target="aarch64-unknown-linux-gnu" ;; \
      *) target="" ;; \
    esac; \
    if [ -n "$target" ]; then \
      url="https://github.com/contentauth/c2patool/releases/download/${C2PATOOL_VERSION}/c2patool-${C2PATOOL_VERSION}-${target}.tar.gz"; \
      if curl -fsSL "$url" -o /tmp/c2patool.tar.gz; then \
        tar -xzf /tmp/c2patool.tar.gz -C /tmp; \
        install -m 0755 "$(find /tmp -name c2patool -type f | head -1)" /usr/local/bin/c2patool; \
        rm -rf /tmp/c2patool.tar.gz /tmp/c2patool*; \
      else \
        echo "c2patool download failed; continuing without it"; \
      fi; \
    fi

RUN useradd --create-home --shell /usr/sbin/nologin --uid 10001 wmtk
WORKDIR /app

# Build context is deny-by-default via .dockerignore; copy only what runs.
COPY --chown=wmtk:wmtk wmtk/ ./wmtk/
COPY --chown=wmtk:wmtk ui/ ./ui/

USER wmtk

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/health', timeout=4).status==200 else 1)"

# 0.0.0.0 inside the container; compose maps it to 127.0.0.1 on the host only.
CMD ["python", "-m", "wmtk.server", "--host", "0.0.0.0", "--port", "8765", "--allow-non-local"]
