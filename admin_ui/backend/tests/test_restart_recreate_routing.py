"""MED-R2: a restart with recreate=true must force-recreate the container so
env_file (.env) changes are re-read, instead of a plain Docker-SDK restart that
does not reload .env."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

pytest.importorskip("fastapi")

from api import system  # noqa: E402


@pytest.mark.asyncio
async def test_recreate_true_routes_to_force_recreate() -> None:
    """recreate=True for local_ai_server uses compose force-recreate, not a plain restart."""
    sentinel = {"status": "success", "method": "compose-recreate", "output": "Service recreated"}
    with patch.object(
        system, "_recreate_via_compose", new=AsyncMock(return_value=sentinel)
    ) as mock_recreate, patch.object(system, "docker") as mock_docker:
        result = await system.restart_container("local_ai_server", recreate=True)

    assert result is sentinel
    mock_recreate.assert_awaited_once_with("local_ai_server", health_check=True)
    # A plain Docker-SDK restart must NOT happen when recreating.
    mock_docker.from_env.assert_not_called()


@pytest.mark.asyncio
async def test_recreate_false_does_not_force_recreate() -> None:
    """A plain restart (recreate=False) does not invoke compose force-recreate."""
    fake_client = MagicMock()
    with patch.object(
        system, "_recreate_via_compose", new=AsyncMock()
    ) as mock_recreate, patch.object(system, "docker") as mock_docker:
        mock_docker.from_env.return_value = fake_client
        result = await system.restart_container("local_ai_server", recreate=False)

    assert result["status"] == "success"
    mock_recreate.assert_not_called()
    fake_client.containers.get.assert_called_once_with("local_ai_server")
