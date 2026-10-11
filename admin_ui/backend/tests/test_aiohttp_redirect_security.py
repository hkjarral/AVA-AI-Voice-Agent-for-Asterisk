"""Keep the aiohttp redirect credential fix covered across dependency updates."""

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
from multidict import CIMultiDict
import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("header", ["Authorization", "Cookie", "Proxy-Authorization"])
@pytest.mark.parametrize("cross_origin", [True, False], ids=["cross-origin", "same-origin"])
async def test_redirect_credential_header_boundary(header, cross_origin):
    received = []

    async def sink(request):
        received.append((request.headers.getall(header, []), request.headers.get("X-Review-Control")))
        return web.Response(text="ok")

    target_app = web.Application()
    target_app.router.add_get("/sink", sink)
    async with TestServer(target_app) as target:
        async def redirect(_request):
            destination = str(target.make_url("/sink")) if cross_origin else "/sink"
            return web.Response(status=302, headers={"Location": destination})

        source_app = web.Application()
        source_app.router.add_get("/redirect", redirect)
        source_app.router.add_get("/sink", sink)
        async with TestServer(source_app) as source:
            values = ["synthetic-one", "synthetic-two"]
            headers = CIMultiDict([(header, value) for value in values])
            headers["X-Review-Control"] = "preserved"
            async with aiohttp.ClientSession() as client:
                async with client.get(source.make_url("/redirect"), headers=headers) as response:
                    assert response.status == 200
                    assert await response.text() == "ok"

    assert received == [([] if cross_origin else values, "preserved")]
