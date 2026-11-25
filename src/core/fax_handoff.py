"""Recognize fax ownership evidence supplied by Asterisk, never by an LLM."""

from typing import Any, Optional


def fax_dialplan(channel: Any, *, stasis_ended: bool) -> Optional[dict]:
    """Return bounded routing evidence for a fax receiver or fax redirect.

    A StasisEnd snapshot may still name Stasis while already pointing at the
    special ``fax`` extension. A live lookup without that event must show an
    application outside Stasis; a context name alone isn't an ownership change.
    """
    if not isinstance(channel, dict) or not isinstance(channel.get("dialplan"), dict):
        return None
    dp = channel["dialplan"]
    context = dp.get("context")
    extension = dp.get("exten")
    application = dp.get("app_name")
    if not all(isinstance(value, str) for value in (context, extension, application)):
        return None
    if not stasis_ended and (not application or application.lower() == "stasis"):
        return None
    if not (
        extension == "fax"
        or context == "ext-fax"
        or context.startswith("ext-fax-")
        or application.lower() == "receivefax"
    ):
        return None
    return {"context": context[:128], "extension": extension[:128], "application": application[:64]}
