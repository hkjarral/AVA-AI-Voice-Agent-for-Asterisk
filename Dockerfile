# --- Stage 1: Builder (Dependencies) ---
# Use slim image for smaller build
FROM python:3.14-slim as builder

WORKDIR /usr/src/app

# Install build dependencies for packages like webrtcvad that need compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy requirements first for optimal caching
COPY requirements.txt .

# Install dependencies (this layer will be cached)
RUN pip install --no-cache-dir -r requirements.txt

# --- Stage 2: Final Runtime Image ---
# Use slim image for smaller footprint
FROM python:3.14-slim

# Optimization env vars
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Note: sox/curl/unzip removed - not needed at runtime
# Audio processing uses Python audioop, downloads done in install.sh

WORKDIR /app

# Create non-root user for security and grant access to asterisk group
# GID defaults to 995 (FreePBX standard) but can be overridden at build time
ARG ASTERISK_GID=995
RUN groupadd -g ${ASTERISK_GID} asterisk || true \
    && useradd --create-home appuser \
    && usermod -aG ${ASTERISK_GID} appuser

# Copy the virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Copy application source code
COPY --chown=appuser:appuser src/ ./src
COPY --chown=appuser:appuser config/ ./config
COPY --chown=appuser:appuser main.py ./

# Prepare log directory for file logging
RUN mkdir -p /app/logs && chown appuser:appuser /app/logs

# Set PATH for virtual environment
ENV PATH="/opt/venv/bin:$PATH"

# Run the application
USER appuser
CMD ["python", "main.py"]