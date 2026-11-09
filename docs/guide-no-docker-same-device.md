# OCR_Paddle_Model: run without Docker

Runs the **CPU model** (PP-OCRv5) directly on this PC. The GPU model (PaddleOCR-VL) needs a Linux container, so on Windows a native run always uses the CPU model even with an NVIDIA GPU. For the GPU model use Docker (see [guide-docker-same-device.md](guide-docker-same-device.md)) or a Linux host as described at the end.

## Requirements

- [uv](https://docs.astral.sh/uv/) (`winget install astral-sh.uv`). It installs Python for you.
- Internet for admin Download (tens of MB) and online version checks; loading itself is local only.
- Port 29262 free.

## Steps

1. Configure: `copy .env.example .env.local` and set `SERVICE_ADMIN_PASSWORD` (empty = no auth and no admin page, fine for a local test).
2. Start: `run.bat run`. It runs `uv sync --group cpu` (installs PaddlePaddle and PaddleOCR, large) and then `python src/app.py --dotenv`.
3. Open `http://127.0.0.1:29262/admin/`, sign in, go to **Updates** and press **Download**. Startup never downloads weights.
4. Check: `curl http://127.0.0.1:29262/health`. `ready` is `true` once the model has loaded.
5. Make a token on the admin page and try a call (see [guide-cli.md](guide-cli.md)).

Model files go to `SERVICE_MODELS_DIR` (default `~/.cache/paddle-ocr`); tokens and settings go to the folder of `SERVICE_TOKENS_FILE` (default `tokens.json` in the current folder).

## Linux with an NVIDIA GPU, no Docker

Not tested. Needs compute capability 8.0 or higher and CUDA 12.6 or higher. Install Baidu's GPU packages by hand as the `gpu` flavor of the `Dockerfile` does (`paddlepaddle-gpu` from Baidu's CUDA 12.6 index, `paddleocr[doc-parser]`, then `paddleocr install_genai_server_deps vllm`), then `uv run python src/app.py`. Do not install the CPU and GPU PaddlePaddle builds together.

## Problems

| Symptom | Fix |
|---|---|
| `ready` stays `false` | Download the weights from the Updates card. |
| `uv sync --group cpu` fails | PaddlePaddle may have no wheel for your Python or platform; set `UV_PYTHON=3.12` and retry. |
| Device is `cpu` but you expected the GPU | Expected on Windows. On Linux, `GET /v1/device` gives the reason. |
