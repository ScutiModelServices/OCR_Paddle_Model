# OCR_Paddle_Model

PaddleOCR served over HTTP, with one model chosen for the machine it runs on:

- **PaddleOCR-VL** (Apache-2.0) on a capable **NVIDIA GPU**: Markdown with layout. The container runs Baidu's `paddleocr genai_server` (vLLM) as a child process and the PaddleOCR-VL pipeline in the service.
- **PP-OCRv5** (Apache-2.0) on the **CPU** everywhere else: plain text lines. Small and fast, no GPU needed, runs natively on Windows.

The service probes the hardware at start (`nvidia-smi`) and loads one model. Only that model's weights are downloaded. It puts the same admin page, tokens, rate limits, metrics, settings and update checks in front of it as the other model services. The caller is `ToolServices/Reader`.

A token's *projects* list on the admin page limits the model it may use. The model is `paddleocr-vl` on a GPU machine and `pp-ocrv5` otherwise; `GET /v1/device` says which.

> **Not run yet.** Written from Baidu's PaddleOCR and PaddleOCR-VL docs without a GPU host. The Dockerfile has never been built, the Hub repository ids of the weights and the PaddleOCR / PaddleOCRVL arguments are unverified, and the tests use fakes for both models. The 8192 MiB GPU memory minimum for `auto` is a guess; Baidu states no VRAM figure.

## Which model a machine gets

| `device` setting | Result |
|---|---|
| `auto` (default) | GPU model if the machine is Linux, has an NVIDIA GPU with compute capability 8.0 or higher and at least `min_vram_mb` of memory. Otherwise the CPU model. |
| `gpu` | GPU model whenever an NVIDIA GPU is found on Linux; otherwise the CPU model. |
| `cpu` | CPU model always. |

The admin page and `GET /v1/device` show the choice and the reason, for example "NVIDIA RTX 3050 has 4096 MiB; the GPU model needs 8192 MiB." The device is chosen when the service starts; change the setting, then restart the service.

## API

| Call | What it does |
|---|---|
| `POST /v1/ocr` | The body is one PNG, JPEG or WebP page image. Returns `{"text", "format", "model", "device", "duration_ms"}`. `format` is `markdown` (GPU model) or `text` (CPU model). No options. |
| `GET /v1/device` | `{"device", "reason", "model", "format", "gpu", "vram_mb", "compute"}`. |
| `GET /health` | `{"status": "ok", "model", "ready", "num_thread"}`; `ready` is true when the chosen model is loaded. No token needed. |
| `POST /admin/restart` | Admin password. Unloads the model, probes the hardware again and loads it. |

Errors: 413 over the *Largest image* setting, 415 not a PNG, JPEG or WebP, 422 image cannot be read, 429 when too many calls wait or the token's rate is used up, 503 when the model is not loaded (weights not downloaded, or still loading).

```bash
curl -H "X-Internal-Token: paddle_..." --data-binary @page.png http://<host>:29262/v1/ocr
```

Calls run one at a time. A multi-page document is one call per page; `ToolServices/Reader` does that.

## Admin page

`/admin/` (password `SERVICE_ADMIN_PASSWORD`): tokens with model scope, rate limit and expiry, usage numbers, the CPU threads card (the CPU model is rebuilt with the new count before the next call; the GPU model ignores it), the Settings card and the Updates card. **Check now** compares the Hub revisions of the chosen model's repositories with the ones downloaded here; **Download new weights** downloads them into the models volume and loads them. `/metrics` (admin password) is Prometheus text per token; `paddle_request_seconds` is a histogram of how long each `/v1` call took.

## Run

Weights load only from downloaded files. An empty installation still serves `/admin/` and `/health` with `ready: false`; `POST /v1/ocr` returns 503. Open `/admin/` → **Updates** → **Download** before the first call. Checks run online without downloading.

```bash
docker compose --env-file .env.compose up -d --build        # CPU machine, http://<host>:29262
docker compose --env-file .env.compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build   # NVIDIA GPU, needs the NVIDIA Container Toolkit
```

Windows without Docker: `run.bat` → Run (always the CPU model). See `deployment.md`.

## Tests

```bash
uv sync
uv run pytest
```

No Paddle packages, GPU or `nvidia-smi` are needed.

## Limits worth knowing

- One replica behind `ocr_paddle_model_lb` (nginx, the same front door as the other model services). Calls run one at a time in one process.
- The GPU image is large (Baidu's vLLM image is about 13 GB) and the CPU image still carries PaddlePaddle. Build only the flavor you need.
- The CPU model returns text lines only: no tables, no reading order beyond the detector's. For layout, use a GPU machine.
- The GPU server runs only in a Linux container; there is no native Windows GPU run.
- Changing `device`, `min_vram_mb` or `gpu_mem_fraction` needs a service restart.
- No answer cache and no streaming.

## Try it from the admin page

Open `/admin/` and use **Try it** to call the public API with a service token held only in memory. Runs use normal token scopes and limits; the panel shows HTTP status, latency, the response and a curl equivalent with a token placeholder. See [the web UI guide](docs/guide-web-ui.md#try-it-send-a-real-request).

## Documentation

- [Purpose: input, output and what it is for](docs/purpose.md)
- [Deployment](deployment.md)
- [Changelog](CHANGELOG.md)
- [Docker on another machine](docs/guide-docker-deployment-device.md)
- [Docker on this PC](docs/guide-docker-same-device.md)
- [Run without Docker](docs/guide-no-docker-same-device.md)
- [Test it from the command line](docs/guide-cli.md)
