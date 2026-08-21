#!/usr/bin/env bash
# Create a local MarkLLM checkout and venv for the Layer B detection harness.
#
# MarkLLM (THU-BPM, Apache-2.0) is never vendored into this repo. This script
# clones it into ./vendor and installs torch plus the transformers stack into a
# separate venv, so the core service stays dependency-free.
#
#   bash scripts/setup_markllm.sh
#   source vendor/markllm-venv/bin/activate
#   export WATERMARKS_MARKLLM_PATH="$PWD/vendor/MarkLLM"

set -euo pipefail

REPO_URL="https://github.com/THU-BPM/MarkLLM.git"
# Pin to a specific commit for reproducibility. Override with MARKLLM_COMMIT.
MARKLLM_COMMIT="${MARKLLM_COMMIT:-main}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR="$ROOT/vendor"
CHECKOUT="$VENDOR/MarkLLM"
VENV="$VENDOR/markllm-venv"

mkdir -p "$VENDOR"

# ── clone, sparse ─────────────────────────────────────────────────────────
if [ -d "$CHECKOUT/.git" ]; then
  echo "==> MarkLLM checkout already exists at $CHECKOUT"
else
  echo "==> Cloning MarkLLM (sparse) into $CHECKOUT"
  git clone --depth 1 --filter=blob:none --sparse "$REPO_URL" "$CHECKOUT"
  git -C "$CHECKOUT" sparse-checkout set \
      watermark config utils exceptions evaluation/dataset.py visualize
fi

if [ "$MARKLLM_COMMIT" != "main" ]; then
  echo "==> Pinning to $MARKLLM_COMMIT"
  git -C "$CHECKOUT" fetch --depth 1 origin "$MARKLLM_COMMIT"
  git -C "$CHECKOUT" checkout "$MARKLLM_COMMIT"
fi

echo "==> Checkout is at commit $(git -C "$CHECKOUT" rev-parse HEAD)"

# ── venv ──────────────────────────────────────────────────────────────────
if [ ! -d "$VENV" ]; then
  echo "==> Creating venv at $VENV"
  python3 -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --upgrade pip wheel >/dev/null

# ── pick a torch build ────────────────────────────────────────────────────
TORCH_INDEX="cpu"
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  CUDA_VER="$(nvidia-smi | grep -oP 'CUDA Version: \K[0-9]+\.[0-9]+' | head -1 || true)"
  echo "==> NVIDIA GPU detected (CUDA ${CUDA_VER:-unknown})"
  case "${CUDA_VER:-0}" in
    12.*) TORCH_INDEX="cu121" ;;
    11.*) TORCH_INDEX="cu118" ;;
    *)    echo "    Unrecognized CUDA version; falling back to the CPU build" ;;
  esac
else
  echo "==> No GPU detected; installing the CPU build"
fi

echo "==> Installing torch (${TORCH_INDEX})"
pip install --index-url "https://download.pytorch.org/whl/${TORCH_INDEX}" \
    "torch==2.4.1"

echo "==> Installing the transformers stack"
pip install \
    "transformers==4.44.2" \
    "tokenizers==0.19.1" \
    "datasets==2.21.0" \
    "accelerate==0.34.2" \
    "sentencepiece==0.2.0" \
    "nltk==3.9.1" \
    "numpy==1.26.4" \
    "scipy==1.14.1" \
    "huggingface_hub==0.25.1"

cat <<EOF

Done.

  source $VENV/bin/activate
  export WATERMARKS_MARKLLM_PATH="$CHECKOUT"

Then, from the repo root:

  python -m scrubai.cli.detect_text_watermark --scheme kgw detect sample.txt
  python -m scrubai.cli.detect_text_watermark --scheme kgw generate "Once upon a time" --with-unwatermarked

Detection only tells you whether text carries a mark under the scheme, key, and
config you supply. A negative result is not evidence that no watermark exists.
EOF
