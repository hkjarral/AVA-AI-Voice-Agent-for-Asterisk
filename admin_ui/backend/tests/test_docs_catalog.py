"""The documentation allowlist exposes the current public guides, not secrets."""
from pathlib import Path

import pytest

from api import docs

ROOT = Path(__file__).resolve().parents[3]
RELEASE_GUIDES = {
    "Appointment-booking-providers.md", "Calendly-calendar-tool.md", "In-Call-Summaries.md", "AGENTS.md",
    "ARI-Endpoint-Discovery.md", "Microsoft-teams-verification.md",
    "MIGRATION.md", "INTEGRATION_BACKUPS.md", "AGENTS_EXPERIENCE_ROLLOUT.md",
}


@pytest.mark.asyncio
async def test_documentation_catalog_is_unique_and_serves_release_guides(monkeypatch):
    monkeypatch.setattr(docs, "get_docs_path", lambda: ROOT / "docs")
    files = [doc["file"] for cat in docs.DOC_CATEGORIES.values() for doc in cat["docs"]]
    assert len(files) == len(set(files))
    assert RELEASE_GUIDES.issubset(files)
    for relative in files:
        assert (ROOT / "docs" / relative).is_file(), relative
    for relative in RELEASE_GUIDES:
        result = await docs.get_doc_content(relative)
        assert result.content == (ROOT / "docs" / relative).read_text()
        assert result.title and result.category


@pytest.mark.asyncio
async def test_documentation_catalog_does_not_authorize_arbitrary_paths(monkeypatch):
    monkeypatch.setattr(docs, "get_docs_path", lambda: ROOT / "docs")
    for path in ("../.env", "../../secrets/calendly.json", "/etc/passwd"):
        with pytest.raises(docs.HTTPException) as exc:
            await docs.get_doc_content(path)
        assert exc.value.status_code == 404
