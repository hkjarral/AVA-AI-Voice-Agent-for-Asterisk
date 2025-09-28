#!/usr/bin/env python3
"""Model setup utility for the Asterisk AI Voice Agent.

This script detects the host system capabilities, selects an appropriate
model tier from ``models/registry.json``, downloads the required
artifacts, and prints expected conversational performance so users know
what to expect before placing a call with the local provider.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse
from urllib.request import urlopen

REGISTRY_PATH = Path("models/registry.json")
DEFAULT_MODELS_DIR = Path("models")


class DownloadError(RuntimeError):
    """Raised when a download fails."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Setup local AI provider models")
    parser.add_argument(
        "--registry",
        type=Path,
        default=REGISTRY_PATH,
        help="Path to model registry (default: %(default)s)",
    )
    parser.add_argument(
        "--tier",
        choices=["LIGHT", "MEDIUM", "HEAVY"],
        help="Override detected system tier",
    )
    parser.add_argument(
        "--assume-yes",
        action="store_true",
        help="Proceed without prompting",
    )
    parser.add_argument(
        "--models-dir",
        type=Path,
        default=DEFAULT_MODELS_DIR,
        help="Base directory for downloaded models (default: %(default)s)",
    )
    return parser.parse_args()


def detect_cpu_cores() -> int:
    return max(1, os.cpu_count() or 1)


def detect_total_ram_gb() -> int:
    # Try psutil if present
    try:
        import psutil  # type: ignore

        return int(psutil.virtual_memory().total / (1024 ** 3))
    except Exception:
        pass

    # Linux: /proc/meminfo
    if Path("/proc/meminfo").exists():
        try:
            with open("/proc/meminfo", "r") as meminfo:
                for line in meminfo:
                    if line.startswith("MemTotal:"):
                        parts = line.split()
                        if len(parts) >= 2:
                            kb = int(parts[1])
                            return max(1, kb // (1024 ** 2))
        except Exception:
            pass

    # macOS: sysctl
    if sys.platform == "darwin":
        try:
            output = subprocess.check_output(["sysctl", "-n", "hw.memsize"])
            return int(output.strip()) // (1024 ** 3)
        except Exception:
            pass

    # Fallback
    return 0


def detect_available_disk_gb(path: Path) -> int:
    usage = shutil.disk_usage(path)
    return int(usage.free / (1024 ** 3))


def detect_environment() -> str:
    if Path("/.dockerenv").exists():
        return "docker"
    if "KUBERNETES_SERVICE_HOST" in os.environ:
        return "kubernetes"
    return "host"


def determine_tier(
    registry: Dict[str, Any],
    cpu_cores: int,
    ram_gb: int,
    override: Optional[str] = None,
    *,
    gpu_info: Optional[Dict[str, Any]] = None,
) -> str:
    tiers = registry.get("tiers", {})
    if override:
        if override not in tiers:
            raise SystemExit(f"Requested tier '{override}' not found in registry")
        return override

    # Select the highest tier whose requirements are satisfied
    if not tiers:
        raise SystemExit("Registry does not contain any tiers")

    gpu_info = gpu_info or {"available": False, "max_vram_gb": 0}
    max_vram = float(gpu_info.get("max_vram_gb", 0) or 0)
    has_gpu = bool(gpu_info.get("available", False))

    tier_records: List[Tuple[str, Dict[str, Any], Tuple[float, ...]]] = []
    for name, info in tiers.items():
        requirements = info.get("requirements")
        if not isinstance(requirements, dict):
            raise SystemExit(f"Tier '{name}' is missing a requirements object")

        try:
            min_cpu = float(requirements["min_cpu_cores"])
            min_ram = float(requirements["min_ram_gb"])
        except KeyError as exc:
            raise SystemExit(
                f"Tier '{name}' requirements must include 'min_cpu_cores' and 'min_ram_gb'"
            ) from exc

        requires_gpu = bool(requirements.get("requires_gpu", False))
        min_vram = float(requirements.get("min_vram_gb", 0) or 0)

        if "priority" in requirements:
            priority = float(requirements["priority"])
            sort_key = (priority,)
        else:
            sort_key = (
                1.0 if requires_gpu else 0.0,
                min_cpu,
                min_ram,
                min_vram,
            )

        tier_records.append((name, info, sort_key))

    # Highest priority / capability first
    tier_records.sort(key=lambda item: item[2], reverse=True)

    for name, info, _ in tier_records:
        requirements = info["requirements"]
        min_cpu = float(requirements["min_cpu_cores"])
        min_ram = float(requirements["min_ram_gb"])
        requires_gpu = bool(requirements.get("requires_gpu", False))
        min_vram = float(requirements.get("min_vram_gb", 0) or 0)

        if cpu_cores < min_cpu or ram_gb < min_ram:
            continue

        if requires_gpu:
            if not has_gpu or max_vram < min_vram:
                continue

        return name

    # Default to LIGHT if nothing matches
    if "LIGHT" in tiers:
        return "LIGHT"

    raise SystemExit(
        "Unable to determine a compatible tier and no 'LIGHT' fallback is available"
    )


def human_readable_size_mb(size_mb: float) -> str:
    if size_mb >= 1024:
        return f"{size_mb / 1024:.1f} GB"
    return f"{size_mb:.0f} MB"


def prompt_yes_no(message: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    response = input(f"{message} [y/N]: ").strip().lower()
    return response in {"y", "yes"}


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def download_file(url: str, dest: Path, label: str) -> None:
    ensure_parent(dest)
    tmp_fd, tmp_path = tempfile.mkstemp(prefix="download_", suffix=dest.suffix)
    os.close(tmp_fd)
    tmp_file = Path(tmp_path)
    try:
        print(f"Downloading {label} → {dest}...")
        with urlopen(url) as response, tmp_file.open("wb") as out:
            shutil.copyfileobj(response, out)
        tmp_file.replace(dest)
    except Exception as exc:
        tmp_file.unlink(missing_ok=True)
        raise DownloadError(f"Failed to download {label} from {url}: {exc}")


def extract_zip(archive: Path, target_dir: Path) -> None:
    print(f"Extracting {archive.name} → {target_dir}")
    target_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        with zipfile.ZipFile(archive, "r") as zip_ref:
            zip_ref.extractall(tmp_path)

        for item in tmp_path.iterdir():
            destination = target_dir / item.name
            if item.is_dir():
                shutil.copytree(item, destination, dirs_exist_ok=True)
            else:
                shutil.copy2(item, destination)


def _safe_relative_path(base: Path, relative: Path, *, purpose: str) -> Path:
    if relative.is_absolute():
        raise SystemExit(f"{purpose} must be a relative path (got: {relative})")

    base_resolved = base.resolve(strict=False)
    target = (base / relative).resolve(strict=False)

    try:
        target.relative_to(base_resolved)
    except ValueError as exc:
        raise SystemExit(f"{purpose} must stay within {base_resolved} (got: {relative})") from exc

    if target == base_resolved:
        raise SystemExit(f"{purpose} must not be the base directory {base_resolved}")

    return target


def _derive_archive_stem(url: str) -> str:
    parsed = urlparse(url)
    name = Path(parsed.path or "model").name
    stem = Path(name).stem
    if stem.endswith(".tar"):
        stem = stem[: -len(".tar")]
    return stem or "model"


def download_models_for_tier(tier_info: Dict[str, Any], models_dir: Path) -> None:
    models = tier_info.get("models", {})

    stt = models.get("stt")
    if stt:
        url = stt["url"]
        dest_dir_value = stt.get("dest_dir") or "downloads"
        dest_dir_rel = Path(dest_dir_value)
        if dest_dir_rel in {Path(""), Path(".")}:
            dest_dir_rel = Path("downloads")
        dest_dir = _safe_relative_path(models_dir, dest_dir_rel, purpose="STT dest_dir")
        archive_name = Path(urlparse(url).path).name or "model.zip"
        archive_path = dest_dir / archive_name

        target_rel = stt.get("target_path")
        if target_rel:
            target_path = _safe_relative_path(
                models_dir, Path(target_rel), purpose="STT target_path"
            )
        else:
            default_rel = Path("stt") / _derive_archive_stem(url)
            target_path = _safe_relative_path(
                models_dir, default_rel, purpose="STT target_path"
            )

        if target_path.exists() and any(target_path.iterdir()):
            print(f"STT model already present: {target_path}")
        else:
            download_file(url, archive_path, stt["name"])
            extract_zip(archive_path, target_path)
            archive_path.unlink(missing_ok=True)

    llm = models.get("llm")
    if llm:
        dest_path = models_dir / llm.get("dest_path", "")
        if dest_path.exists():
            print(f"LLM model already present: {dest_path}")
        else:
            download_file(llm["url"], dest_path, llm["name"])

    tts = models.get("tts")
    if tts:
        files: Iterable[Dict[str, str]] = tts.get("files", [])
        for item in files:
            dest_path = models_dir / item.get("dest_path", "")
            if dest_path.exists():
                print(f"TTS artifact already present: {dest_path}")
            else:
                download_file(item["url"], dest_path, item["name"])


def print_expectations(tier_name: str, tier_info: Dict[str, Any]) -> None:
    expectations = tier_info.get("expectations", {})
    summary = expectations.get("two_way_summary", "")
    print("\n=== Conversational Expectations ===")
    print(f"Tier: {tier_name}")
    if summary:
        print(f"Summary: {summary}")
    stt = expectations.get("stt_latency_sec")
    llm = expectations.get("llm_latency_sec")
    tts = expectations.get("tts_latency_sec")
    if stt or llm or tts:
        print("Approximate latencies per turn:")
        if stt:
            print(f"  STT: ~{stt} sec")
        if llm:
            print(f"  LLM: ~{llm} sec")
        if tts:
            print(f"  TTS: ~{tts} sec")
    rec_calls = expectations.get("recommended_concurrent_calls")
    if rec_calls is not None:
        print(f"Recommended concurrent calls: {rec_calls}")


def detect_gpu_info() -> Dict[str, Any]:
    gpus: List[Dict[str, Any]] = []

    # torch-based detection
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            for idx in range(torch.cuda.device_count()):
                try:
                    device_name = torch.cuda.get_device_name(idx)
                    props = torch.cuda.get_device_properties(idx)
                    total_gb = round(props.total_memory / (1024**3), 1)
                except Exception:
                    device_name = f"CUDA:{idx}"
                    total_gb = 0.0
                gpus.append({"name": device_name, "total_vram_gb": total_gb})
    except Exception:
        pass

    # nvidia-smi fallback
    if not gpus:
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total",
                    "--format=csv,noheader",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            for line in result.stdout.strip().splitlines():
                if not line.strip():
                    continue
                parts = [part.strip() for part in line.split(",")]
                name = parts[0]
                memory_gb = 0.0
                if len(parts) > 1:
                    tokens = parts[1].split()
                    if tokens:
                        try:
                            value = float(tokens[0])
                            unit = tokens[1].lower() if len(tokens) > 1 else "mb"
                            if unit.startswith("gb"):
                                memory_gb = value
                            elif unit.startswith("mb"):
                                memory_gb = value / 1024
                        except Exception:
                            memory_gb = 0.0
                gpus.append({"name": name, "total_vram_gb": round(memory_gb, 1)})
        except Exception:
            pass

    max_vram = max((gpu.get("total_vram_gb", 0) or 0) for gpu in gpus) if gpus else 0.0
    return {"available": bool(gpus), "gpus": gpus, "max_vram_gb": max_vram}


def main() -> None:
    args = parse_args()
    if not args.registry.exists():
        raise SystemExit(f"Registry file not found: {args.registry}")

    registry: Dict[str, Any] = json.loads(args.registry.read_text())
    cpu = detect_cpu_cores()
    ram = detect_total_ram_gb()
    disk = detect_available_disk_gb(Path.cwd())
    env = detect_environment()
    gpu_info = detect_gpu_info()

    print("=== System detection ===")
    print(f"CPU cores: {cpu}")
    print(f"Total RAM: {ram} GB")
    print(f"Available disk: {disk} GB")
    print(f"Environment: {env}")
    print(f"Architecture: {platform.machine()} ({platform.system()})")
    if gpu_info["available"]:
        gpu_descriptions = ", ".join(
            f"{gpu['name']} ({gpu['total_vram_gb']} GB)" for gpu in gpu_info["gpus"]
        )
        print(f"GPU(s): {gpu_descriptions}")
    else:
        print("GPU(s): none detected")

    tier_name = determine_tier(registry, cpu, ram, args.tier, gpu_info=gpu_info)
    tier_info = registry["tiers"][tier_name]
    print(f"\nSelected tier: {tier_name} — {tier_info.get('description','')}")

    if not prompt_yes_no("Proceed with model download/setup?", args.assume_yes):
        print("Aborted by user.")
        return

    download_models_for_tier(tier_info, args.models_dir)
    print_expectations(tier_name, tier_info)

    print("\nModels ready. Update your config to point at the downloaded paths and run a quick regression call.")


if __name__ == "__main__":
    try:
        main()
    except DownloadError as exc:
        raise SystemExit(f"Error: {exc}")
