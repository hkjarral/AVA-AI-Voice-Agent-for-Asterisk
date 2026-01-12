# Debugging Guide

For operator and developer debugging workflows, start with:

- Troubleshooting (operators + contributors): [`docs/TROUBLESHOOTING_GUIDE.md`](../TROUBLESHOOTING_GUIDE.md)
- Milestone context (Call History–first): [`milestone-21-call-history.md`](milestones/milestone-21-call-history.md)

## Useful Tools

- `agent check`
- `agent rca`

## Logs & Data

- Container logs: `docker compose logs ai_engine`
- Call history DB: `./data/call_history.db` (host)
