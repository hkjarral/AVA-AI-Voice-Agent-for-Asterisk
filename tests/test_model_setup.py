from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from scripts import model_setup


def test_determine_tier_prefers_highest_capability() -> None:
    registry = {
        "tiers": {
            "LIGHT": {
                "requirements": {"min_cpu_cores": 2, "min_ram_gb": 4},
                "models": {},
            },
            "HEAVY": {
                "requirements": {
                    "min_cpu_cores": 16,
                    "min_ram_gb": 32,
                    "requires_gpu": True,
                    "min_vram_gb": 16,
                },
                "models": {},
            },
            "MEDIUM": {
                "requirements": {"min_cpu_cores": 8, "min_ram_gb": 16},
                "models": {},
            },
        }
    }

    tier = model_setup.determine_tier(
        registry,
        cpu_cores=32,
        ram_gb=128,
        gpu_info={"available": True, "max_vram_gb": 24},
    )

    assert tier == "HEAVY"


def test_determine_tier_respects_gpu_requirements() -> None:
    registry = {
        "tiers": {
            "LIGHT": {
                "requirements": {"min_cpu_cores": 2, "min_ram_gb": 4},
                "models": {},
            },
            "GPU_ONLY": {
                "requirements": {
                    "min_cpu_cores": 8,
                    "min_ram_gb": 16,
                    "requires_gpu": True,
                    "min_vram_gb": 12,
                },
                "models": {},
            },
            "CPU_ONLY": {
                "requirements": {"min_cpu_cores": 8, "min_ram_gb": 16},
                "models": {},
            },
        }
    }

    tier = model_setup.determine_tier(
        registry,
        cpu_cores=16,
        ram_gb=64,
        gpu_info={"available": False, "max_vram_gb": 0},
    )

    assert tier == "CPU_ONLY"


def test_extract_zip_preserves_existing_files(tmp_path: Path) -> None:
    target_dir = tmp_path / "stt" / "model"
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "existing.txt").write_text("keep me")

    archive = tmp_path / "archive.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("new.txt", "fresh data")

    model_setup.extract_zip(archive, target_dir)

    assert (target_dir / "existing.txt").read_text() == "keep me"
    assert (target_dir / "new.txt").read_text() == "fresh data"


def test_download_models_for_tier_derives_safe_stt_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models_dir = tmp_path / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    archive_contents = {"model.bin": b"binary"}

    def fake_download(url: str, dest: Path, label: str) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w") as zf:
            for name, data in archive_contents.items():
                zf.writestr(name, data)

    monkeypatch.setattr(model_setup, "download_file", fake_download)

    tier_info = {
        "models": {
            "stt": {
                "name": "Example STT",
                "url": "https://example.com/assets/stt-large.zip",
            }
        }
    }

    model_setup.download_models_for_tier(tier_info, models_dir)

    expected_target = models_dir / "stt" / "stt-large"
    assert expected_target.exists()
    assert (expected_target / "model.bin").exists()


def test_download_models_for_tier_derives_llm_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models_dir = tmp_path / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    def fake_download(url: str, dest: Path, label: str) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("llm weights")

    monkeypatch.setattr(model_setup, "download_file", fake_download)

    tier_info = {
        "models": {
            "llm": {
                "name": "Chat Model",
                "url": "https://example.com/llm/chat-model.bin",
            }
        }
    }

    model_setup.download_models_for_tier(tier_info, models_dir)

    expected_path = models_dir / "llm" / "chat-model.bin"
    assert expected_path.exists()
    assert expected_path.read_text() == "llm weights"


def test_download_models_for_tier_derives_tts_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    models_dir = tmp_path / "models"
    models_dir.mkdir(parents=True, exist_ok=True)

    def fake_download(url: str, dest: Path, label: str) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("tts data")

    monkeypatch.setattr(model_setup, "download_file", fake_download)

    tier_info = {
        "models": {
            "tts": {
                "files": [
                    {
                        "name": "TTS voice",
                        "url": "https://example.com/voices/voice.pt",
                    }
                ]
            }
        }
    }

    model_setup.download_models_for_tier(tier_info, models_dir)

    expected_path = models_dir / "tts" / "voice.pt"
    assert expected_path.exists()
    assert expected_path.read_text() == "tts data"

