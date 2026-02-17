# API Reference

Asterisk AI Voice Agent exposes two HTTP API services:

1. **Admin UI Backend** (FastAPI, port 3003) — Configuration, system management, call history
2. **AI Engine Health Server** (aiohttp, port 15000) — Health probes, metrics, runtime status

---

## Interactive API Documentation

The Admin UI Backend provides **OpenAPI 3.0** documentation via Swagger UI and ReDoc.

| URL | Description |
|-----|-------------|
| `http://<host>:3003/docs` | **Swagger UI** — Interactive API explorer with "Try it out" |
| `http://<host>:3003/redoc` | **ReDoc** — Clean, readable API reference |
| `http://<host>:3003/openapi.json` | **OpenAPI spec** — Import into Postman, Insomnia, or SDK generators |

### Disabling API Docs in Production

For security-hardened deployments, you can disable API documentation endpoints by setting:

```bash
# In .env file
ENABLE_API_DOCS=false
```

When disabled, `/docs`, `/redoc`, and `/openapi.json` will return 404. Default is `true` (enabled).

### Authentication

Most Admin UI endpoints require JWT authentication:

> ⚠️ **Security Note:** The credentials below (`admin/admin`) are initial defaults. Change the admin password immediately after first login and never use default credentials in production.

```bash
# 1. Login to get a token
curl -X POST http://localhost:3003/api/auth/login \
  -d "username=admin&password=admin"

# Response: {"access_token": "eyJ...", "token_type": "bearer"}

# 2. Use the token in subsequent requests
curl -H "Authorization: Bearer eyJ..." \
  http://localhost:3003/api/config/yaml
```

---

## Admin UI Backend Endpoints (Port 3003)

### Auth (`/api/auth`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/auth/login` | Obtain JWT access token |
| POST | `/api/auth/change-password` | Change current user's password |
| GET | `/api/auth/me` | Get current user info |

### Config (`/api/config`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/config/yaml` | Get merged YAML configuration |
| POST | `/api/config/yaml` | Update YAML configuration |
| GET | `/api/config/env` | Get environment variables |
| POST | `/api/config/env` | Update environment variables |
| GET | `/api/config/env/status` | Check if containers are out-of-sync with .env |
| POST | `/api/config/providers/test` | Test provider connection |
| GET | `/api/config/export` | Export configuration as ZIP |
| POST | `/api/config/import` | Import configuration from ZIP |
| POST | `/api/config/env/smtp/test` | Test SMTP settings |
| GET | `/api/config/export-logs` | Export logs for troubleshooting |
| GET | `/api/config/options/{provider_type}` | Get provider options (models, voices) |

### System (`/api/system`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/system/containers` | List Docker containers |
| POST | `/api/system/containers/{id}/start` | Start a container |
| POST | `/api/system/containers/{id}/restart` | Restart a container |
| POST | `/api/system/containers/ai_engine/reload` | Hot-reload AI Engine config |
| GET | `/api/system/metrics` | Get system metrics (CPU, RAM) |
| GET | `/api/system/health` | Aggregate health status |
| GET | `/api/system/sessions` | Get active call sessions |
| GET | `/api/system/directories` | Check directory health |
| POST | `/api/system/directories/fix` | Fix directory permissions |
| GET | `/api/system/docker/disk-usage` | Get Docker disk usage |
| POST | `/api/system/docker/prune` | Clean up Docker resources |
| GET | `/api/system/platform` | Get platform detection results |
| POST | `/api/system/preflight` | Run preflight checks |
| POST | `/api/system/test-ari` | Test Asterisk ARI connection |
| GET | `/api/system/ari/extension-status` | Get extension status via ARI |
| GET | `/api/system/updates/status` | Get update status |
| GET | `/api/system/updates/branches` | List available branches |
| GET | `/api/system/updates/plan` | Get update plan |
| POST | `/api/system/updates/run` | Run update |
| POST | `/api/system/updates/rollback` | Rollback to previous version |
| GET | `/api/system/updates/history` | Get update history |
| GET | `/api/system/updates/jobs/{job_id}` | Get update job details |
| GET | `/api/system/asterisk-status` | Get Asterisk config status |

### Calls (`/api/calls`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/calls` | List call records with filters |
| GET | `/api/calls/stats` | Get call statistics |
| GET | `/api/calls/filters` | Get filter options (providers, contexts, outcomes) |
| GET | `/api/calls/{record_id}` | Get single call record |
| GET | `/api/calls/{record_id}/transcript` | Get call transcript |
| DELETE | `/api/calls/{record_id}` | Delete a call record |
| DELETE | `/api/calls` | Bulk delete calls |
| GET | `/api/calls/export/csv` | Export calls as CSV |
| GET | `/api/calls/export/json` | Export calls as JSON |

### Outbound (`/api/outbound`, `/api/campaigns`, `/api/leads`, `/api/recordings`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/recordings` | List recordings |
| POST | `/api/recordings/upload` | Upload a recording |
| GET | `/api/recordings/preview.wav` | Preview recording as WAV |
| GET | `/api/meta` | Get outbound metadata |
| GET | `/api/sample.csv` | Download sample CSV |
| GET | `/api/campaigns` | List campaigns |
| POST | `/api/campaigns` | Create campaign |
| GET | `/api/campaigns/{id}` | Get campaign |
| PATCH | `/api/campaigns/{id}` | Update campaign |
| DELETE | `/api/campaigns/{id}` | Delete campaign |
| POST | `/api/campaigns/{id}/clone` | Clone campaign |
| POST | `/api/campaigns/{id}/archive` | Archive campaign |
| POST | `/api/campaigns/{id}/status` | Set campaign status |
| GET | `/api/campaigns/{id}/stats` | Get campaign stats |
| POST | `/api/campaigns/{id}/leads/import` | Import leads from CSV |
| GET | `/api/campaigns/{id}/leads` | List leads |
| GET | `/api/campaigns/{id}/attempts` | List call attempts |
| POST | `/api/leads/{id}/cancel` | Cancel a lead |
| POST | `/api/leads/{id}/ignore` | Ignore a lead |
| POST | `/api/leads/{id}/recycle` | Recycle a lead |
| DELETE | `/api/leads/{id}` | Delete a lead |

### Local AI (`/api/local-ai`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/local-ai/models` | List installed models |
| GET | `/api/local-ai/capabilities` | Get backend capabilities |
| GET | `/api/local-ai/status` | Get local AI status |
| POST | `/api/local-ai/switch` | Switch active model |
| DELETE | `/api/local-ai/models` | Delete a model |
| POST | `/api/local-ai/rebuild` | Rebuild local AI container |
| GET | `/api/local-ai/backends` | List available backends |
| GET | `/api/local-ai/backends/{type}/{name}/schema` | Get backend config schema |

### Tools (`/api/tools`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/tools/catalog` | Get tool catalog |
| POST | `/api/tools/test-http` | Test HTTP tool configuration |
| GET | `/api/tools/test-values` | Get default test values |
| GET | `/api/tools/email-templates/defaults` | Get email template defaults |
| POST | `/api/tools/email-templates/preview` | Preview email template |

### MCP (`/api/mcp`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/mcp/status` | Get MCP server status (proxied from AI Engine) |
| POST | `/api/mcp/servers/{server_id}/test` | Test MCP server connection |

### Logs (`/api/logs`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/logs/{container_name}` | Get container logs |
| GET | `/api/logs/{container_name}/events` | Get structured log events |

### Wizard (`/api/wizard`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/wizard/init-env` | Initialize .env file |
| GET | `/api/wizard/load-config` | Load existing configuration |
| GET | `/api/wizard/status` | Get setup status |
| POST | `/api/wizard/save` | Save wizard configuration |
| POST | `/api/wizard/skip` | Skip setup wizard |
| POST | `/api/wizard/validate-key` | Validate API key |
| POST | `/api/wizard/validate-connection` | Validate Asterisk connection |
| GET | `/api/wizard/engine-status` | Get AI Engine status |
| POST | `/api/wizard/setup-media-paths` | Setup media directories |
| POST | `/api/wizard/start-engine` | Start AI Engine |

### Ollama (`/api/ollama`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/ollama/test` | Test Ollama connection |
| GET | `/api/ollama/models` | List Ollama models |
| GET | `/api/ollama/tool-capable-models` | Get tool-capable models |

### Documentation (`/api/docs`)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/docs/categories` | Get documentation categories |
| GET | `/api/docs/content/{file_path}` | Get documentation content |
| GET | `/api/docs/search` | Search documentation |

---

## AI Engine Health Server (Port 15000)

The AI Engine exposes a lightweight aiohttp server for health probes and metrics.
Default bind: `127.0.0.1:15000` (configurable via `HEALTH_BIND_HOST`, `HEALTH_BIND_PORT`).

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/live` | Kubernetes liveness probe (always returns 200) |
| GET | `/ready` | Kubernetes readiness probe |
| GET | `/health` | Detailed health status JSON |
| GET | `/metrics` | Prometheus metrics |
| POST | `/reload` | Hot-reload YAML configuration |
| GET | `/mcp/status` | MCP server status |
| POST | `/mcp/test/{server_id}` | Test MCP server connection |
| GET | `/tools/definitions` | Get tool catalog (read-only) |
| GET | `/sessions/stats` | Get active session statistics |

### Example: Health Check

```bash
curl http://localhost:15000/health
```

```json
{
  "status": "healthy",
  "uptime_seconds": 3600,
  "active_calls": 2,
  "provider": "openai_realtime",
  "transport": "external_media"
}
```

### Example: Prometheus Metrics

```bash
curl http://localhost:15000/metrics
```

```text
# HELP aava_active_calls Number of active calls
# TYPE aava_active_calls gauge
aava_active_calls 2
# HELP aava_total_calls_handled Total calls handled since startup
# TYPE aava_total_calls_handled counter
aava_total_calls_handled 150
```

### Authentication (Optional)

Set `HEALTH_API_TOKEN` in `.env` to require bearer token authentication:

```bash
curl -H "Authorization: Bearer your-token" http://localhost:15000/reload -X POST
```

---

## Related Documentation

- Architecture overview: [`architecture-quickstart.md`](architecture-quickstart.md)
- Architecture deep dive: [`architecture-deep-dive.md`](architecture-deep-dive.md)
- Engine source: [`src/engine.py`](../../src/engine.py)
- Configuration: [`src/config.py`](../../src/config.py)

