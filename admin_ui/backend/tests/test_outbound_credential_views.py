import asyncio
import csv
import io
import json
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from api import outbound
from src.core.outbound_store import OutboundStore


PASSWORD = "synthetic-password-value"
QUERY = "synthetic-query-value"
URI = f"https://reader:{PASSWORD}@media.example.invalid/messages/one?token={QUERY}&part=2"
OTHER = "https://media.example.invalid/messages/two?sig=second-synthetic-value"


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("CALL_HISTORY_ENABLED", "true")
    store = OutboundStore(str(tmp_path / "outbound.db"))
    monkeypatch.setattr(outbound, "_get_outbound_store", lambda: store)
    monkeypatch.setattr(outbound, "_media_dir", lambda: str(tmp_path))
    monkeypatch.setattr(outbound, "_load_known_agent_selectors", lambda: (None, None))
    app = FastAPI()
    app.include_router(outbound.router)
    return TestClient(app), store


def seed(client, name="one"):
    result = client.post("/outbound/campaigns", json={"name": name, "voicemail_drop_media_uri": URI,
                                                     "consent_media_uri": OTHER})
    assert result.status_code == 200
    return result.json()


def masked(row, field="voicemail_drop_media_uri"):
    value = row[field]
    assert "media.example.invalid/messages/" in value and isinstance(value, str)
    assert all(private not in value for private in (PASSWORD, QUERY, "second-synthetic-value", "token=", "part=2"))
    return value


def test_campaign_views_and_actions_keep_original_uri_for_dialer(boundary):
    client, store = boundary
    campaign = seed(client)
    path = "/outbound/campaigns/" + campaign["id"]
    public = masked(campaign)
    assert "https://reader:" in public
    assert masked(client.get(path).json()) == public
    assert masked(client.get("/outbound/campaigns").json()[0]) == public
    assert masked(client.patch(path, json={"name":"updated","voicemail_drop_media_uri":public}).json()) == public
    assert masked(client.post(path+"/status", json={"status":"paused"}).json()) == public
    clone = client.post(path+"/clone").json()
    masked(clone)
    assert clone["id"] != campaign["id"]
    assert masked(client.post(path+"/archive").json()) == public
    assert store.get_campaign_sync(campaign["id"])["voicemail_drop_media_uri"] == URI


@pytest.mark.parametrize(("uploaded", "other"), [
    ("consent", "voicemail_drop_media_uri"), ("voicemail", "consent_media_uri"),
])
def test_upload_masks_the_other_recording_in_its_campaign(boundary, uploaded, other):
    client, _ = boundary
    campaign = seed(client)
    result = client.post(f"/outbound/campaigns/{campaign['id']}/{uploaded}/upload",
                         files={"file": ("sample.ulaw", b"fixture", "application/octet-stream")})
    assert result.status_code == 200
    assert result.json()["media_uri"].startswith("sound:ai-generated/")
    masked(result.json()["campaign"], other)


def test_retained_uri_is_bound_to_campaign_field_current_secret_and_exact_display(boundary):
    client, store = boundary
    first, second = seed(client), seed(client, "two")
    first_path = f"/outbound/campaigns/{first['id']}"
    field = "voicemail_drop_media_uri"
    marker = masked(first)
    for path, target, value in [
        (f"/outbound/campaigns/{second['id']}", field, marker),
        (first_path, "consent_media_uri", marker),
        (first_path, field, marker.replace("messages/one", "messages/other")),
        (first_path, field, marker.replace("media.example.invalid", "another.invalid")),
    ]:
        refused = client.patch(path, json={target:value, "name":"would change"})
        assert refused.status_code == 400
        assert PASSWORD not in refused.text and QUERY not in refused.text
    assert client.post("/outbound/campaigns", json={"name":"new", field:marker}).status_code == 400
    replaced = URI.replace(QUERY, "synthetic-rotated-query")
    assert client.patch(first_path, json={field:replaced}).status_code == 200
    stale = client.patch(first_path, json={field:marker, "name":"would change"})
    assert stale.status_code == 400
    stored = store.get_campaign_sync(first["id"])
    assert stored[field] == replaced and stored["name"] == "one"
    assert client.patch(first_path, json={field:"sound:ai-generated/fixture"}).json()[field] == "sound:ai-generated/fixture"
    assert client.patch(first_path, json={field:None}).json()[field] is None


def test_lead_list_masks_only_business_secrets_but_import_and_scheduled_data_stay_intact(boundary):
    client, store = boundary
    row = seed(client)
    values = {"nested":[{"SMTP_PASS":PASSWORD, "location":URI}], "ordinary":"keep", "text":"Bearer ordinary business text"}
    entries = io.StringIO()
    writer = csv.writer(entries)
    writer.writerow(["name","phone_number","custom_vars"])
    writer.writerow(["Fixture","+12025550144",json.dumps(values)])
    imported = asyncio.run(store.import_leads_csv(row["id"], entries.getvalue().encode()))
    assert imported["accepted"] == 1
    response = client.get(f"/outbound/campaigns/{row['id']}/leads")
    assert response.status_code == 200
    shown = response.json()["leads"][0]["custom_vars"]
    assert shown["ordinary"] == values["ordinary"] and shown["text"] == values["text"]
    assert PASSWORD not in json.dumps(shown) and QUERY in shown["nested"][0]["location"]
    with sqlite3.connect(store._db_path) as db:
        assert json.loads(db.execute("SELECT custom_vars_json FROM outbound_leads").fetchone()[0]) == values


def test_racing_secret_rotation_is_kept_and_stale_public_edit_is_rejected(boundary, monkeypatch):
    client, editor = boundary
    campaign = seed(client)
    field = "voicemail_drop_media_uri"
    shown = masked(campaign)
    path = f"/outbound/campaigns/{campaign['id']}"
    concurrent = OutboundStore(editor._db_path)
    original = editor.update_campaign
    async def rotate_before_transaction(campaign_id, updates, **kwargs):
        await concurrent.update_campaign(campaign_id, {field: OTHER})
        return await original(campaign_id, updates, **kwargs)
    monkeypatch.setattr(editor, "update_campaign", rotate_before_transaction)
    response = client.patch(path, json={field:shown, "name":"stale action"})
    assert response.status_code == 400
    current = editor.get_campaign_sync(campaign["id"])
    assert current[field] == OTHER and current["name"] == "one"
