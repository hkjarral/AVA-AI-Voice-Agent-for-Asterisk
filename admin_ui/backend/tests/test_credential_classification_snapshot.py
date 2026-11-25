"""Inventory every declared configuration-model field, including dynamic provider schemas."""
import ast
import inspect
import json
from pathlib import Path
import re

from pydantic import BaseModel

from api import tools
from services.secret_keys import is_secret_key
from services.yaml_secrets import field_classification, mask_config
from src.config import _parent_config
from src.integrations import pushover


def classification_snapshot():
    result = {}
    models = {model for module in (_parent_config, pushover)
              for _, model in inspect.getmembers(module, inspect.isclass)
              if issubclass(model, BaseModel) and model is not BaseModel}
    models.update({tools.ManagedToolWrite, tools.ManagedToolPatch,
                   tools.ManagedToolParameter, tools.ManagedCallMetadataField})
    for model in list(models):
        models.update(nested for _, nested in inspect.getmembers(model, inspect.isclass)
                      if issubclass(nested, BaseModel) and nested.__qualname__.startswith(model.__qualname__ + "."))
    def add_field(path, field):
        model = path.rsplit(".", 1)[0]
        location = ("tools", "*", field) if model in {
            "ManagedToolWrite", "ManagedToolPatch", "HTTPLookupConfig", "InCallHTTPConfig", "WebhookConfig", "A2AAgentConfig",
        } else (field,)
        result[path] = field_classification(location)
        if field in {"headers", "extra_headers", "env", "query_params"} or field.endswith("_extra_headers"):
            result[path + ".*"] = "credential by location"

    for model in models:
        for field in model.model_fields:
            add_field(f"{model.__qualname__}.{field}", field)
    # Tool and transport schemas in the open tools/profiles maps also use
    # dataclasses, some with optional runtime SDKs. Read declarations directly.
    repo = Path(__file__).resolve().parents[3]
    for source in sorted((repo / "src").rglob("*.py")):
        for cls in ast.walk(ast.parse(source.read_text())):
            if not isinstance(cls, ast.ClassDef) or not cls.name.endswith(("Config", "Settings", "Profile")):
                continue
            decorators = [ast.unparse(decorator).split("(")[0].split(".")[-1] for decorator in cls.decorator_list]
            if "dataclass" in decorators:
                for field in cls.body:
                    if isinstance(field, ast.AnnAssign) and isinstance(field.target, ast.Name):
                        add_field(f"{cls.name}.{field.target.id}", field.target.id)
    for path in [("tools", "microsoft_calendar", "accounts", "*", "token_cache_path"),
                 ("tools", "leave_voicemail", "default_mailbox_key")]:
        result[".".join(path)] = field_classification(path)
    for field in ("base_url", "ws_url"):
        url = "https://example.invalid/path?region=example"
        for path, document in [
            (field, {field: url}),
            (f"providers.*.{field}", {"providers": {"*": {field: url}}}),
        ]:
            visible = json.dumps(mask_config(document))
            result[path + " (query)"] = "visible query" if "region=example" in visible else "masked query"
    for match in re.finditer(r"^\s*(?:#\s*)?(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=", (repo / ".env.example").read_text(), re.M):
        key = match.group(1)
        result[f".env.{key}"] = "credential" if is_secret_key(key) else "ordinary environment setting"
    return "".join(f"{path}\t{result[path]}\n" for path in sorted(result))


def test_every_configuration_field_has_a_reviewed_classification():
    snapshot = Path(__file__).with_name("credential_classification.tsv")
    assert snapshot.read_text() == classification_snapshot()
