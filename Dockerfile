# Official NVIDIA CUDA base — no community layers
FROM nvidia/cuda:13.0.2-cudnn-runtime-ubuntu22.04

ARG COMFYUI_VERSION
LABEL org.opencontainers.image.title="ComfyUI" \
      org.opencontainers.image.description="ComfyUI" \
      org.opencontainers.image.source="https://github.com/Comfy-Org/ComfyUI" \
      org.opencontainers.image.version="${COMFYUI_VERSION}"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# build-essential is a runtime dependency, not just a build one: Triton
# JIT-compiles its launcher stubs on first use of the fp8 kernels and shells
# out to a C compiler to do it.
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-dev \
    build-essential \
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
    --index-url https://download.pytorch.org/whl/cu130

# Install ComfyUI dependencies from the cloned repo's own requirements
RUN pip3 install --no-cache-dir -r requirements.txt

# ComfyUI-MultiGPU's peer-access check dlopens the unversioned libcudart.so,
# which only ships in the CUDA devel images. Link it against the runtime's
# versioned library instead of pulling in that much larger base. Without this
# every cross-device tensor access raises an OSError.
RUN set -eu; \
    lib="$(ldconfig -p | awk '/libcudart\.so\.[0-9]+ / {print $NF; exit}')"; \
    test -n "$lib"; \
    ln -s "$(basename "$lib")" "$(dirname "$lib")/libcudart.so"; \
    ldconfig; \
    python3 -c "import ctypes; ctypes.CDLL('libcudart.so')"

# ComfyUI-Manager is a pip package as of 4.x and must not live in custom_nodes.
# ComfyUI pins the version it expects, so track that file rather than a pin of
# our own. Activated by --enable-manager below.
RUN pip3 install --no-cache-dir -r manager_requirements.txt

# Custom nodes owned by this image. The comfyui StatefulSet mounts a volume
# over /app/custom_nodes, so its seed step re-copies these on every start —
# bumping a pin here is what actually rolls them out.
#
# Neither publishes tags, so both pin to a commit.
ARG MULTIGPU_VERSION=b51c99a525e9607e43545ee2a8b7694c74a4775a
ARG GGUF_VERSION=6ea2651e7df66d7585f6ffee804b20e92fb38b8a
RUN set -eu; \
    pin() { \
      git init -q "custom_nodes/$1"; \
      git -C "custom_nodes/$1" remote add origin "$2"; \
      git -C "custom_nodes/$1" fetch -q --depth 1 origin "$3"; \
      git -C "custom_nodes/$1" checkout -q FETCH_HEAD; \
    }; \
    pin ComfyUI-MultiGPU https://github.com/pollockjj/ComfyUI-MultiGPU.git "${MULTIGPU_VERSION}"; \
    pin ComfyUI-GGUF     https://github.com/city96/ComfyUI-GGUF.git        "${GGUF_VERSION}"; \
    pip3 install --no-cache-dir -r custom_nodes/ComfyUI-GGUF/requirements.txt

RUN useradd -m -u 1000 comfyui && chown -R comfyui:comfyui /app
USER comfyui

EXPOSE 8188

CMD ["python3", "main.py", "--listen", "0.0.0.0", "--port", "8188", "--enable-manager"]
