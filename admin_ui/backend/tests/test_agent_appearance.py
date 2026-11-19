"""Avatar trust boundary, additive upgrades and consistent backup/restore."""
import base64
import io
import sqlite3

import pytest
from PIL import Image, PngImagePlugin
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_appearance import normalize_avatar, MAX_UPLOAD_BYTES
from agents_store import AgentsStore, SCHEMA
from api import agents as agents_api
from export_agents_yaml import export_yaml


def picture(format="PNG", size=(400, 300)):
    image = Image.new("RGB", size, "coral")
    output = io.BytesIO()
    info = PngImagePlugin.PngInfo()
    info.add_text("private-note", "must not survive")
    image.save(output, format=format, pnginfo=info)
    mime = {"PNG": "png", "JPEG": "jpeg", "WEBP": "webp"}[format]
    return f"data:image/{mime};base64," + base64.b64encode(output.getvalue()).decode()


@pytest.mark.parametrize("format", ["PNG", "JPEG", "WEBP"])
def test_normalizes_real_images_without_metadata(format):
    result = normalize_avatar(picture(format))
    raw = base64.b64decode(result.split(",", 1)[1])
    with Image.open(io.BytesIO(raw)) as image:
        assert image.format == "PNG"
        assert image.size == (256, 192)
        assert "private-note" not in image.info
    assert normalize_avatar(None) is None


@pytest.mark.parametrize("value", [
    "data:image/svg+xml;base64,PHN2Zy8+", "https://example.com/avatar.png",
    "data:image/png;base64,!!!!", "data:image/png;base64,YmFk",
    "data:image/png;base64," + "A" * (MAX_UPLOAD_BYTES * 2),
])
def test_rejects_unsafe_or_oversized_uploads(value):
    with pytest.raises(ValueError):
        normalize_avatar(value)


def test_mismatched_type_and_pixel_bomb_are_rejected():
    with pytest.raises(ValueError, match="not a valid supported image"):
        normalize_avatar(picture().replace("image/png", "image/jpeg"))
    with pytest.raises(ValueError, match="16 million"):
        normalize_avatar(picture(size=(4001, 4000)))


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = str(tmp_path / "relocated" / "agents.db")
    config = tmp_path / "ai-agent.yaml"
    monkeypatch.setenv("AGENTS_DB_PATH", db)
    monkeypatch.setattr(agents_api, "_yaml_path", lambda: str(config))
    monkeypatch.setattr(agents_api, "_contexts_dir", lambda: str(tmp_path / "contexts"))
    app = FastAPI()
    app.include_router(agents_api.router, prefix="/api")
    return TestClient(app), db, config


def test_appearance_patch_preserves_call_configuration_and_clear_is_explicit(env):
    client, db, _ = env
    before = client.post("/api/agents", json={"display_name": "Sales", "provider": "deepgram",
        "voice": "aura-2-thalia-en", "prompt": "Keep this", "tools_json": '["hangup_call"]',
        "email_enabled": None}).json()
    image = picture()
    response = client.patch("/api/agents/sales", json={"avatar_preset": "female", "avatar_image": image})
    assert response.status_code == 200, response.text
    after = response.json()
    for key, value in before.items():
        if key not in {"avatar_preset", "avatar_image", "updated_at"}:
            assert after[key] == value, key
    assert AgentsStore(db).get_by_slug("sales")["avatar_image"] == after["avatar_image"]
    client.patch("/api/agents/sales", json={"notes": "Changed in older UI"})
    assert client.get("/api/agents/sales").json()["avatar_image"] == after["avatar_image"]
    assert client.patch("/api/agents/sales", json={"avatar_preset": "invalid"}).status_code == 422
    rejected = client.patch("/api/agents/sales", json={"avatar_image": "bad", "prompt": "Must not save"})
    assert rejected.status_code == 422
    assert client.get("/api/agents/sales").json()["prompt"] == "Keep this"
    cleared = client.patch("/api/agents/sales", json={"avatar_image": None}).json()
    assert cleared["avatar_image"] is None and cleared["avatar_preset"] == "female"
    assert client.delete("/api/agents/sales").status_code == 204
    assert client.get("/api/agents/sales").status_code == 404


@pytest.mark.parametrize("partial", [False, True])
def test_upgrade_is_additive_repeatable_and_resumes_partial_schema(tmp_path, partial):
    db = str(tmp_path / "agents.db")
    old_schema = "\n".join(line for line in SCHEMA.splitlines() if "avatar_" not in line)
    with sqlite3.connect(db) as conn:
        conn.executescript(old_schema)
        conn.execute("INSERT INTO agents (id,slug,display_name,provider,prompt,created_at,updated_at) VALUES ('id','sales','Sales','p','prompt','old','old')")
        if partial:
            conn.execute("ALTER TABLE agents ADD COLUMN avatar_preset TEXT")
            conn.execute("UPDATE agents SET avatar_preset='female'")
    migrated = AgentsStore(db)
    before = migrated.get_by_slug("sales")
    assert before["id"] == "id" and before["updated_at"] == "old"
    assert before["avatar_preset"] == ("female" if partial else None)
    assert before["avatar_image"] is None
    migrated.conn.close()
    repeated = AgentsStore(db)
    assert repeated.get_by_slug("sales") == before


def test_backup_restore_captures_image_and_settings_in_one_snapshot(env, tmp_path):
    _, db, _ = env
    store = AgentsStore(db)
    row = store.create(display_name="Sales", provider="p", prompt="keep", avatar_preset="female", avatar_image=normalize_avatar(picture()))
    backup = str(tmp_path / "snapshot.db")
    # Same SQLite online-backup primitive as the updater; includes committed WAL.
    with sqlite3.connect(backup) as destination:
        store.conn.backup(destination)
    store.update("sales", avatar_image=None, prompt="later")
    restored = AgentsStore(backup)
    assert restored.get_by_slug("sales") == row
    assert "avatar" not in export_yaml(restored)


def test_yaml_reconciliation_preserves_appearance_and_ownership(env):
    client, db, config = env
    config.write_text("contexts:\n  Sales:\n    provider: p\n    prompt: first\n")
    assert client.post("/api/agents-migration/reconcile").status_code == 200
    client.patch("/api/agents/sales", json={"avatar_preset": "female", "avatar_image": picture()})
    before = AgentsStore(db).get_by_slug("sales")
    config.write_text("contexts:\n  Sales:\n    provider: p\n    prompt: updated\n")
    assert client.post("/api/agents-migration/reconcile").status_code == 200
    after = AgentsStore(db).get_by_slug("sales")
    assert after["prompt"] == "updated"
    assert after["avatar_image"] == before["avatar_image"]
    assert after["avatar_preset"] == "female"
    assert after["is_operator_managed"] == before["is_operator_managed"] == 0
