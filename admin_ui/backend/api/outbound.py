"""
Outbound Campaign Dialer API endpoints (Milestone 22).

MVP scope:
- Campaign CRUD + status transitions (running/paused/stopped)
- CSV lead import (skip_existing default)
- Leads list + cancel
- Attempts list + basic stats
- Voicemail drop media upload + WAV preview (for browser playback)
"""

import io
import logging
import os
import re
import sys
import uuid
import wave
import audioop
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from pathlib import Path
try:
    from zoneinfo import ZoneInfo, available_timezones
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore
    available_timezones = None  # type: ignore

# Add project root to path for imports (mirrors calls.py)
project_root = os.environ.get("PROJECT_ROOT", "/app/project")
if project_root not in sys.path:
    sys.path.insert(0, project_root)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/outbound", tags=["outbound"])


def _get_outbound_store():
    try:
        from src.core.outbound_store import get_outbound_store
        return get_outbound_store()
    except ImportError as e:
        logger.error("Failed to import outbound_store module: %s", e)
        raise HTTPException(status_code=500, detail="Outbound dialer module not available")


def _media_dir() -> str:
    return os.getenv("AAVA_MEDIA_DIR", "/mnt/asterisk_media/ai-generated")

def _vm_upload_max_bytes() -> int:
    try:
        # Default: 12MB (enough for ~30s stereo 44.1k WAV) while still preventing abuse.
        return max(1, int(os.getenv("AAVA_VM_UPLOAD_MAX_BYTES", "12582912")))
    except Exception:
        return 12582912


_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")

def _detect_server_timezone() -> str:
    """
    Best-effort detection of server timezone as an IANA string.
    Prefer explicit env var, then /etc/localtime symlink, then /etc/timezone.
    """
    env_tz = (os.getenv("AAVA_SERVER_TIMEZONE") or "").strip()
    if env_tz:
        if ZoneInfo is None:
            return env_tz
        try:
            ZoneInfo(env_tz)
            return env_tz
        except Exception:
            return "UTC"

    try:
        target = os.path.realpath("/etc/localtime")
        marker = f"{os.sep}zoneinfo{os.sep}"
        if marker in target:
            tz = target.split(marker, 1)[1].strip(os.sep)
            if tz:
                if ZoneInfo is None:
                    return tz
                ZoneInfo(tz)
                return tz
    except Exception:
        pass

    try:
        tz = Path("/etc/timezone").read_text(encoding="utf-8").strip()
        if tz:
            if ZoneInfo is None:
                return tz
            ZoneInfo(tz)
            return tz
    except Exception:
        pass

    return "UTC"


@router.get("/meta")
async def outbound_meta():
    """
    UI helper metadata:
    - server_timezone: what the server/container thinks is the local timezone (IANA)
    - iana_timezones: list for validation/autocomplete
    """
    tz = _detect_server_timezone()
    tzs: List[str] = []
    if available_timezones is not None:
        try:
            tzs = sorted(list(available_timezones()))
        except Exception:
            tzs = []
    return {
        "server_timezone": tz,
        "iana_timezones": tzs,
        "server_now_iso": datetime.now(timezone.utc).isoformat(),
    }


class CampaignCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    timezone: str = "UTC"
    run_start_at_utc: Optional[str] = None
    run_end_at_utc: Optional[str] = None
    daily_window_start_local: str = "09:00"
    daily_window_end_local: str = "17:00"
    max_concurrent: int = Field(1, ge=1, le=5)
    min_interval_seconds_between_calls: int = Field(5, ge=0, le=3600)
    default_context: str = "default"
    voicemail_drop_mode: str = "upload"  # upload|tts
    voicemail_drop_text: Optional[str] = None
    voicemail_drop_media_uri: Optional[str] = None
    amd_options: Dict[str, Any] = Field(default_factory=dict)


class CampaignStatusRequest(BaseModel):
    status: str  # running|paused|stopped|draft|archived|completed
    cancel_pending: bool = False


class LeadImportResponse(BaseModel):
    accepted: int = 0
    rejected: int = 0
    duplicates: int = 0
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    error_csv: str = ""
    error_csv_truncated: bool = False


@router.get("/sample.csv")
async def download_sample_csv():
    """
    Download a sample CSV for lead import.

    Columns supported by the importer:
      - phone_number (required)
        - Can be E.164 (+15551234567) or an internal extension (e.g., 2765)
      - context (optional)
      - timezone (optional)
      - caller_id (optional)
      - custom_vars (optional JSON object)
    """
    csv_text = (
        "phone_number,context,timezone,caller_id,custom_vars\n"
        # Internal extension example (useful for PBX-to-PBX / lab testing).
        "2765,,,6789,\"{\"\"name\"\":\"\"Extension Test\"\",\"\"note\"\":\"\"Call internal extension\"\"}\"\n"
        # Leave context/timezone blank to use campaign defaults.
        "+15551234567,,,6789,\"{\"\"name\"\":\"\"Alice Example\"\",\"\"account_id\"\":\"\"A-1001\"\"}\"\n"
        # Example override: per-lead context + timezone.
        "+15557654321,demo_google_live,America/New_York,6789,\"{\"\"name\"\":\"\"Bob Example\"\",\"\"note\"\":\"\"Follow up from web form\"\"}\"\n"
    )
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="outbound_sample_leads.csv"'},
    )


@router.get("/campaigns")
async def list_campaigns(include_archived: bool = Query(False)):
    store = _get_outbound_store()
    return await store.list_campaigns(include_archived=bool(include_archived))


@router.post("/campaigns")
async def create_campaign(req: CampaignCreateRequest):
    store = _get_outbound_store()
    return await store.create_campaign(req.model_dump())


@router.get("/campaigns/{campaign_id}")
async def get_campaign(campaign_id: str):
    store = _get_outbound_store()
    try:
        return await store.get_campaign(campaign_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Campaign not found")


@router.patch("/campaigns/{campaign_id}")
async def update_campaign(campaign_id: str, body: Dict[str, Any]):
    store = _get_outbound_store()
    try:
        return await store.update_campaign(campaign_id, body or {})
    except KeyError:
        raise HTTPException(status_code=404, detail="Campaign not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/campaigns/{campaign_id}/clone")
async def clone_campaign(campaign_id: str):
    store = _get_outbound_store()
    try:
        return await store.clone_campaign(campaign_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Campaign not found")

@router.post("/campaigns/{campaign_id}/archive")
async def archive_campaign(campaign_id: str):
    store = _get_outbound_store()
    try:
        campaign = await store.get_campaign(campaign_id)
        if str(campaign.get("status") or "").strip().lower() == "running":
            raise HTTPException(status_code=400, detail="Pause/stop the campaign before archiving")
        return await store.set_campaign_status(campaign_id, "archived", cancel_pending=False)
    except KeyError:
        raise HTTPException(status_code=404, detail="Campaign not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/campaigns/{campaign_id}")
async def delete_campaign(campaign_id: str):
    store = _get_outbound_store()
    try:
        await store.delete_campaign(campaign_id)
        return {"ok": True}
    except KeyError:
        raise HTTPException(status_code=404, detail="Campaign not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/campaigns/{campaign_id}/status")
async def set_campaign_status(campaign_id: str, req: CampaignStatusRequest):
    store = _get_outbound_store()
    try:
        # Guardrails: require voicemail media before running.
        if req.status.strip().lower() == "running":
            campaign = await store.get_campaign(campaign_id)
            media_uri = (campaign.get("voicemail_drop_media_uri") or "").strip()
            if not media_uri:
                raise HTTPException(
                    status_code=400,
                    detail="voicemail_drop_media_uri is required before starting a campaign (upload/generate voicemail first)",
                )
            tz_name = (campaign.get("timezone") or "").strip() or "UTC"
            if ZoneInfo is not None and tz_name.upper() != "UTC":
                try:
                    ZoneInfo(tz_name)
                except Exception:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid timezone '{tz_name}'. Use an IANA timezone like 'America/Phoenix' or 'UTC'.",
                    )
        return await store.set_campaign_status(campaign_id, req.status, cancel_pending=bool(req.cancel_pending))
    except KeyError:
        raise HTTPException(status_code=404, detail="Campaign not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/campaigns/{campaign_id}/stats")
async def campaign_stats(campaign_id: str):
    store = _get_outbound_store()
    return await store.campaign_stats(campaign_id)


@router.post("/campaigns/{campaign_id}/leads/import", response_model=LeadImportResponse)
async def import_leads(
    campaign_id: str,
    file: UploadFile = File(...),
    skip_existing: bool = Query(True),
    max_error_rows: int = Query(20, ge=1, le=200),
):
    store = _get_outbound_store()
    try:
        data = await file.read()
        result = await store.import_leads_csv(
            campaign_id,
            data,
            skip_existing=bool(skip_existing),
            max_error_rows=int(max_error_rows),
        )
        return LeadImportResponse(**result)
    except KeyError:
        raise HTTPException(status_code=404, detail="Campaign not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/campaigns/{campaign_id}/leads")
async def list_leads(
    campaign_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    state: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
):
    store = _get_outbound_store()
    return await store.list_leads(campaign_id, page=page, page_size=page_size, state=state, q=q)


@router.post("/leads/{lead_id}/cancel")
async def cancel_lead(lead_id: str):
    store = _get_outbound_store()
    ok = await store.cancel_lead(lead_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Lead cannot be canceled in its current state")
    return {"ok": True}

@router.post("/leads/{lead_id}/recycle")
async def recycle_lead(lead_id: str):
    store = _get_outbound_store()
    ok = await store.recycle_lead(lead_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Lead cannot be recycled in its current state")
    return {"ok": True}


@router.get("/campaigns/{campaign_id}/attempts")
async def list_attempts(
    campaign_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    store = _get_outbound_store()
    return await store.list_attempts(campaign_id, page=page, page_size=page_size)


@router.post("/campaigns/{campaign_id}/voicemail/upload")
async def upload_voicemail_media(campaign_id: str, file: UploadFile = File(...)):
    store = _get_outbound_store()
    filename = (file.filename or "").strip() or "voicemail.ulaw"
    ext = os.path.splitext(filename)[1].lower().strip()
    if ext not in (".ulaw", ".wav"):
        raise HTTPException(status_code=400, detail="Upload must be .ulaw (8kHz μ-law) or .wav (PCM) audio")

    raw_name = os.path.basename(filename)
    if not _SAFE_NAME_RE.match(raw_name):
        raise HTTPException(status_code=400, detail="Invalid filename")

    media_dir = _media_dir()
    os.makedirs(media_dir, exist_ok=True)
    unique = f"outbound-vm-{campaign_id[:8]}-{uuid.uuid4().hex[:8]}.ulaw"
    path = os.path.join(media_dir, unique)
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty upload")
    max_bytes = _vm_upload_max_bytes()
    if len(data) > max_bytes:
        raise HTTPException(status_code=400, detail=f"Upload too large (max {max_bytes} bytes)")

    if ext == ".ulaw":
        ulaw_data = data
    else:
        # Convert WAV (PCM) -> 8kHz μ-law so Asterisk Playback() can use it directly.
        try:
            with wave.open(io.BytesIO(data), "rb") as wavf:
                nch = wavf.getnchannels()
                sampwidth = wavf.getsampwidth()
                fr = wavf.getframerate()
                nframes = wavf.getnframes()
                frames = wavf.readframes(nframes)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid WAV file: {e}")

        if nch not in (1, 2):
            raise HTTPException(status_code=400, detail="WAV must be mono or stereo (1–2 channels)")
        if sampwidth not in (1, 2, 3, 4):
            raise HTTPException(status_code=400, detail="Unsupported WAV sample width")

        # Normalize to 16-bit little-endian PCM for processing.
        if sampwidth != 2:
            frames = audioop.lin2lin(frames, sampwidth, 2)
        if nch == 2:
            # Downmix stereo -> mono.
            frames = audioop.tomono(frames, 2, 0.5, 0.5)

        # Resample to 8kHz if needed.
        if fr != 8000:
            frames, _ = audioop.ratecv(frames, 2, 1, fr, 8000, None)

        ulaw_data = audioop.lin2ulaw(frames, 2)

    with open(path, "wb") as f:
        f.write(ulaw_data)
    try:
        os.chmod(path, 0o664)
    except Exception:
        pass

    media_uri = f"sound:ai-generated/{unique[:-5]}"
    try:
        campaign = await store.update_campaign(campaign_id, {"voicemail_drop_media_uri": media_uri})
    except KeyError:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return {"media_uri": media_uri, "campaign": campaign}


@router.get("/campaigns/{campaign_id}/voicemail/preview.wav")
async def preview_voicemail_wav(campaign_id: str):
    store = _get_outbound_store()
    try:
        campaign = await store.get_campaign(campaign_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Campaign not found")

    media_uri = (campaign.get("voicemail_drop_media_uri") or "").strip()
    if not media_uri.startswith("sound:ai-generated/"):
        raise HTTPException(status_code=400, detail="Campaign voicemail media is not in ai-generated")
    base = media_uri.split("sound:ai-generated/", 1)[1].strip()
    if not base:
        raise HTTPException(status_code=400, detail="Invalid media_uri")

    ulaw_path = os.path.join(_media_dir(), f"{base}.ulaw")
    if not os.path.exists(ulaw_path):
        raise HTTPException(status_code=404, detail="Voicemail media file not found on server")
    with open(ulaw_path, "rb") as f:
        ulaw_data = f.read()

    pcm16 = audioop.ulaw2lin(ulaw_data, 2)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wavf:
        wavf.setnchannels(1)
        wavf.setsampwidth(2)
        wavf.setframerate(8000)
        wavf.writeframes(pcm16)
    wav_bytes = buf.getvalue()
    return Response(content=wav_bytes, media_type="audio/wav")
