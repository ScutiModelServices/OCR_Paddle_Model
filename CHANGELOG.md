# Changelog

## Unreleased

- First version, built from `OCR_SGLang_Model`: one service with two OCR models chosen by the hardware. PaddleOCR-VL (GPU model server plus layout pipeline) on a Linux NVIDIA GPU with compute capability 8.0 or higher and `min_vram_mb` of memory; PP-OCRv5 mobile on the CPU otherwise. `device` setting `auto`, `gpu` or `cpu`; `GET /v1/device` and the admin page show the choice and the reason.
- Only the chosen model's weights are checked, downloaded and loaded (two Hub repositories each; one combined revision string on the Updates card).
- `POST /v1/ocr` (one PNG, JPEG or WebP image as the body) replaces SGLang's chat forwarding. No streaming, no answer cache, calls run one at a time.
- Docker: one Dockerfile with `FLAVOR=cpu|gpu` build argument, `docker-compose.gpu.yml` adds the NVIDIA GPU. `run.bat` Run starts the CPU model natively (`uv sync --group cpu`).
- Not run yet: the Dockerfile, the weight repository ids, the PaddleOCR and PaddleOCRVL arguments and the GPU memory minimum are unverified.
