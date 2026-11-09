"""HTTP face of the PaddleOCR engine: the same tokens, rate limits, settings and admin page as the other model services.

POST /v1/ocr takes one PNG, JPEG or WebP page image as the body and returns {"text", "format", "model", "device",
"duration_ms"}: Markdown from the GPU model (PaddleOCR-VL), plain lines from the CPU model (PP-OCRv5). GET /v1/device says
which one this machine uses and why. GET /health reports whether the chosen model is loaded."""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from engine import EngineError, PaddleEngine
from options import SETTINGS
import settings
from tokens import LatencyHistogram, TokenStore
from updates import Updates

_log = logging.getLogger(__name__)

NAME_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$"  # a model name; a token's "projects" list limits the models


class SettingsUpdate(BaseModel):
    values: dict[str, Any]  # None resets an option to its environment variable or default


class NewToken(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    projects: list[Annotated[str, Field(pattern=NAME_PATTERN)]] | None = Field(None, max_length=32)
    rate: int | None = Field(None, ge=1, le=100_000)  # calls per minute
    expires_days: int | None = Field(None, ge=1, le=3650)


class TokenScope(BaseModel):
    projects: list[Annotated[str, Field(pattern=NAME_PATTERN)]] | None = Field(None, max_length=32)
    rate: int | None = Field(None, ge=1, le=100_000)


class Threads(BaseModel):
    num_thread: int = Field(ge=1, le=256)


def create_app(client: PaddleEngine | None = None, admin_password: str | None = None,
               tokens: TokenStore | None = None, warm: bool = False,
               updates: Updates | None = None, check_hours: float = 0, max_inflight: int | None = None,
               max_body_mb: int = 32, config: settings.Config | None = None) -> FastAPI:
    """`admin_password` (SERVICE_ADMIN_PASSWORD) unlocks /admin/, where each caller gets a token of its own; when it
    is set, every call except /health must send one of those tokens in the X-Internal-Token header. Unset = no
    auth at all (local use only). `tokens` holds the tokens (SERVICE_TOKENS_FILE). `config` (the admin page's settings)
    replaces `warm`, `check_hours`, `max_inflight` and `max_body_mb` and keeps them live; without it they stay fixed."""
    tokens = tokens or TokenStore(os.environ.get("SERVICE_TOKENS_FILE", "tokens.json"))
    if client is None:
        options = {spec.key: config.get(spec.key) for spec in SETTINGS} if config else {spec.key: spec.default for spec in SETTINGS}
        client = PaddleEngine(options)
    inflight = 0

    def inflight_limit() -> int:
        """Calls this service lets wait for the model at once; more get 429. 0 = 4."""
        value = config.get("max_inflight") if config else (
            max_inflight if max_inflight is not None else int(os.environ.get("SERVICE_MAX_INFLIGHT") or 0))
        return value or 4

    def body_limit() -> int:
        return (config.get("max_body_mb") if config else max_body_mb) * 1024 * 1024

    updates = updates or Updates("PaddleOCR (the model chosen for this device)", cached_sha=lambda: client.cached_sha(), remote_sha=lambda: client.remote_sha(),
                                 download=lambda: client.pull(), unload=lambda: client.unload(),
                                 description="Looks for newer revisions of the chosen model on the Hugging Face Hub without downloading. "
                                             "Only the model for this machine's device is downloaded.",
                                 reload_note="Files downloaded and the model is loading them; /health turns ready when it is done.")

    async def check_forever() -> None:
        """Checks every `update_check_hours` (0 = off). The value is read again every second, so the admin page can change it."""
        last = None
        while True:
            wait = (config.get("update_check_hours") if config else check_hours) * 3600
            now = time.monotonic()
            if wait > 0 and (last is None or now - last >= wait):
                last = now
                try:
                    status = await updates.check()
                    if status["error"]:
                        _log.warning("Update check failed: %s", status["error"])
                except Exception:
                    _log.exception("Update check failed")
            await asyncio.sleep(min(wait, 1.0) if wait > 0 else 1.0)

    async def sync_forever() -> None:
        """Applies the CPU threads another save wrote to the shared settings file."""
        while True:
            saved = settings.load(tokens.settings_path)
            if saved.get("num_thread") and saved["num_thread"] != client.num_thread:
                client.num_thread = int(saved["num_thread"])
            await asyncio.sleep(1)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        background: list[asyncio.Task] = [asyncio.create_task(client.supervise())]
        try:
            await client.select()  # which device this machine uses; the admin page and token scope need it
            if config.get("warm") if config else warm:
                await client.start()  # loading can take minutes; /health answers meanwhile
            if config or check_hours > 0:
                background.append(asyncio.create_task(check_forever()))
            if config:
                background.append(asyncio.create_task(sync_forever()))
            yield
        finally:
            for task in background:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            await client.close()

    def authorize(request: Request, x_internal_token: str = Header("")) -> list[str] | None:
        """The models the token is limited to, or None for all. Unset admin password = no auth."""
        if not admin_password:
            return None
        row = tokens.lookup(x_internal_token)
        if row is None:
            raise HTTPException(401, "Invalid token")
        wait = tokens.throttle(row)
        if wait:
            raise HTTPException(429, "Too many requests for this token", headers={"Retry-After": str(wait)})
        request.state.token_id = row["id"]
        return row.get("projects")

    def check_scope(allowed: list[str] | None, model: str) -> None:
        if allowed is not None and model not in allowed:
            raise HTTPException(403, f"This token may not use model {model!r}")

    def admin(x_internal_token: str = Header("")) -> None:
        if not admin_password or not hmac.compare_digest(x_internal_token, admin_password):
            raise HTTPException(401, "Admin password required (set SERVICE_ADMIN_PASSWORD)")

    app = FastAPI(title="PaddleOCR service (GPU or CPU model)", lifespan=lifespan)

    async def read_body(request: Request) -> bytes:
        """The request body; 413 past the size setting (a declared length is checked before anything is read)."""
        limit = body_limit()
        if int(request.headers.get("content-length") or 0) > limit:
            raise HTTPException(413, f"The request is over {limit // (1024 * 1024)} MB")
        data = bytearray()
        async for chunk in request.stream():
            data += chunk
            if len(data) > limit:
                raise HTTPException(413, f"The request is over {limit // (1024 * 1024)} MB")
        return bytes(data)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        await client.probe()
        return {"status": "ok", "model": client.model, "ready": client.ready, "num_thread": client.num_thread}

    @app.get("/v1/device")
    async def device(_: list[str] | None = Depends(authorize)) -> dict[str, Any]:
        """Which model this machine uses and why."""
        return client.info()

    @app.post("/v1/ocr")
    async def ocr(request: Request, allowed: list[str] | None = Depends(authorize)) -> dict[str, Any]:
        """One page image as the body (PNG, JPEG or WebP). Markdown from the GPU model, plain lines from the CPU model."""
        nonlocal inflight
        if client.device is None:
            await client.select()
        check_scope(allowed, client.model)
        if inflight >= inflight_limit():
            raise HTTPException(429, "The model is busy: too many requests waiting", headers={"Retry-After": "5"})
        inflight += 1
        try:
            return await client.ocr(await read_body(request))
        except EngineError as error:
            raise HTTPException(error.status or 500, str(error)) from None
        finally:
            inflight -= 1

    @app.post("/admin/restart", dependencies=[Depends(admin)])
    async def restart() -> dict[str, Any]:
        """Unloads the model and loads it again (the device is probed again). Device and memory settings are read at the
        service's own start, so a changed one needs a service restart; this applies weights and clears a stuck model server."""
        await client.restart()
        return {"restarted": True}

    @app.put("/admin/threads", dependencies=[Depends(admin)])
    def set_threads(body: Threads) -> dict[str, Any]:
        """Saved; the CPU model is rebuilt with it before the next call. The GPU model ignores it."""
        client.num_thread = body.num_thread
        settings.save(tokens.settings_path, num_thread=body.num_thread)
        return {"num_thread": client.num_thread}

    @app.get("/admin/workers", dependencies=[Depends(admin)])
    def workers() -> dict[str, Any]:
        """Read-only. Calls run one at a time in one process, so there is one replica."""
        return {"replicas": 1, "parallel": inflight_limit()}

    @app.get("/admin/updates", dependencies=[Depends(admin)])
    async def update_status() -> dict[str, Any]:
        """Return the last check without I/O; zero check hours disables only the timer."""
        return {"enabled": True, **updates.status()}

    @app.post("/admin/updates/check", dependencies=[Depends(admin)])
    async def check_updates() -> dict[str, Any]:
        return {"enabled": True, **await updates.check()}

    @app.post("/admin/updates/weights", dependencies=[Depends(admin)])
    async def update_weights() -> dict[str, Any]:
        try:
            return {"enabled": True, **await updates.update_weights()}
        except Exception as error:
            _log.exception("Weight update failed")
            raise HTTPException(502, f"Weight update failed: {error}") from None

    @app.get("/admin/tokens", dependencies=[Depends(admin)])
    def list_tokens() -> dict[str, Any]:
        return {"tokens": tokens.list()}

    @app.post("/admin/tokens", dependencies=[Depends(admin)])
    def create_token(body: NewToken) -> dict[str, Any]:
        return tokens.create(body.name.strip(), body.projects, body.rate, body.expires_days)

    @app.get("/admin/settings", dependencies=[Depends(admin)])
    def get_settings() -> dict[str, Any]:
        return {"settings": config.describe() if config else []}

    @app.put("/admin/settings", dependencies=[Depends(admin)])
    def put_settings(body: SettingsUpdate) -> dict[str, Any]:
        """Saves options; null resets one. Options marked `restart` apply after the service restarts."""
        if config is None:
            raise HTTPException(409, "Settings are fixed in this setup")
        try:
            config.set(body.values)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        return {"settings": config.describe()}

    @app.put("/admin/tokens/{token_id}", dependencies=[Depends(admin)])
    def update_token(token_id: str, body: TokenScope) -> dict[str, bool]:
        if not tokens.update(token_id, body.projects, body.rate):
            raise HTTPException(404, "No such token")
        return {"ok": True}

    latency = LatencyHistogram()

    @app.middleware("http")
    async def measure(request: Request, call_next):
        started = time.perf_counter()
        response = await call_next(request)
        if request.url.path.startswith("/v1/"):
            latency.observe(time.perf_counter() - started)
        token_id = getattr(request.state, "token_id", None)
        if token_id:
            await run_in_threadpool(tokens.record, token_id, (time.perf_counter() - started) * 1000, response.status_code >= 500)
        return response

    @app.get("/metrics", dependencies=[Depends(admin)])
    def metrics() -> PlainTextResponse:
        """Prometheus text per token, from the shared token file (up to a minute behind). Send the admin password."""
        return PlainTextResponse(tokens.metrics("paddle") + latency.render("paddle"))

    @app.delete("/admin/tokens/{token_id}", dependencies=[Depends(admin)])
    def revoke_token(token_id: str) -> dict[str, bool]:
        if not tokens.revoke(token_id):
            raise HTTPException(404, "No such token")
        return {"ok": True}

    web = Path(os.environ.get("SERVICE_WEB_DIR", Path(__file__).resolve().parents[1] / "web"))
    if web.is_dir():
        app.mount("/admin", StaticFiles(directory=web, html=True))

    return app


def main() -> None:
    import sys

    import uvicorn

    if "--dotenv" in sys.argv[1:]:  # run.bat asks for it; a plain `python src/app.py` and Docker never read .env files
        loaded = settings.load_env_files(Path(__file__).resolve().parents[1])
        for name, names in loaded.items():
            print(f"{name}: loaded {', '.join(names) or 'no variables'} (names only; a real environment variable wins)", flush=True)
        if not loaded:
            print(".env.local / .env: no file in the service folder", flush=True)
    settings.apply_models_dir()  # SERVICE_MODELS_DIR -> HF_HOME / OLLAMA_MODELS

    store = TokenStore(os.environ.get("SERVICE_TOKENS_FILE", "tokens.json"))
    config = settings.Config(store.settings_path, SETTINGS)
    options = {spec.key: config.get(spec.key) for spec in SETTINGS}
    client = PaddleEngine(options)
    saved = settings.load(store.settings_path)
    threads = saved.get("num_thread") or os.environ.get("SERVICE_NUM_THREAD", "").strip()
    if threads:
        client.num_thread = int(threads)
    app = create_app(client=client, admin_password=os.environ.get("SERVICE_ADMIN_PASSWORD") or None,
                     tokens=store, config=config)
    host, port = os.environ.get("SERVICE_HOST", "127.0.0.1"), int(os.environ.get("SERVICE_PORT", "29262"))

    class Server(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets)  # after uvicorn's own "running on" line
            shown = "127.0.0.1" if host in ("0.0.0.0", "::") else host
            print(f"INFO:     Admin page: http://{shown}:{port}/admin/", flush=True)

    Server(uvicorn.Config(app, host=host, port=port)).run()


if __name__ == "__main__":
    main()
