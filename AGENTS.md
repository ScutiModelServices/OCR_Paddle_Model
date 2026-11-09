# OCR_Paddle_Model

PaddleOCR behind the shared model-service admin page, tokens and settings. It picks one model at start: PaddleOCR-VL (GPU) or PP-OCRv5 (CPU). Port 29262, env prefix `SERVICE_`, token prefix `paddle_`. Its caller is `AI/ToolServices/Reader`. Built from `OCR_SGLang_Model`.

## Agent instructions
This file is the one instruction file for every coding agent; `CLAUDE.md` only imports it (`@AGENTS.md`). Edit this file, never `CLAUDE.md`.

## Commands (from this folder, venv `.venv`, managed by uv)
- Install: `uv sync` (`winget install astral-sh.uv` if uv is missing). `uv sync --group cpu` adds PaddlePaddle and PaddleOCR for a native run.
- Test: `uv run pytest -q` (no Paddle, GPU or `nvidia-smi` needed: both backends are fakes, the GPU server is a sleeping Python process). Set `UV_CACHE_DIR=.uv_cache UV_LINK_MODE=copy`.
- Run: `run.bat run` (CPU model, native), or `docker compose --env-file .env.compose up -d --build` (CPU), or with `-f docker-compose.yml -f docker-compose.gpu.yml` for the GPU flavor.

## Rules
- Load downloaded weights only. Startup, warm-up and inference never download; only the authenticated Updates Download callback may fetch weights. Missing weights keep health/admin available and inference returns 503 with an admin Download hint.
- Only the model chosen for this machine is looked at, downloaded or loaded (`PaddleEngine.backend.repos`). Never download both.
- Follow the `model-service-parity` skill. `web/index.html`, `web/playground.js`, `src/tokens.py`, `src/settings.py`, `src/updates.py`, `src/progress.py` and `.dockerignore` are copies shared with the other model services (`python tools/check_sync.py`).
- `engine.choose` is the device rule: `auto` = GPU only on Linux with compute capability 8.0+ and `min_vram_mb`; `gpu` forces it when an NVIDIA GPU is found; `cpu` never. Keep the thresholds in step with Baidu's PaddleOCR-VL docs. `min_vram_mb` (8192) is a guess, not a measurement.
- The GPU backend runs `paddleocr genai_server` (vLLM) as a child process and the `PaddleOCRVL` pipeline in this process; the CPU backend runs `PaddleOCR` from local folders with every auto-downloading sub-model switched off. Neither was ever run against the real models; the repo ids (`CPU_REPOS`, `GPU_REPOS`), the pipeline arguments and the Dockerfile are unverified. Say so in the docs until that is done.
- API: `POST /v1/ocr` (image body, no options) returns `{text, format, model, device, duration_ms}`; `GET /v1/device`. A token's "projects" list limits the model, which is `paddleocr-vl` on a GPU machine and `pp-ocrv5` otherwise. Calls run one at a time (one replica); no answer cache, no streaming.
- Behaviour changes update `README.md`, `deployment.md`, `docs/guide-web-ui.md`, `.env.example` and `CHANGELOG.md` in the same commit.
- Never read or print `.env`, `.env.local`, `.env.compose`, `tokens.json` or `settings.json`.
