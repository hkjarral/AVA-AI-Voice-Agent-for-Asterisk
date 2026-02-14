"""
Outbound Campaign Dialer API endpoints (Milestone 22).

MVP scope:
- Campaign CRUD + status transitions (running/paused/stopped)
- CSV lead import (skip_existing default)
- Leads list + ignore/recycle/delete
- Attempts list + basic stats
- Voicemail drop media upload + WAV preview (for browser playback)
- Optional consent media upload + WAV preview (for browser playback)
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
from src.audio.resampler import resample_audio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from pathlib import Path
import yaml
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

def _dotenv_value(key: str) -> Optional[str]:
    """Read a key from the project's `.env` file (best-effort)."""
    try:
        env_path = os.path.join(project_root, ".env")
        if not os.path.exists(env_path):
            return None
        from dotenv import dotenv_values

        raw = dotenv_values(env_path)
        val = raw.get(key)
        if val is None:
            return None
        return str(val).strip()
    except Exception:
        return None


def _get_outbound_store():
    try:
        from src.core.outbound_store import get_outbound_store
        return get_outbound_store()
    except ImportError as e:
        logger.error("Failed to import outbound_store module: %s", e)
        raise HTTPException(status_code=500, detail="Outbound dialer module not available")


def _media_dir() -> str:
    # SECURITY: Keep media dir anchored to the known, docker-mounted location.
    # Avoid using a fully user-controlled path via env var (CodeQL path-injection).
    return "/mnt/asterisk_media/ai-generated"

def _vm_upload_max_bytes() -> int:
    try:
        # Default: 12MB (enough for ~30s stereo 44.1k WAV) while still preventing abuse.
        return max(1, int(os.getenv("AAVA_VM_UPLOAD_MAX_BYTES", "12582912")))
    except Exception:
        return 12582912


DEFAULT_CONSENT_MEDIA_URI = "sound:ai-generated/aava-consent-default"
DEFAULT_VOICEMAIL_MEDIA_URI = "sound:ai-generated/aava-voicemail-default"

class RecordingRow(BaseModel):
    media_uri: str
    filename: str
    size_bytes: int = 0


def _media_uri_exists(media_uri: str) -> bool:
    uri = (media_uri or "").strip()
    if not uri.startswith("sound:ai-generated/"):
        return False
    base = os.path.basename(uri.split("sound:ai-generated/", 1)[1].strip())
    if not base:
        return False
    if (not _SAFE_NAME_RE.match(base)) or (".." in base):
        return False
    p = os.path.join(_media_dir(), f"{base}.ulaw")
    return os.path.exists(p)

def _safe_ai_generated_basename(media_uri: str) -> str:
    uri = (media_uri or "").strip()
    if not uri.startswith("sound:ai-generated/"):
        raise HTTPException(status_code=400, detail="media_uri must be in sound:ai-generated/")
    raw_base = uri.split("sound:ai-generated/", 1)[1].strip()
    base = os.path.basename(raw_base)
    if not base:
        raise HTTPException(status_code=400, detail="Invalid media_uri")
    # SECURITY: sanitize and reject path traversal attempts.
    if base != raw_base or (".." in base) or (not _SAFE_NAME_RE.match(base)):
        raise HTTPException(status_code=400, detail="Invalid media_uri basename")
    return base


_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")

def _ulaw_to_wav_bytes(ulaw_data: bytes) -> bytes:
    pcm16 = audioop.ulaw2lin(ulaw_data, 2)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wavf:
        wavf.setnchannels(1)
        wavf.setsampwidth(2)
        wavf.setframerate(8000)
        wavf.writeframes(pcm16)
    return buf.getvalue()

def _read_media_ulaw(media_uri: str) -> bytes:
    base = _safe_ai_generated_basename(media_uri)
    media_dir = _media_dir()
    target = f"{base}.ulaw"
    try:
        for entry in os.listdir(media_dir):
            if entry != target:
                continue
            ulaw_path = os.path.join(media_dir, entry)
            with open(ulaw_path, "rb") as f:
                return f.read()
    except FileNotFoundError:
        pass
    raise HTTPException(status_code=404, detail="Media file not found on server")

def _convert_upload_to_ulaw(data: bytes, ext: str) -> bytes:
    if not data:
        raise HTTPException(status_code=400, detail="Empty upload")
    max_bytes = _vm_upload_max_bytes()
    if len(data) > max_bytes:
        raise HTTPException(status_code=400, detail=f"Upload too large (max {max_bytes} bytes)")

    if ext == ".ulaw":
        return data

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

    if sampwidth != 2:
        frames = audioop.lin2lin(frames, sampwidth, 2)
    if nch == 2:
        frames = audioop.tomono(frames, 2, 0.5, 0.5)
    if fr != 8000:
        frames, _ = resample_audio(frames, fr, 8000)
    return audioop.lin2ulaw(frames, 2)

@router.get("/recordings", response_model=List[RecordingRow])
async def list_recordings():
    """
    List available recordings in the shared media directory.

    These are selectable by campaigns and can be reused across campaigns by referencing `media_uri`.
    """
    media_dir = _media_dir()
    try:
        os.makedirs(media_dir, exist_ok=True)
    except Exception:
        pass

    rows: List[RecordingRow] = []
    try:
        for entry in sorted(os.listdir(media_dir)):
            if not entry.endswith(".ulaw"):
                continue
            filename = entry
            base = entry[:-5]
            path = os.path.join(media_dir, entry)
            try:
                size_bytes = int(os.path.getsize(path))
            except Exception:
                size_bytes = 0
            rows.append(
                RecordingRow(
                    media_uri=f"sound:ai-generated/{base}",
                    filename=filename,
                    size_bytes=size_bytes,
                )
            )
    except Exception:
        return []
    return rows

@router.get("/recordings/preview.wav")
async def preview_recording_wav(media_uri: str = Query(...)):
    """
    Preview any `sound:ai-generated/*` recording as WAV (browser-playable).
    Useful for Create Campaign flow (no campaign_id yet).
    """
    ulaw_data = _read_media_ulaw(media_uri)
    wav_bytes = _ulaw_to_wav_bytes(ulaw_data)
    return Response(content=wav_bytes, media_type="audio/wav")

@router.post("/recordings/upload")
async def upload_recording_to_library(kind: str = Query("generic"), file: UploadFile = File(...)):
    """
    Upload a recording to the shared library (`AAVA_MEDIA_DIR`), returning its `media_uri`.

    - Accepts `.ulaw` (8kHz μ-law) or `.wav` (PCM; auto-converted to 8kHz μ-law).
    - Enforces max upload size via `AAVA_VM_UPLOAD_MAX_BYTES`.
    """
    filename = (file.filename or "").strip() or "recording.ulaw"
    ext = os.path.splitext(filename)[1].lower().strip()
    if ext not in (".ulaw", ".wav"):
        raise HTTPException(status_code=400, detail="Upload must be .ulaw (8kHz μ-law) or .wav (PCM) audio")

    raw_name = os.path.basename(filename)
    if raw_name and not _SAFE_NAME_RE.match(raw_name):
        raise HTTPException(status_code=400, detail="Invalid filename")

    media_dir = _media_dir()
    os.makedirs(media_dir, exist_ok=True)
    unique = f"outbound-recording-{uuid.uuid4().hex[:10]}.ulaw"
    path = os.path.join(media_dir, unique)

    data = await file.read()
    ulaw_data = _convert_upload_to_ulaw(data, ext)

    with open(path, "wb") as f:
        f.write(ulaw_data)

    media_uri = f"sound:ai-generated/{unique[:-5]}"
    return {"media_uri": media_uri}

def _detect_server_timezone() -> str:
    """
    Best-effort detection of server timezone as an IANA string.
    Prefer explicit env var (TZ or AAVA_SERVER_TIMEZONE), then /etc/localtime symlink, then /etc/timezone.
    """
    def _validate_iana(tz: str) -> Optional[str]:
        tz = (tz or "").strip()
        if not tz:
            return None
        if tz.upper() == "UTC":
            return "UTC"
        if ZoneInfo is None:
            return tz
        try:
            ZoneInfo(tz)
            return tz
        except Exception:
            return None

    # Prefer configured `.env` (UI saves here), then container environment.
    env_tz = _validate_iana(_dotenv_value("TZ") or "")
    if env_tz:
        return env_tz
    # Standard Docker env var
    env_tz = _validate_iana(os.getenv("TZ") or "")
    if env_tz:
        return env_tz
    env_tz = _validate_iana(_dotenv_value("AAVA_SERVER_TIMEZONE") or "")
    if env_tz:
        return env_tz
    env_tz = _validate_iana(os.getenv("AAVA_SERVER_TIMEZONE") or "")
    if env_tz:
        return env_tz

    try:
        target = os.path.realpath("/etc/localtime")
        marker = f"{os.sep}zoneinfo{os.sep}"
        if marker in target:
            tz = target.split(marker, 1)[1].strip(os.sep)
            if tz:
                validated = _validate_iana(tz)
                if validated:
                    return validated
    except Exception:
        pass

    try:
        tz = Path("/etc/timezone").read_text(encoding="utf-8").strip()
        validated = _validate_iana(tz)
        if validated:
            return validated
    except Exception:
        pass

    return "UTC"

def _load_known_context_names() -> List[str]:
    """
    Best-effort list of known context names from the active config.

    Reads the merged config (base + local override) so operator-added
    contexts in ai-agent.local.yaml are included.
    """
    try:
        from api.config import _read_merged_config_dict
        parsed = _read_merged_config_dict()
    except Exception:
        # Fallback to reading base file directly.
        try:
            from settings import CONFIG_PATH
            if not os.path.exists(CONFIG_PATH):
                return []
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                parsed = yaml.safe_load(f) or {}
        except Exception:
            return []
    if not isinstance(parsed, dict):
        return []
    ctxs = parsed.get("contexts") or {}
    if not isinstance(ctxs, dict):
        return []
    return [str(k).strip() for k in ctxs.keys() if str(k).strip()]

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
        "default_amd_options": {
            "initial_silence_ms": 2000,
            "greeting_ms": 2000,
            "after_greeting_silence_ms": 1000,
            "total_analysis_time_ms": 5000,
        },
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
    voicemail_drop_enabled: bool = True
    voicemail_drop_mode: str = "upload"  # upload|tts
    voicemail_drop_text: Optional[str] = None
    voicemail_drop_media_uri: Optional[str] = None
    consent_enabled: bool = False
    consent_media_uri: Optional[str] = None
    consent_timeout_seconds: int = Field(5, ge=1, le=30)
    amd_options: Dict[str, Any] = Field(default_factory=dict)


class CampaignStatusRequest(BaseModel):
    status: str  # running|paused|stopped|draft|archived|completed
    cancel_pending: bool = False

class LeadRecycleRequest(BaseModel):
    mode: str = Field("redial", pattern="^(redial|reset)$")  # redial|reset


class LeadImportResponse(BaseModel):
    accepted: int = 0
    rejected: int = 0
    duplicates: int = 0
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    error_csv: str = ""
    error_csv_truncated: bool = False
    warnings: List[Dict[str, Any]] = Field(default_factory=list)
    warnings_truncated: bool = False


@router.get("/sample.csv")
async def download_sample_csv():
    """
    Download a sample CSV for lead import.

    Columns supported by the importer (full format):
      - name (optional)
      - phone_number (required)
        - Can be E.164 (+15551234567) or an internal extension (e.g., 2765)
      - context (optional)
      - timezone (optional)
      - caller_id (optional)
      - custom_vars (optional JSON object)
    """
    csv_text = (
        "name,phone_number,context,timezone,caller_id,custom_vars\n"
        "Extension Test,2765,demo_outbound,America/Phoenix,6789,\"{\"\"name\"\":\"\"Extension Test\"\",\"\"note\"\":\"\"Call internal extension\"\"}\"\n"
        "Alice Example,+15557654321,demo_outbound,America/Phoenix,6789,\"{\"\"name\"\":\"\"Alice Example\"\",\"\"account_id\"\":\"\"A-1002\"\",\"\"note\"\":\"\"US lead example\"\"}\"\n"
        "International Example,+447700900123,demo_outbound,America/Phoenix,6789,\"{\"\"name\"\":\"\"International Example\"\",\"\"account_id\"\":\"\"A-1003\"\",\"\"note\"\":\"\"International lead example\"\"}\"\n"
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
    payload = req.model_dump()
    try:
        if payload.get("voicemail_drop_enabled") and not (payload.get("voicemail_drop_media_uri") or "").strip():
            if _media_uri_exists(DEFAULT_VOICEMAIL_MEDIA_URI):
                payload["voicemail_drop_media_uri"] = DEFAULT_VOICEMAIL_MEDIA_URI
        if payload.get("consent_enabled") and not (payload.get("consent_media_uri") or "").strip():
            if _media_uri_exists(DEFAULT_CONSENT_MEDIA_URI):
                payload["consent_media_uri"] = DEFAULT_CONSENT_MEDIA_URI
    except Exception:
        pass
    return await store.create_campaign(payload)


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
        # Guardrails: require enabled recordings before running.
        if req.status.strip().lower() == "running":
            campaign = await store.get_campaign(campaign_id)
            stats = await store.campaign_stats(campaign_id)
            lead_states = (stats or {}).get("lead_states") or {}
            pending = int(lead_states.get("pending") or 0)
            if pending <= 0:
                canceled = int(lead_states.get("canceled") or 0)
                completed = int(lead_states.get("completed") or 0)
                raise HTTPException(
                    status_code=400,
                    detail=f"No pending leads to dial (canceled={canceled}, completed={completed}). Recycle leads back to pending, then Start again.",
                )
            if bool(int(campaign.get("voicemail_drop_enabled") or 1)):
                media_uri = (campaign.get("voicemail_drop_media_uri") or "").strip()
                if not media_uri:
                    raise HTTPException(
                        status_code=400,
                        detail="Voicemail drop is enabled but no voicemail recording is set. Upload/generate voicemail before starting.",
                    )
            if bool(int(campaign.get("consent_enabled") or 0)):
                consent_uri = (campaign.get("consent_media_uri") or "").strip()
                if not consent_uri:
                    raise HTTPException(
                        status_code=400,
                        detail="Consent gate is enabled but no consent recording is set. Upload consent before starting.",
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
        known_contexts = _load_known_context_names()
        result = await store.import_leads_csv(
            campaign_id,
            data,
            skip_existing=bool(skip_existing),
            max_error_rows=int(max_error_rows),
            known_contexts=known_contexts or None,
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

@router.post("/leads/{lead_id}/ignore")
async def ignore_lead(lead_id: str):
    store = _get_outbound_store()
    ok = await store.ignore_lead(lead_id)
    if not ok:
        raise HTTPException(status_code=400, detail="Lead cannot be ignored in its current state")
    return {"ok": True}

@router.post("/leads/{lead_id}/recycle")
async def recycle_lead(lead_id: str, req: LeadRecycleRequest):
    store = _get_outbound_store()
    ok = await store.recycle_lead(lead_id, mode=req.mode)
    if not ok:
        raise HTTPException(status_code=400, detail="Lead cannot be recycled in its current state")
    return {"ok": True}

@router.delete("/leads/{lead_id}")
async def delete_lead(lead_id: str):
    store = _get_outbound_store()
    try:
        await store.delete_lead(lead_id)
        return {"ok": True}
    except KeyError:
        raise HTTPException(status_code=404, detail="Lead not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


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
    unique = f"outbound-vm-{uuid.uuid4().hex[:10]}.ulaw"
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
            frames, _ = resample_audio(frames, fr, 8000)

        ulaw_data = audioop.lin2ulaw(frames, 2)

    with open(path, "wb") as f:
        f.write(ulaw_data)

    media_uri = f"sound:ai-generated/{unique[:-5]}"
    try:
        campaign = await store.update_campaign(campaign_id, {"voicemail_drop_media_uri": media_uri})
    except KeyError:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return {"media_uri": media_uri, "campaign": campaign}

@router.post("/campaigns/{campaign_id}/consent/upload")
async def upload_consent_media(campaign_id: str, file: UploadFile = File(...)):
    store = _get_outbound_store()
    filename = (file.filename or "").strip() or "consent.ulaw"
    ext = os.path.splitext(filename)[1].lower().strip()
    if ext not in (".ulaw", ".wav"):
        raise HTTPException(status_code=400, detail="Upload must be .ulaw (8kHz μ-law) or .wav (PCM) audio")

    raw_name = os.path.basename(filename)
    if not _SAFE_NAME_RE.match(raw_name):
        raise HTTPException(status_code=400, detail="Invalid filename")

    media_dir = _media_dir()
    os.makedirs(media_dir, exist_ok=True)
    unique = f"outbound-consent-{uuid.uuid4().hex[:10]}.ulaw"
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

        if sampwidth != 2:
            frames = audioop.lin2lin(frames, sampwidth, 2)
        if nch == 2:
            frames = audioop.tomono(frames, 2, 0.5, 0.5)
        if fr != 8000:
            frames, _ = resample_audio(frames, fr, 8000)
        ulaw_data = audioop.lin2ulaw(frames, 2)

    with open(path, "wb") as f:
        f.write(ulaw_data)

    media_uri = f"sound:ai-generated/{unique[:-5]}"
    try:
        campaign = await store.update_campaign(campaign_id, {"consent_media_uri": media_uri})
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
    ulaw_data = _read_media_ulaw(media_uri)

    wav_bytes = _ulaw_to_wav_bytes(ulaw_data)
    return Response(content=wav_bytes, media_type="audio/wav")


@router.get("/campaigns/{campaign_id}/consent/preview.wav")
async def preview_consent_wav(campaign_id: str):
    store = _get_outbound_store()
    try:
        campaign = await store.get_campaign(campaign_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Campaign not found")

    media_uri = (campaign.get("consent_media_uri") or "").strip()
    ulaw_data = _read_media_ulaw(media_uri)

    wav_bytes = _ulaw_to_wav_bytes(ulaw_data)
    return Response(content=wav_bytes, media_type="audio/wav")
