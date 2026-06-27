import pytest

from src.live_status_publisher import (
    LiveStatusPublisher,
    LiveStatusPublisherConfig,
    live_status_component,
)


def test_live_status_component_defaults_are_stable():
    component = live_status_component(state="ready", summary="ok")

    assert component == {
        "state": "ready",
        "summary": "ok",
        "details": {},
        "metrics": {},
        "warnings": [],
        "errors": [],
    }


@pytest.mark.asyncio
async def test_live_status_publisher_disabled_without_token():
    publisher = LiveStatusPublisher(
        LiveStatusPublisherConfig(source="ai_engine", admin_url="http://admin", token="")
    )

    assert publisher.enabled is False
    assert publisher.publish_now({"ai_engine": live_status_component(state="ready", summary="ok")}) is None
    assert await publisher.publish({"ai_engine": live_status_component(state="ready", summary="ok")}) is False


@pytest.mark.asyncio
async def test_live_status_publisher_posts_expected_contract():
    calls = []

    def fake_post(endpoint, payload, token, timeout):
        calls.append({"endpoint": endpoint, "payload": payload, "token": token, "timeout": timeout})

    publisher = LiveStatusPublisher(
        LiveStatusPublisherConfig(
            source="ai_engine",
            admin_url="http://admin.local:3003",
            token="secret",
            timeout_seconds=1.5,
        ),
        post=fake_post,
    )

    ok = await publisher.publish(
        {
            "ai_engine": live_status_component(
                state="ready",
                summary="AI Engine ready",
                details={"ari_connected": True},
            )
        }
    )

    assert ok is True
    assert calls == [
        {
            "endpoint": "http://admin.local:3003/api/system/live-status/publish",
            "payload": {
                "source": "ai_engine",
                "components": {
                    "ai_engine": {
                        "state": "ready",
                        "summary": "AI Engine ready",
                        "details": {"ari_connected": True},
                        "metrics": {},
                        "warnings": [],
                        "errors": [],
                    }
                },
            },
            "token": "secret",
            "timeout": 1.5,
        }
    ]


@pytest.mark.asyncio
async def test_live_status_publisher_failure_is_best_effort():
    def fake_post(*_args):
        raise RuntimeError("admin down")

    publisher = LiveStatusPublisher(
        LiveStatusPublisherConfig(source="ai_engine", admin_url="http://admin", token="secret"),
        post=fake_post,
    )

    ok = await publisher.publish({"ai_engine": live_status_component(state="ready", summary="ok")})

    assert ok is False
