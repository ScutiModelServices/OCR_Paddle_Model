import asyncio

import pytest

from conftest import make_engine
from updates import Updates


def make_updates(engine):
    return Updates(engine.model, cached_sha=engine.cached_sha, remote_sha=engine.remote_sha, download=engine.pull, unload=engine.unload)


def updates(cached="old", latest="new", downloads=None):
    engine = make_engine(ready=False, cached_sha=lambda repos: cached, remote_sha=lambda repos: latest,
                         download=lambda repos: (downloads if downloads is not None else []).append("m"))
    return engine, make_updates(engine)


def test_a_check_reports_a_newer_revision_without_downloading():
    downloads = []
    _, found = updates(downloads=downloads)
    status = asyncio.run(found.check())
    assert status["weights"] == [{"repo": "pp-ocrv5", "cached": "old", "latest": "new", "update": True}]
    assert status["error"] is None and downloads == []


def test_the_same_revision_is_not_an_update():
    _, found = updates(cached="same", latest="same")
    assert asyncio.run(found.check())["weights"][0]["update"] is False


def test_a_failed_check_is_reported_not_raised():
    def broken(repos):
        raise OSError("offline")

    status = asyncio.run(make_updates(make_engine(remote_sha=broken)).check())
    assert "offline" in status["error"]


def test_updating_downloads_then_loads_the_new_weights():
    downloads = []
    engine, found = updates(downloads=downloads)  # built outside the loop: make_engine runs one itself

    async def scenario():
        await engine.start()
        await found.update_weights()

    asyncio.run(scenario())
    assert downloads == ["m"] and engine.backend.loads == 2 and engine.ready


def test_a_failed_download_keeps_the_loaded_model():
    def broken(repos):
        raise OSError("disk full")

    engine = make_engine(ready=False, cached_sha=lambda repos: "old", remote_sha=lambda repos: "new", download=broken)

    async def scenario():
        await engine.start()
        with pytest.raises(OSError):
            await make_updates(engine).update_weights()

    asyncio.run(scenario())
    assert engine.backend.loads == 1 and engine.ready
