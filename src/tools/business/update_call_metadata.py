"""Provider-independent correction of selected call enrichment metadata."""

from __future__ import annotations

from typing import Any, Dict, Mapping

from src.tools.base import Tool, ToolCategory, ToolDefinition, ToolParameter
from src.tools.context import ToolExecutionContext


class UpdateCallMetadataTool(Tool):
    """Update only call-local fields selected and marked correctable by an operator."""

    def __init__(self, field_policies: Mapping[str, Mapping[str, Any]] | None = None):
        self._field_policies = {
            str(key): dict(policy)
            for key, policy in (field_policies or {}).items()
            if isinstance(policy, Mapping) and bool(policy.get("correctable", False))
        }

    @property
    def definition(self) -> ToolDefinition:
        field_names = sorted(self._field_policies)
        descriptions = [
            f"{name}: {self._field_policies[name].get('description')}"
            for name in field_names
            if self._field_policies[name].get("description")
        ]
        description = (
            "Correct one operator-approved, non-authoritative metadata value for this call. "
            "This does not update a CRM, caller identity, routing, consent, transfer, or disposition."
        )
        if descriptions:
            description += " Allowed fields: " + "; ".join(descriptions)
        return ToolDefinition(
            name="update_call_metadata",
            description=description,
            category=ToolCategory.BUSINESS,
            parameters=[
                ToolParameter(
                    name="field",
                    type="string",
                    description="The metadata field to correct.",
                    required=True,
                    enum=field_names,
                ),
                ToolParameter(
                    name="value",
                    type="string",
                    description="The caller-confirmed replacement value.",
                    required=True,
                ),
            ],
        )

    async def execute(
        self,
        parameters: Dict[str, Any],
        context: ToolExecutionContext,
    ) -> Dict[str, Any]:
        if not context.session_store:
            return {"status": "error", "message": "Call state is unavailable."}
        field = str(parameters.get("field") or "").strip()
        if field not in self._field_policies:
            return {"status": "error", "message": f"'{field}' is not an allowed correctable field."}
        if "value" not in parameters:
            return {"status": "error", "message": "A replacement value is required."}
        return await context.session_store.update_call_metadata(
            context.call_id,
            field,
            parameters.get("value"),
        )
