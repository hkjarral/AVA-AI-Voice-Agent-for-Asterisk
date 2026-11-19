import pytest


@pytest.mark.unit
def test_tool_registry_filtered_schema():
    from src.tools.base import Tool, ToolCategory, ToolDefinition
    from src.tools.registry import tool_registry

    class ToolA(Tool):
        @property
        def definition(self) -> ToolDefinition:
            return ToolDefinition(
                name="tool_a",
                description="A",
                category=ToolCategory.BUSINESS,
            )

        async def execute(self, parameters, context):
            return {"status": "success"}

    class ToolB(Tool):
        @property
        def definition(self) -> ToolDefinition:
            return ToolDefinition(
                name="tool_b",
                description="B",
                category=ToolCategory.BUSINESS,
            )

        async def execute(self, parameters, context):
            return {"status": "success"}

    tool_registry.clear()
    tool_registry.register(ToolA)
    tool_registry.register(ToolB)

    schemas = tool_registry.to_openai_realtime_schema_filtered(["tool_b"])
    assert len(schemas) == 1
    assert schemas[0]["name"] == "tool_b"

    tool_registry.clear()



def test_optional_calendly_import_failure_does_not_abort_telephony_registration(monkeypatch):
    import builtins
    from src.tools.registry import ToolRegistry
    original = builtins.__import__
    def unavailable(name, *args, **kwargs):
        if name == 'src.tools.business.calendly_calendar':
            raise ImportError('synthetic optional dependency failure')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', unavailable)
    registry = ToolRegistry.isolated()
    registry.initialize_default_tools()
    assert registry._initialized
    assert registry.get('hangup_call') is not None
    assert registry.get('calendly_calendar') is None
