"""
Custom (community) models — let operators add LLM/TTS/STT models that are
not in the curated catalog. Off by default; enable with ENABLE_CUSTOM_MODELS=true
in .env or via the admin UI toggle.

Storage: data/custom_models.json (gitignored, persists across upgrades).

Validation is intentionally minimal — the feature is "best effort". For LLM
GGUF files we read the header after download to surface architecture, parameter
count, and quantization in the UI so users have something to compare against
their hardware. For TTS/STT we only verify the file landed and is non-empty.
"""

from __future__ import annotations

import json
import logging
import os
import re
import struct
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from settings import PROJECT_ROOT, ENV_PATH
from services.fs import upsert_env_vars

logger = logging.getLogger(__name__)

# Storage layout — sits next to the existing call_history.db in data/
CUSTOM_MODELS_FILE = Path(PROJECT_ROOT) / "data" / "custom_models.json"
CUSTOM_MODELS_LOCK = threading.Lock()  # serialise JSON writes within this process

# Toggle env var
ENABLE_ENV_KEY = "ENABLE_CUSTOM_MODELS"

# Allowed model types
ALLOWED_TYPES = {"llm", "tts", "stt"}

# llama.cpp arch names that the bundled wrapper has been verified with.
# Anything not in this list still gets to download but the UI shows a
# yellow warning so users know the model may not load.
KNOWN_LLM_ARCHITECTURES = {
    "llama", "qwen2", "qwen3", "phi3", "phi4", "gemma", "gemma2",
    "mistral", "mixtral", "command-r", "stablelm", "starcoder2",
    "deepseek2", "internlm2", "minicpm", "olmo", "orion", "rwkv6",
    "tinyllama", "exaone", "granite", "nemotron",
}


# ============== Settings toggle ==============

def is_enabled() -> bool:
    """Read the toggle from .env. Default: disabled."""
    if not ENV_PATH or not os.path.exists(ENV_PATH):
        return False
    try:
        with open(ENV_PATH, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() == ENABLE_ENV_KEY:
                        return v.strip().lower() in ("true", "1", "yes", "on")
    except Exception as e:
        logger.warning("Could not read %s from .env: %s", ENABLE_ENV_KEY, e)
    return False


def set_enabled(enabled: bool) -> None:
    """Persist the toggle to .env."""
    upsert_env_vars(ENV_PATH, {ENABLE_ENV_KEY: "true" if enabled else "false"})


# ============== Storage ==============

def _ensure_dir() -> None:
    CUSTOM_MODELS_FILE.parent.mkdir(parents=True, exist_ok=True)


def load_custom_models() -> List[Dict[str, Any]]:
    """Read custom models from disk. Returns empty list if file missing/invalid."""
    if not CUSTOM_MODELS_FILE.exists():
        return []
    try:
        data = json.loads(CUSTOM_MODELS_FILE.read_text())
        if isinstance(data, list):
            return data
        logger.warning("custom_models.json is not a list, ignoring")
    except Exception as e:
        logger.warning("Could not parse custom_models.json: %s", e)
    return []


def save_custom_models(models: List[Dict[str, Any]]) -> None:
    """Atomic write."""
    _ensure_dir()
    tmp = CUSTOM_MODELS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(models, indent=2))
    os.replace(tmp, CUSTOM_MODELS_FILE)


# ============== GGUF header introspection ==============
#
# Stdlib-only parser for the metadata section of a GGUF file. We only need
# a handful of fields (architecture, param count, quant, context length,
# layer count) so we read the KV section and skip past anything we don't
# care about by computing its on-disk size. Pulling in the official `gguf`
# Python package would work too but adds 500KB+ of model-conversion
# tooling we don't use; this stays at ~100 lines and zero deps.
#
# Format reference: github.com/ggml-org/ggml/blob/master/docs/gguf.md

_GGUF_MAGIC = b"GGUF"

# Value types from the GGUF spec
_T_UINT8, _T_INT8, _T_UINT16, _T_INT16 = 0, 1, 2, 3
_T_UINT32, _T_INT32, _T_FLOAT32, _T_BOOL = 4, 5, 6, 7
_T_STRING, _T_ARRAY = 8, 9
_T_UINT64, _T_INT64, _T_FLOAT64 = 10, 11, 12

_FIXED_SIZES = {
    _T_UINT8: 1, _T_INT8: 1,
    _T_UINT16: 2, _T_INT16: 2,
    _T_UINT32: 4, _T_INT32: 4, _T_FLOAT32: 4,
    _T_BOOL: 1,
    _T_UINT64: 8, _T_INT64: 8, _T_FLOAT64: 8,
}

_FIXED_FORMATS = {
    _T_UINT8: "B", _T_INT8: "b",
    _T_UINT16: "H", _T_INT16: "h",
    _T_UINT32: "I", _T_INT32: "i", _T_FLOAT32: "f",
    _T_BOOL: "?",
    _T_UINT64: "Q", _T_INT64: "q", _T_FLOAT64: "d",
}

# llama.cpp file_type → human label (subset; covers Q*_K_M, Q4_0, etc.)
# Source: ggml/src/ggml.c — LLAMA_FTYPE_*
_FTYPE_LABELS = {
    0: "F32", 1: "F16",
    2: "Q4_0", 3: "Q4_1",
    7: "Q8_0", 8: "Q5_0", 9: "Q5_1",
    10: "Q2_K", 11: "Q3_K_S", 12: "Q3_K_M", 13: "Q3_K_L",
    14: "Q4_K_S", 15: "Q4_K_M",
    16: "Q5_K_S", 17: "Q5_K_M",
    18: "Q6_K",
    19: "IQ2_XXS", 20: "IQ2_XS", 21: "Q2_K_S",
    22: "IQ3_XS", 23: "IQ3_XXS",
    24: "IQ1_S", 25: "IQ4_NL",
    26: "IQ3_S", 27: "IQ3_M",
    28: "IQ2_S", 29: "IQ2_M",
    30: "IQ4_XS", 31: "IQ1_M",
    32: "BF16",
}

# Fields we extract by exact key match
_WANTED_KEYS = {
    "general.architecture",
    "general.name",
    "general.parameter_count",
    "general.quantization_version",
    "general.file_type",
}
# Plus any *.context_length and *.block_count (architecture-prefixed)
_WANTED_PATTERNS = (".context_length", ".block_count", ".embedding_length")


def _read_string(f) -> str:
    (length,) = struct.unpack("<Q", f.read(8))
    return f.read(length).decode("utf-8", errors="replace")


def _read_value(f, vtype: int) -> Any:
    if vtype in _FIXED_FORMATS:
        size = _FIXED_SIZES[vtype]
        fmt = "<" + _FIXED_FORMATS[vtype]
        return struct.unpack(fmt, f.read(size))[0]
    if vtype == _T_STRING:
        return _read_string(f)
    if vtype == _T_ARRAY:
        (elem_type,) = struct.unpack("<I", f.read(4))
        (count,) = struct.unpack("<Q", f.read(8))
        # Skip array contents; we never need the values of array fields
        if elem_type in _FIXED_SIZES:
            f.seek(count * _FIXED_SIZES[elem_type], 1)
        elif elem_type == _T_STRING:
            for _ in range(count):
                _read_string(f)
        else:
            raise ValueError(f"Nested array of type {elem_type} unsupported")
        return None
    raise ValueError(f"Unknown GGUF value type {vtype}")


def parse_gguf_header(path: str) -> Dict[str, Any]:
    """Read the metadata section of a GGUF file. Returns a flat dict.

    Raises ValueError on a non-GGUF file or a header that can't be parsed.
    Does NOT load tensor data — only seeks past it.
    """
    with open(path, "rb") as f:
        magic = f.read(4)
        if magic != _GGUF_MAGIC:
            raise ValueError(f"not a GGUF file (magic={magic!r})")
        (version,) = struct.unpack("<I", f.read(4))
        (tensor_count,) = struct.unpack("<Q", f.read(8))
        (kv_count,) = struct.unpack("<Q", f.read(8))

        meta: Dict[str, Any] = {"_gguf_version": version, "_tensor_count": tensor_count}
        for _ in range(kv_count):
            key = _read_string(f)
            (vtype,) = struct.unpack("<I", f.read(4))
            value = _read_value(f, vtype)
            wanted = key in _WANTED_KEYS or any(key.endswith(p) for p in _WANTED_PATTERNS)
            if wanted and value is not None:
                meta[key] = value
        return meta


def _format_param_count(n: Optional[int]) -> Optional[str]:
    if n is None:
        return None
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.1f}B"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.0f}M"
    return str(n)


def introspect_gguf(model_path: str, file_size_bytes: int) -> Dict[str, Any]:
    """High-level wrapper: header parse + arch compatibility + RAM estimate.

    Returns a structured dict ready for the UI. Never raises — on parse
    failure returns ok=False so the UI can show "couldn't read header".
    """
    try:
        meta = parse_gguf_header(model_path)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    arch = meta.get("general.architecture")
    name = meta.get("general.name")
    params = meta.get("general.parameter_count")
    ftype = meta.get("general.file_type")

    # Some models put context_length under <arch>.context_length
    ctx = next((v for k, v in meta.items() if k.endswith(".context_length")), None)
    layers = next((v for k, v in meta.items() if k.endswith(".block_count")), None)
    embed = next((v for k, v in meta.items() if k.endswith(".embedding_length")), None)

    arch_supported = arch in KNOWN_LLM_ARCHITECTURES if arch else False
    arch_warning = None
    if arch and not arch_supported:
        arch_warning = (
            f"Architecture '{arch}' is not in the verified-supported list — "
            "it may load if your llama.cpp build is recent enough, or it may not."
        )

    # RAM estimate: file size on disk + KV cache scratch (rough).
    # Q-quantized GGUFs need ≈ file_size for weights, plus ~ ctx * layers * embed * 2 bytes
    # for the KV cache at full context. We add 1 GB headroom.
    file_gb = file_size_bytes / (1024 ** 3)
    kv_cache_gb = 0.0
    if ctx and layers and embed:
        kv_cache_gb = (ctx * layers * embed * 2 * 2) / (1024 ** 3)  # K + V, fp16
    estimated_ram_gb = round(file_gb + kv_cache_gb + 1.0, 1)

    return {
        "ok": True,
        "metadata": {
            "architecture": arch,
            "name": name,
            "param_count": params,
            "param_count_display": _format_param_count(params),
            "quantization": _FTYPE_LABELS.get(ftype, f"type_{ftype}" if ftype is not None else None),
            "context_length": ctx,
            "block_count": layers,
            "embedding_length": embed,
        },
        "compatibility": {
            "arch_supported": arch_supported,
            "arch_warning": arch_warning,
            "estimated_ram_gb": estimated_ram_gb,
            "file_size_gb": round(file_gb, 2),
        },
    }


# ============== Pydantic request/response models ==============

class CustomModelIn(BaseModel):
    type: str = Field(..., description="One of: llm, tts, stt")
    name: str = Field(..., min_length=1, max_length=120,
                      description="Display name shown in the UI")
    download_url: str = Field(..., description="HTTPS URL to the model file")
    config_url: Optional[str] = Field(None, description="Optional companion config URL (TTS)")
    expected_sha256: Optional[str] = Field(None, pattern=r"^[a-fA-F0-9]{64}$",
                                           description="Optional SHA256 for integrity check")
    chat_format: Optional[str] = Field(None, description="LLM chat format hint (chatml, llama-3, mistral-instruct, ...)")
    notes: Optional[str] = Field(None, max_length=500)


class ToggleIn(BaseModel):
    enabled: bool


# ============== CRUD helpers ==============

_ID_RE = re.compile(r"[^a-z0-9_]+")


def _slugify(name: str) -> str:
    return _ID_RE.sub("_", name.lower()).strip("_") or "custom_model"


def _derive_model_path(model: CustomModelIn) -> str:
    """Pick a filename from the URL, sanitised."""
    url_tail = model.download_url.rstrip("/").rsplit("/", 1)[-1]
    # Strip query string
    url_tail = url_tail.split("?", 1)[0]
    # Replace anything weird
    url_tail = re.sub(r"[^A-Za-z0-9._-]", "_", url_tail)
    return url_tail or f"{_slugify(model.name)}.bin"


def add_custom_model(model: CustomModelIn) -> Dict[str, Any]:
    """Persist a new custom model entry. Generates an id from the name."""
    if model.type not in ALLOWED_TYPES:
        raise ValueError(f"type must be one of {sorted(ALLOWED_TYPES)}")
    # HTTPS only — http:// could be redirected to internal addresses by a
    # malicious upstream and the download flow follows redirects. HuggingFace
    # and every other legitimate model host serves over HTTPS.
    if not model.download_url.startswith("https://"):
        raise ValueError("download_url must be https")
    if model.config_url and not model.config_url.startswith("https://"):
        raise ValueError("config_url must be https")

    with CUSTOM_MODELS_LOCK:
        models = load_custom_models()
        existing_ids = {m["id"] for m in models}
        base_id = f"custom_{model.type}_{_slugify(model.name)}"
        candidate, n = base_id, 1
        while candidate in existing_ids:
            n += 1
            candidate = f"{base_id}_{n}"

        entry: Dict[str, Any] = {
            "id": candidate,
            "name": model.name,
            "download_url": model.download_url,
            "model_path": _derive_model_path(model),
            "source": "user",  # so the UI can badge it "Community / best-effort"
            "size_mb": 0,  # filled in after download via Content-Length sniff
            "size_display": "?",
        }
        if model.config_url:
            entry["config_url"] = model.config_url
        if model.expected_sha256:
            entry["expected_sha256"] = model.expected_sha256
        if model.chat_format and model.type == "llm":
            entry["chat_format"] = model.chat_format
        if model.notes:
            entry["notes"] = model.notes
        # Type goes alongside the entry so the merge logic can route it
        entry["_type"] = model.type

        models.append(entry)
        save_custom_models(models)
        return entry


def _resolve_model_path(model_type: str, model_path: str) -> Path:
    """Validate + resolve a model file path under models/<type>/.

    Two layers of defence against path traversal: (1) reject any input
    containing path separators or '..', and (2) resolve the constructed
    path and assert it stays under models/<type>/. CodeQL's taint
    analysis flags constructed paths when they touch user input, so
    anchoring on the resolved path is what satisfies the analyzer too.
    """
    if model_type not in ALLOWED_TYPES:
        raise ValueError("invalid model type")
    if "/" in model_path or "\\" in model_path or ".." in model_path or not model_path:
        raise ValueError("model_path must be a bare filename")
    base = (Path(PROJECT_ROOT) / "models" / model_type).resolve()
    candidate = (base / model_path).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        raise ValueError("model_path escapes models/ directory")
    return candidate


def delete_custom_model(model_id: str) -> bool:
    """Remove from JSON and delete the file on disk if present."""
    with CUSTOM_MODELS_LOCK:
        models = load_custom_models()
        target = next((m for m in models if m["id"] == model_id), None)
        if not target:
            return False
        mtype = target.get("_type", "llm")
        model_path = target.get("model_path")
        if model_path and mtype in ALLOWED_TYPES:
            try:
                disk_path = _resolve_model_path(mtype, model_path)
                if disk_path.is_file():
                    disk_path.unlink()
                cfg = disk_path.with_suffix(disk_path.suffix + ".json")
                if cfg.is_file():
                    cfg.unlink()
            except (OSError, ValueError) as e:
                # Don't echo path back to logs — just the failure reason.
                logger.warning("Could not delete model file for %s: %s", model_id, e)
        models = [m for m in models if m["id"] != model_id]
        save_custom_models(models)
        return True


def delete_catalog_model_file(model_type: str, model_path: str) -> bool:
    """Delete a downloaded catalog model from disk. Catalog entry stays."""
    disk_path = _resolve_model_path(model_type, model_path)
    deleted = False
    if disk_path.is_file():
        disk_path.unlink()
        deleted = True
    cfg = disk_path.with_suffix(disk_path.suffix + ".json")
    if cfg.is_file():
        cfg.unlink()
        deleted = True
    return deleted


def merge_into_catalog(catalog: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
    """Return a new catalog dict with custom-model entries (when enabled)
    appended to the appropriate type list.

    IMPORTANT: must NOT mutate the input lists in place. The caller
    (wizard.py /local/available-models) passes lists that share references
    with module-level catalog constants (notably LLM_MODELS). Mutating
    them would re-append duplicates on every API call and pollute other
    code paths that read those module-level lists in-process.
    """
    if not is_enabled():
        return catalog
    custom_entries = load_custom_models()
    if not custom_entries:
        return catalog
    # Copy every type list before appending; new dict so the caller can
    # also pass the original around safely.
    out: Dict[str, List[Dict[str, Any]]] = {k: list(v) for k, v in catalog.items()}
    for entry in custom_entries:
        mtype = entry.get("_type")
        if mtype not in ALLOWED_TYPES:
            continue
        # Strip the internal _type field from the user-facing copy.
        # setdefault("source", ...) is defence-in-depth; add_custom_model
        # already sets it on save, but we want the badge to render even
        # for entries written by older versions of this code.
        public = {k: v for k, v in entry.items() if k != "_type"}
        public.setdefault("source", "user")
        out.setdefault(mtype, []).append(public)
    return out


# ============== Router ==============

router = APIRouter()


@router.get("/enabled")
async def get_enabled():
    return {"enabled": is_enabled()}


@router.post("/enabled")
async def set_enabled_endpoint(body: ToggleIn):
    set_enabled(body.enabled)
    return {"enabled": body.enabled}


@router.get("")
async def list_custom_models():
    """List custom models. Returns empty list if feature is disabled."""
    if not is_enabled():
        return {"enabled": False, "models": []}
    return {"enabled": True, "models": load_custom_models()}


@router.post("")
async def create_custom_model(body: CustomModelIn):
    if not is_enabled():
        raise HTTPException(status_code=403, detail="Custom models are disabled. Enable in Settings.")
    try:
        entry = add_custom_model(body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return entry


@router.delete("/{model_id}")
async def remove_custom_model(model_id: str):
    if not is_enabled():
        raise HTTPException(status_code=403, detail="Custom models are disabled.")
    ok = delete_custom_model(model_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Custom model not found")
    return {"deleted": model_id}


class CatalogDeleteIn(BaseModel):
    type: str
    model_path: str


@router.post("/delete-file")
async def delete_downloaded_model(body: CatalogDeleteIn):
    """Delete a downloaded catalog model file from disk (entry stays)."""
    try:
        deleted = delete_catalog_model_file(body.type, body.model_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not deleted:
        raise HTTPException(status_code=404, detail="Model file not on disk")
    return {"deleted": body.model_path}


class IntrospectIn(BaseModel):
    type: str
    model_path: str


@router.post("/introspect")
async def introspect_model(body: IntrospectIn):
    """Read the GGUF header of a downloaded LLM file. Returns metadata + RAM estimate."""
    if body.type != "llm":
        raise HTTPException(status_code=400, detail="introspect only supported for LLM models")
    try:
        disk_path = _resolve_model_path("llm", body.model_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not disk_path.is_file():
        # Echo only the bare filename, not the full server path.
        raise HTTPException(status_code=404, detail="Model file not found — has it been downloaded?")
    size = disk_path.stat().st_size
    return introspect_gguf(str(disk_path), size)
