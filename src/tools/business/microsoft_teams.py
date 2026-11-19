"""Teams calendar capabilities and conservative handling of Graph-owned HTML.

Only the AVA details container is rendered again. All bytes outside that
container, including Microsoft's meeting blob, are retained on rescheduling.
If Graph removes/rewrites the container beyond recognition, mutation fails closed.
"""

from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit

from src.tools.business.microsoft_booking import BookingValidationError

DETAILS_ID = "ava-booking-details"


def teams_enabled(config: dict) -> bool:
    enabled = config.get("teams_enabled", False)
    if type(enabled) is not bool:
        raise BookingValidationError(
            "invalid_configuration", "teams_enabled must be a boolean."
        )
    return enabled


def require_teams_calendar(calendar: dict) -> None:
    if not isinstance(calendar, dict) or not calendar.get("id"):
        raise BookingValidationError(
            "calendar_not_found",
            "The configured calendar is unavailable. Verify its saved selection.",
        )
    if calendar.get("canEdit") is False:
        raise BookingValidationError(
            "calendar_read_only",
            "The configured calendar is read-only. Verify its permissions.",
        )
    if "teamsForBusiness" not in (calendar.get("allowedOnlineMeetingProviders") or []):
        raise BookingValidationError(
            "teams_not_supported",
            "The selected calendar does not advertise Microsoft Teams support. Verify the organizer's Teams/Exchange license and tenant policy, then Verify this same calendar. No appointment was substituted.",
        )


def details_html(text: str) -> str:
    return (
        escape(text, quote=True)
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", "<br>")
    )


def teams_body(text: str) -> dict:
    return {
        "contentType": "HTML",
        "content": f'<div id="{DETAILS_ID}">{details_html(text)}</div>',
    }


class _DetailsParser(HTMLParser):
    def __init__(self, content):
        super().__init__(convert_charrefs=False)
        self.content = content
        # HTMLParser's line counter advances on LF only (not Unicode separators).
        self.line_starts = [0] + [
            i + 1 for i, char in enumerate(content) if char == "\n"
        ]
        self.depth = 0
        self.start = None
        self.spans = []
        self.invalid = False

    def source_offset(self):
        line, column = self.getpos()
        return self.line_starts[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if tag != "div":
            return
        owned = ("id", DETAILS_ID) in attrs
        if owned:
            if self.depth:
                self.invalid = True
            self.start = self.source_offset() + len(self.get_starttag_text())
        if self.depth or owned:
            self.depth += 1

    def handle_endtag(self, tag):
        if tag == "div" and self.depth:
            self.depth -= 1
            if not self.depth:
                self.spans.append((self.start, self.source_offset()))


def _details_region(body: dict) -> tuple[str, int, int]:
    content = body.get("content")
    if str(body.get("contentType", "")).lower() != "html" or not isinstance(
        content, str
    ):
        raise BookingValidationError(
            "booking_changed",
            "Cannot safely preserve the Teams meeting body; ask staff to handle this booking.",
        )
    parser = _DetailsParser(content)
    parser.feed(content)
    parser.close()
    if parser.invalid or parser.depth or len(parser.spans) != 1:
        raise BookingValidationError(
            "booking_changed",
            "The Teams booking details container changed; ask staff to handle this booking.",
        )
    start, end = parser.spans[0]
    if start is None or end < start:
        raise BookingValidationError(
            "booking_changed",
            "Cannot identify the Teams details container safely; ask staff.",
        )
    return content, start, end


def replace_details(body: dict, text: str) -> dict:
    content, start, end = _details_region(body)
    return {
        "contentType": body["contentType"],
        "content": content[:start] + details_html(text) + content[end:],
    }


class _EquivalentHTML(HTMLParser):
    """Conservative serialization fingerprint; never used for pre-write approval."""

    def __init__(self, *, details=False):
        super().__init__(convert_charrefs=True)
        self.details = details
        self.tokens = []
        self.invalid = False

    @staticmethod
    def _font_style(value):
        return all(
            rule.strip().lower().startswith("font-family:")
            for rule in value.split(";")
            if rule.strip()
        )

    def handle_starttag(self, tag, attrs):
        attrs = sorted((key, value or "") for key, value in attrs)
        # Graph can add presentational wrappers to AVA's plain text. Only the
        # narrow font-family style is ignored; links, hidden text, etc. fail closed.
        presentation = all(
            key == "style" and self._font_style(value) for key, value in attrs
        )
        if self.details:
            if tag == "span" and presentation:
                return
            if tag == "br" and not attrs:
                self.handle_data(" ")
                return
            self.invalid = True
        elif tag == "div" and ("id", DETAILS_ID) in attrs:
            # Keep existing attributes comparable, including ones we don't
            # interpret. Only a narrow presentation-only style may be added.
            attrs = [
                (key, value)
                for key, value in attrs
                if key != "style" or not self._font_style(value)
            ]
        self.tokens.append(("start", tag, tuple(attrs)))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in {"br", "hr", "img", "meta", "link", "input", "wbr"}:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.details and tag == "span":
            return
        self.tokens.append(("end", tag))

    def handle_data(self, data):
        if self.details:
            # Keep text across span boundaries contiguous; entities are decoded
            # by HTMLParser. Plain details use whitespace only for layout.
            self.tokens.append(data)
        elif data.strip():
            self.tokens.append(("text", " ".join(data.split())))

    def handle_comment(self, data):
        self.tokens.append(("comment", data))

    def handle_decl(self, decl):
        self.tokens.append(("decl", decl.lower()))


def _html_fingerprint(content: str, *, details=False):
    parser = _EquivalentHTML(details=details)
    parser.feed(content)
    parser.close()
    if parser.invalid or (
        details and any(not isinstance(t, str) for t in parser.tokens)
    ):
        return None
    return " ".join("".join(parser.tokens).split()) if details else parser.tokens


def _nonempty_metadata(value):
    if isinstance(value, dict):
        return {
            k: v
            for k, item in value.items()
            if (v := _nonempty_metadata(item)) not in (None, "", [], {})
        }
    if isinstance(value, list):
        return [_nonempty_metadata(item) for item in value]
    return value


def pending_update_matches(event: dict, expected: dict) -> bool:
    """Accept harmless Graph serialization only for a known conditional PATCH.

    Preserve the join metadata and Microsoft's HTML outside AVA's details, then
    adopt the observed representation as the next exact pre-write baseline.
    """
    actual = meeting_snapshot(event)
    if (
        actual["is_online_meeting"] is not True
        or actual["provider"] != "teamsForBusiness"
        or not join_url(event)
        or _nonempty_metadata({k: v for k, v in actual.items() if k != "body"})
        != _nonempty_metadata({k: v for k, v in expected.items() if k != "body"})
    ):
        return False
    try:
        actual_content, a, b = _details_region(actual["body"])
        expected_content, c, d = _details_region(expected["body"])
        actual_details = _html_fingerprint(actual_content[a:b], details=True)
        expected_details = _html_fingerprint(expected_content[c:d], details=True)
        actual_outside = _html_fingerprint(actual_content[:a] + actual_content[b:])
        expected_outside = _html_fingerprint(
            expected_content[:c] + expected_content[d:]
        )
        return (
            actual_details is not None
            and actual_details == expected_details
            and actual_outside is not None
            and actual_outside == expected_outside
        )
    except (BookingValidationError, TypeError, ValueError):
        return False


def join_url(event: dict) -> str | None:
    meeting = event.get("onlineMeeting")
    value = meeting.get("joinUrl") if isinstance(meeting, dict) else None
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme == "https"
            and parsed.hostname
            and not parsed.username
            and not parsed.password
        ):
            return value
    except ValueError:
        pass
    return None


def meeting_snapshot(event: dict) -> dict:
    body = event.get("body") or {}
    return {
        "body": {
            "contentType": str(body.get("contentType", "")).lower(),
            "content": body.get("content"),
        },
        "is_online_meeting": event.get("isOnlineMeeting"),
        "provider": event.get("onlineMeetingProvider"),
        "online_meeting": event.get("onlineMeeting"),
    }
