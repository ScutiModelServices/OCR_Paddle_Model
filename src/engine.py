"""One OCR engine with two backends, picked from the hardware: PaddleOCR-VL on a capable NVIDIA GPU, classic PP-OCRv5 on the CPU.

`select()` probes the machine once (`nvidia-smi`) and the `device` setting decides: `auto` takes the GPU only on Linux with
compute capability 8.0 or higher and enough VRAM, `gpu` forces it when any NVIDIA GPU is found, `cpu` never uses it. Only the
chosen backend's weights are ever downloaded or loaded.

shortcut: calls to one backend run one at a time (Paddle predictors are not documented as thread-safe); upgrade with
replicas behind the load balancer when one process is not enough.
shortcut: the GPU backend is written from Baidu's PaddleOCR-VL docs and was never run on a GPU host; the CPU backend was
never run against the real models either (the tests use fakes). Say so in the docs until that is done."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

_log = logging.getLogger(__name__)

MODEL_CPU = "pp-ocrv5"  # what a token's "projects" list limits
MODEL_GPU = "paddleocr-vl"
CPU_REPOS = ("PaddlePaddle/PP-OCRv5_mobile_det", "PaddlePaddle/PP-OCRv5_mobile_rec")  # unverified repo ids: check on first download
GPU_REPOS = ("PaddlePaddle/PaddleOCR-VL-1.6", "PaddlePaddle/PP-DocLayoutV2")  # unverified: the layout model's id
VL_SERVED = "PaddleOCR-VL-1.6-0.9B"
INTERNAL_PORT = 10001
MIN_COMPUTE = 8.0  # vLLM and SGLang need compute capability 8.0 (Baidu's PaddleOCR-VL docs)
IMAGE_TYPES = {b"\x89PNG": "image/png", b"\xff\xd8\xff": "image/jpeg", b"RIFF": "image/webp"}


class EngineError(RuntimeError):
    """The call could not be served. `status` is the HTTP status the service maps it to."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def sniff(data: bytes) -> str:
    """The MIME type of an image upload from its first bytes. Anything else is refused with 415."""
    for magic, mime in IMAGE_TYPES.items():
        if data.startswith(magic) and (mime != "image/webp" or data[8:12] == b"WEBP"):  # RIFF also starts WAV and AVI
            return mime
    raise EngineError("Send one PNG, JPEG or WebP image as the body", 415)


# ---- hardware ---------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Hardware:
    gpu: str | None = None  # the NVIDIA GPU with the most memory
    vram_mb: int | None = None
    compute: float | None = None
    linux: bool = True


@dataclass(frozen=True)
class Device:
    kind: str  # "gpu" or "cpu"
    reason: str  # shown on the admin page
    hardware: Hardware = Hardware()


async def _smi() -> str:
    """`nvidia-smi` output, or "" when there is no NVIDIA driver, it hangs, or this is not a machine it can run on."""
    try:
        process = await asyncio.create_subprocess_exec(
            "nvidia-smi", "--query-gpu=name,memory.total,compute_cap", "--format=csv,noheader,nounits",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(process.communicate(), 10)
        return out.decode("utf-8", "replace") if process.returncode == 0 else ""
    except (OSError, asyncio.TimeoutError):
        return ""


def parse_smi(text: str) -> tuple[str | None, int | None, float | None]:
    """(name, MiB, compute capability) of the GPU with the most memory; None for a field the driver did not give."""
    best: tuple[str, int | None, float | None] | None = None
    for line in text.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 2 or not parts[0]:
            continue
        try:
            vram = int(float(parts[1]))
        except ValueError:
            vram = None
        try:
            compute = float(parts[2]) if len(parts) > 2 else None
        except ValueError:
            compute = None
        if best is None or (vram or 0) > (best[1] or 0):
            best = (parts[0], vram, compute)
    return best if best else (None, None, None)


async def probe_hardware(smi: Callable[[], Awaitable[str]] = _smi, platform: str = sys.platform) -> Hardware:
    name, vram, compute = parse_smi(await smi())
    return Hardware(name, vram, compute, platform.startswith("linux"))


def choose(wanted: str, hardware: Hardware, min_vram_mb: int) -> Device:
    """The device for the `device` setting. The GPU backend's servers (vLLM, SGLang) run only in Linux containers."""
    wanted = (wanted or "auto").strip().lower()
    if wanted == "cpu":
        return Device("cpu", "The device setting is cpu.", hardware)
    if hardware.gpu is None:
        return Device("cpu", "No NVIDIA GPU was found." if wanted == "auto" else "The device setting is gpu, but no NVIDIA GPU was found.", hardware)
    if not hardware.linux:
        return Device("cpu", "The GPU model server runs only in a Linux container.", hardware)
    if wanted == "gpu":
        return Device("gpu", f"The device setting is gpu ({hardware.gpu}).", hardware)
    if hardware.compute is None:
        return Device("cpu", f"{hardware.gpu}: the driver did not report its compute capability.", hardware)
    if hardware.compute < MIN_COMPUTE:
        return Device("cpu", f"{hardware.gpu} has compute capability {hardware.compute}; the GPU model needs {MIN_COMPUTE} or higher.", hardware)
    if (hardware.vram_mb or 0) < min_vram_mb:
        return Device("cpu", f"{hardware.gpu} has {hardware.vram_mb} MiB; the GPU model needs {min_vram_mb} MiB.", hardware)
    return Device("gpu", f"{hardware.gpu}, {hardware.vram_mb} MiB, compute capability {hardware.compute}.", hardware)


# ---- weights ----------------------------------------------------------------------------------------------------

def models_dir() -> Path:
    return Path(os.environ.get("SERVICE_MODELS_DIR") or os.environ.get("HF_HOME") or "~/.cache/paddle-ocr").expanduser()


def _revisions_file() -> Path:
    return models_dir() / ".paddle-revisions.json"


def _saved_revisions() -> dict[str, str]:
    try:
        data = json.loads(_revisions_file().read_text("utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def cached_revision(repos: tuple[str, ...]) -> str | None:
    """The downloaded revisions of `repos` as one string, or None when any of them is missing."""
    saved = _saved_revisions()
    return ",".join(f"{repo}@{saved[repo]}" for repo in repos) if all(repo in saved for repo in repos) else None


def remote_revision(repos: tuple[str, ...]) -> str:
    from huggingface_hub import HfApi

    api = HfApi()
    return ",".join(f"{repo}@{api.model_info(repo).sha}" for repo in repos)


def download_repos(repos: tuple[str, ...]) -> None:
    """Downloads the newest revision of each repo into the Hub cache under the models folder and records it. Only the admin
    Download action calls this."""
    from huggingface_hub import HfApi, snapshot_download
    from progress import hub_blobs, hub_total, tracking

    cache = str(models_dir() / "hub")
    for repo in repos:
        sha = HfApi().model_info(repo).sha
        with tracking(repo, hub_total(repo, sha), hub_blobs(repo, cache)):
            snapshot_download(repo, revision=sha, cache_dir=cache)
        models_dir().mkdir(parents=True, exist_ok=True)
        _revisions_file().write_text(json.dumps({**_saved_revisions(), repo: sha}), "utf-8")


def local_paths(repos: tuple[str, ...]) -> list[str]:
    """The downloaded folder of each repo, without touching the network."""
    from huggingface_hub import snapshot_download

    saved = _saved_revisions()
    try:
        return [snapshot_download(repo, revision=saved.get(repo), cache_dir=str(models_dir() / "hub"), local_files_only=True)
                for repo in repos]
    except (OSError, ValueError):
        raise EngineError("Model weights are not downloaded. Ask an admin to press Download on the Updates card.", 503) from None


# ---- backends ---------------------------------------------------------------------------------------------------

def _decode(image: bytes) -> Any:
    import cv2
    import numpy as np

    array = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
    if array is None:
        raise EngineError("The image could not be read", 422)
    return array


def _paddleocr(paths: list[str], threads: int | None) -> Callable[[bytes], str]:
    """Classic PP-OCRv5 (mobile detection + recognition) from local folders, on the CPU. Every model that would download on
    its own (orientation, unwarping) is switched off so loading never reaches the network."""
    from paddleocr import PaddleOCR

    ocr = PaddleOCR(text_detection_model_name="PP-OCRv5_mobile_det", text_detection_model_dir=paths[0],
                    text_recognition_model_name="PP-OCRv5_mobile_rec", text_recognition_model_dir=paths[1],
                    use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False,
                    device="cpu", **({"cpu_threads": threads} if threads else {}))

    def run(image: bytes) -> str:
        return "\n".join(line for result in ocr.predict(_decode(image)) for line in result["rec_texts"])

    return run


def _vl_pipeline(paths: list[str], url: str) -> Callable[[bytes], str]:
    """The PaddleOCR-VL pipeline: layout detection here, text recognition on the model server at `url`."""
    from paddleocr import PaddleOCRVL

    pipeline = PaddleOCRVL(vl_rec_backend="vllm-server", vl_rec_server_url=url, vl_rec_api_model_name=VL_SERVED,
                           layout_detection_model_dir=paths[1], use_doc_orientation_classify=False, use_doc_unwarping=False)

    def run(image: bytes) -> str:
        return "\n\n".join(result.markdown["markdown_texts"] for result in pipeline.predict(_decode(image)))

    return run


class Backend:
    """What the engine needs from a backend. `format` is what `ocr` returns: "text" or "markdown"."""

    model: str
    repos: tuple[str, ...]
    format: str
    loaded = False

    async def load(self, threads: int | None) -> None:
        raise NotImplementedError

    async def unload(self) -> None:
        self.loaded = False

    async def tick(self) -> None:
        """Called every two seconds by the supervisor (restart a dead child, update readiness)."""

    async def ocr(self, image: bytes) -> str:
        raise NotImplementedError


class CpuBackend(Backend):
    model, repos, format = MODEL_CPU, CPU_REPOS, "text"

    def __init__(self, factory: Callable[[list[str], int | None], Callable[[bytes], str]] = _paddleocr) -> None:
        self._factory = factory
        self._run: Callable[[bytes], str] | None = None
        self._lock = asyncio.Lock()
        self.threads: int | None = None  # the count the loaded predictor was built with

    async def load(self, threads: int | None) -> None:
        paths = await asyncio.to_thread(local_paths, self.repos)
        self._run = await asyncio.to_thread(self._factory, paths, threads)
        self.threads, self.loaded = threads, True

    async def unload(self) -> None:
        self._run, self.loaded = None, False

    async def ocr(self, image: bytes) -> str:
        if self._run is None:
            raise EngineError("Model is not ready. Ask an admin to press Download on the Updates card if weights are missing.", 503)
        async with self._lock:
            return await asyncio.to_thread(self._run, image)


class GpuBackend(Backend):
    """Runs `paddleocr genai_server` (vLLM) as a child process on a loopback port and the PaddleOCR-VL pipeline in this process."""

    model, repos, format = MODEL_GPU, GPU_REPOS, "markdown"

    def __init__(self, mem_fraction: float = 0.7, *, factory: Callable[[list[str], str], Callable[[bytes], str]] = _vl_pipeline,
                 http: httpx.AsyncClient | None = None, backoff: float = 5.0, command: list[str] | None = None) -> None:
        self._mem_fraction, self._factory, self._backoff = mem_fraction, factory, backoff
        self._command = command  # tests pass a sleeping process
        self._http = http or httpx.AsyncClient(timeout=3)
        self._url = f"http://127.0.0.1:{INTERNAL_PORT}"
        self._paths: list[str] = []
        self._process: asyncio.subprocess.Process | None = None
        self._run: Callable[[bytes], str] | None = None
        self._wanted = False
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def load(self, threads: int | None) -> None:
        self._paths = await asyncio.to_thread(local_paths, self.repos)
        self._wanted = True
        await self._spawn()

    async def _spawn(self) -> None:
        if self.running:
            return
        command = self._command or self._server_command()
        env = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
        self._process = await asyncio.create_subprocess_exec(*command, env=env)

    def _server_command(self) -> list[str]:
        config = models_dir() / "vllm.yaml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(f"gpu-memory-utilization: {self._mem_fraction}\n", "utf-8")  # the key as in Baidu's docs example
        return [shutil.which("paddleocr") or "paddleocr", "genai_server", "--model_name", VL_SERVED, "--model_dir", self._paths[0],
                "--host", "127.0.0.1", "--port", str(INTERNAL_PORT), "--backend", "vllm", "--backend_config", str(config)]

    async def unload(self) -> None:
        self._wanted = self.loaded = False
        self._run = None
        process, self._process = self._process, None
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), 30)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

    async def tick(self) -> None:
        if not self._wanted:
            return
        if not self.running:
            self.loaded, self._run = False, None
            _log.warning("The model server is not running; restarting in %.0f s", self._backoff)
            await asyncio.sleep(self._backoff)
            if self._wanted:
                await self._spawn()
            return
        try:
            up = (await self._http.get(f"{self._url}/v1/models")).status_code == 200
        except httpx.HTTPError:
            up = False
        if up and self._run is None:
            self._run = await asyncio.to_thread(self._factory, self._paths, f"{self._url}/v1")
        self.loaded = up and self._run is not None

    async def ocr(self, image: bytes) -> str:
        if self._run is None or not self.loaded:
            raise EngineError("Model is not ready. Ask an admin to press Download on the Updates card if weights are missing.", 503)
        async with self._lock:
            return await asyncio.to_thread(self._run, image)


# ---- engine -----------------------------------------------------------------------------------------------------

class PaddleEngine:
    """`options` are the settings (`device`, `min_vram_mb`, `gpu_mem_fraction`). `hardware`, `backends` and the three weight
    callables are injectable for tests."""

    def __init__(self, options: dict[str, Any] | None = None, *, hardware: Callable[[], Awaitable[Hardware]] = probe_hardware,
                 backends: dict[str, Backend] | None = None,
                 cached_sha: Callable[[tuple[str, ...]], str | None] = cached_revision,
                 remote_sha: Callable[[tuple[str, ...]], str] = remote_revision,
                 download: Callable[[tuple[str, ...]], None] = download_repos) -> None:
        self._options = options or {}
        self._hardware = hardware
        self._backends = backends or {"cpu": CpuBackend(), "gpu": GpuBackend(float(self._options.get("gpu_mem_fraction", 0.7)))}
        self._cached, self._remote, self._download = cached_sha, remote_sha, download
        self._threads: int | None = None
        self.device: Device | None = None

    # -- selection

    async def select(self) -> Device:
        """Probes the hardware and picks the backend. Runs at start and again on restart."""
        self.device = choose(str(self._options.get("device", "auto")), await self._hardware(), int(self._options.get("min_vram_mb", 8192)))
        _log.info("OCR device: %s (%s)", self.device.kind, self.device.reason)
        return self.device

    @property
    def backend(self) -> Backend:
        return self._backends[(self.device or Device("cpu", "")).kind]

    @property
    def model(self) -> str:
        """The model a token's "projects" list must allow."""
        return self.backend.model if self.device else MODEL_CPU

    @property
    def ready(self) -> bool:
        return self.device is not None and self.backend.loaded

    @property
    def num_thread(self) -> int | None:
        return self._threads

    @num_thread.setter
    def num_thread(self, count: int) -> None:
        self._threads = count  # the CPU predictor is rebuilt with it before the next call

    def info(self) -> dict[str, Any]:
        """What the admin page and /v1/device show."""
        if self.device is None:
            return {"device": None}
        hardware = self.device.hardware
        return {"device": self.device.kind, "reason": self.device.reason, "model": self.backend.model, "format": self.backend.format,
                "gpu": hardware.gpu, "vram_mb": hardware.vram_mb, "compute": hardware.compute}

    # -- life cycle

    async def start(self) -> None:
        """Loads the chosen backend's downloaded weights. Missing weights leave the service up and not ready."""
        if self.device is None:
            await self.select()
        try:
            await self.backend.load(self._threads)
        except EngineError as error:
            _log.warning("%s", error)

    async def stop(self) -> None:
        for backend in self._backends.values():
            await backend.unload()

    async def restart(self) -> None:
        await self.stop()
        await self.select()
        await self.start()

    async def supervise(self) -> None:
        """Keeps the backend current. Runs until cancelled."""
        while True:
            if self.device is not None:
                await self.backend.tick()
            await asyncio.sleep(2)

    async def probe(self) -> bool:
        if self.device is not None:
            await self.backend.tick()
        return self.ready

    async def close(self) -> None:
        await self.stop()

    # -- work

    async def ocr(self, image: bytes) -> dict[str, Any]:
        """The text of one page image: {"text", "format", "model", "device", "duration_ms"}."""
        sniff(image)
        if self.device is None:
            await self.select()
        backend = self.backend
        if isinstance(backend, CpuBackend) and backend.loaded and backend.threads != self._threads:
            await backend.load(self._threads)  # cpu_threads is fixed when the predictor is built
        started = time.perf_counter()
        text = await backend.ocr(image)
        return {"text": text, "format": backend.format, "model": backend.model, "device": self.device.kind if self.device else "cpu",
                "duration_ms": round((time.perf_counter() - started) * 1000)}

    # -- updates: only the chosen backend's repos are looked at or downloaded

    async def cached_sha(self) -> str | None:
        return await asyncio.to_thread(self._cached, self.backend.repos)

    async def remote_sha(self) -> str:
        return await asyncio.to_thread(self._remote, self.backend.repos)

    async def pull(self) -> None:
        await asyncio.to_thread(self._download, self.backend.repos)

    async def unload(self) -> None:
        """New weights are on disk: drop the old copy and load the new one."""
        await self.backend.unload()
        await self.start()
