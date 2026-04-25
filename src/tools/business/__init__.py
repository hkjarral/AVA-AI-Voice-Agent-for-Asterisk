"""
Business tools package.

Contains tools for business operations like email, calendar, CRM, etc.

Each tool import is wrapped in try/except so the package can still be loaded
in environments (e.g. admin_ui) that only need a subset of submodules but
don't have every optional runtime dependency installed (`dnspython` for the
email validator, Google API client for gcal, etc.). admin_ui's
`/microsoft-calendar/verify` endpoint imports
`src.tools.business.ms_graph_client` directly, which still triggers this
__init__; without the guards a missing optional dep crashes the verify call
with an unrelated `ModuleNotFoundError`.
"""

__all__: list[str] = []

try:
    from src.tools.business.email_summary import SendEmailSummaryTool
    __all__.append("SendEmailSummaryTool")
except ImportError:
    pass

try:
    from src.tools.business.request_transcript import RequestTranscriptTool
    __all__.append("RequestTranscriptTool")
except ImportError:
    pass

try:
    from src.tools.business.gcal_tool import GCalendarTool
    __all__.append("GCalendarTool")
except ImportError:
    pass

try:
    from src.tools.business.microsoft_calendar import MicrosoftCalendarTool
    __all__.append("MicrosoftCalendarTool")
except ImportError:
    pass
