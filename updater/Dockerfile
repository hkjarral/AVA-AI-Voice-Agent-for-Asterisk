# AAVA Update Runner
#
# Purpose:
# - Run `agent update` and `agent update --plan` in a detached container started by Admin UI.
# - Must include: git, curl, docker CLI, docker compose plugin.
#
# The container does NOT run as part of normal docker-compose services.

FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    git \
    gnupg \
    jq \
    tzdata \
    && install -m 0755 -d /etc/apt/keyrings \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg \
    && chmod a+r /etc/apt/keyrings/docker.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(. /etc/os-release && echo \"$VERSION_CODENAME\") stable" > /etc/apt/sources.list.d/docker.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends docker-ce-cli docker-compose-plugin \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /runner

COPY run.sh /usr/local/bin/aava-update-runner
RUN chmod +x /usr/local/bin/aava-update-runner

ENTRYPOINT ["/usr/local/bin/aava-update-runner"]
