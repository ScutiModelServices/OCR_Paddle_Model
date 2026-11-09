# OCR_Paddle_Model: Docker on a deployment machine

Run PaddleOCR (PaddleOCR-VL on a capable NVIDIA GPU, PP-OCRv5 on the CPU) on a separate always-on machine (default `lex@scutiserver1.local`, folder
`/media/ZimaOS-HD/Apps/ModelServices/OCR_Paddle_Model`). Callers reach it over the published port.

## Requirements on the machine

- Docker + Compose v2, and ssh access (root over ssh is refused; use the normal user).
- CPU model: nothing more. The default Deploy target (`lex@scutiserver1.local`, ZimaOS) has no GPU and gets the CPU model.
- GPU model: Linux with an NVIDIA GPU of compute capability 8.0 or higher, the NVIDIA driver and the NVIDIA Container Toolkit. Check it: `docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu24.04 nvidia-smi`. Point `DEPLOY_HOST` and `DEPLOY_DIR` at that machine (see step 1).
- The images have never been built here, and Baidu states no VRAM minimum for PaddleOCR-VL.

## 1. Publish the code (from your PC)

Run `OCR_Paddle_Model\run.bat` and choose **Deploy** (or `run.bat deploy`). Menu:

| Option | Does |
|---|---|
| 1 | Publish code to the `live` branch |
| 2 | Copy your `.env` to the server (one ssh/tar session, one password prompt) |
| 3 | Both |
| 4, 5 | Dry runs of 1 and 2 |
| 6 | chown helper, if option 2 says `Permission denied` |
| R | Release: publish, then tag `VERSION` |

Keep your own target in the git-ignored `tools\deploy.local.ini` (copy `tools\deploy.local.example.ini`), or set
`DEPLOY_HOST`, `DEPLOY_DIR` and `DEPLOY_WEB_PORT` before running. Commit `main` first: publish reads committed `main`.

## 2. First-time setup on the machine

```bash
mkdir -p /media/ZimaOS-HD/Apps/ModelServices
git clone -b live <your-repo-url> /media/ZimaOS-HD/Apps/ModelServices/OCR_Paddle_Model
cd /media/ZimaOS-HD/Apps/ModelServices/OCR_Paddle_Model
```
Create `.env` (deploy option 2 copies yours, or `cp .env.example .env` and edit):
- `SERVICE_ADMIN_PASSWORD`: a real password.
- `SERVICE_PORT=29262` (published on all interfaces; firewall it if the LAN should not reach it, or bind it to
  `127.0.0.1` in the `ports:` line of `ocr_paddle_model_lb` when only callers on the machine need it).

## 3. Start

```bash
docker compose up -d --build                                              # CPU machine
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build   # NVIDIA GPU machine
docker compose ps
curl http://127.0.0.1:29262/health
curl http://127.0.0.1:29262/admin/   # the page shows which model was chosen and why
```
The admin Download installs only the chosen model's weights into the `ocr_paddle_model_models` volume; `ocr_paddle_model_lb` waits for a healthy service
(up to 300 s grace).

## 4. Connect callers

1. Open `http://<machine-ip>:29262/admin/`, sign in with the admin password and create one token per caller
   (see [guide-web-ui.md](guide-web-ui.md)).
2. The caller holds the URL and token:
   - Caller on another machine: `http://<machine-ip>:29262`.
   - Caller in a Docker stack on the same machine: `http://host.docker.internal:29262`.
3. Try a call from the machine:
   ```bash
   curl http://127.0.0.1:29262/v1/device -H "X-Internal-Token: paddle_..."
   ```

## Updating

```bash
cd /media/ZimaOS-HD/Apps/ModelServices/OCR_Paddle_Model
git pull
docker compose up -d --build
docker compose restart ocr_paddle_model_lb
```

The Dockerfile does not pin the PaddlePaddle and PaddleOCR versions yet; pin them after the first successful build.
Switching between the CPU and GPU flavor needs `--build` with the other compose files.
`run.bat` (Deploy) publishes the `live` branch and pushes `.env` to the server.

## Releasing

A plain publish (`run.bat`, Deploy, option 1) only updates the `live` branch. For a release set `VERSION` (and `pyproject.toml` to the same number), commit, then run `run.bat`, Deploy, option R or `py tools\publish_live.py --release`. `run.bat`, Build the release ZIP, then builds `_zip_versions\OCR_Paddle_Model-<VERSION>.zip`.

## Problems

| Symptom | Fix |
|---|---|
| Caller cannot reach the service | Check the caller's URL and token, and that `curl http://<machine-ip>:29262/health` works. |
| nginx 502 after an update | `docker compose restart ocr_paddle_model_lb`. |
| Permission denied on push | run.bat Deploy option 6. |
| Port 29262 in use | Another stack on the machine already publishes it. |

Before first inference, follow [Downloaded weights](guide-web-ui.md#downloaded-weights): open `/admin/` → Updates → Download. Startup never downloads weights.
