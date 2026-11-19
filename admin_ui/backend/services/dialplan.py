"""Safe dialplan generation shared by Agents and first-call onboarding."""
import re

DEFAULT_APP = "asterisk-ai-voice-agent"


def identifier(value: str, label: str) -> str:
    value = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        raise ValueError(f"{label} must contain only letters, numbers, underscores, dots or hyphens")
    return value


def stasis_app(config: dict) -> str:
    return identifier(str((config.get("asterisk") or {}).get("app_name") or DEFAULT_APP), "Stasis application")


def agent_dialplan(slug: str, app: str, *, context: str = "from-ai-agent", extension: str = "s") -> str:
    slug = identifier(slug, "Agent slug")
    app = identifier(app, "Stasis application")
    context = identifier(context, "Dialplan context")
    if not re.fullmatch(r"[A-Za-z0-9_*#+.-]+", extension):
        raise ValueError("Invalid dialplan extension")
    return (
        "; Add to /etc/asterisk/extensions_custom.conf (FreePBX) or your included custom dialplan\n"
        f"[{context}]\n"
        f"exten => {extension},1,NoOp(AVA agent {slug})\n"
        f" same => n,Set(AI_AGENT={slug})\n"
        f" same => n,Stasis({app})\n"
        " same => n,Hangup()\n"
    )
