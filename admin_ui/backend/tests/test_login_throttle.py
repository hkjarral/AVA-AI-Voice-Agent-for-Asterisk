import asyncio
import ipaddress
from unittest.mock import AsyncMock
import secrets
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import auth
from services.login_throttle import LoginThrottle


@pytest.fixture
def login(monkeypatch, tmp_path):
    # Use the real lookup, hashing and token routes with disposable credentials.
    username = f"user-{uuid4().hex}"
    password = secrets.token_urlsafe()
    monkeypatch.setattr(auth, "USERS_PATH", str(tmp_path / "users.json"))
    monkeypatch.setattr(auth, "SECRET_KEY", secrets.token_hex(32))
    auth.save_users({
        username: {
            "username": username,
            "hashed_password": auth.get_password_hash(password),
            "must_change_password": True,
        },
    })
    app = FastAPI()
    app.include_router(auth.router, prefix="/api/auth")
    client_host = f"client-{uuid4().hex}"
    wrong_password = secrets.token_urlsafe()

    async def post(name=username, credential=wrong_password, host=client_host, headers=None):
        transport = httpx.ASGITransport(app=app, client=(host, 1))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/auth/login", headers=headers,
                data={"username": name, "password": credential},
            )

    return post, username, password


def test_sliding_window_and_fixed_lockout():
    now = [0.0]
    throttle = LoginThrottle(clock=lambda: now[0])
    key = uuid4().hex
    throttle.record_failure(key)
    now[0] = 899
    for _ in range(3):
        throttle.record_failure(key)
    now[0] = 900
    throttle.record_failure(key)
    assert throttle.retry_after(key) == 0
    throttle.record_failure(key)
    assert throttle.retry_after(key) == 900
    now[0] = 1799.9
    throttle.record_failure(key)
    assert throttle.retry_after(key) == 1
    now[0] = 1800
    assert throttle.retry_after(key) == 0
    throttle.record_failure(key)
    assert throttle.retry_after(key) == 0


def test_capacity_preserves_locks_and_recovers_after_expiry():
    now = [0]
    throttle = LoginThrottle(max_failures=2, capacity=2, clock=lambda: now[0])
    for key in ("first", "second"):
        throttle.record_failure(key)
        throttle.record_failure(key)
    assert throttle.retry_after("new") == 900
    assert throttle.retry_after("first") == 900
    now[0] = 900
    assert throttle.retry_after("new") == 0
    throttle.record_failure("new")
    assert throttle.retry_after("new") == 0


def test_unlocked_entries_can_be_evicted_under_pressure():
    throttle = LoginThrottle(max_failures=2, capacity=2, clock=lambda: 0)
    for key in ("known", "known", "partial", "new"):
        throttle.record_failure(key)
    assert throttle.retry_after("known") == 900
    throttle.record_failure("new")
    assert throttle.retry_after("new") == 900


@pytest.mark.asyncio
async def test_account_lock_is_case_insensitive_and_checked_before_lookup(login, monkeypatch):
    post, username, password = login
    unknown = uuid4().hex
    for name in (username, unknown):
        assert (await post(name.upper())).status_code == 401
        for _ in range(4):
            assert (await post(name)).status_code == 401
    assert (await post(uuid4().hex)).status_code == 401

    monkeypatch.setattr(auth, "get_user", lambda _: pytest.fail("lookup during lockout"))
    known_reply = await post(username, password)
    unknown_reply = await post(unknown)
    assert known_reply.status_code == unknown_reply.status_code == 429
    assert known_reply.json() == unknown_reply.json()
    assert 0 < int(known_reply.headers["Retry-After"]) <= 900


@pytest.mark.asyncio
async def test_success_clears_account_failures(login):
    post, username, password = login
    for _ in range(2):
        for _ in range(4):
            assert (await post()).status_code == 401
        response = await post(username, password)
        assert response.status_code == 200
        assert response.json()["must_change_password"]
        assert response.json()["access_token"]


@pytest.mark.asyncio
async def test_login_succeeds_at_end_of_account_lockout(login, monkeypatch):
    post, username, password = login
    now = [0.0]
    monkeypatch.setattr(auth, "_pair_throttle", LoginThrottle(clock=lambda: now[0]))
    for _ in range(5):
        assert (await post()).status_code == 401
    now[0] = 899.5
    blocked = await post(username, password)
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == "1"
    now[0] = 900
    assert (await post(username, password)).status_code == 200


@pytest.mark.asyncio
async def test_address_limit_counts_failures_across_accounts(login):
    post, username, password = login
    for index in range(30):
        if index == 15:
            assert (await post(username, password)).status_code == 200
        response = await post(uuid4().hex, headers={"X-Forwarded-For": uuid4().hex})
        assert response.status_code == 401
    limited = await post(username, password, headers={"X-Forwarded-For": uuid4().hex})
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0
    assert (await post(username, password, host=uuid4().hex)).status_code == 200


@pytest.mark.asyncio
async def test_concurrent_login_attempts_respect_account_limit(login):
    post, _, _ = login
    responses = await asyncio.gather(*(post() for _ in range(12)))
    statuses = [response.status_code for response in responses]
    assert statuses.count(401) == 5
    assert statuses.count(429) == 7

@pytest.mark.asyncio
async def test_distinct_origins_slow_unfamiliar_logins_but_do_not_lock_an_account(login, monkeypatch):
    post, username, password = login
    wait = AsyncMock()
    monkeypatch.setattr(auth, "_delay_login", wait)
    home, other = uuid4().hex, uuid4().hex
    assert (await post(username, password, host=home)).status_code == 200
    for _ in range(8):
        assert (await post(host=uuid4().hex)).status_code == 401
    assert len(wait.await_args_list) >= 2
    assert wait.await_args_list[-1].args[0] > wait.await_args_list[0].args[0]
    wait.reset_mock()
    assert (await post(username, password, host=home)).status_code == 200
    wait.assert_not_awaited()
    for _ in range(5):
        assert (await post(host=other)).status_code == 401
    assert (await post(username, password, host=other)).status_code == 429
    assert (await post(username, password, host=home)).status_code == 200


@pytest.mark.asyncio
async def test_unknown_user_still_runs_the_password_verifier(login, monkeypatch):
    post, *_ = login
    hashes = []
    def verify(_, encoded):
        hashes.append(encoded)
        return False
    monkeypatch.setattr(auth, "verify_password", verify)
    assert (await post(uuid4().hex)).status_code == 401
    assert len(hashes) == 1
    assert auth.pwd_context.identify(hashes[0]) == auth.pwd_context.default_scheme()


def test_recognized_origin_expires_after_thirty_days(tmp_path):
    from services.login_throttle import known_login_origin, remember_login_origin
    location = tmp_path / "origins.db"
    remember_login_origin(location, "user", "first", now=100)
    assert known_login_origin(location, "user", "first", now=100 + 29*86400)
    assert not known_login_origin(location, "user", "second", now=101)
    assert not known_login_origin(location, "other", "first", now=101)
    assert not known_login_origin(location, "user", "first", now=100 + 30*86400)


def test_forwarded_chain_uses_only_explicitly_trusted_hops():
    from services.login_throttle import client_address
    external, untrusted, proxy, inner = map(lambda n: str(ipaddress.IPv4Address(n)), (1, 2, 3, 4))
    chain = ", ".join([external, untrusted, inner])
    assert client_address(proxy, chain, "") == proxy
    assert client_address(proxy, chain, inner) == proxy
    assert client_address(proxy, chain, f"{proxy}/32,{inner}") == untrusted
    assert client_address(proxy, "invalid", proxy) == proxy


@pytest.mark.asyncio
async def test_account_protection_recognizes_existing_origin_for_full_window(login, monkeypatch):
    post, username, password = login
    now = [100.0]
    assert (await post(username, password, host="known")).status_code == 200
    account = LoginThrottle(max_failures=20, clock=lambda: now[0])
    monkeypatch.setattr(auth, "_username_throttle", account)
    monkeypatch.setattr(auth, "_delay_login", AsyncMock())
    for _ in range(20):
        account.record_failure(username)
    assert (await post(username, password, host="new")).status_code == 429
    assert (await post(username, password, host="known")).status_code == 200
    assert (await post(username, password, host="new")).status_code == 429
    now[0] += 900
    assert (await post(username, password, host="new")).status_code == 200


@pytest.mark.asyncio
async def test_known_origin_has_its_own_pair_budget_under_saturation(login, monkeypatch):
    post, username, password = login
    assert (await post(username, password, host="known")).status_code == 200
    for attr in ("_pair_throttle", "_address_throttle"):
        full = LoginThrottle(max_failures=1, capacity=1)
        full.record_failure("already locked")
        monkeypatch.setattr(auth, attr, full)
    assert (await post(username, password, host="known")).status_code == 200
    assert (await post(username, password, host="new")).status_code == 429
    for _ in range(5):
        assert (await post(host="known")).status_code == 401
    assert (await post(username, password, host="known")).status_code == 429


@pytest.mark.asyncio
async def test_password_change_obeys_the_valid_session_budget(login, monkeypatch):
    post, username, password = login
    token = (await post(username, password)).json()["access_token"]
    budget = LoginThrottle(max_failures=1)
    monkeypatch.setattr(auth, "_export_throttle", budget)
    budget.record_failure(token)
    app = FastAPI()
    app.include_router(auth.router, prefix="/api/auth")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as cli:
        response = await cli.post("/api/auth/change-password", headers={"Authorization": f"Bearer {token}"},
                                  json={"old_password":password, "new_password":secrets.token_urlsafe()})
    assert response.status_code == 429
    assert (await post(username, password)).status_code == 200
