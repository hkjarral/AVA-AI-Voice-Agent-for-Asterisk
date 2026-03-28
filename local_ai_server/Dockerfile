# --- Stage 1: Builder (Dependencies) ---
# Note: Using trixie (Debian 13) for glibc compatibility with Kroko binary
FROM python:3.11-slim-trixie as builder

# ─────────────────────────────────────────────────────────────────────────────
# Build Args - Conditionally include backends (AAVA-112)
# Default: all backends enabled for backward compatibility
# Usage: docker build --build-arg INCLUDE_VOSK=false ...
# ─────────────────────────────────────────────────────────────────────────────
ARG INCLUDE_VOSK=true
ARG INCLUDE_SHERPA=true
ARG INCLUDE_FASTER_WHISPER=false
ARG INCLUDE_WHISPER_CPP=false
ARG INCLUDE_TONE=false
ARG INCLUDE_PIPER=true
ARG INCLUDE_KOKORO=true
ARG INCLUDE_MELOTTS=false
ARG INCLUDE_SILERO=false
ARG INCLUDE_LLAMA=true
ARG MELOTTS_GIT_REF=209145371cff8fc3bd60d7be902ea69cbdb7965a

WORKDIR /usr/src/app

# Some optional backends require native builds (e.g., llama-cpp-python) or git installs (MeloTTS).
# Install build tooling only when those backends are enabled to keep CI "ci-min" builds lean.
RUN apt-get -o Acquire::Retries=5 update && \
    if [ "$INCLUDE_LLAMA" = "true" ] || [ "$INCLUDE_MELOTTS" = "true" ] || [ "$INCLUDE_WHISPER_CPP" = "true" ] || [ "$INCLUDE_TONE" = "true" ]; then \
        apt-get install -y --no-install-recommends \
            build-essential \
            cmake \
            git \
            ; \
    fi && \
    rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV NLTK_DATA="/opt/venv/nltk_data"

# Copy requirements first for optimal caching
COPY requirements.txt .
COPY requirements-base.txt .

# Install base dependencies (always required)
RUN pip install --no-cache-dir -r requirements-base.txt

# Conditionally install backends based on build args
RUN if [ "$INCLUDE_VOSK" = "true" ]; then \
        echo "📦 Installing Vosk STT..." && \
        pip install --no-cache-dir vosk==0.3.45; \
    fi

RUN if [ "$INCLUDE_SHERPA" = "true" ]; then \
        echo "📦 Installing Sherpa-ONNX STT..." && \
        pip install --no-cache-dir sherpa-onnx==1.12.19; \
    fi

RUN if [ "$INCLUDE_FASTER_WHISPER" = "true" ]; then \
        echo "📦 Installing Faster-Whisper STT..." && \
        pip install --no-cache-dir faster-whisper==1.0.3; \
    fi

RUN if [ "$INCLUDE_WHISPER_CPP" = "true" ]; then \
        echo "📦 Installing Whisper.cpp (pywhispercpp)..." && \
        pip install --no-cache-dir pywhispercpp==1.2.0; \
    fi

RUN if [ "$INCLUDE_TONE" = "true" ]; then \
        echo "📦 Installing T-one STT..." && \
        pip install --no-cache-dir "git+https://github.com/voicekit-team/T-one.git@3c5b6c015038173840e62cea99e10cdb1c759116#egg=tone"; \
    fi

RUN if [ "$INCLUDE_PIPER" = "true" ]; then \
        echo "📦 Installing Piper TTS..." && \
        pip install --no-cache-dir piper-tts==1.2.0; \
    fi

RUN if [ "$INCLUDE_KOKORO" = "true" ]; then \
        echo "📦 Installing Kokoro TTS..." && \
        pip install --no-cache-dir "kokoro>=0.9.2"; \
    fi

RUN if [ "$INCLUDE_MELOTTS" = "true" ]; then \
        echo "📦 Installing MeloTTS..." && \
        git clone https://github.com/myshell-ai/MeloTTS.git /tmp/MeloTTS && \
        cd /tmp/MeloTTS && \
        git checkout "${MELOTTS_GIT_REF}" && \
        python -c "from pathlib import Path; p=Path('/tmp/MeloTTS/setup.py'); s=p.read_text(); s=s.replace(\"from pip.req import parse_requirements\\n\", \"\"); s=s.replace(\"install_reqs = parse_requirements('requirements.txt')\\nreqs = [str(ir.req) for ir in install_reqs]\\n\", \"with open(os.path.join(cwd, 'requirements.txt'), encoding='utf-8') as _f:\\n    reqs = [ln.strip() for ln in _f if ln.strip() and not ln.startswith('#')]\\n\"); p.write_text(s); req=[]; ex=('torch','torchaudio','gradio'); [req.append(ln.strip()) for ln in Path('/tmp/MeloTTS/requirements.txt').read_text().splitlines() if ln.strip() and not ln.strip().startswith('#') and not ln.strip().lower().startswith(ex)]; Path('/tmp/MeloTTS/requirements.filtered.txt').write_text('\\n'.join(req)+'\\n'); print(f'MeloTTS filtered requirements prepared: {len(req)} packages')" && \
        pip install --no-cache-dir -r requirements.filtered.txt && \
        pip install --no-cache-dir "setuptools<81" && \
        if ! python -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('torch') else 1)"; then pip install --no-cache-dir torch; fi && \
        if ! python -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('torchaudio') else 1)"; then pip install --no-cache-dir torchaudio; fi && \
        pip install --no-cache-dir --no-build-isolation --no-deps . && \
        cd / && rm -rf /tmp/MeloTTS && \
        python -m unidic download || true && \
        python -c "import nltk; nltk.download('averaged_perceptron_tagger_eng', download_dir='/opt/venv/nltk_data')" || true && \
        echo "📦 Pre-downloading MeloTTS BERT model (best effort)..." && \
        python -c "from transformers import AutoTokenizer; AutoTokenizer.from_pretrained('bert-base-uncased')" || true && \
        python -c "from melo.api import TTS; print('MeloTTS package ready')"; \
    fi

# Ensure cache dir exists so COPY --from=builder never fails
RUN mkdir -p /opt/silero-cache

RUN if [ "$INCLUDE_SILERO" = "true" ]; then \
        echo "📦 Installing Silero TTS dependencies..." && \
        if ! python -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('torch') else 1)"; then \
            pip install --no-cache-dir torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu; \
        fi && \
        pip install --no-cache-dir omegaconf && \
        echo "📦 Pre-caching Silero Russian model (best effort)..." && \
        python -c "import torch; torch.hub.set_dir('/opt/silero-cache'); torch.hub.load('snakers4/silero-models:v5.5', 'silero_tts', language='ru', speaker='v3_1_ru', trust_repo=True)" || true && \
        echo "✅ Silero TTS ready"; \
    fi

RUN if [ "$INCLUDE_LLAMA" = "true" ]; then \
        echo "📦 Installing llama-cpp-python LLM..." && \
        pip install --no-cache-dir llama-cpp-python==0.3.16; \
    fi

# Pre-install spacy model for Kokoro TTS (only if Kokoro included)
RUN if [ "$INCLUDE_KOKORO" = "true" ]; then \
        python -m spacy download en_core_web_sm || true; \
    fi

# --- Stage 2: Final Runtime Image ---
# Using trixie (Debian 13) for Kroko binary compatibility (requires glibc 2.38+)
FROM python:3.11-slim-trixie

# Build argument to optionally include Kroko embedded server
ARG INCLUDE_KROKO_EMBEDDED=false

# Install runtime dependencies
RUN apt-get -o Acquire::Retries=5 update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    tzdata \
    libsndfile1 \
    libasound2 \
    libportaudio2 \
    libgomp1 \
    libatomic1 \
    sox \
    wget \
    espeak-ng \
    && rm -rf /var/lib/apt/lists/*

# ─────────────────────────────────────────────────────────────────────────────
# Optional: Install Kroko ONNX server for embedded mode
# Only included if INCLUDE_KROKO_EMBEDDED=true during build
# SECURITY: SHA256 checksums verify binary integrity (AAVA-134)
# Build will FAIL if checksums don't match (fail-closed)
# ─────────────────────────────────────────────────────────────────────────────
# Known checksums - update these when upgrading versions
# ONNX Runtime 1.17.1 Linux x64: https://github.com/microsoft/onnxruntime/releases/tag/v1.17.1
ARG ONNX_RUNTIME_SHA256="89b153af88746665909c758a06797175ae366280cbf25502c41eb5955f9a555e"
# Kroko server: Obtain from official Kroko release notes or contact vendor
# IMPORTANT: Set via --build-arg KROKO_SERVER_SHA256=<hash> or update this default
ARG KROKO_SERVER_SHA256=""

RUN if [ "$INCLUDE_KROKO_EMBEDDED" = "true" ]; then \
        echo "📦 Installing ONNX Runtime for Kroko..." && \
        wget -q https://github.com/microsoft/onnxruntime/releases/download/v1.17.1/onnxruntime-linux-x64-1.17.1.tgz \
            -O /tmp/onnxruntime.tgz && \
        echo "🔐 Verifying ONNX Runtime checksum..." && \
        ACTUAL_ONNX_SHA=$(sha256sum /tmp/onnxruntime.tgz | cut -d' ' -f1) && \
        echo "   Expected: ${ONNX_RUNTIME_SHA256}" && \
        echo "   Actual:   ${ACTUAL_ONNX_SHA}" && \
        if [ -n "$ONNX_RUNTIME_SHA256" ] && [ "$ACTUAL_ONNX_SHA" != "$ONNX_RUNTIME_SHA256" ]; then \
            echo "🚨 SECURITY: ONNX Runtime checksum mismatch! Build aborted." && \
            exit 1; \
        fi && \
        tar -xzf /tmp/onnxruntime.tgz -C /tmp && \
        mv /tmp/onnxruntime-linux-x64-1.17.1/lib/* /usr/lib/ && \
        rm -rf /tmp/onnxruntime* && \
        echo "📦 Downloading Kroko ONNX server binary..." && \
        wget -q https://download.kroko.ai/binaries/kroko-onnx-online-websocket-server \
            -O /usr/local/bin/kroko-server && \
        echo "🔐 Verifying Kroko server checksum..." && \
        ACTUAL_KROKO_SHA=$(sha256sum /usr/local/bin/kroko-server | cut -d' ' -f1) && \
        echo "   Expected: ${KROKO_SERVER_SHA256:-'(not set - verification skipped)'}" && \
        echo "   Actual:   ${ACTUAL_KROKO_SHA}" && \
        if [ -n "$KROKO_SERVER_SHA256" ] && [ "$ACTUAL_KROKO_SHA" != "$KROKO_SERVER_SHA256" ]; then \
            echo "🚨 SECURITY: Kroko server checksum mismatch! Build aborted." && \
            exit 1; \
        fi && \
        if [ -z "$KROKO_SERVER_SHA256" ]; then \
            echo "⚠️  WARNING: KROKO_SERVER_SHA256 not set. For production, set --build-arg KROKO_SERVER_SHA256=${ACTUAL_KROKO_SHA}"; \
        fi && \
        chmod +x /usr/local/bin/kroko-server && \
        echo "✅ Kroko embedded server installed successfully"; \
    else \
        echo "ℹ️  Skipping Kroko embedded server (use KROKO_URL for external/hosted)"; \
    fi

WORKDIR /app

# Create non-root user for security
RUN useradd --create-home appuser
USER appuser

# Copy the virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
COPY --from=builder --chown=appuser:appuser /opt/silero-cache /app/models/tts/silero

# Copy application source code
COPY --chown=appuser:appuser . .

# Set PATH for virtual environment
ENV PATH="/opt/venv/bin:$PATH"
ENV NLTK_DATA="/opt/venv/nltk_data"

# Document the WebSocket server port (informational; host networking ignores EXPOSE)
EXPOSE 8765

# Run the application
CMD ["python", "main.py"]
