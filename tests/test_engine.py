import asyncio
import json
import sys

import httpx
import pytest

import engine as engine_module
from conftest import PNG, FakeBackend, make_engine
from engine import (CPU_REPOS, MODEL_CPU, CpuBackend, EngineError, GpuBackend, Hardware, PaddleEngine, cached_revision, choose,
                    parse_smi, probe_hardware, sniff)

GPU = Hardware("NVIDIA RTX 3060", 12288, 8.6, True)


def test_smi_output_is_parsed_and_the_biggest_gpu_wins():
    assert parse_smi("NVIDIA T4, 15360, 7.5\nNVIDIA A100, 40960, 8.0\n") == ("NVIDIA A100", 40960, 8.0)
    assert parse_smi("NVIDIA RTX 3060, 12288\n") == ("NVIDIA RTX 3060", 12288, None)  # an older driver gives no compute capability
    assert parse_smi("") == (None, None, None)
    assert parse_smi("garbage") == (None, None, None)


def test_the_hardware_probe_reads_smi_and_the_platform():
    async def smi():
        return "NVIDIA RTX 3060, 12288, 8.6\n"

    assert asyncio.run(probe_hardware(smi, "linux")) == GPU
    assert asyncio.run(probe_hardware(smi, "win32")).linux is False

    async def none():
        return ""

    assert asyncio.run(probe_hardware(none, "linux")).gpu is None


@pytest.mark.parametrize("wanted, hardware, kind, reason", [
    ("auto", GPU, "gpu", "RTX 3060"),
    ("auto", Hardware(), "cpu", "No NVIDIA GPU"),
    ("auto", Hardware("NVIDIA T4", 15360, 7.5), "cpu", "compute capability 7.5"),
    ("auto", Hardware("NVIDIA RTX 3050", 4096, 8.6), "cpu", "needs 8192 MiB"),
    ("auto", Hardware("NVIDIA RTX 3060", 12288, None), "cpu", "did not report"),
    ("auto", Hardware("NVIDIA RTX 3060", 12288, 8.6, linux=False), "cpu", "Linux container"),
    ("cpu", GPU, "cpu", "setting is cpu"),
    ("gpu", Hardware("NVIDIA T4", 15360, 7.5), "gpu", "setting is gpu"),
    ("gpu", Hardware(), "cpu", "no NVIDIA GPU"),
    ("gpu", Hardware("NVIDIA RTX 3060", 12288, 8.6, linux=False), "cpu", "Linux container"),
    ("", GPU, "gpu", "RTX 3060"),  # an empty value means auto
])
def test_the_device_choice(wanted, hardware, kind, reason):
    device = choose(wanted, hardware, 8192)
    assert device.kind == kind and reason in device.reason


def test_only_images_are_accepted():
    assert sniff(PNG) == "image/png"
    assert sniff(b"\xff\xd8\xff\xe0" + b"0" * 8) == "image/jpeg"
    assert sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    for body in (b"%PDF-1.7", b"RIFF\x00\x00\x00\x00WAVEfmt ", b""):
        with pytest.raises(EngineError) as raised:
            sniff(body)
        assert raised.value.status == 415


def test_the_engine_takes_the_gpu_model_on_a_gpu_machine_and_the_cpu_model_otherwise():
    assert make_engine("gpu").model == "paddleocr-vl" and make_engine("cpu").model == "pp-ocrv5"


def test_only_the_chosen_models_weights_are_looked_at_and_downloaded():
    seen = []
    cpu = make_engine("cpu", cached_sha=lambda repos: seen.append(("cached", repos)), remote_sha=lambda repos: seen.append(("remote", repos)) or "x",
                      download=lambda repos: seen.append(("download", repos)))

    async def go():
        await cpu.cached_sha(), await cpu.remote_sha(), await cpu.pull()

    asyncio.run(go())
    assert {repos for _, repos in seen} == {CPU_REPOS}


def test_missing_weights_keep_the_engine_up_but_not_ready():
    engine = make_engine("cpu", ready=False, weights=False)
    asyncio.run(engine.start())
    assert not engine.ready
    with pytest.raises(EngineError) as raised:
        asyncio.run(engine.ocr(PNG))
    assert raised.value.status == 503


def test_unload_after_a_download_loads_the_new_weights():
    engine = make_engine("cpu", ready=False)

    async def go():
        await engine.start()
        await engine.unload()

    asyncio.run(go())
    assert engine.backend.loads == 2 and engine.ready


# ---- CPU backend ------------------------------------------------------------------------------------------------

def cpu_engine(monkeypatch, builds):
    monkeypatch.setattr(engine_module, "local_paths", lambda repos: [f"/models/{repo}" for repo in repos])

    def factory(paths, threads):
        builds.append((paths, threads))
        return lambda image: f"{len(image)} bytes"

    async def probe():
        return Hardware()

    return PaddleEngine({"device": "auto"}, hardware=probe, backends={"cpu": CpuBackend(factory), "gpu": FakeBackend("g", "markdown", ())})


def test_the_cpu_backend_builds_the_predictor_from_local_folders_and_runs_it(monkeypatch):
    builds = []
    engine = cpu_engine(monkeypatch, builds)

    async def go():
        await engine.start()
        return await engine.ocr(PNG)

    result = asyncio.run(go())
    assert result["text"] == f"{len(PNG)} bytes" and result["device"] == "cpu" and result["model"] == MODEL_CPU
    assert builds == [(["/models/PaddlePaddle/PP-OCRv5_mobile_det", "/models/PaddlePaddle/PP-OCRv5_mobile_rec"], None)]


def test_a_new_thread_count_rebuilds_the_cpu_predictor_before_the_next_call(monkeypatch):
    builds = []
    engine = cpu_engine(monkeypatch, builds)

    async def go():
        await engine.start()
        engine.num_thread = 4
        await engine.ocr(PNG)
        await engine.ocr(PNG)

    asyncio.run(go())
    assert [threads for _, threads in builds] == [None, 4]


def test_the_cpu_backend_without_downloaded_weights_is_not_ready(monkeypatch):
    def missing(repos):
        raise EngineError("Model weights are not downloaded.", 503)

    engine = cpu_engine(monkeypatch, [])
    monkeypatch.setattr(engine_module, "local_paths", missing)
    asyncio.run(engine.start())
    assert not engine.ready


# ---- GPU backend ------------------------------------------------------------------------------------------------

def test_the_gpu_backend_is_ready_when_its_server_answers_and_the_pipeline_is_built(monkeypatch):
    monkeypatch.setattr(engine_module, "local_paths", lambda repos: ["/models/vl", "/models/layout"])
    answers = {"up": False}
    built = []

    def handler(request):
        return httpx.Response(200 if answers["up"] else 503)

    def factory(paths, url):
        built.append((paths, url))
        return lambda image: "# page"

    backend = GpuBackend(factory=factory, http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), backoff=0.01,
                         command=[sys.executable, "-c", "import time; time.sleep(60)"])

    async def go():
        await backend.load(None)
        await backend.tick()
        assert backend.running and not backend.loaded and built == []  # the server is still starting
        answers["up"] = True
        await backend.tick()
        assert backend.loaded and built == [(["/models/vl", "/models/layout"], "http://127.0.0.1:10001/v1")]
        assert await backend.ocr(PNG) == "# page"
        await backend.unload()
        assert not backend.running and not backend.loaded

    asyncio.run(go())


def test_the_gpu_backend_restarts_a_server_that_died(monkeypatch):
    monkeypatch.setattr(engine_module, "local_paths", lambda repos: ["/models/vl", "/models/layout"])
    backend = GpuBackend(factory=lambda paths, url: (lambda image: ""), backoff=0.01,
                         http=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200))),
                         command=[sys.executable, "-c", "pass"])  # exits at once

    async def go():
        await backend.load(None)
        first = backend._process
        await first.wait()
        await backend.tick()
        assert backend._process is not first
        await backend.unload()

    asyncio.run(go())


# ---- weights bookkeeping ----------------------------------------------------------------------------------------

def test_cached_revision_is_none_until_every_repo_was_downloaded(tmp_path, monkeypatch):
    monkeypatch.setenv("SERVICE_MODELS_DIR", str(tmp_path))
    assert cached_revision(CPU_REPOS) is None
    (tmp_path / ".paddle-revisions.json").write_text(json.dumps({CPU_REPOS[0]: "aaa"}))
    assert cached_revision(CPU_REPOS) is None
    (tmp_path / ".paddle-revisions.json").write_text(json.dumps({CPU_REPOS[0]: "aaa", CPU_REPOS[1]: "bbb"}))
    assert cached_revision(CPU_REPOS) == f"{CPU_REPOS[0]}@aaa,{CPU_REPOS[1]}@bbb"
