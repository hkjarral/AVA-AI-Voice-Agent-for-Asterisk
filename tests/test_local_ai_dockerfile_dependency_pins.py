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
    assert re.search(r"^faster-whisper==\d+\.\d+\.\d+$", requirements, re.MULTILINE)
    assert re.search(r"^kokoro==\d+\.\d+\.\d+$", requirements, re.MULTILINE)
    assert re.search(r"^torch==\d+\.\d+\.\d+$", requirements, re.MULTILINE)
    assert re.search(r"^torchaudio==\d+\.\d+\.\d+$", requirements, re.MULTILINE)
    assert re.search(r"^transformers==\d+\.\d+\.\d+$", requirements, re.MULTILINE)
    assert re.search(r"^huggingface-hub==\d+\.\d+\.\d+$", requirements, re.MULTILINE)

    for dockerfile in DOCKERFILES:
        content = dockerfile.read_text(encoding="utf-8")
        assert "COPY requirements.txt ." in content, dockerfile
        assert "grep -m 1 '^sherpa-onnx==' requirements.txt" in content, dockerfile
        assert 'pip install --no-cache-dir "$SHERPA_REQUIREMENT"' in content, dockerfile
        assert re.search(
            r'echo "❌ INCLUDE_SHERPA=true but requirements\.txt has no '
            r'sherpa-onnx entry" >&2; \\\s*exit 1;',
            content,
        ), dockerfile
        assert "grep -m 1 '^llama-cpp-python==' requirements.txt" in content, dockerfile
        assert 'pip install --no-cache-dir "$LLAMA_CPP_REQUIREMENT"' in content, dockerfile
        assert re.search(
            r'echo "❌ INCLUDE_LLAMA=true but requirements\.txt has no '
            r'llama-cpp-python entry" >&2; \\\s*exit 1;',
            content,
        ), dockerfile
        assert not re.search(r"pip install[^\n]*sherpa-onnx==", content), dockerfile
        assert not re.search(r"pip install[^\n]*llama-cpp-python==", content), dockerfile
        assert "grep -m 1 '^faster-whisper==' requirements.txt" in content, dockerfile
        assert 'pip install --no-cache-dir "$FASTER_WHISPER_REQUIREMENT"' in content, dockerfile
        assert "grep -m 1 '^kokoro==' requirements.txt" in content, dockerfile
        assert 'pip install --no-cache-dir "$KOKORO_REQUIREMENT"' in content, dockerfile
        assert "grep -m 1 '^torch==' requirements.txt" in content, dockerfile
        assert 'pip install --no-cache-dir "$TORCH_REQUIREMENT"' in content, dockerfile
        assert "grep -m 1 '^torchaudio==' /usr/src/app/requirements.txt" in content, dockerfile
        assert 'pip install --no-cache-dir "$TORCHAUDIO_REQUIREMENT"' in content, dockerfile
        assert "grep -m 1 '^transformers==' requirements.txt" in content, dockerfile
        assert "grep -m 1 '^huggingface-hub==' requirements.txt" in content, dockerfile
        assert '"$TRANSFORMERS_REQUIREMENT" "$HF_HUB_REQUIREMENT"' in content, dockerfile
        assert "pip check 2>&1" in content, dockerfile
        assert "requires gradio, which is not installed" in content, dockerfile
        assert "No unexpected broken requirements found." in content, dockerfile
        assert not re.search(r"pip install[^\n]*faster-whisper==", content), dockerfile
        assert not re.search(r"pip install[^\n]*kokoro(?:==|>=)", content), dockerfile
        assert not re.search(r"pip install[^\n]*torch(?:==|>=)", content), dockerfile
        assert not re.search(r"pip install[^\n]*torchaudio(?:==|>=)", content), dockerfile


def test_cpu_image_uses_cpu_only_pytorch_wheels() -> None:
    content = (LOCAL_AI_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert content.count("https://download.pytorch.org/whl/cpu") >= 3


def test_gpu_image_includes_cudnn_for_faster_whisper() -> None:
    content = (LOCAL_AI_ROOT / "Dockerfile.gpu").read_text(encoding="utf-8")
    assert "12.4.1-cudnn-devel-ubuntu22.04" in content
    assert "12.4.1-cudnn-runtime-ubuntu22.04" in content


def test_llama_builds_exclude_non_runtime_targets() -> None:
    for dockerfile in DOCKERFILES:
        content = dockerfile.read_text(encoding="utf-8")
        assert "-DLLAMA_BUILD_TESTS=OFF" in content
        assert "-DLLAMA_BUILD_EXAMPLES=OFF" in content
        assert "-DLLAMA_BUILD_TOOLS=OFF" in content


def test_gpu_ci_limits_llama_build_to_representative_architecture() -> None:
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "local-ai-gpu-build.yml"
    ).read_text(encoding="utf-8")
    assert "--build-arg LLAMA_CUDA_ARCHITECTURES=70" in workflow
    assert "import faster_whisper, kokoro, importlib.util" in workflow
    assert "find_spec(\\\"llama_cpp\\\")" in workflow
    assert 'find /opt/venv/lib/python3.10/site-packages/llama_cpp/lib' in workflow
    assert 'grep -q "libcuda.so.1"' in workflow
