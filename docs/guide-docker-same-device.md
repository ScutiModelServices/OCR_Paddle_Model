# OCR_Paddle_Model: Docker Desktop on this PC

Run PaddleOCR in Docker Desktop on the machine you work on, so other programs on it can call it. The service uses PaddleOCR-VL on a capable NVIDIA GPU and PP-OCRv5 on the CPU otherwise. The CPU flavor runs anywhere Docker runs. The GPU flavor needs a Linux container with GPU support (on Windows: the WSL2 backend, never tried here). There is also a native CPU run (see [guide-no-docker-same-device.md](guide-no-docker-same-device.md)).

## Requirements

- Docker Desktop with Compose v2 (Windows), or Docker with Compose v2 on Linux.
- GPU flavor only: an NVIDIA GPU with compute capability 8.0 or higher, a current driver, the NVIDIA Container Toolkit (Linux) or GPU support in Docker Desktop. Check it: `docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu24.04 nvidia-smi` must print your card.
- This is untested. The images were never built here, and Baidu states no VRAM minimum for PaddleOCR-VL.
- Internet at the first build (PaddlePaddle and PaddleOCR packages) and at admin Download.
- Disk: the CPU image is moderate; the GPU image is large (Baidu's vLLM image is about 13 GB).
- Port 29262 free (`SERVICE_PORT`).

## Steps

1. Configure. In `OCR_Paddle_Model/`:
   ```bash
   cp .env.example .env
   ```
   Set `SERVICE_ADMIN_PASSWORD` (empty = no auth and no admin page, fine for a local test). Leave `SERVICE_PORT=29262`.
2. Download and start. Pick the flavor for this machine:
   ```bash
   docker compose --env-file .env.compose up -d --build                                                   # CPU
   docker compose --env-file .env.compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build   # NVIDIA GPU
   ```
   The service picks its model when it starts. The admin Download then installs only that model's weights into the `ocr_paddle_model_models` volume (CPU model: tens of MB; GPU model: a few GB). `ocr_paddle_model_lb` starts only after the service is healthy
   (up to 300 s grace). Watch with `docker compose ps` and `docker compose logs -f ocr_paddle_model`.
3. Check:
   ```bash
   curl http://127.0.0.1:29262/health
   ```
   `ready` is `false` until the model has loaded, then `true`.
4. Open the admin page: `http://127.0.0.1:29262/admin/` (see [guide-web-ui.md](guide-web-ui.md)) and make a token for each caller.
5. Try a call:
   ```bash
   curl http://127.0.0.1:29262/v1/ocr -H "X-Internal-Token: paddle_..." --data-binary @page.png
   ```
   Expect JSON with `text`, `model` and `device`. `curl http://127.0.0.1:29262/v1/device -H "X-Internal-Token: paddle_..."` says which model this machine uses and why.
6. Callers connect to the published port. The caller holds the URL and token:
   - Caller on the host: `http://127.0.0.1:29262`.
   - Caller in another Docker stack on the same host: `http://host.docker.internal:29262`.

## Settings

| Variable | Default | What it does |
|---|---|---|
| `SERVICE_ADMIN_PASSWORD` | empty | Unlocks `/admin/`; with it set, every call except `/health` needs a token. Empty = no auth. |
| `SERVICE_PORT` | `29262` | Port `ocr_paddle_model_lb` publishes. |
| `SERVICE_NUM_THREAD` | none | Starting value for the CPU threads card. The CPU model is rebuilt with it before the next call; the GPU model ignores it. |
| `SERVICE_DEVICE` | `auto` | `auto`, `gpu` or `cpu` (see the README). Read at start. |

The other options are on the admin Settings card; `.env.example` lists the matching variables.

## Memory (GPU flavor)

If loading runs out of GPU memory, lower *GPU memory share* (`gpu_mem_fraction`, vLLM `gpu-memory-utilization`) and restart the service. Baidu states no VRAM minimum; `min_vram_mb` (8192) is a guess for `auto`. The GPU compose file gives the container `shm_size: 8gb`. The CPU model needs no GPU and little memory.

## Daily use

```bash
docker compose ps          # state
docker compose logs -f ocr_paddle_model
docker compose down        # stop (the weights volume is kept)
docker compose --env-file .env.compose up -d --build && docker compose restart ocr_paddle_model_lb   # after a code change
```

## Problems

| Symptom | Fix |
|---|---|
| `address already in use` on 29262 | Another copy (`run.bat`, or the same stack on another compose project) is running. Stop it or change `SERVICE_PORT`. |
| `/health` shows `ready: false` for minutes | Download weights from the Updates card, then wait for loading. Check `docker compose logs -f ocr_paddle_model`. |
| 401 Invalid token | The caller's token is missing or revoked; make one on `/admin/`. |
| `/health` shows `device: cpu` on a machine with a GPU | `GET /v1/device` gives the reason (compute capability below 8.0, less memory than `min_vram_mb`, GPU not visible to Docker, or the CPU flavor image). Fix it, or set `device` to `gpu` on the Settings card, then restart the service. |
| GPU flavor: container restarts in a loop or the model never loads | Out of GPU memory. Lower `gpu_mem_fraction` on the Settings card, or free the GPU. |
| `/health` stays `ready: false` | Download weights from the Updates card (only the chosen model's), then wait for loading. Read `docker compose logs ocr_paddle_model`. |
| 503 "Model is not ready" | Same cause as `ready: false`. |
| 413 | The request is over *Largest request*. |
| 429 | Too many calls wait (*Calls allowed to wait*) or the token's rate is used up; see `Retry-After`. |
| The model server keeps restarting (GPU) | It crashes at start; read the log above the restart line. The service tries again every 5 seconds. |

The load balancer also joins external `citadel_core` as `ocr_paddle_model_lb`. Start the Citadel stack first so that network exists; tool services on it use `http://ocr_paddle_model_lb:29262`. Docker project and API image are `ocr_paddle_model` and `ocr_paddle_model:latest`; persistent volumes have explicit `ocr_paddle_model_*` names. Volume migration and rollback instructions are in [CHANGELOG.md](../CHANGELOG.md).

Before first inference, follow [Downloaded weights](guide-web-ui.md#downloaded-weights): open `/admin/` → Updates → Download. Startup never downloads weights.
