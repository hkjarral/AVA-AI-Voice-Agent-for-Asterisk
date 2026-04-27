#!/usr/bin/env python3
"""
Regenerate Piper TTS catalog entries from the HuggingFace rhasspy/piper-voices repo.

Usage:
    python scripts/regenerate_piper_catalog.py [--out FILE] [--include-existing]

Walks the rhasspy/piper-voices repo at v1.0.0 and emits Python dict literals in
the same format used by admin_ui/backend/api/models_catalog.py::PIPER_TTS_MODELS.

By default, voices already present in the catalog (matched by id) are skipped so
the output is purely "what's new." Pass --include-existing to regenerate every
voice for a full diff (e.g. when verifying the script's output matches what's
already there).

The output is a draft. Single-speaker voices have gender set to "?" because that
information is not in HuggingFace metadata; review by hand and fill in before
pasting into the catalog. Multi-speaker voices are tagged gender="multi"
automatically (matches the existing convention used by nl_mls).

Stdlib only — no third-party dependencies.
"""

import argparse
import concurrent.futures
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HF_REPO = "rhasspy/piper-voices"
HF_REV = "v1.0.0"
HF_API_BASE = f"https://huggingface.co/api/models/{HF_REPO}/tree/{HF_REV}"
HF_RESOLVE_BASE = f"https://huggingface.co/{HF_REPO}/resolve/{HF_REV}"
REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG_PATH = REPO_ROOT / "admin_ui" / "backend" / "api" / "models_catalog.py"

# Region tags mirror what the existing catalog uses. Anything not listed
# defaults to "europe" since most Piper voices are European languages.
REGION_BY_LANG_COUNTRY = {
    "en_US": "global", "en_GB": "global",
    "es_MX": "americas", "pt_BR": "americas", "es_AR": "americas",
    "zh_CN": "asia", "ja_JP": "asia", "ko_KR": "asia",
    "vi_VN": "asia", "ne_NP": "asia", "kk_KZ": "asia",
    "hi_IN": "asia", "ml_IN": "asia", "ka_GE": "asia",
    "ar_JO": "middle_east", "fa_IR": "middle_east",
    "sw_CD": "africa",
}

# Speaker-name fragments that are abbreviations or initialisms — preserved
# as-uppercase in display names instead of "Mls" / "Vctk" / "Tts".
TITLE_CASE_PRESERVE = {
    "mls": "MLS", "vctk": "VCTK", "tts": "TTS", "upc": "UPC",
    "upmc": "UPMC", "hfc": "HFC", "l2arctic": "L2-Arctic", "bu": "BU",
    "ukrainian_tts": "Ukrainian TTS", "vais1000": "VAIS-1000",
    "25hours_single": "25hours", "mc_speech": "MC Speech",
    "mls_5809": "MLS-5809", "mls_6892": "MLS-6892",
    "mls_7432": "MLS-7432", "mls_9972": "MLS-9972",
    "mls_10246": "MLS-10246", "mls_1840": "MLS-1840",
    "libritts": "LibriTTS", "libritts_r": "LibriTTS-R",
    "ljspeech": "LJSpeech", "northern_english_male": "Northern English Male",
    "southern_english_female": "Southern English Female",
    "thorsten_emotional": "Thorsten Emotional",
    "jenny_dioco": "Jenny Dioco", "joe": "Joe",
    "serbski_institut": "Serbski Institut",
}

# Heuristic gender map for common single-speaker names. Conservative — only
# names where confidence is high. Anything not in the map gets gender="unknown".
KNOWN_GENDERS = {
    # Female names
    "amy": "female", "alba": "female", "alma": "female", "ona": "female",
    "eva_k": "female", "kerstin": "female", "ramona": "female", "kathleen": "female",
    "kristin": "female", "irina": "female", "lada": "female", "gosia": "female",
    "siwis": "female", "lisa": "female", "paola": "female", "berta": "female",
    "raya": "female", "denise": "female", "claude": "female", "carlfm": "female",
    "cori": "female", "hfc_female": "female", "southern_english_female": "female",
    "sharvard": "female", "cadu": "female",
    # Male names
    "alan": "male", "ryan": "male", "joe": "male", "lessac": "male",
    "thorsten": "male", "thorsten_emotional": "male", "karlsson": "male",
    "danny": "male", "john": "male", "bryce": "male", "sam": "male",
    "norman": "male", "kareem": "male", "jirka": "male", "imre": "male",
    "darkman": "male", "issai": "male", "denis": "male", "dmitri": "male",
    "ruslan": "male", "fahrettin": "male", "fettah": "male", "edresson": "male",
    "jeff": "male", "tom": "male", "gilles": "male", "pim": "male",
    "ronnie": "male", "pau": "male", "davefx": "male", "ald": "male",
    "faber": "male", "riccardo": "male", "aru": "male", "hfc_male": "male",
    "northern_english_male": "male", "chitwan": "male",
    "amir": "male", "harri": "male", "arjun": "male", "pratham": "male",
    "steinn": "male", "aivars": "male", "mihai": "male", "artur": "male",
    "kusal": "male", "reza_ibrahim": "male",
    # Additional female names (Indian, Georgian, Slavic, Romance)
    "daniela": "female", "ljspeech": "female", "meera": "female",
    "priyamvada": "female", "natia": "female", "nathalie": "female",
    "salka": "female", "ugla": "female", "lili": "female",
}



def _safe_url(url):
    """Percent-encode the path portion so non-ASCII voice names work."""
    parts = urllib.parse.urlsplit(url)
    return urllib.parse.urlunsplit(
        parts._replace(path=urllib.parse.quote(parts.path, safe="/:@%"))
    )


def _parse_retry_after(value, default):
    """Retry-After is either delta-seconds or an HTTP-date. Be permissive
    so a date-format header doesn't crash the worker."""
    if not value:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        from email.utils import parsedate_to_datetime
        from datetime import datetime, timezone
        when = parsedate_to_datetime(value)
        delta = (when - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, delta)
    except Exception:
        return default


def fetch_json(url, timeout=30, max_retries=6):
    """GET JSON with exponential backoff on 429 (HF rate limit).

    HF caps the tree API around 60 req/min; backoff is 2/4/8/16/32/64s
    so a single endpoint can sleep up to ~2 minutes total before failing.
    """
    req = urllib.request.Request(_safe_url(url), headers={"Accept": "application/json"})
    delay = 2.0
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < max_retries - 1:
                # Honor server's Retry-After if present, else exponential backoff
                wait = _parse_retry_after(e.headers.get("Retry-After"), delay)
                time.sleep(wait)
                delay = min(delay * 2, 64.0)
                continue
            raise


def list_tree(path):
    url = f"{HF_API_BASE}/{path}".rstrip("/")
    try:
        return fetch_json(url)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise


def list_dirs(path):
    return [e for e in list_tree(path) if e.get("type") == "directory"]


def list_files(path):
    return [e for e in list_tree(path) if e.get("type") == "file"]


def parse_existing_ids(catalog_path):
    text = catalog_path.read_text(encoding="utf-8")
    return set(re.findall(r'"id":\s*"(piper_[^"]+)"', text))


def parse_existing_model_paths(catalog_path):
    """Pull every Piper model_path out of the existing catalog so we can
    skip generating entries that point at the same .onnx file under a
    different id (caught the ca_ES upc_ona duplicate flagged in PR #359
    review)."""
    text = catalog_path.read_text(encoding="utf-8")
    # Only match model_paths within Piper-style entries (lang_country prefix)
    return set(re.findall(r'"model_path":\s*"([a-z]{2,3}_[A-Z]{2,3}-[^"]+\.onnx)"', text))


# Languages with multiple country variants that need the country part in
# the id to disambiguate (en_us vs en_gb, pt_br vs pt_pt, es_mx vs es_es).
# Everything else collapses to the language code only (de_DE → de, pl_PL → pl)
# matching the convention used by the original hand-written catalog entries.
LONG_FORM_LANG_COUNTRIES = {
    "en_US", "en_GB", "en_AU",
    "pt_BR", "pt_PT",
    "es_MX", "es_AR",
}


def short_prefix(lang_country):
    if lang_country in LONG_FORM_LANG_COUNTRIES:
        return lang_country.lower()
    return lang_country.split("_", 1)[0]


def fetch_voice_meta(lang, lang_country, voice, quality):
    base = f"{lang}/{lang_country}/{voice}/{quality}"
    onnx_filename = f"{lang_country}-{voice}-{quality}.onnx"
    config_filename = f"{onnx_filename}.json"

    files = list_files(base)
    onnx_size = next((f["size"] for f in files if f["path"].endswith(onnx_filename)), None)
    if onnx_size is None:
        return None

    cfg_url = f"{HF_RESOLVE_BASE}/{base}/{config_filename}"
    try:
        cfg = fetch_json(cfg_url)
    except Exception as e:
        # Don't silently fall back to {} — that would default num_speakers
        # to 1 and misclassify a multi-speaker voice as single. Skip the
        # entry instead so the human reviewer notices it's missing rather
        # than catching the wrong gender/quality after the fact.
        print(f"  ! Skipping {lang_country}/{voice}/{quality}: config fetch failed ({e})",
              file=sys.stderr)
        return None

    return {
        "lang": lang,
        "lang_country": lang_country,
        "voice": voice,
        "quality": quality,
        "size_mb": max(1, round(onnx_size / 1024 / 1024)),
        "num_speakers": cfg.get("num_speakers", 1),
        "sample_rate": cfg.get("audio", {}).get("sample_rate"),
        "onnx_filename": onnx_filename,
        "config_filename": config_filename,
    }


def speaker_display_name(voice):
    """Human-friendly speaker name with abbreviation preservation."""
    if voice in TITLE_CASE_PRESERVE:
        return TITLE_CASE_PRESERVE[voice]
    parts = voice.split("_")
    out = []
    for p in parts:
        out.append(TITLE_CASE_PRESERVE.get(p, p.title()))
    return " ".join(out)


def render_entry(meta):
    lc = meta["lang_country"]
    short = short_prefix(lc)
    voice = meta["voice"]
    quality = meta["quality"]
    lang_display = lc.replace("_", "-")
    region = REGION_BY_LANG_COUNTRY.get(lc, "europe")

    if meta["num_speakers"] > 1:
        gender = "multi"
        gender_display = "Multi"
    else:
        gender = KNOWN_GENDERS.get(voice, "unknown")
        gender_display = gender.capitalize() if gender != "unknown" else None

    voice_id = f"piper_{short}_{voice}_{quality}"
    speaker = speaker_display_name(voice)
    if gender_display:
        name = f"{speaker} ({lang_display}, {gender_display})"
    else:
        name = f"{speaker} ({lang_display})"
    # Voice/filename may contain non-ASCII (e.g. pt_PT 'tugão'). Percent-encode
    # for the URL so urllib downloaders don't choke; keep model_path raw since
    # filesystems handle UTF-8.
    voice_q = urllib.parse.quote(f"{voice}/{quality}", safe="/")
    base_url = f"{HF_RESOLVE_BASE}/{meta['lang']}/{lc}/{voice_q}"
    onnx_q = urllib.parse.quote(meta["onnx_filename"])
    config_q = urllib.parse.quote(meta["config_filename"])

    return (
        f'    {{"id": "{voice_id}", "name": "{name}", "language": "{lang_display}", '
        f'"region": "{region}", "backend": "piper",\n'
        f'     "gender": "{gender}", "quality": "{quality}", '
        f'"size_mb": {meta["size_mb"]}, "size_display": "{meta["size_mb"]} MB",\n'
        f'     "model_path": "{meta["onnx_filename"]}",\n'
        f'     "download_url": "{base_url}/{onnx_q}",\n'
        f'     "config_url": "{base_url}/{config_q}"}},'
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, help="Write to file (default: stdout)")
    ap.add_argument("--include-existing", action="store_true",
                    help="Emit entries already present in the catalog (for diff/verification)")
    ap.add_argument("--max-workers", type=int, default=3,
                    help="Concurrent HF fetches (default 3; HF tree API limits ~60/min)")
    args = ap.parse_args()

    print(f"Walking {HF_REPO} @ {HF_REV} …", file=sys.stderr)
    existing_ids = parse_existing_ids(CATALOG_PATH)
    existing_paths = parse_existing_model_paths(CATALOG_PATH)
    print(f"  Catalog has {len(existing_ids)} piper voices ({len(existing_paths)} unique files) already.",
          file=sys.stderr)

    targets = []
    for lang_dir in list_dirs(""):
        lang = lang_dir["path"]
        for country_dir in list_dirs(lang):
            lang_country = country_dir["path"].split("/")[-1]
            for voice_dir in list_dirs(country_dir["path"]):
                voice = voice_dir["path"].split("/")[-1]
                for quality_dir in list_dirs(voice_dir["path"]):
                    quality = quality_dir["path"].split("/")[-1]
                    targets.append((lang, lang_country, voice, quality))
    print(f"  Found {len(targets)} voice×quality combos on HF.", file=sys.stderr)

    metas = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as ex:
        futures = {ex.submit(fetch_voice_meta, *t): t for t in targets}
        for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            t = futures[fut]
            try:
                m = fut.result()
                if m:
                    metas.append(m)
            except Exception as e:
                print(f"  ! Failed {t}: {e}", file=sys.stderr)
            if i % 25 == 0:
                print(f"  … {i}/{len(targets)}", file=sys.stderr)

    metas.sort(key=lambda m: (m["lang_country"], m["voice"], m["quality"]))

    if not args.include_existing:
        before = len(metas)
        # Skip if either the generated id OR the underlying file path is
        # already in the catalog. Path-level dedup catches the case where
        # an existing entry uses a different id but points at the same
        # .onnx file (the ca_ES upc/upc_ona collision found in PR #359).
        metas = [
            m for m in metas
            if f"piper_{short_prefix(m['lang_country'])}_{m['voice']}_{m['quality']}" not in existing_ids
            and m["onnx_filename"] not in existing_paths
        ]
        print(f"  Filtered out {before - len(metas)} already-in-catalog entries.", file=sys.stderr)

    print(f"  Emitting {len(metas)} entries.", file=sys.stderr)

    out_lines = [
        "# Generated by scripts/regenerate_piper_catalog.py",
        "# Review before pasting into models_catalog.py:",
        '#   - Voices with gender="unknown" come from names not in KNOWN_GENDERS',
        "#     (extend that map in the script if you want to fill them in).",
        "#   - Auto-aligned corpora (mls_*, libritts*) are usable but typically",
        "#     lower deployment quality than dedicated voices — consider trimming.",
        "",
    ]
    current_lc = None
    for m in metas:
        if m["lang_country"] != current_lc:
            if current_lc is not None:
                out_lines.append("")
            out_lines.append(f"    # === {m['lang_country']} ===")
            current_lc = m["lang_country"]
        out_lines.append(render_entry(m))

    output = "\n".join(out_lines) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        # Explicit UTF-8 — voice names like pt_PT 'tugão' need it and the
        # platform default would otherwise vary by locale.
        args.out.write_text(output, encoding="utf-8")
        print(f"\nWrote {len(metas)} entries to {args.out}", file=sys.stderr)
    else:
        # CodeQL flags this as "clear-text logging of sensitive information"
        # because the data was fetched from a remote URL. False positive: the
        # contents are exclusively public HuggingFace catalog metadata (model
        # filenames, sample rates) intended for paste into models_catalog.py.
        # No credentials, PII, or secrets are involved.
        sys.stdout.write(output)


if __name__ == "__main__":
    main()
