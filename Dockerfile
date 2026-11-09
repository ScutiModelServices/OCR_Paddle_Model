# syntax=docker/dockerfile:1
# Two flavors from one file (the service picks its model at run time, see engine.choose):
#   cpu (default)  docker compose up -d --build
#                  python slim + PaddlePaddle CPU + PaddleOCR. Runs the PP-OCRv5 model only.
#   gpu            docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
#                  CUDA 12.6 + PaddlePaddle GPU + PaddleOCR-VL and its vLLM server. Needs an NVIDIA GPU with compute
#                  capability 8.0 or higher, the host driver and the NVIDIA Container Toolkit. It can still run the CPU model.
# UNTESTED: written from Baidu's PaddleOCR-VL docs without a GPU host or a full build here. Package versions are not pinned
# yet; pin them (and regenerate nothing else) after the first successful build.
ARG BASE=python:3.12-slim
FROM ${BASE}
ARG FLAVOR=cpu
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates libgl1 libglib2.0-0 build-essential \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
ENV SERVICE_WEB_DIR=/app/web SERVICE_TOKENS_FILE=/data/tokens.json SERVICE_MODELS_DIR=/models HF_HOME=/models SERVICE_HOST=0.0.0.0 \
    PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv UV_PYTHON=3.12 \
    PATH="/opt/venv/bin:$PATH" PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True
WORKDIR /app

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
# Never install both PaddlePaddle builds (Baidu's docs). The CPU wheel is on PyPI; the GPU wheel is on Baidu's CUDA 12.6 index.
RUN if [ "$FLAVOR" = "gpu" ]; then \
        uv pip install paddlepaddle-gpu --index-url https://www.paddlepaddle.org.cn/packages/stable/cu126/ --extra-index-url https://pypi.org/simple \
        && uv pip install "paddleocr[doc-parser]" \
        && paddleocr install_genai_server_deps vllm; \
    else \
        uv pip install paddlepaddle "paddleocr[doc-parser]"; \
    fi
COPY src ./src
COPY web ./web

ENV SERVICE_PORT=29262
EXPOSE 29262
CMD ["python", "src/app.py"]
