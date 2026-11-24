"""Use a saved ARI password only with the connection it belongs to."""

from fastapi import HTTPException


ARI_PASSWORD_ENV = "ASTERISK_ARI_PASSWORD"


def saved_ari_password(values, *, host, port, scheme, username, ssl_verify) -> str:
    try:
        stored_port = int(values.get("ASTERISK_ARI_PORT") or "8088")
    except ValueError:
        stored_port = None
    stored_verify = str(values.get("ASTERISK_ARI_SSL_VERIFY") or "true").lower() not in {"0", "false", "no", "off"}
    stored_scheme = values.get("ASTERISK_ARI_SCHEME") or (
        "https" if values.get("ASTERISK_ARI_WEBSOCKET_SCHEME") == "wss" else "http"
    )
    if (
        host != (values.get("ASTERISK_HOST") or "127.0.0.1")
        or port != stored_port or scheme != stored_scheme
        or username != (values.get("ASTERISK_ARI_USERNAME") or "")
        or ssl_verify != stored_verify
    ):
        raise HTTPException(400, "Enter the ARI password for the changed connection settings.")
    password = values.get(ARI_PASSWORD_ENV)
    if not password:
        raise HTTPException(400, "Enter an ARI password.")
    return password
