"""Opaque ARI media identities compatible with common PBX record schemas."""

import base64
import uuid


def new_websocket_channel_id() -> str:
    """Keep the full UUID entropy within FreePBX's varchar(32) uniqueid limit.

    Unpadded URL-safe base64 encodes the UUID in 22 characters. Together with
    the recognizable prefix this is 30 characters, with no dial-string or URI
    delimiters. The separate media nonce remains the connection authenticator.
    """
    opaque_id = base64.urlsafe_b64encode(uuid.uuid4().bytes).decode("ascii").rstrip("=")
    return f"aava-ws-{opaque_id}"
