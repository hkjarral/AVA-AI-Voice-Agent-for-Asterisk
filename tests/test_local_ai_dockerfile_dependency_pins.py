from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
LOCAL_AI_ROOT = REPO_ROOT / "local_ai_server"
DOCKERFILES = (
    LOCAL_AI_ROOT / "Dockerfile",
    LOCAL_AI_ROOT / "Dockerfile.gpu",
)


def test_optional_native_dependency_pins_have_one_source_of_truth() -> None:
    requirements = (LOCAL_AI_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert re.search(r"^sherpa-onnx==\d+\.\d+\.\d+$", requirements, re.MULTILINE)
    assert re.search(
        r"^llama-cpp-python==\d+\.\d+\.\d+$", requirements, re.MULTILINE
    )

    for dockerfile in DOCKERFILES:
        content = dockerfile.read_text(encoding="utf-8")
        assert "COPY requirements.txt ." in content, dockerfile
        assert "grep -m 1 '^sherpa-onnx' requirements.txt" in content, dockerfile
        assert 'pip install --no-cache-dir "$SHERPA_REQUIREMENT"' in content, dockerfile
        assert "grep -m 1 '^llama-cpp-python' requirements.txt" in content, dockerfile
        assert 'pip install --no-cache-dir "$LLAMA_CPP_REQUIREMENT"' in content, dockerfile
        assert not re.search(r"pip install[^\n]*sherpa-onnx==", content), dockerfile
        assert not re.search(r"pip install[^\n]*llama-cpp-python==", content), dockerfile
