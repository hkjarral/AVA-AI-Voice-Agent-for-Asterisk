import os
import sys
from typing import Any, Dict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import settings

if settings.PROJECT_ROOT not in sys.path:
    sys.path.insert(0, settings.PROJECT_ROOT)

router = APIRouter()


class VicidialTestRequest(BaseModel):
    config: Dict[str, Any]


def _expand_env_refs(value: Any, env_map: Dict[str, str]) -> Any:
    from src.tools.telephony.vicidial import resolve_env_reference

    if isinstance(value, str):
        return resolve_env_reference(value, env_map)
    if isinstance(value, dict):
        return {k: _expand_env_refs(v, env_map) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env_refs(v, env_map) for v in value]
    return value


@router.post("/vicidial/test")
async def test_vicidial_connection(req: VicidialTestRequest):
    try:
        from dotenv import dotenv_values
        from src.tools.telephony.vicidial import VicidialAgentApiClient, validate_vicidial_config

        dotenv_map = dotenv_values(settings.ENV_PATH) if os.path.exists(settings.ENV_PATH) else {}
        env_map = {str(k): str(v or "") for k, v in dotenv_map.items()}
        env_map.update({k: v for k, v in os.environ.items() if isinstance(v, str)})

        integrations = {"integrations": {"vicidial": _expand_env_refs(req.config or {}, env_map)}}
        validate_vicidial_config(integrations)
        result = await VicidialAgentApiClient(integrations).test_connection()
        if not result.success:
            raise HTTPException(status_code=400, detail=result.message)
        return {"status": "success", "message": result.message, "raw": result.raw}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
