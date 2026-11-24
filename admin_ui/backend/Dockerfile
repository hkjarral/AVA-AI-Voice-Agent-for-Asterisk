FROM python:3.11-slim-bookworm

WORKDIR /app

# Install system dependencies if needed (e.g. curl for healthchecks)
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Expose port 8000
EXPOSE 8000

CMD ["sh", "-c", "exec uvicorn main:app --http services.proxy_peer:PeerAwareHTTPProtocol --forwarded-allow-ips \"${AAVA_TRUSTED_PROXIES:-${FORWARDED_ALLOW_IPS:-127.0.0.1}}\" --host 0.0.0.0 --port 8000"]
