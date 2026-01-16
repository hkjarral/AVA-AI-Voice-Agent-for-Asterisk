# Makefile for the Asterisk AI Voice Agent

# Default values - can be overridden from the command line
SERVER_USER := root
SERVER_HOST := your-server.example.com
PROJECT_PATH := /root/Asterisk-Agent-Develop
SERVICE ?= ai_engine
provider ?= local

# ------------------------------------------------------------------------------
# Localhost vs Remote operation
# - If SERVER_HOST is this machine (localhost/127.0.0.1/::1 or matches hostname),
#   deploy/servers targets run LOCALLY: we cd into $(PROJECT_PATH) and execute.
# - Otherwise we SSH to $(SERVER_USER)@$(SERVER_HOST) and run the same commands.
# Tip: When running on the same server where the repo is cloned, prefer setting:
#   make <target> SERVER_HOST=localhost PROJECT_PATH=$(PWD)
# ------------------------------------------------------------------------------
HOSTNAME := $(shell hostname)
FQDN := $(shell hostname -f 2>/dev/null || echo)

# Helper to run a command locally or remotely depending on SERVER_HOST
define run_remote
	@if [ "$(SERVER_HOST)" = "localhost" ] || [ "$(SERVER_HOST)" = "127.0.0.1" ] || [ "$(SERVER_HOST)" = "::1" ] \
	     || [ "$(SERVER_HOST)" = "$(HOSTNAME)" ] || [ "$(SERVER_HOST)" = "$(FQDN)" ]; then \
		echo "--> [local] $(1)"; \
		cd $(PROJECT_PATH) && sh -lc '$(1)'; \
	else \
		echo "--> [remote $(SERVER_USER)@$(SERVER_HOST)] $(1)"; \
		ssh $(SERVER_USER)@$(SERVER_HOST) 'cd $(PROJECT_PATH) && sh -lc '\''$(1)'\'''; \
	fi
endef

# ----------------------------------------------------------------------------
# ----------------------------------------------------------------------------
# Compose detection (supports Docker Compose v2 plugin and legacy docker-compose)
# ----------------------------------------------------------------------------
DC := $(shell (docker compose version >/dev/null 2>&1 && echo "docker compose") || (docker-compose --version >/dev/null 2>&1 && echo "docker-compose") || echo "")
COMPOSE := $(DC) -p asterisk-ai-voice-agent

# ------------------------------------------------------------------------------
# Python runner shim
# Prefer host python3; if absent, use containerized python via compose.
# Also emit a helpful message when falling back.
PY := $(shell if command -v python3 >/dev/null 2>&1; then echo python3; else echo "$(COMPOSE) exec -T ai_engine python"; fi)
PY_INFO := $(shell if command -v python3 >/dev/null 2>&1; then echo ""; else echo "Host python3 not found; using '$(COMPOSE) exec -T ai_engine python' inside the ai_engine container."; fi)

# ==============================================================================
# LOCAL DEVELOPMENT
# ==============================================================================

## build: Build or rebuild all service images
build:
	$(COMPOSE) build

## up: Start all services in the background
up:
	$(COMPOSE) up -d

## down: Stop and remove all services
down:
	$(COMPOSE) down --remove-orphans

## logs: Tail the logs of a specific service (default: ai_engine)
logs:
	$(COMPOSE) logs -f $(SERVICE)

## logs-all: Tail the logs of all services
logs-all:
	$(COMPOSE) logs -f

## engine-reload: Restart ai_engine locally to pick up config changes
engine-reload:
	$(COMPOSE) up -d ai_engine

## ps: Show the status of running services
ps:
	$(COMPOSE) ps

## model-setup: Detect host tier, download required local provider models, and skip if cached
model-setup:
	@if [ -f scripts/model_setup.sh ]; then \
		echo "Using bash-based model setup"; \
		bash scripts/model_setup.sh --assume-yes; \
	elif command -v python3 >/dev/null 2>&1; then \
		python3 scripts/model_setup.py --assume-yes; \
	else \
		echo "No host bash/python found or script missing; running one-off container for model setup."; \
		$(COMPOSE) run --rm ai_engine bash /app/scripts/model_setup.sh --assume-yes || $(COMPOSE) run --rm ai_engine python /app/scripts/model_setup.py --assume-yes; \
	fi

# ==============================================================================
# DEPLOYMENT & SERVER MANAGEMENT
# ==============================================================================

## deploy: Pull latest code and deploy the ai_engine on the server (with --no-cache)
deploy:
	@echo "--> Deploying latest code to $(SERVER_HOST) with --no-cache..."
	@echo "⚠️  WARNING: This will deploy uncommitted changes if any exist!"
	@echo "   Use 'make deploy-safe' for validation, or 'make deploy-force' to skip checks"
	$(call run_remote, git pull && $(COMPOSE) build --no-cache ai_engine && $(COMPOSE) up -d ai_engine)

## deploy-safe: Validate changes are committed before deploying
deploy-safe:
	@echo "--> Safe deployment with validation..."
	@echo "🔍 Checking for uncommitted changes..."
	@if [ -n "$$(git status --porcelain)" ]; then \
		echo "❌ ERROR: You have uncommitted changes!"; \
		echo "   Please commit your changes first:"; \
		echo "   git add . && git commit -m 'Your commit message'"; \
		echo "   Or use 'make deploy-force' to skip this check"; \
		exit 1; \
	fi
	@echo "✅ No uncommitted changes found"
	@echo "🚀 Pushing changes to remote..."
	git push origin develop
	@echo "⏳ Waiting 5 seconds for remote propagation..."
	sleep 5
	@echo "🔍 Verifying remote has latest commit..."
	@make verify-remote-sync
	@echo "📦 Deploying to server with --no-cache..."
	$(call run_remote, git pull && $(COMPOSE) build --no-cache ai_engine && $(COMPOSE) up -d ai_engine)
	@echo "🔍 Verifying server has latest commit..."
	@make verify-server-commit
	@echo "🔍 Verifying deployment..."
	@make verify-deployment

## deploy-force: Deploy without validation (use with caution)
deploy-force:
	@echo "--> Force deployment (skipping validation)..."
	@echo "⚠️  WARNING: This will deploy even with uncommitted changes!"
	@echo "⏳ Waiting 5 seconds before deployment..."
	sleep 5
	$(call run_remote, git pull && $(COMPOSE) build --no-cache ai_engine && $(COMPOSE) up -d ai_engine)
	@echo "🔍 Verifying server has latest commit..."
	@make verify-server-commit
	@echo "🔍 Verifying deployment..."
	@make verify-deployment

## deploy-full: Pull latest and rebuild all services on the server
deploy-full:
	@echo "--> Performing a full rebuild and deployment on $(SERVER_HOST)..."
	$(call run_remote, git pull && $(COMPOSE) up --build -d)

## deploy-no-cache: Pull latest and force a no-cache rebuild of ai_engine
deploy-no-cache:
	@echo "--> Forcing a no-cache rebuild and deployment of ai_engine on $(SERVER_HOST)..."
	$(call run_remote, git pull && $(COMPOSE) build --no-cache ai_engine && $(COMPOSE) up -d ai_engine)

## server-logs: View live logs for a service on the server (follow mode - use Ctrl+C to exit)
server-logs:
	$(call run_remote, $(COMPOSE) logs -f $(SERVICE))

## server-logs-snapshot: View last N lines of logs and exit (default: 50)
server-logs-snapshot:
	$(call run_remote, $(COMPOSE) logs --tail=$(LINES) $(SERVICE))

## server-status: Check the status of services on the server
server-status:
	$(call run_remote, $(COMPOSE) ps)

## server-clear-logs: Truncate Docker logs on server and restart containers
server-clear-logs:
	@echo "--> Truncating Docker container logs on $(SERVER_HOST)..."
	@$(call run_remote, sudo sh -c "truncate -s 0 /var/lib/docker/containers/*/*-json.log")
	@echo "--> Restarting ai_engine and local_ai_server services via docker-compose..."
	@$(call run_remote, $(COMPOSE) restart local_ai_server ai_engine)
	@echo "✅ Server logs cleared and containers restarted"

## server-capture-logs: Capture full logs from server containers into timestamped files
server-capture-logs:
	@echo "--> Capturing ai_engine logs to logs/ai-engine-$$(date +%Y%m%d-%H%M%S).log"
	@if [ "$(SERVER_HOST)" = "localhost" ] || [ "$(SERVER_HOST)" = "127.0.0.1" ] || [ "$(SERVER_HOST)" = "::1" ] \
	     || [ "$(SERVER_HOST)" = "$(HOSTNAME)" ] || [ "$(SERVER_HOST)" = "$(FQDN)" ]; then \
		(cd $(PROJECT_PATH) && $(COMPOSE) logs --no-color ai_engine) > logs/ai-engine-$$(date +%Y%m%d-%H%M%S).log; \
	else \
		ssh $(SERVER_USER)@$(SERVER_HOST) 'cd $(PROJECT_PATH) && $(COMPOSE) logs --no-color ai_engine' > logs/ai-engine-$$(date +%Y%m%d-%H%M%S).log; \
	fi
	@echo "--> Capturing local_ai_server logs to logs/local-ai-server-$$(date +%Y%m%d-%H%M%S).log"
	@if [ "$(SERVER_HOST)" = "localhost" ] || [ "$(SERVER_HOST)" = "127.0.0.1" ] || [ "$(SERVER_HOST)" = "::1" ] \
	     || [ "$(SERVER_HOST)" = "$(HOSTNAME)" ] || [ "$(SERVER_HOST)" = "$(FQDN)" ]; then \
		(cd $(PROJECT_PATH) && $(COMPOSE) logs --no-color local_ai_server) > logs/local-ai-server-$$(date +%Y%m%d-%H%M%S).log; \
	else \
		ssh $(SERVER_USER)@$(SERVER_HOST) 'cd $(PROJECT_PATH) && $(COMPOSE) logs --no-color local_ai_server' > logs/local-ai-server-$$(date +%Y%m%d-%H%M%S).log; \
	fi

## server-health: Check deployment health (ARI, ExternalMedia, Providers)
server-health:
	@echo "--> Checking deployment health on $(SERVER_HOST)..."
	@echo "🔍 Checking ARI connections..."
	@$(call run_remote, $(COMPOSE) logs --tail=200 ai_engine | grep -E "(Successfully connected to ARI HTTP endpoint|Successfully connected to ARI WebSocket)" || echo "❌ ARI connection issues")
	@echo "🔍 Checking RTP server..."
	@$(call run_remote, $(COMPOSE) logs --tail=200 ai_engine | grep -E "(RTP server started|RTP server listening)" || echo "❌ RTP server issues")
	@echo "🔍 Checking provider loading..."
	@$(call run_remote, $(COMPOSE) logs --tail=200 ai_engine | grep -E "(Provider.*loaded successfully|Default provider.*is available)" || echo "❌ Provider loading issues")
	@echo "🔍 Checking engine status..."
	@$(call run_remote, $(COMPOSE) logs --tail=200 ai_engine | grep -E "(Engine started and listening for calls)" || echo "❌ Engine startup issues")
	@echo "✅ Health check complete"

# ==============================================================================
# TESTING & VERIFICATION
# ==============================================================================

## test-local: Run local tests
test-local:
	$(COMPOSE) exec ai_engine python /app/test_ai_engine.py
	$(COMPOSE) exec local_ai_server python /app/test_local_ai_server.py

## test-integration: Run integration tests
test-integration:
	$(COMPOSE) exec ai_engine python /app/test_integration.py

## test-ari: Test ARI commands
test-ari:
	$(call run_remote, $(COMPOSE) exec -T ai_engine python /app/test_ari_commands.py)

## test-externalmedia: Test ExternalMedia + RTP implementation
test-externalmedia:
	@echo "--> Testing ExternalMedia + RTP implementation..."
	@echo "$(PY_INFO)"; \
	$(PY) scripts/validate_externalmedia_config.py
	$(PY) scripts/test_externalmedia_call.py

## test-health: Check the local health endpoint (defaults to http://127.0.0.1:15000/health)
test-health:
	@HEALTH_URL=$${HEALTH_URL:-http://127.0.0.1:15000/health}; \
	echo "--> Checking $$HEALTH_URL"; \
	if ! curl -sS $$HEALTH_URL ; then \
		echo "❌ Health check failed"; \
		exit 1; \
	else \
		echo "✅ Health check succeeded"; \
	fi

## quick-regression: Run health check and print manual call checklist
quick-regression:
	@$(MAKE) --no-print-directory test-health
	@echo
	@echo "Next steps:" \
	  && echo "1. Clear logs (local: make logs --tail=0 ai_engine | remote: make server-clear-logs)." \
	  && echo "2. Place a short call into the AI context." \
	  && echo "3. Watch for ExternalMedia bridge join, RTP frames, provider input, playback start/finish, and cleanup." \
	  && echo "4. Re-run make test-health to ensure active_calls resets to 0." \
	  && echo "5. Capture findings in docs/resilience.md (and/or archived/regressions/) or your issue tracker."

## provider-switch: Update default provider locally
provider-switch:
	@if [ -z "$(provider)" ]; then \
		echo "Usage: make provider=<name> provider-switch"; \
		exit 1; \
	fi
	@python3 scripts/switch_provider.py --config config/ai-agent.yaml --provider $(provider)

## provider-switch-remote: Update default provider on the server
provider-switch-remote:
	@if [ -z "$(provider)" ]; then \
		echo "Usage: make provider=<name> provider-switch-remote"; \
		exit 1; \
	fi
	@$(call run_remote, $(COMPOSE) exec -T ai_engine python /app/scripts/switch_provider.py --config /app/config/ai-agent.yaml --provider $(provider))

## provider-reload: Switch provider on server, restart ai_engine, and run health check
provider-reload:
	@$(MAKE) --no-print-directory provider-switch-remote provider=$(provider)
	@$(call run_remote, $(COMPOSE) up -d ai_engine)
	@$(MAKE) --no-print-directory server-health

## verify-deployment: Verify that deployment was successful
verify-deployment:
	@echo "🔍 Verifying deployment..."
	@echo "📊 Checking container status..."
	@$(call run_remote, $(COMPOSE) ps)
	@echo "📋 Checking recent logs for errors..."
	@$(call run_remote, $(COMPOSE) logs --tail=10 ai_engine | grep -E "(ERROR|CRITICAL|Exception|Traceback)" || echo "✅ No errors found in recent logs")
	@echo "⚙️  Checking configuration..."
	@$(call run_remote, $(COMPOSE) logs --tail=20 ai_engine | grep -E "(audio_transport|RTP Server|ExternalMedia)" || echo "⚠️  Configuration logs not found")
	@echo "✅ Deployment verification complete"

## verify-remote-sync: Verify that remote repository has the latest commit
verify-remote-sync:
	@echo "🔍 Verifying remote repository sync..."
	@echo "📋 Getting local commit hash..."
	@LOCAL_COMMIT=$$(git rev-parse HEAD); \
	echo "Local commit: $$LOCAL_COMMIT"; \
	echo "📋 Getting remote commit hash..."; \
	REMOTE_COMMIT=$$(git ls-remote origin develop | cut -f1); \
	echo "Remote commit: $$REMOTE_COMMIT"; \
	if [ "$$LOCAL_COMMIT" = "$$REMOTE_COMMIT" ]; then \
		echo "✅ Remote repository is in sync with local"; \
	else \
		echo "❌ ERROR: Remote repository is not in sync!"; \
		echo "   Local:  $$LOCAL_COMMIT"; \
		echo "   Remote: $$REMOTE_COMMIT"; \
		echo "   Waiting 5 more seconds and retrying..."; \
		sleep 5; \
		REMOTE_COMMIT=$$(git ls-remote origin develop | cut -f1); \
		if [ "$$LOCAL_COMMIT" = "$$REMOTE_COMMIT" ]; then \
			echo "✅ Remote repository is now in sync"; \
		else \
			echo "❌ ERROR: Remote repository still not in sync after retry!"; \
			exit 1; \
		fi; \
	fi

## verify-server-commit: Verify that server has the expected commit
verify-server-commit:
	@echo "🔍 Verifying server has the latest commit..."
	@echo "📋 Getting local commit hash..."
	@LOCAL_COMMIT=$$(git rev-parse HEAD); \
	echo "Local commit: $$LOCAL_COMMIT"; \
	echo "📋 Getting server commit hash..."; \
	if [ "$(SERVER_HOST)" = "localhost" ] || [ "$(SERVER_HOST)" = "127.0.0.1" ] || [ "$(SERVER_HOST)" = "::1" ] \
	     || [ "$(SERVER_HOST)" = "$(HOSTNAME)" ] || [ "$(SERVER_HOST)" = "$(FQDN)" ]; then \
		SERVER_COMMIT=$$(cd $(PROJECT_PATH) && git rev-parse HEAD); \
	else \
		SERVER_COMMIT=$$(ssh $(SERVER_USER)@$(SERVER_HOST) 'cd $(PROJECT_PATH) && git rev-parse HEAD'); \
	fi; \
	echo "Server commit: $$SERVER_COMMIT"; \
	if [ "$$LOCAL_COMMIT" = "$$SERVER_COMMIT" ]; then \
		echo "✅ Server has the latest commit"; \
	else \
		echo "❌ ERROR: Server does not have the latest commit!"; \
		echo "   Local:  $$LOCAL_COMMIT"; \
		echo "   Server: $$SERVER_COMMIT"; \
		exit 1; \
	fi

## verify-config: Verify the configuration is correct
verify-config:
	@echo "🔍 Verifying configuration..."
	@echo "📋 Local configuration:"
	@python3 scripts/validate_externalmedia_config.py
	@echo "📋 Server configuration:"
	@$(call run_remote, python3 scripts/validate_externalmedia_config.py)

## monitor-externalmedia: Monitor ExternalMedia + RTP status
monitor-externalmedia:
	@echo "--> Starting ExternalMedia + RTP monitoring..."
	$(PY) scripts/monitor_externalmedia.py

## monitor-externalmedia-once: Check ExternalMedia + RTP status once
monitor-externalmedia-once:
	@echo "--> Checking ExternalMedia + RTP status..."
	@echo "$(PY_INFO)"; \
	$(PY) scripts/monitor_externalmedia.py --once

## monitor-up: Start Prometheus +Grafana monitoring stack (host network)
monitor-up:
	@echo "--> Starting monitoring stack (Prometheus +Grafana) on host network..."
	$(DOCKER_COMPOSE) -f docker-compose.yml -f docker-compose.monitor.yml up -d prometheus grafana

## monitor-down: Stop monitoring stack
monitor-down:
	@echo "--> Stopping monitoring stack..."
	$(DOCKER_COMPOSE) -f docker-compose.yml -f docker-compose.monitor.yml down

## monitor-logs: Tail monitoring stack logs
monitor-logs:
	@echo "--> Tailing Prometheus +Grafana logs... (Ctrl+C to exit)"
	$(DOCKER_COMPOSE) -f docker-compose.yml -f docker-compose.monitor.yml logs -f prometheus grafana

## capture-logs: Capture structured logs during test call (default: 40 seconds)
capture-logs:
	@echo "--> Starting structured log capture for test call..."
	@echo "Make your test call now!"
	$(PY) scripts/capture_test_logs.py --duration 40

## capture-logs-short: Capture logs for 30 seconds
capture-logs-short:
	@echo "--> Starting 30-second log capture..."
	@echo "📞 Make your test call now!"
	@echo "$(PY_INFO)"; \
	$(PY) scripts/capture_test_logs.py --duration 30

## capture-logs-long: Capture logs for 60 seconds
capture-logs-long:
	@echo "--> Starting 60-second log capture..."
	@echo "📞 Make your test call now!"
	@echo "$(PY_INFO)"; \
	$(PY) scripts/capture_test_logs.py --duration 60

## analyze-logs: Analyze the most recent captured logs
analyze-logs:
	@echo "--> Analyzing most recent test call logs..."
	@if [ -d "logs" ] && [ "$$(ls -A logs/*.json 2>/dev/null)" ]; then \
		LATEST_LOG=$$(ls -t logs/*.json | head -1); \
		echo "📊 Analyzing: $$LATEST_LOG"; \
		echo "$(PY_INFO)"; \
		$(PY) scripts/analyze_logs.py "$$LATEST_LOG"; \
	else \
		echo "❌ No log files found in logs/ directory"; \
	fi

## test-call: Complete test call workflow (capture + analyze)
test-call:
	@echo "--> Starting complete test call workflow..."
	@echo "📞 Make your test call now!"
	@echo "⏱️  Capturing logs for 40 seconds..."
	@echo "$(PY_INFO)"; \
	$(PY) scripts/capture_test_logs.py --duration 40
	@echo "🔍 Analyzing captured logs..."
	@if [ -d "logs" ] && [ "$$(ls -A logs/*.json 2>/dev/null)" ]; then \
		LATEST_LOG=$$(ls -t logs/*.json | head -1); \
		LATEST_FRAMEWORK=$$(ls -t logs/*.md | head -1); \
		echo "📊 Analysis complete!"; \
		echo "📁 JSON logs: $$LATEST_LOG"; \
		echo "📋 Framework analysis: $$LATEST_FRAMEWORK"; \
		echo "🔍 View framework analysis:"; \
		echo "   cat $$LATEST_FRAMEWORK"; \
	fi

## rca-collect: Collect RCA artifacts (logs, taps, recordings) and run analyzer (env: SERVER_HOST, PROJECT_PATH, SINCE_MIN)
rca-collect:
	@echo "--> Collecting RCA artifacts from server..."
	@bash scripts/rca_collect.sh

## check-python: Check for host python3 and print fallback guidance
check-python:
	@if command -v python3 >/dev/null 2>&1; then \
		echo "✅ Host python3 detected: $$(python3 --version)"; \
	else \
		echo "⚠️ Host python3 not found."; \
		echo "   You can run all helper scripts via container Python, e.g.:"; \
		echo "   $(COMPOSE) exec -T ai_engine python /app/scripts/validate_externalmedia_config.py"; \
		echo "   $(COMPOSE) exec -T ai_engine python /app/scripts/test_externalmedia_call.py"; \
		echo "   $(COMPOSE) exec -T ai_engine python /app/scripts/monitor_externalmedia.py"; \
		echo "   $(COMPOSE) exec -T ai_engine python /app/scripts/capture_test_logs.py --duration 40"; \
		echo "   $(COMPOSE) exec -T ai_engine python /app/scripts/analyze_logs.py /app/logs/latest.json"; \
	fi

# ==============================================================================
# CLI TOOLS (v5.1.4)
# ==============================================================================

# Version management (uses git tags or fallback)
VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo "5.0.0-dev")
BUILD_TIME := $(shell date -u +"%Y-%m-%dT%H:%M:%SZ")
LDFLAGS := -s -w -X main.version=$(VERSION) -X main.buildTime=$(BUILD_TIME)

## cli-build: Build agent CLI for current platform
cli-build:
	@echo "Building agent CLI (version: $(VERSION))..."
	@mkdir -p bin
	@cd cli && CGO_ENABLED=0 go build -ldflags="$(LDFLAGS)" -o ../bin/agent ./cmd/agent
	@echo "✅ Binary created: bin/agent"
	@./bin/agent version

## cli-build-all: Build agent CLI for all platforms
cli-build-all:
	@echo "Building agent CLI for all platforms (version: $(VERSION))..."
	@mkdir -p bin
	@echo "Building Linux AMD64 (static binary)..."
	@cd cli && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build -ldflags="$(LDFLAGS)" -o ../bin/agent-linux-amd64 ./cmd/agent
	@echo "Building Linux ARM64 (static binary)..."
	@cd cli && CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -ldflags="$(LDFLAGS)" -o ../bin/agent-linux-arm64 ./cmd/agent
	@echo "Building macOS AMD64 (Intel)..."
	@cd cli && CGO_ENABLED=0 GOOS=darwin GOARCH=amd64 go build -ldflags="$(LDFLAGS)" -o ../bin/agent-darwin-amd64 ./cmd/agent
	@echo "Building macOS ARM64 (Apple Silicon)..."
	@cd cli && CGO_ENABLED=0 GOOS=darwin GOARCH=arm64 go build -ldflags="$(LDFLAGS)" -o ../bin/agent-darwin-arm64 ./cmd/agent
	@echo "Building Windows AMD64..."
	@cd cli && CGO_ENABLED=0 GOOS=windows GOARCH=amd64 go build -ldflags="$(LDFLAGS)" -o ../bin/agent-windows-amd64.exe ./cmd/agent
	@echo ""
	@echo "✅ All binaries built (static, GLIBC-independent):"
	@ls -lh bin/agent-*

## cli-checksums: Generate SHA256 checksums for binaries
cli-checksums:
	@echo "Generating checksums..."
	@cd bin && sha256sum agent-* > SHA256SUMS 2>/dev/null || shasum -a 256 agent-* > SHA256SUMS
	@echo "✅ Checksums saved to bin/SHA256SUMS"
	@cat bin/SHA256SUMS

## cli-test: Test built binaries
cli-test:
	@echo "Testing agent CLI..."
	@./bin/agent version
	@./bin/agent --help
	@echo "✅ CLI tests passed"

## cli-install: Install agent CLI to /usr/local/bin
cli-install: cli-build
	@echo "Installing agent CLI to /usr/local/bin/agent..."
	@sudo install -m 755 bin/agent /usr/local/bin/agent
	@echo "✅ Installed. Run 'agent version' to verify"

## cli-clean: Remove built binaries
cli-clean:
	@echo "Cleaning CLI binaries..."
	@rm -rf bin/agent*
	@echo "✅ Cleaned"

## cli-release: Build all binaries and create checksums (for releases)
cli-release: cli-build-all cli-checksums
	@echo ""
	@echo "🎉 Release build complete (version: $(VERSION))"
	@echo ""
	@echo "Upload these files to GitHub Releases:"
	@echo "  - bin/agent-linux-amd64"
	@echo "  - bin/agent-linux-arm64"
	@echo "  - bin/agent-darwin-amd64"
	@echo "  - bin/agent-darwin-arm64"
	@echo "  - bin/agent-windows-amd64.exe"
	@echo "  - bin/SHA256SUMS"

# ==============================================================================
# UTILITIES & HELP
# ==============================================================================

## help: Show this help message
help:
	@echo "Usage: make [target]"
	@echo ""
	@echo "Targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

.PHONY: build up down logs logs-all ps deploy deploy-safe deploy-force deploy-full deploy-no-cache server-logs server-logs-snapshot server-status server-clear-logs server-health test-local test-integration test-ari test-externalmedia verify-deployment verify-remote-sync verify-server-commit verify-config monitor-externalmedia monitor-externalmedia-once monitor-up monitor-down monitor-logs monitor-status cli-build cli-build-all cli-checksums cli-test cli-install cli-clean cli-release help
