import asyncio

import pytest

from engine import CPU_REPOS, GPU_REPOS, MODEL_CPU, MODEL_GPU, Backend, EngineError, Hardware, PaddleEngine

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16  # enough for the magic-byte check; the fake backends never decode it


@pytest.fixture(autouse=True)
def _tmp_tokens_file(tmp_path, monkeypatch):
    """Keeps tokens.json and settings.json out of the repo, and a saved setting from leaking into the next test."""
    monkeypatch.setenv("SERVICE_TOKENS_FILE", str(tmp_path / "tokens.json"))


class FakeBackend(Backend):
    """Stands in for a model: loading marks it ready, `ocr` echoes its model name. `fail` = the weights are not downloaded."""

    def __init__(self, model: str, fmt: str, repos: tuple[str, ...], fail: bool = False) -> None:
        self.model, self.format, self.repos, self.fail = model, fmt, repos, fail
        self.loads, self.calls, self.threads = 0, [], None

    async def load(self, threads):
        if self.fail:
            raise EngineError("Model weights are not downloaded. Ask an admin to press Download on the Updates card.", 503)
        self.loads, self.threads, self.loaded = self.loads + 1, threads, True

    async def ocr(self, image):
        if not self.loaded:
            raise EngineError("Model is not ready.", 503)
        self.calls.append(image)
        return f"{self.model} text"


def make_engine(kind: str = "cpu", ready: bool = True, weights: bool = True, **options) -> PaddleEngine:
    """An engine on a pretend machine: `kind="gpu"` has a 12 GiB compute-8.6 NVIDIA GPU, `"cpu"` has none. Both backends are
    fakes. `ready=False` leaves the model unloaded; `weights=False` makes loading fail as if nothing was downloaded."""
    hardware = Hardware("NVIDIA RTX 3060", 12288, 8.6, True) if kind == "gpu" else Hardware()

    async def probe():
        return hardware

    backends = {"cpu": FakeBackend(MODEL_CPU, "text", CPU_REPOS, not weights), "gpu": FakeBackend(MODEL_GPU, "markdown", GPU_REPOS, not weights)}
    options.setdefault("cached_sha", lambda repos: "sha")
    options.setdefault("remote_sha", lambda repos: "sha")
    options.setdefault("download", lambda repos: None)
    engine = PaddleEngine({"device": "auto"}, hardware=probe, backends=backends, **options)
    asyncio.run(engine.select())
    if ready:
        engine.backend.loaded = True
    return engine
