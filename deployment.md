# Deploying OCR_Paddle_Model

OCR_Paddle_Model is one HTTP service (port 29262) that runs PaddleOCR-VL on a capable NVIDIA GPU and PP-OCRv5 on the CPU otherwise. `ocr_paddle_model_lb` (nginx) publishes the port and passes calls to the one `ocr_paddle_model` container.

Weights are downloaded explicitly from the [Updates card](docs/guide-web-ui.md#downloaded-weights); startup never downloads weights, and only the model chosen for the machine is downloaded.

## Ways to run it

Each path has its own guide. Pick one.

| Path | Guide |
|---|---|
| Docker Desktop on this PC (CPU model; GPU model needs Linux) | [guide-docker-same-device.md](docs/guide-docker-same-device.md) |
| Docker on another always-on machine, published with `run.bat` (Deploy) | [guide-docker-deployment-device.md](docs/guide-docker-deployment-device.md) |
| Directly on Windows, no Docker (CPU model only) | [guide-no-docker-same-device.md](docs/guide-no-docker-same-device.md) |

The Docker image has two flavors: `cpu` (default) and `gpu` (add `-f docker-compose.gpu.yml`). Both are untested.

Once it runs, the admin page is covered by [guide-web-ui.md](docs/guide-web-ui.md).
