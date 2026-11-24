"""Keep the socket peer available to login after Uvicorn applies proxy headers."""

from starlette.requests import Request
from uvicorn.protocols.http.auto import AutoHTTPProtocol


class PeerAwareHTTPProtocol(AutoHTTPProtocol):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        forwarded_app = self.app

        async def remember_peer(scope, receive, send):
            # Uvicorn's ProxyHeadersMiddleware may replace scope["client"] in place.
            scope["ava.socket_peer"] = scope.get("client")
            await forwarded_app(scope, receive, send)

        self.app = remember_peer


def raw_http_peer(request: Request) -> str:
    peer = request.scope.get("ava.socket_peer", request.scope.get("client"))
    return peer[0] if peer else ""
