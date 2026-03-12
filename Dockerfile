# Official NVIDIA CUDA base — no community layers
FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ARG COMFYUI_VERSION=v0.16.4
LABEL org.opencontainers.image.title="ComfyUI" \
      org.opencontainers.image.description="ComfyUI" \
      org.opencontainers.image.source="https://github.com/Comfy-Org/ComfyUI" \
      org.opencontainers.image.version="${COMFYUI_VERSION}"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-dev \
    git \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Clone official ComfyUI at a pinned tag — verify the tag at:
# https://github.com/Comfy-Org/ComfyUI/releases
RUN git clone --depth 1 --branch ${COMFYUI_VERSION} https://github.com/Comfy-Org/ComfyUI.git .

# Install PyTorch from official PyTorch index (not community PyPI)
RUN pip3 install --no-cache-dir \
    torch \
    torchvision \
    torchaudio \
    --index-url https://download.pytorch.org/whl/cu124

# Install ComfyUI dependencies from the cloned repo's own requirements
RUN pip3 install --no-cache-dir -r requirements.txt

RUN useradd -m -u 1000 comfyui && chown -R comfyui:comfyui /app
USER comfyui

EXPOSE 8188

CMD ["python3", "main.py", "--listen", "0.0.0.0", "--port", "8188"]
