"""
Documentation API - Serves markdown documentation from the docs/ folder.
"""
import os
from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/docs", tags=["documentation"])

# Documentation categories with their files
DOC_CATEGORIES = {
    "getting_started": {
        "title": "Getting Started",
        "icon": "📖",
        "description": "Installation, setup, and quick start guides",
        "docs": [
            {"file": "INSTALLATION.md", "title": "Installation Guide", "description": "Complete setup instructions"},
            {"file": "FreePBX-Integration-Guide.md", "title": "FreePBX Integration", "description": "Dialplan and queue configuration"},
            {"file": "Configuration-Reference.md", "title": "Configuration Reference", "description": "All YAML settings explained"},
            {"file": "TOOL_CALLING_GUIDE.md", "title": "Tool Calling Guide", "description": "AI-powered actions and integrations"},
            {"file": "Pushover.md", "title": "Pushover Tool", "description": "Named notifications, recipients/groups, summaries and testing"},
            {"file": "Appointment-booking-providers.md", "title": "Appointment Booking Providers", "description": "Self-hosted MCP booking and optional provider adapters"},
            {"file": "Calendly-calendar-tool.md", "title": "Optional Calendly Adapter", "description": "Paid-provider booking, confirmation and uncertain outcomes"},
            {"file": "In-Call-Summaries.md", "title": "In-call Summaries", "description": "Generated summaries for supported tools"},
            {"file": "AGENTS.md", "title": "Agents", "description": "Agent workspace, permissions and appearance"},
            {"file": "ARI-Endpoint-Discovery.md", "title": "ARI Endpoint Discovery", "description": "Discover and review transfer destinations"},
            {"file": "Microsoft-teams-verification.md", "title": "Microsoft Teams Verification", "description": "Opt-in setup and verification boundaries"},
            {"file": "TROUBLESHOOTING_GUIDE.md", "title": "Troubleshooting", "description": "Common issues and solutions"},
        ]
    },
    "providers": {
        "title": "Provider Setup",
        "icon": "🔌",
        "description": "Configure AI service providers",
        "docs": [
            {"file": "Provider-OpenAI-Setup.md", "title": "OpenAI Realtime", "description": "GPT-4o Realtime integration"},
            {"file": "Provider-Deepgram-Setup.md", "title": "Deepgram", "description": "Deepgram Voice Agent setup"},
            {"file": "Provider-Google-Setup.md", "title": "Google Live", "description": "Google Cloud Speech integration"},
            {"file": "Provider-ElevenLabs-Setup.md", "title": "ElevenLabs", "description": "Premium voice synthesis"},
        ]
    },
    "operations": {
        "title": "Operations & Production",
        "icon": "🚀",
        "description": "Deployment, monitoring, and scaling",
        "docs": [
            {"file": "PRODUCTION_DEPLOYMENT.md", "title": "Production Deployment", "description": "Security and best practices"},
            {"file": "MONITORING_GUIDE.md", "title": "Monitoring Guide", "description": "Prometheus + Grafana setup"},
            {"file": "HARDWARE_REQUIREMENTS.md", "title": "Hardware Requirements", "description": "CPU, RAM, and GPU specs"},
            {"file": "LOCAL_PROFILES.md", "title": "Local Profiles", "description": "Recommended local model configs"},
            {"file": "OUTBOUND_CALLING.md", "title": "Outbound Calling", "description": "Scheduled campaigns and voicemail"},
            {"file": "MIGRATION.md", "title": "Migration Guide", "description": "Release upgrade and rollback boundaries"},
            {"file": "INTEGRATION_BACKUPS.md", "title": "Integration Backups", "description": "Private credentials and SQLite journal recovery"},
            {"file": "AGENTS_EXPERIENCE_ROLLOUT.md", "title": "Agents Experience Rollout", "description": "Workspace rollout, compatibility and rollback"},
        ]
    },
    "developer": {
        "title": "Developer Documentation",
        "icon": "💻",
        "description": "Contributing, architecture, and APIs",
        "docs": [
            {"file": "contributing/README.md", "title": "Contributing Guide", "description": "Start here for development"},
            {"file": "contributing/architecture-quickstart.md", "title": "Architecture Overview", "description": "System design (10-minute read)"},
            {"file": "contributing/architecture-deep-dive.md", "title": "Architecture Deep Dive", "description": "Complete technical architecture"},
            {"file": "DEVELOPER_ONBOARDING.md", "title": "Developer Onboarding", "description": "Repo orientation and dev flow"},
            {"file": "contributing/COMMON_PITFALLS.md", "title": "Common Pitfalls", "description": "Real issues and solutions"},
        ]
    },
    "reference": {
        "title": "Reference & Case Studies",
        "icon": "📋",
        "description": "Roadmap, baselines, and examples",
        "docs": [
            {"file": "ROADMAP.md", "title": "Roadmap", "description": "Planned features and priorities"},
            {"file": "Transport-Mode-Compatibility.md", "title": "Transport Modes", "description": "AudioSocket vs ExternalMedia"},
            {"file": "case-studies/OpenAI-Realtime-Golden-Baseline.md", "title": "OpenAI Baseline", "description": "Golden baseline configuration"},
            {"file": "case-studies/Deepgram-Agent-Golden-Baseline.md", "title": "Deepgram Baseline", "description": "Deepgram golden baseline"},
            {"file": "Tuning-Recipes.md", "title": "Tuning Recipes", "description": "Performance optimization tips"},
        ]
    }
}


class DocInfo(BaseModel):
    file: str
    title: str
    description: str


class CategoryInfo(BaseModel):
    id: str
    title: str
    icon: str
    description: str
    docs: List[DocInfo]


class DocContent(BaseModel):
    file: str
    title: str
    content: str
    category: str
    prev_doc: Optional[DocInfo] = None
    next_doc: Optional[DocInfo] = None


def get_docs_path() -> Path:
    """Get the path to the docs folder."""
    # Try different locations based on deployment context
    possible_paths = [
        Path("/app/project/docs"),  # Docker container
        Path(os.environ.get("PROJECT_ROOT", "")) / "docs",
        Path(__file__).parent.parent.parent.parent.parent / "docs",  # Relative to this file
    ]
    
    for path in possible_paths:
        if path.exists():
            return path
    
    # Fallback - use the relative path from project root
    return Path("/app/project/docs")


@router.get("/categories", response_model=List[CategoryInfo])
async def get_categories():
    """Get all documentation categories with their docs."""
    categories = []
    for cat_id, cat_data in DOC_CATEGORIES.items():
        categories.append(CategoryInfo(
            id=cat_id,
            title=cat_data["title"],
            icon=cat_data["icon"],
            description=cat_data["description"],
            docs=[DocInfo(**doc) for doc in cat_data["docs"]]
        ))
    return categories


@router.get("/content/{file_path:path}", response_model=DocContent)
async def get_doc_content(file_path: str):
    """Get the content of a specific documentation file."""
    requested = (file_path or "").strip()
    docs_path = get_docs_path()
    for cat_id, cat_data in DOC_CATEGORIES.items():
        docs = cat_data.get("docs", [])
        for index, doc in enumerate(docs):
            canonical_file = str(doc.get("file", ""))
            if canonical_file != requested:
                continue

            full_path = docs_path / canonical_file
            if not full_path.is_file():
                raise HTTPException(status_code=404, detail=f"Documentation file not found: {file_path}")

            try:
                content = full_path.read_text(encoding="utf-8")
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Error reading file: {str(e)}")

            prev_doc = DocInfo(**docs[index - 1]) if index > 0 else None
            next_doc = DocInfo(**docs[index + 1]) if index < len(docs) - 1 else None
            return DocContent(
                file=canonical_file,
                title=str(doc.get("title", requested)),
                content=content,
                category=cat_id,
                prev_doc=prev_doc,
                next_doc=next_doc
            )

    raise HTTPException(status_code=404, detail=f"Documentation file not found: {file_path}")


@router.get("/search")
async def search_docs(q: str):
    """Search documentation content."""
    if not q or len(q) < 2:
        return {"results": []}
    
    docs_path = get_docs_path()
    results = []
    query_lower = q.lower()
    
    for cat_id, cat_data in DOC_CATEGORIES.items():
        for doc in cat_data["docs"]:
            file_path = docs_path / doc["file"]
            if not file_path.exists():
                continue
            
            try:
                content = file_path.read_text(encoding="utf-8")
                if query_lower in content.lower() or query_lower in doc["title"].lower():
                    # Find a snippet around the match
                    content_lower = content.lower()
                    pos = content_lower.find(query_lower)
                    if pos >= 0:
                        start = max(0, pos - 50)
                        end = min(len(content), pos + len(q) + 100)
                        snippet = content[start:end].replace("\n", " ").strip()
                        if start > 0:
                            snippet = "..." + snippet
                        if end < len(content):
                            snippet = snippet + "..."
                    else:
                        snippet = doc["description"]
                    
                    results.append({
                        "file": doc["file"],
                        "title": doc["title"],
                        "category": cat_data["title"],
                        "snippet": snippet
                    })
            except Exception:
                continue
    
    return {"results": results[:10]}  # Limit to 10 results
