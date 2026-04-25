"""
Tests for Google Calendar Tool (GCalendarTool).

Covers definition, config handling, and execution for list_events, get_event,
create_event, delete_event, and get_free_slots actions.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock

from src.tools.business.gcal_tool import GCalendarTool
from src.tools.base import ToolCategory


class TestGCalendarToolDefinition:
    """Test tool definition and schema."""

    def test_definition_name_and_category(self):
        """Tool name and category match project convention."""
        tool = GCalendarTool()
        definition = tool.definition
        assert definition.name == "google_calendar"
        assert definition.category == ToolCategory.BUSINESS

    def test_definition_uses_input_schema(self):
        """Tool uses input_schema for provider-agnostic schema (Google Live, OpenAI)."""
        tool = GCalendarTool()
        definition = tool.definition
        assert definition.input_schema is not None
        assert definition.input_schema.get("type") == "object"
        assert "properties" in definition.input_schema
        assert "action" in definition.input_schema["properties"]

    def test_definition_action_enum_includes_all_actions(self):
        """Action enum includes list_events, get_event, create_event, delete_event, get_free_slots."""
        tool = GCalendarTool()
        definition = tool.definition
        actions = definition.input_schema["properties"]["action"]["enum"]
        assert "list_events" in actions
        assert "get_event" in actions
        assert "create_event" in actions
        assert "delete_event" in actions
        assert "get_free_slots" in actions

    def test_definition_required_includes_action(self):
        """Schema required array includes action."""
        tool = GCalendarTool()
        definition = tool.definition
        assert "action" in definition.input_schema.get("required", [])


class TestGCalendarToolExecution:
    """Test execute() behavior: disabled config, missing params, delete_event flow."""

    @pytest.fixture
    def gcal_tool(self):
        return GCalendarTool()

    @pytest.fixture
    def gcal_enabled_context(self, tool_context):
        """Context with google_calendar enabled."""
        tool_context.get_config_value = Mock(
            return_value={
                "enabled": True,
                "credentials_path": "/fake/creds.json",
                "calendar_id": "primary",
            }
        )
        return tool_context

    @pytest.mark.asyncio
    async def test_disabled_returns_error(self, gcal_tool, tool_context):
        """When tool is disabled by config, returns error status."""
        tool_context.get_config_value = Mock(
            return_value={"enabled": False}
        )
        result = await gcal_tool.execute(
            parameters={"action": "list_events", "time_min": "2025-01-01T00:00:00", "time_max": "2025-01-02T00:00:00"},
            context=tool_context,
        )
        assert result["status"] == "error"
        assert "disabled" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_missing_action_returns_error(self, gcal_tool, gcal_enabled_context):
        """Missing action parameter returns error."""
        result = await gcal_tool.execute(
            parameters={},
            context=gcal_enabled_context,
        )
        assert result["status"] == "error"
        assert "action" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_delete_event_missing_event_id_returns_error(
        self, gcal_tool, gcal_enabled_context
    ):
        """delete_event without event_id returns error."""
        with patch.object(
            gcal_tool, "_get_cal", return_value=MagicMock(service=MagicMock())
        ):
            result = await gcal_tool.execute(
                parameters={"action": "delete_event"},
                context=gcal_enabled_context,
            )
        assert result["status"] == "error"
        assert "event_id" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_delete_event_success(
        self, gcal_tool, gcal_enabled_context
    ):
        """delete_event with valid event_id returns success when calendar deletes."""
        mock_cal = MagicMock()
        mock_cal.delete_event = Mock(return_value=True)
        with patch.object(gcal_tool, "_get_cal", return_value=mock_cal):
            result = await gcal_tool.execute(
                parameters={"action": "delete_event", "event_id": "evt_123"},
                context=gcal_enabled_context,
            )
        assert result["status"] == "success"
        assert result.get("id") == "evt_123"
        assert "deleted" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_delete_event_failure_returns_error(
        self, gcal_tool, gcal_enabled_context
    ):
        """delete_event when calendar returns False returns error."""
        mock_cal = MagicMock()
        mock_cal.delete_event = Mock(return_value=False)
        with patch.object(gcal_tool, "_get_cal", return_value=mock_cal):
            result = await gcal_tool.execute(
                parameters={"action": "delete_event", "event_id": "evt_unknown"},
                context=gcal_enabled_context,
            )
        assert result["status"] == "error"
        assert "delete" in result["message"].lower()


class TestGetFreeSlotsStructuredResponse:
    """get_free_slots returns a structured response distinguishing the empty-result causes.

    Covers:
    (a) Slots available → reason="available", slots non-empty, legacy "Free slot starts:" preserved
    (b) Open windows exist but all blocked by Busy events → reason="fully_booked"
    (c) No Open windows configured on the calendar → reason="no_open_windows"
    (d) Missing prefixes (LLM and config both silent) → backend defaults to "Open" / "Busy"
    (e) Empty-string prefix in config → treated as "use default" (operator may have cleared the field)
    """

    @pytest.fixture
    def gcal_tool(self):
        return GCalendarTool()

    @pytest.fixture
    def gcal_enabled_context(self, tool_context):
        # NOTE: free_prefix='Open' is set explicitly so these tests exercise
        # title-prefix mode (which has the no_open_windows / fully_booked
        # reason semantics). Post-round-2.5 the tool treats blank/missing
        # free_prefix as a switch to free/busy mode, so a fixture that
        # omits it would land in the wrong code path for these tests.
        tool_context.get_config_value = Mock(
            return_value={
                "enabled": True,
                "credentials_path": "/fake/creds.json",
                "calendar_id": "primary",
                "timezone": "America/Los_Angeles",
                "free_prefix": "Open",
                "busy_prefix": "Busy",
            }
        )
        return tool_context

    def _make_event(self, summary, start, end):
        return {
            "summary": summary,
            "start": {"dateTime": start},
            "end": {"dateTime": end},
        }

    def _mock_cal_with_events(self, events):
        mock_cal = MagicMock()
        # Truthy `service` so the "calendar unavailable" guard passes
        mock_cal.service = MagicMock()
        mock_cal.list_events = Mock(return_value=events)
        return mock_cal

    @pytest.mark.asyncio
    async def test_no_open_windows_returns_no_open_windows_reason(
        self, gcal_tool, gcal_enabled_context
    ):
        """No 'Open'-prefixed events on the calendar → reason='no_open_windows'."""
        events = [
            self._make_event(
                "Busy meeting",
                "2026-04-29T10:00:00-07:00",
                "2026-04-29T11:00:00-07:00",
            ),
        ]
        mock_cal = self._mock_cal_with_events(events)
        # Patch both: legacy_single guard uses _get_cal, the loop uses _get_or_create_cal
        with patch.object(gcal_tool, "_get_cal", return_value=mock_cal), \
             patch.object(gcal_tool, "_get_or_create_cal", return_value=mock_cal):
            result = await gcal_tool.execute(
                parameters={
                    "action": "get_free_slots",
                    "time_min": "2026-04-29T00:00:00",
                    "time_max": "2026-04-30T00:00:00",
                    "duration": 30,
                },
                context=gcal_enabled_context,
            )
        assert result["status"] == "success"
        assert result["reason"] == "no_open_windows"
        assert result["slots"] == []
        assert result["open_windows_found"] is False
        assert result["busy_blocks_found"] is True
        # Message names the prefix so the LLM can communicate it
        assert "Open" in result["message"]

    @pytest.mark.asyncio
    async def test_fully_booked_returns_fully_booked_reason(
        self, gcal_tool, gcal_enabled_context
    ):
        """Open windows exist but are entirely covered by Busy blocks → reason='fully_booked'."""
        events = [
            self._make_event(
                "Open hours",
                "2026-04-29T09:00:00-07:00",
                "2026-04-29T17:00:00-07:00",
            ),
            self._make_event(
                "Busy",
                "2026-04-29T09:00:00-07:00",
                "2026-04-29T17:00:00-07:00",
            ),
        ]
        mock_cal = self._mock_cal_with_events(events)
        # Patch both: legacy_single guard uses _get_cal, the loop uses _get_or_create_cal
        with patch.object(gcal_tool, "_get_cal", return_value=mock_cal), \
             patch.object(gcal_tool, "_get_or_create_cal", return_value=mock_cal):
            result = await gcal_tool.execute(
                parameters={
                    "action": "get_free_slots",
                    "time_min": "2026-04-29T00:00:00",
                    "time_max": "2026-04-30T00:00:00",
                    "duration": 30,
                },
                context=gcal_enabled_context,
            )
        assert result["status"] == "success"
        assert result["reason"] == "fully_booked"
        assert result["slots"] == []
        assert result["open_windows_found"] is True
        assert result["busy_blocks_found"] is True

    @pytest.mark.asyncio
    async def test_slots_available_returns_available_reason(
        self, gcal_tool, gcal_enabled_context
    ):
        """Open window with no overlapping Busy → reason='available', slots non-empty,
        and message preserves legacy 'Free slot starts:' format for prompt back-compat."""
        events = [
            self._make_event(
                "Open",
                "2026-04-29T09:00:00-07:00",
                "2026-04-29T11:00:00-07:00",
            ),
        ]
        mock_cal = self._mock_cal_with_events(events)
        # Patch both: legacy_single guard uses _get_cal, the loop uses _get_or_create_cal
        with patch.object(gcal_tool, "_get_cal", return_value=mock_cal), \
             patch.object(gcal_tool, "_get_or_create_cal", return_value=mock_cal):
            result = await gcal_tool.execute(
                parameters={
                    "action": "get_free_slots",
                    "time_min": "2026-04-29T00:00:00",
                    "time_max": "2026-04-30T00:00:00",
                    "duration": 30,
                },
                context=gcal_enabled_context,
            )
        assert result["status"] == "success"
        assert result["reason"] == "available"
        assert len(result["slots"]) > 0
        assert result["open_windows_found"] is True
        # Back-compat: legacy "Free slot starts:" prefix still appears in message
        # (existing prompt templates pattern-match on this string)
        assert "Free slot starts:" in result["message"]

    @pytest.mark.asyncio
    async def test_uses_backend_default_prefixes_when_unset(
        self, gcal_tool, gcal_enabled_context
    ):
        """When neither LLM parameters nor config supply prefixes, backend defaults to
        'Open' / 'Busy' and the tool succeeds (no longer the legacy 'prefix required' error)."""
        events = [
            self._make_event(
                "Open",
                "2026-04-29T09:00:00-07:00",
                "2026-04-29T10:00:00-07:00",
            ),
        ]
        mock_cal = self._mock_cal_with_events(events)
        # Patch both: legacy_single guard uses _get_cal, the loop uses _get_or_create_cal
        with patch.object(gcal_tool, "_get_cal", return_value=mock_cal), \
             patch.object(gcal_tool, "_get_or_create_cal", return_value=mock_cal):
            result = await gcal_tool.execute(
                parameters={
                    "action": "get_free_slots",
                    "time_min": "2026-04-29T00:00:00",
                    "time_max": "2026-04-30T00:00:00",
                    "duration": 30,
                    # NOTE: no free_prefix / busy_prefix passed
                },
                context=gcal_enabled_context,
            )
        # Pre-fix: this would have errored with "free_prefix and busy_prefix are required".
        # Post-fix: backend defaults to "Open" / "Busy", the test event matches "Open", slots returned.
        assert result["status"] == "success"
        assert result["reason"] == "available"
        assert len(result["slots"]) > 0

    @pytest.mark.asyncio
    async def test_absent_config_prefix_also_switches_to_freebusy_mode(
        self, gcal_tool, tool_context
    ):
        """The canonical mode-selection rule says blank OR absent free_prefix
        switches to freebusy mode. The previous test pinned the explicit-
        blank case; this one pins the absent-key case. Both are common
        configurations: a fresh install has no free_prefix key, while an
        operator UI clear stores empty string. They must behave identically."""
        # Note: free_prefix and busy_prefix are both ABSENT from the config
        # dict (not set to '', not set to anything). This is what a fresh
        # install or a YAML config that never mentions these keys looks like.
        tool_context.get_config_value = Mock(
            return_value={
                "enabled": True,
                "credentials_path": "/fake/creds.json",
                "calendar_id": "primary",
                "timezone": "America/Los_Angeles",
                # NO free_prefix / busy_prefix keys
            }
        )
        mock_cal = MagicMock()
        mock_cal.service = MagicMock()
        mock_cal.freebusy_query = Mock(return_value=[])
        mock_cal.list_events = Mock(side_effect=AssertionError(
            "list_events must NOT be called when free_prefix is absent (freebusy mode)"
        ))
        with patch.object(gcal_tool, "_get_cal", return_value=mock_cal), \
             patch.object(gcal_tool, "_get_or_create_cal", return_value=mock_cal):
            result = await gcal_tool.execute(
                parameters={
                    "action": "get_free_slots",
                    "time_min": "2026-04-27T00:00:00",
                    "time_max": "2026-04-28T00:00:00",
                    "duration": 30,
                },
                context=tool_context,
            )
        assert result["status"] == "success"
        assert result["availability_mode"] == "freebusy", \
            "Absent free_prefix in config must switch to freebusy mode (canonical rule)"

    @pytest.mark.asyncio
    async def test_empty_string_config_prefix_switches_to_freebusy_mode(
        self, gcal_tool, tool_context
    ):
        """Operator clears the prefix field in the UI (free_prefix=''): per
        the canonical mode-selection rule, blank or absent free_prefix
        switches the tool to free/busy mode (Google's native API + working
        hours mask), regardless of any free_prefix value the LLM passes.

        Earlier behavior (and earlier name of this test) treated blank as
        "use backend default Open" which silently kept title-prefix mode
        active — that was the bug that made operators wonder why their UI
        change didn't take effect."""
        tool_context.get_config_value = Mock(
            return_value={
                "enabled": True,
                "credentials_path": "/fake/creds.json",
                "calendar_id": "primary",
                "timezone": "America/Los_Angeles",
                "free_prefix": "",  # explicitly cleared by operator
                "busy_prefix": "",
            }
        )
        # In freebusy mode, list_events isn't called — freebusy_query is.
        mock_cal = MagicMock()
        mock_cal.service = MagicMock()
        mock_cal.freebusy_query = Mock(return_value=[])  # nothing busy
        mock_cal.list_events = Mock(side_effect=AssertionError(
            "list_events must NOT be called in freebusy mode"
        ))
        with patch.object(gcal_tool, "_get_cal", return_value=mock_cal), \
             patch.object(gcal_tool, "_get_or_create_cal", return_value=mock_cal):
            result = await gcal_tool.execute(
                parameters={
                    "action": "get_free_slots",
                    "time_min": "2026-04-27T00:00:00",
                    "time_max": "2026-04-28T00:00:00",
                    "duration": 30,
                    # Even with LLM trying to force title-prefix mode, the
                    # operator's blank config wins.
                    "free_prefix": "Open",
                },
                context=tool_context,
            )
        assert result["status"] == "success"
        assert result["availability_mode"] == "freebusy", \
            "Blank operator free_prefix must switch to freebusy mode regardless of LLM-supplied value"
        assert result["reason"] == "available"
        assert len(result["slots"]) > 0


class TestCalendarClientCacheKey:
    """Regression test for Codex feedback on Phase 1 (DWD): the GCalendar
    instance cache MUST include `subject` in its key.

    Without this, switching impersonation targets — e.g. one calendar
    impersonates user_a@dom.com while another impersonates user_b@dom.com,
    both using the same SA + same target calendar_id — would silently reuse
    the FIRST cached client (whichever subject was set up first), and the
    second calendar would act as the wrong user. That's a security/correctness
    bug, not just performance: every API call would target the wrong user's
    calendar with no error visible.

    These tests pin the cache key shape so a future refactor can't quietly
    drop `subject` without breaking them."""

    def test_cache_key_distinguishes_subjects(self):
        """Same (path, cal_id, tz) but different subjects = different cache entries."""
        from unittest.mock import patch
        from src.tools.business.gcal_tool import GCalendarTool
        tool = GCalendarTool()

        constructed = []

        class FakeGCal:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.service = object()  # truthy so cache treats as live
                constructed.append(kwargs)

        with patch("src.tools.business.gcal_tool.GCalendar", FakeGCal):
            a = tool._get_or_create_cal(
                "/fake/sa.json", "primary", "UTC", subject="alice@dom.com"
            )
            b = tool._get_or_create_cal(
                "/fake/sa.json", "primary", "UTC", subject="bob@dom.com"
            )
            # Different subjects must yield two separate GCalendar instances —
            # NOT a cache hit reusing the first one.
            assert a is not b
            assert len(constructed) == 2
            assert constructed[0]["subject"] == "alice@dom.com"
            assert constructed[1]["subject"] == "bob@dom.com"

    def test_cache_key_reuses_same_subject(self):
        """Same (path, cal_id, tz, subject) = single cached instance reused."""
        from unittest.mock import patch
        from src.tools.business.gcal_tool import GCalendarTool
        tool = GCalendarTool()

        constructed = []

        class FakeGCal:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.service = object()
                constructed.append(kwargs)

        with patch("src.tools.business.gcal_tool.GCalendar", FakeGCal):
            a = tool._get_or_create_cal("/fake/sa.json", "primary", "UTC", subject="alice@dom.com")
            b = tool._get_or_create_cal("/fake/sa.json", "primary", "UTC", subject="alice@dom.com")
            assert a is b
            assert len(constructed) == 1

    def test_cache_key_no_subject_doesnt_collide_with_subject(self):
        """Empty subject (no DWD) and any non-empty subject must be different keys."""
        from unittest.mock import patch
        from src.tools.business.gcal_tool import GCalendarTool
        tool = GCalendarTool()

        constructed = []

        class FakeGCal:
            def __init__(self, **kwargs):
                self.kwargs = kwargs
                self.service = object()
                constructed.append(kwargs)

        with patch("src.tools.business.gcal_tool.GCalendar", FakeGCal):
            a = tool._get_or_create_cal("/fake/sa.json", "primary", "UTC")  # default subject=""
            b = tool._get_or_create_cal("/fake/sa.json", "primary", "UTC", subject="alice@dom.com")
            assert a is not b
            assert len(constructed) == 2
            assert constructed[0]["subject"] == ""
            assert constructed[1]["subject"] == "alice@dom.com"


class TestRoundTwoToFiveFixes:
    """Regression tests for the round-2 → round-5 calendar-improvements fixes
    surfaced by live voiprnd test calls.

    Each test pins behavior that, if quietly broken by a future refactor, would
    re-introduce a real bug we already paid for in test calls.
    """

    @pytest.fixture
    def gcal_tool(self):
        return GCalendarTool()

    @pytest.fixture
    def gcal_enabled_context(self, tool_context):
        # Note: free_prefix omitted — let each test set it via the config dict
        # if it wants title-prefix mode.
        tool_context.get_config_value = Mock(
            return_value={
                "enabled": True,
                "credentials_path": "/fake/creds.json",
                "calendar_id": "primary",
                "timezone": "America/Los_Angeles",
                "free_prefix": "Open",
                "busy_prefix": "Busy",
            }
        )
        return tool_context

    def _make_event(self, summary, start, end):
        return {
            "summary": summary,
            "start": {"dateTime": start},
            "end": {"dateTime": end},
        }

    def _mock_cal_with_events(self, events):
        mock_cal = MagicMock()
        mock_cal.service = MagicMock()
        mock_cal.list_events = Mock(return_value=events)
        return mock_cal

    @pytest.mark.asyncio
    async def test_max_slots_returned_caps_slots_with_truncation_flag(
        self, gcal_tool, tool_context
    ):
        """A wide Open window with many candidate slots is capped to
        max_slots_returned (default 3), the response sets slots_truncated=True,
        total_slots_available reflects the uncapped count, and the message
        includes the truncation nudge to the LLM.

        Without this cap, get_free_slots over a multi-day window can return
        20-50+ slot starts; the LLM was observed reading the entire list aloud
        verbatim (≈16 syllables per ISO timestamp → minutes of monologue).
        """
        tool_context.get_config_value = Mock(
            return_value={
                "enabled": True,
                "credentials_path": "/fake/creds.json",
                "calendar_id": "primary",
                "timezone": "America/Los_Angeles",
                "free_prefix": "Open",
                "busy_prefix": "Busy",
            }
        )
        # 8-hour Open window → 16 slots at 30-min duration
        events = [
            self._make_event(
                "Open business hours",
                "2026-04-29T09:00:00-07:00",
                "2026-04-29T17:00:00-07:00",
            ),
        ]
        mock_cal = self._mock_cal_with_events(events)
        with patch.object(gcal_tool, "_get_cal", return_value=mock_cal), \
             patch.object(gcal_tool, "_get_or_create_cal", return_value=mock_cal):
            result = await gcal_tool.execute(
                parameters={
                    "action": "get_free_slots",
                    "time_min": "2026-04-29T00:00:00",
                    "time_max": "2026-04-30T00:00:00",
                    "duration": 30,
                },
                context=tool_context,
            )
        assert result["status"] == "success"
        assert result["reason"] == "available"
        assert len(result["slots"]) == 3, "Default cap is 3 slots"
        assert result["slots_truncated"] is True
        assert result["total_slots_available"] == 16, "Uncapped count surfaced"
        assert "showing 3 of 16" in result["message"]
        assert "do not read the full list" in result["message"]

    @pytest.mark.asyncio
    async def test_max_slots_returned_zero_disables_cap(self, gcal_tool, tool_context):
        """Setting max_slots_returned: 0 disables the cap (back-compat for
        operators who want all slots returned, e.g. for a custom UI that
        renders them as buttons).
        """
        tool_context.get_config_value = Mock(
            return_value={
                "enabled": True,
                "credentials_path": "/fake/creds.json",
                "calendar_id": "primary",
                "timezone": "America/Los_Angeles",
                "free_prefix": "Open",
                "busy_prefix": "Busy",
                "max_slots_returned": 0,
            }
        )
        events = [
            self._make_event(
                "Open business hours",
                "2026-04-29T09:00:00-07:00",
                "2026-04-29T17:00:00-07:00",
            ),
        ]
        mock_cal = self._mock_cal_with_events(events)
        with patch.object(gcal_tool, "_get_cal", return_value=mock_cal), \
             patch.object(gcal_tool, "_get_or_create_cal", return_value=mock_cal):
            result = await gcal_tool.execute(
                parameters={
                    "action": "get_free_slots",
                    "time_min": "2026-04-29T00:00:00",
                    "time_max": "2026-04-30T00:00:00",
                    "duration": 30,
                },
                context=tool_context,
            )
        assert result["status"] == "success"
        assert len(result["slots"]) == 16, "All slots returned when cap is 0"
        assert result["slots_truncated"] is False

    @pytest.mark.asyncio
    async def test_message_starts_with_legacy_free_slot_starts_prefix(
        self, gcal_tool, tool_context
    ):
        """User prompt templates that pattern-match on the literal string
        'Free slot starts:' must keep working post-PR. The new message format
        adds duration+TZ guidance but preserves the legacy prefix as the
        opening token.
        """
        tool_context.get_config_value = Mock(
            return_value={
                "enabled": True,
                "credentials_path": "/fake/creds.json",
                "calendar_id": "primary",
                "timezone": "America/Los_Angeles",
                "free_prefix": "Open",
                "busy_prefix": "Busy",
            }
        )
        events = [
            self._make_event(
                "Open hours",
                "2026-04-29T09:00:00-07:00",
                "2026-04-29T11:00:00-07:00",
            ),
        ]
        mock_cal = self._mock_cal_with_events(events)
        with patch.object(gcal_tool, "_get_cal", return_value=mock_cal), \
             patch.object(gcal_tool, "_get_or_create_cal", return_value=mock_cal):
            result = await gcal_tool.execute(
                parameters={
                    "action": "get_free_slots",
                    "time_min": "2026-04-29T00:00:00",
                    "time_max": "2026-04-30T00:00:00",
                    "duration": 30,
                },
                context=tool_context,
            )
        assert result["status"] == "success"
        assert result["message"].startswith("Free slot starts:"), \
            "Legacy 'Free slot starts:' prefix must be preserved for prompt-template back-compat"

    @pytest.mark.asyncio
    async def test_operator_blank_free_prefix_overrides_llm_supplied_value(
        self, gcal_tool, tool_context
    ):
        """Real bug from voiprnd round-2.5: operator cleared Free prefix in the
        Tools UI (saved as free_prefix: ''), but Gemini auto-filled
        free_prefix='Open' from the schema example on every call —
        LLM-supplied per-call value won over the (correctly) blank config.

        Operator's deliberate choice (blank string in config) must override
        the LLM. Distinguishes 'operator chose free/busy' (key present + blank)
        from 'operator never configured' (key absent).
        """
        # Config has free_prefix explicitly set to empty string — this is the
        # signal that the operator cleared the field in the UI.
        tool_context.get_config_value = Mock(
            return_value={
                "enabled": True,
                "credentials_path": "/fake/creds.json",
                "calendar_id": "primary",
                "timezone": "America/Los_Angeles",
                "free_prefix": "",  # ← operator deliberately cleared
                "busy_prefix": "Busy",
            }
        )
        # Mock the freebusy_query path used in freebusy mode
        mock_cal = MagicMock()
        mock_cal.service = MagicMock()
        mock_cal.freebusy_query = Mock(return_value=[])  # nothing busy
        # No list_events should be called in freebusy mode — but mock anyway
        # in case the code falls through to title_prefix
        mock_cal.list_events = Mock(return_value=[])
        with patch.object(gcal_tool, "_get_cal", return_value=mock_cal), \
             patch.object(gcal_tool, "_get_or_create_cal", return_value=mock_cal):
            result = await gcal_tool.execute(
                parameters={
                    "action": "get_free_slots",
                    "time_min": "2026-04-27T00:00:00",
                    "time_max": "2026-04-28T00:00:00",
                    "duration": 30,
                    # LLM tries to force title-prefix mode — should be ignored
                    "free_prefix": "Open",
                },
                context=tool_context,
            )
        assert result["status"] == "success"
        assert result["availability_mode"] == "freebusy", \
            "Operator's blank free_prefix must override LLM-supplied 'Open'"

    @pytest.mark.asyncio
    async def test_create_event_rejects_duration_over_max(
        self, gcal_tool, gcal_enabled_context
    ):
        """The duration guard refuses bookings > max_event_duration_minutes
        with a fail-fast error code that the LLM can recognize and retry.

        Real bug: ElevenLabs/Claude was observed booking 7-hour meetings (11:00
        → 18:00) when the schema didn't constrain end_datetime. The guard kicks
        in before the API call, returns error_code='duration_too_long', and
        the tool description tells the model how to recover.
        """
        mock_cal = MagicMock()
        mock_cal.service = MagicMock()
        mock_cal.create_event = Mock(side_effect=AssertionError("Should not reach API"))
        with patch.object(gcal_tool, "_get_cal", return_value=mock_cal), \
             patch.object(gcal_tool, "_get_or_create_cal", return_value=mock_cal):
            result = await gcal_tool.execute(
                parameters={
                    "action": "create_event",
                    "summary": "Long meeting",
                    "start_datetime": "2026-04-27T11:00:00",
                    "end_datetime": "2026-04-27T18:00:00",  # 7 hours
                },
                context=gcal_enabled_context,
            )
        assert result["status"] == "error"
        assert result["error_code"] == "duration_too_long"
        # The error message must tell the LLM how to retry, not just complain
        assert "Retry" in result["message"] or "retry" in result["message"]

    @pytest.mark.asyncio
    async def test_create_event_rejects_non_positive_duration(
        self, gcal_tool, gcal_enabled_context
    ):
        """end_datetime <= start_datetime is an LLM bug — fail fast with a
        distinct error code so the model self-corrects.
        """
        mock_cal = MagicMock()
        mock_cal.service = MagicMock()
        mock_cal.create_event = Mock(side_effect=AssertionError("Should not reach API"))
        with patch.object(gcal_tool, "_get_cal", return_value=mock_cal), \
             patch.object(gcal_tool, "_get_or_create_cal", return_value=mock_cal):
            result = await gcal_tool.execute(
                parameters={
                    "action": "create_event",
                    "summary": "Backwards meeting",
                    "start_datetime": "2026-04-27T11:00:00",
                    "end_datetime": "2026-04-27T11:00:00",  # zero duration
                },
                context=gcal_enabled_context,
            )
        assert result["status"] == "error"
        assert result["error_code"] == "invalid_duration"

    @pytest.mark.asyncio
    async def test_create_event_success_response_carries_event_id_for_delete_flow(
        self, gcal_tool, gcal_enabled_context
    ):
        """Real bug: Gemini hallucinated event_ids for delete_event calls
        because the prior 'Event created.' message gave no anchor for the
        actual id.

        Round-5 first-cut fix put the id + delete-guidance in the `message`
        field, but live testing on round-7 surfaced a UX regression: most
        providers (OpenAI Realtime, ElevenLabs) read `message` verbatim to
        the caller, so the caller heard robotic developer scaffolding
        ('event_id ce8f8153va8n0sdfspseb5e5lc... do not invent or guess one').

        Round-8 final shape:
        - `message` is short and human-friendly: "Event created."
        - `agent_hint` carries the structured guidance (event_id +
          delete-then-recreate rule) for the model to consume but NOT
          read aloud.
        - `id` and `event_id` fields remain available verbatim for tool
          arg passing.

        This test pins all three: clean message, agent_hint with the
        guidance, and id/event_id fields still set.
        """
        mock_cal = MagicMock()
        mock_cal.service = MagicMock()
        mock_cal.create_event = Mock(return_value={
            "id": "real_event_id_xyz",
            "htmlLink": "https://calendar.google.com/...",
        })
        with patch.object(gcal_tool, "_get_cal", return_value=mock_cal), \
             patch.object(gcal_tool, "_get_or_create_cal", return_value=mock_cal):
            result = await gcal_tool.execute(
                parameters={
                    "action": "create_event",
                    "summary": "Test meeting",
                    "start_datetime": "2026-04-27T11:00:00",
                    "end_datetime": "2026-04-27T11:30:00",
                },
                context=gcal_enabled_context,
            )
        assert result["status"] == "success"
        # Structured fields for tool args
        assert result["id"] == "real_event_id_xyz"
        assert result["event_id"] == "real_event_id_xyz"
        # Human-friendly message stays short — does NOT include the id
        # (which would be read aloud) or developer scaffolding text.
        assert result["message"] == "Event created."
        assert "real_event_id_xyz" not in result["message"], \
            "event_id must NOT appear in message field (model reads it aloud); use agent_hint instead"
        assert "do not invent" not in result["message"].lower()
        # agent_hint carries the deletion guidance for the model
        assert "agent_hint" in result, "agent_hint field must be present for delete-then-recreate flow"
        assert "real_event_id_xyz" in result["agent_hint"]
        # The hint must coach the LLM not to invent ids
        assert ("do not invent" in result["agent_hint"].lower()
                or "do not guess" in result["agent_hint"].lower()), \
            "agent_hint must explicitly tell the model not to fabricate event_ids"

    @pytest.mark.asyncio
    async def test_delete_event_fallback_uses_tracked_id_on_404(
        self, gcal_tool, gcal_enabled_context
    ):
        """Server-side fallback when the LLM hallucinates an event_id.

        Flow:
        1. Successful create_event tracks event_id 'real_id_abc' for this call_id
        2. delete_event called with bogus 'fake_id_xyz' → first delete returns False (404)
        3. Tool falls back to tracked id, second delete succeeds
        4. Tool returns success with message explaining the recovery
        5. Tracking entry is cleared so future delete in same call doesn't re-fallback
        """
        mock_cal = MagicMock()
        mock_cal.service = MagicMock()
        # First create succeeds and seeds the tracking
        mock_cal.create_event = Mock(return_value={
            "id": "real_id_abc",
            "htmlLink": "https://calendar.google.com/...",
        })
        # First delete (with bogus id) returns False; second (with real tracked id) returns True
        mock_cal.delete_event = Mock(side_effect=[False, True])

        with patch.object(gcal_tool, "_get_cal", return_value=mock_cal), \
             patch.object(gcal_tool, "_get_or_create_cal", return_value=mock_cal):
            # Step 1 — create
            create_result = await gcal_tool.execute(
                parameters={
                    "action": "create_event",
                    "summary": "Original booking",
                    "start_datetime": "2026-04-27T11:00:00",
                    "end_datetime": "2026-04-27T11:30:00",
                },
                context=gcal_enabled_context,
            )
            assert create_result["status"] == "success"
            assert create_result["id"] == "real_id_abc"

            # Step 2 — delete with hallucinated id; expect fallback
            delete_result = await gcal_tool.execute(
                parameters={
                    "action": "delete_event",
                    "event_id": "fake_id_xyz",  # not a real id
                },
                context=gcal_enabled_context,
            )

        assert delete_result["status"] == "success", \
            "Fallback should turn 404 into success when we have a tracked id"
        assert delete_result["event_id"] == "real_id_abc", \
            "Fallback must report the id that was actually deleted"
        # The recovery message must explain what happened so the model learns
        assert "fake_id_xyz" in delete_result["message"]
        assert "real_id_abc" in delete_result["message"]
        # Two delete attempts: first failed (hallucinated id), second succeeded (tracked)
        assert mock_cal.delete_event.call_count == 2
        # Cleanup invariant: the tracked entry MUST be cleared after a
        # successful fallback, so a subsequent hallucinated delete_event
        # in the same call doesn't double-fallback onto the same id.
        # Without this, an LLM that keeps making up event_ids could keep
        # "succeeding" against the same already-deleted real id.
        assert gcal_enabled_context.call_id not in gcal_tool._last_event_per_call, \
            "Tracked event_id must be cleared after successful fallback delete"

    @pytest.mark.asyncio
    async def test_invalid_working_hours_falls_back_to_defaults(
        self, gcal_tool, tool_context
    ):
        """Operator typo like working_hours_start=25 used to crash deep in
        _working_hours_mask with an unhelpful ValueError. Now coerced to the
        default with a warning so the failure mode is predictable."""
        tool_context.get_config_value = Mock(
            return_value={
                "enabled": True,
                "credentials_path": "/fake/creds.json",
                "calendar_id": "primary",
                "timezone": "America/Los_Angeles",
                # Out-of-range / invalid working hours
                "working_hours_start": 25,        # invalid (>24)
                "working_hours_end": "potato",   # invalid type
                "working_days": [9, "x", 7],     # invalid (>6) and non-int
            }
        )
        mock_cal = MagicMock()
        mock_cal.service = MagicMock()
        mock_cal.freebusy_query = Mock(return_value=[])
        with patch.object(gcal_tool, "_get_cal", return_value=mock_cal), \
             patch.object(gcal_tool, "_get_or_create_cal", return_value=mock_cal):
            # Must not raise — the coercion should normalize invalid values
            # to defaults and continue rather than crashing.
            result = await gcal_tool.execute(
                parameters={
                    "action": "get_free_slots",
                    "time_min": "2026-04-27T00:00:00",
                    "time_max": "2026-04-28T00:00:00",
                    "duration": 30,
                },
                context=tool_context,
            )
        assert result["status"] == "success"
        # Default working hours are 9-17 Mon-Fri, so a Mon-Tue range with
        # nothing busy should produce slots.
        assert result["reason"] == "available"

    @pytest.mark.asyncio
    async def test_last_event_cache_bounded_by_lru_eviction(
        self, gcal_tool, gcal_enabled_context
    ):
        """Codex P2 feedback: _last_event_per_call adds an entry on every
        successful create_event but only removes its own on delete_event,
        so the typical 'book → hang up without correction' flow leaks an
        entry per call indefinitely.

        Pin: the cache is bounded at _LAST_EVENT_CACHE_CAP via LRU eviction.
        Without the bound a long-running engine accumulates unbounded state.
        """
        # Lower the cap for the test so we don't have to issue 1024+ creates
        original_cap = gcal_tool._LAST_EVENT_CACHE_CAP
        gcal_tool._LAST_EVENT_CACHE_CAP = 5
        try:
            mock_cal = MagicMock()
            mock_cal.service = MagicMock()
            # Each create returns a unique event id
            mock_cal.create_event = Mock(side_effect=[
                {"id": f"evt_{i}", "htmlLink": "..."} for i in range(10)
            ])
            with patch.object(gcal_tool, "_get_cal", return_value=mock_cal), \
                 patch.object(gcal_tool, "_get_or_create_cal", return_value=mock_cal):
                # 10 distinct call_ids → 10 successful creates.
                # The cache should hold only the most recent 5 (cap=5).
                for i in range(10):
                    gcal_enabled_context.call_id = f"call_{i:02d}"
                    result = await gcal_tool.execute(
                        parameters={
                            "action": "create_event",
                            "summary": f"Booking {i}",
                            "start_datetime": "2026-04-27T11:00:00",
                            "end_datetime": "2026-04-27T11:30:00",
                        },
                        context=gcal_enabled_context,
                    )
                    assert result["status"] == "success"
            # After 10 creates with cap=5, only the most recent 5 entries
            # should remain. The oldest 5 (call_00..call_04) should be evicted.
            assert len(gcal_tool._last_event_per_call) == 5
            assert "call_00" not in gcal_tool._last_event_per_call
            assert "call_04" not in gcal_tool._last_event_per_call
            assert "call_05" in gcal_tool._last_event_per_call
            assert "call_09" in gcal_tool._last_event_per_call
        finally:
            gcal_tool._LAST_EVENT_CACHE_CAP = original_cap

    @pytest.mark.asyncio
    async def test_last_event_cache_lru_refresh_on_re_create(
        self, gcal_tool, gcal_enabled_context
    ):
        """When the same call_id books again (e.g. delete-then-recreate flow),
        its LRU position must be refreshed so it isn't evicted while still
        actively in use. Confirmed by re-creating an early call_id and then
        triggering eviction — the refreshed call_id should survive."""
        original_cap = gcal_tool._LAST_EVENT_CACHE_CAP
        gcal_tool._LAST_EVENT_CACHE_CAP = 3
        try:
            mock_cal = MagicMock()
            mock_cal.service = MagicMock()
            mock_cal.create_event = Mock(side_effect=[
                {"id": f"evt_{i}", "htmlLink": "..."} for i in range(6)
            ])
            with patch.object(gcal_tool, "_get_cal", return_value=mock_cal), \
                 patch.object(gcal_tool, "_get_or_create_cal", return_value=mock_cal):
                # Three distinct call_ids — fills the cache exactly
                for i in range(3):
                    gcal_enabled_context.call_id = f"call_{i}"
                    await gcal_tool.execute(
                        parameters={
                            "action": "create_event", "summary": "x",
                            "start_datetime": "2026-04-27T11:00:00",
                            "end_datetime": "2026-04-27T11:30:00",
                        },
                        context=gcal_enabled_context,
                    )
                # Re-create on call_0 — this should move it to the most-recent slot
                gcal_enabled_context.call_id = "call_0"
                await gcal_tool.execute(
                    parameters={
                        "action": "create_event", "summary": "x2",
                        "start_datetime": "2026-04-27T12:00:00",
                        "end_datetime": "2026-04-27T12:30:00",
                    },
                    context=gcal_enabled_context,
                )
                # Now add 2 more — that's 5 distinct creates total but cap=3.
                # call_1 is the oldest after the refresh, then call_2.
                # call_3 and call_4 are added — call_1 and call_2 evicted,
                # call_0 (refreshed) survives.
                for i in range(3, 5):
                    gcal_enabled_context.call_id = f"call_{i}"
                    await gcal_tool.execute(
                        parameters={
                            "action": "create_event", "summary": "x",
                            "start_datetime": "2026-04-27T11:00:00",
                            "end_datetime": "2026-04-27T11:30:00",
                        },
                        context=gcal_enabled_context,
                    )
            # call_0 was refreshed mid-stream so it must still be present
            assert "call_0" in gcal_tool._last_event_per_call, \
                "Re-creating on an existing call_id must refresh its LRU position"
            # call_1 was the oldest after refresh; should be evicted
            assert "call_1" not in gcal_tool._last_event_per_call
        finally:
            gcal_tool._LAST_EVENT_CACHE_CAP = original_cap

    @pytest.mark.asyncio
    async def test_delete_event_no_fallback_when_no_tracked_id(
        self, gcal_tool, gcal_enabled_context
    ):
        """If the LLM calls delete_event for an unknown id BEFORE any
        successful create_event in this call (e.g. caller asked to delete a
        booking from a different session), the fallback has nothing to use
        and the error must propagate normally — never silently delete some
        unrelated event.
        """
        mock_cal = MagicMock()
        mock_cal.service = MagicMock()
        mock_cal.delete_event = Mock(return_value=False)

        with patch.object(gcal_tool, "_get_cal", return_value=mock_cal), \
             patch.object(gcal_tool, "_get_or_create_cal", return_value=mock_cal):
            result = await gcal_tool.execute(
                parameters={
                    "action": "delete_event",
                    "event_id": "fake_id_xyz",
                },
                context=gcal_enabled_context,
            )
        assert result["status"] == "error"
        # Single delete attempt (no fallback) since no tracked id existed
        assert mock_cal.delete_event.call_count == 1
