from fastapi.testclient import TestClient

from app import create_app
from conftest import PNG, make_engine
from options import SETTINGS
from settings import Config
from tokens import TokenStore


def client(kind="cpu", **options):
    engine = options.pop("engine", None) or make_engine(kind)
    return TestClient(create_app(engine, **options)), engine


def configured(tmp_path, **values):
    config = Config(tmp_path / "settings.json", SETTINGS)
    config.set({"warm": False, "update_check_hours": 0, **values})
    return config


def test_the_cpu_model_answers_with_plain_text_on_a_machine_without_a_gpu():
    api, engine = client("cpu")
    reply = api.post("/v1/ocr", content=PNG)
    assert reply.status_code == 200
    assert reply.json() | {"duration_ms": 0} == {"text": "pp-ocrv5 text", "format": "text", "model": "pp-ocrv5", "device": "cpu", "duration_ms": 0}
    assert engine.backend.calls == [PNG]


def test_the_gpu_model_answers_with_markdown_on_a_capable_gpu():
    api, _ = client("gpu")
    body = api.post("/v1/ocr", content=PNG).json()
    assert (body["model"], body["format"], body["device"]) == ("paddleocr-vl", "markdown", "gpu")


def test_the_device_endpoint_says_which_model_and_why():
    api, _ = client("gpu")
    info = api.get("/v1/device").json()
    assert info["device"] == "gpu" and info["model"] == "paddleocr-vl" and "RTX 3060" in info["reason"]
    assert client("cpu")[0].get("/v1/device").json()["reason"] == "No NVIDIA GPU was found."


def test_something_that_is_not_an_image_gives_415():
    api, engine = client()
    assert api.post("/v1/ocr", content=b"%PDF-1.7 not an image").status_code == 415
    assert not engine.backend.calls


def test_a_model_that_is_not_loaded_gives_503():
    api, _ = client(engine=make_engine(ready=False))
    assert api.post("/v1/ocr", content=PNG).status_code == 503
    assert api.get("/health").json()["ready"] is False


def test_health_names_the_chosen_model():
    api, _ = client("gpu")
    assert api.get("/health").json() == {"status": "ok", "model": "paddleocr-vl", "ready": True, "num_thread": None}


def test_the_request_size_limit_applies(tmp_path):
    api, engine = client(config=configured(tmp_path, max_body_mb=1))
    assert api.post("/v1/ocr", content=PNG + b"x" * (1024 * 1024)).status_code == 413
    assert not engine.backend.calls


def test_with_an_admin_password_calls_need_a_token_and_the_chosen_model_in_its_list(tmp_path):
    store = TokenStore(tmp_path / "t.json")
    api, _ = client(admin_password="pw", tokens=store)
    admin = {"X-Internal-Token": "pw"}
    assert api.post("/v1/ocr", content=PNG).status_code == 401
    narrow = api.post("/admin/tokens", json={"name": "a", "projects": ["paddleocr-vl"]}, headers=admin).json()["token"]
    assert api.post("/v1/ocr", content=PNG, headers={"X-Internal-Token": narrow}).status_code == 403  # a CPU machine serves pp-ocrv5
    right = api.post("/admin/tokens", json={"name": "b", "projects": ["pp-ocrv5"]}, headers=admin).json()["token"]
    assert api.post("/v1/ocr", content=PNG, headers={"X-Internal-Token": right}).status_code == 200
    assert api.get("/health").status_code == 200
    assert api.post("/admin/restart").status_code == 401


def test_metrics_need_the_admin_password():
    api, _ = client(admin_password="pw")
    assert api.get("/metrics").status_code == 401
    assert "paddle_request_seconds" in api.get("/metrics", headers={"X-Internal-Token": "pw"}).text


def test_restart_loads_the_model_again():
    engine = make_engine(ready=False)
    with TestClient(create_app(engine, admin_password="pw")) as api:
        assert api.post("/admin/restart", headers={"X-Internal-Token": "pw"}).json() == {"restarted": True}
        assert engine.ready and engine.backend.loads == 1


def test_workers_report_one_replica():
    api, _ = client(admin_password="pw", max_inflight=7)
    assert api.get("/admin/workers", headers={"X-Internal-Token": "pw"}).json() == {"replicas": 1, "parallel": 7}
