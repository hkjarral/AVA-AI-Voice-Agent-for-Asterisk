"""Shared SQLite persistence for VICIdial connections and Remote Agent mappings."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Dict, List, Mapping, Optional
import uuid
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.integrations.vicidial import validate_status


_ENV_REFERENCE_RE = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_]*|\$\{[A-Za-z_][A-Za-z0-9_]*\})$"
)
_ENV_REFERENCE_WITH_DEFAULT_RE = re.compile(
    r"^\$\{([A-Za-z_][A-Za-z0-9_]*):-.*\}$"
)
_DIALPLAN_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_VICIDIAL_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_SIP_IDENTITY_RE = re.compile(r"^[A-Za-z0-9_.+@-]+$")
_REVISION_IGNORED_FIELDS = {
    "connection",
    "created_at",
    "last_verification",
    "last_verified_at",
    "name",
    "updated_at",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any, default: Any) -> str:
    return json.dumps(value if value is not None else default, sort_keys=True)


def _decode(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        parsed = json.loads(value)
        return parsed
    except (TypeError, json.JSONDecodeError):
        return default


def vicidial_configuration_revision(
    mapping: Mapping[str, Any], connection: Mapping[str, Any]
) -> str:
    """Return a stable revision for call-affecting mapping and connection state."""
    payload = {
        "mapping": {
            key: value
            for key, value in dict(mapping or {}).items()
            if key not in _REVISION_IGNORED_FIELDS
        },
        "connection": {
            key: value
            for key, value in dict(connection or {}).items()
            if key not in _REVISION_IGNORED_FIELDS
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class VicidialStore:
    """Thread-safe additive store mounted into both engine and Admin UI."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or os.getenv(
            "VICIDIAL_DB_PATH", "/app/data/operator/vicidial.db"
        )
        self._lock = threading.RLock()
        self._init_db()

    def _connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS vicidial_connections (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    base_url TEXT NOT NULL,
                    agent_api_url TEXT,
                    non_agent_api_url TEXT,
                    source TEXT NOT NULL DEFAULT 'aava',
                    username_env TEXT NOT NULL,
                    password_env TEXT NOT NULL,
                    verify_ssl INTEGER NOT NULL DEFAULT 1,
                    timeout_ms INTEGER NOT NULL DEFAULT 5000,
                    topology TEXT NOT NULL DEFAULT 'lan_vpn',
                    vicidial_host TEXT,
                    sip_port INTEGER NOT NULL DEFAULT 5060,
                    rtp_start INTEGER NOT NULL DEFAULT 10000,
                    rtp_end INTEGER NOT NULL DEFAULT 20000,
                    timezone TEXT NOT NULL DEFAULT 'UTC',
                    last_verification_json TEXT,
                    last_verified_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS vicidial_mappings (
                    id TEXT PRIMARY KEY,
                    connection_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    direction TEXT NOT NULL DEFAULT 'both',
                    campaign_id TEXT,
                    closer_campaigns_json TEXT,
                    user_start TEXT NOT NULL,
                    number_of_lines INTEGER NOT NULL DEFAULT 1,
                    conf_exten TEXT NOT NULL,
                    static_agent_user TEXT,
                    ai_agent TEXT NOT NULL,
                    trusted_context TEXT NOT NULL DEFAULT 'from-vicidial-ra',
                    trusted_endpoint TEXT,
                    pbx_setup_mode TEXT NOT NULL DEFAULT 'generated_registration',
                    pbx_technology TEXT NOT NULL DEFAULT 'PJSIP',
                    pbx_trunk_name TEXT,
                    sip_username TEXT,
                    sip_auth_username TEXT,
                    sip_contact_user TEXT,
                    sip_transport TEXT NOT NULL DEFAULT 'udp',
                    dispositions_json TEXT,
                    statuses_json TEXT,
                    destinations_json TEXT,
                    dnc_scope TEXT NOT NULL DEFAULT 'campaign',
                    callback_type TEXT NOT NULL DEFAULT 'ANYONE',
                    last_verification_json TEXT,
                    last_verified_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(connection_id) REFERENCES vicidial_connections(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_vicidial_mappings_connection
                    ON vicidial_mappings(connection_id);
                CREATE INDEX IF NOT EXISTS idx_vicidial_mappings_enabled
                    ON vicidial_mappings(enabled);
                """
            )
            # Additive migration for databases created by an earlier preview.
            connection_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(vicidial_connections)")
            }
            if "timezone" not in connection_columns:
                conn.execute(
                    "ALTER TABLE vicidial_connections ADD COLUMN timezone TEXT NOT NULL DEFAULT 'UTC'"
                )
            mapping_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(vicidial_mappings)")
            }
            additive_mapping_columns = {
                "pbx_setup_mode": "TEXT NOT NULL DEFAULT 'generated_registration'",
                "pbx_technology": "TEXT NOT NULL DEFAULT 'PJSIP'",
                "pbx_trunk_name": "TEXT",
                "sip_username": "TEXT",
                "sip_auth_username": "TEXT",
                "sip_contact_user": "TEXT",
                "sip_transport": "TEXT NOT NULL DEFAULT 'udp'",
            }
            for column_name, definition in additive_mapping_columns.items():
                if column_name not in mapping_columns:
                    conn.execute(
                        f"ALTER TABLE vicidial_mappings ADD COLUMN {column_name} {definition}"
                    )

            # Preview builds accepted ${NAME:-literal} credential references.
            # Strip any persisted inline default so connection reads can never
            # disclose it and runtime authentication remains environment-only.
            for row in conn.execute(
                "SELECT id, username_env, password_env FROM vicidial_connections"
            ).fetchall():
                username_env = str(row["username_env"] or "")
                password_env = str(row["password_env"] or "")
                username_match = _ENV_REFERENCE_WITH_DEFAULT_RE.fullmatch(username_env)
                password_match = _ENV_REFERENCE_WITH_DEFAULT_RE.fullmatch(password_env)
                if username_match or password_match:
                    conn.execute(
                        """
                        UPDATE vicidial_connections
                           SET username_env=?, password_env=?
                         WHERE id=?
                        """,
                        (
                            f"${{{username_match.group(1)}}}"
                            if username_match
                            else username_env,
                            f"${{{password_match.group(1)}}}"
                            if password_match
                            else password_env,
                            row["id"],
                        ),
                    )

    @staticmethod
    def _connection_dict(row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        data["enabled"] = bool(data.get("enabled"))
        data["verify_ssl"] = bool(data.get("verify_ssl"))
        data["last_verification"] = _decode(data.pop("last_verification_json", None), None)
        return data

    @staticmethod
    def _mapping_dict(row: sqlite3.Row) -> Dict[str, Any]:
        data = dict(row)
        data["enabled"] = bool(data.get("enabled"))
        data["closer_campaigns"] = _decode(data.pop("closer_campaigns_json", None), [])
        data["dispositions"] = _decode(data.pop("dispositions_json", None), {})
        data["statuses"] = _decode(data.pop("statuses_json", None), {})
        data["destinations"] = _decode(data.pop("destinations_json", None), {})
        data["last_verification"] = _decode(data.pop("last_verification_json", None), None)
        return data

    @staticmethod
    def validate_connection(payload: Mapping[str, Any]) -> Dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        base_url = str(payload.get("base_url") or "").strip().rstrip("/")
        if not name:
            raise ValueError("Connection name is required")
        parsed_url = urlsplit(base_url)
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
            raise ValueError("Base URL must use http or https")
        if parsed_url.username or parsed_url.password or parsed_url.query or parsed_url.fragment:
            raise ValueError("Base URL must not contain credentials, a query, or a fragment")
        custom_urls: Dict[str, Optional[str]] = {}
        for field_name in ("agent_api_url", "non_agent_api_url"):
            raw_url = str(payload.get(field_name) or "").strip() or None
            if raw_url:
                parsed_custom = urlsplit(raw_url)
                if parsed_custom.scheme not in {"http", "https"} or not parsed_custom.hostname:
                    raise ValueError(f"{field_name} must use http or https")
                if (
                    parsed_custom.username
                    or parsed_custom.password
                    or parsed_custom.query
                    or parsed_custom.fragment
                ):
                    raise ValueError(
                        f"{field_name} must not contain credentials, a query, or a fragment"
                    )
            custom_urls[field_name] = raw_url
        topology = str(payload.get("topology") or "lan_vpn").strip()
        if topology not in {"lan_vpn", "ava_behind_nat", "public_sbc"}:
            raise ValueError("Topology must be lan_vpn, ava_behind_nat, or public_sbc")
        username_env = str(payload.get("username_env") or "").strip()
        password_env = str(payload.get("password_env") or "").strip()
        if not username_env or not password_env:
            raise ValueError("API username and password environment references are required")
        if not _ENV_REFERENCE_RE.fullmatch(username_env) or not _ENV_REFERENCE_RE.fullmatch(password_env):
            raise ValueError("API credentials must be environment-variable names or ${NAME} references")
        timeout_ms = int(payload.get("timeout_ms") or 5000)
        if timeout_ms < 250 or timeout_ms > 30000:
            raise ValueError("Timeout must be between 250 and 30000 milliseconds")
        rtp_start = int(payload.get("rtp_start") or 10000)
        rtp_end = int(payload.get("rtp_end") or 20000)
        if not (1 <= rtp_start <= rtp_end <= 65535):
            raise ValueError("RTP range is invalid")
        sip_port = int(payload.get("sip_port") or 5060)
        if not 1 <= sip_port <= 65535:
            raise ValueError("SIP port is invalid")
        timezone_name = str(payload.get("timezone") or "UTC").strip() or "UTC"
        try:
            ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("VICIdial timezone must be a valid IANA timezone") from exc
        source = str(payload.get("source") or "aava").strip() or "aava"
        if len(source) > 20 or not _DIALPLAN_TOKEN_RE.fullmatch(source):
            raise ValueError("API source must be a 1-20 character identifier")
        return {
            "name": name,
            "enabled": bool(payload.get("enabled", True)),
            "base_url": base_url,
            "agent_api_url": custom_urls["agent_api_url"],
            "non_agent_api_url": custom_urls["non_agent_api_url"],
            "source": source,
            "username_env": username_env,
            "password_env": password_env,
            "verify_ssl": bool(payload.get("verify_ssl", True)),
            "timeout_ms": timeout_ms,
            "topology": topology,
            "vicidial_host": str(payload.get("vicidial_host") or "").strip() or None,
            "sip_port": sip_port,
            "rtp_start": rtp_start,
            "rtp_end": rtp_end,
            "timezone": timezone_name,
        }

    @staticmethod
    def validate_mapping(payload: Mapping[str, Any]) -> Dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        connection_id = str(payload.get("connection_id") or "").strip()
        user_start = str(payload.get("user_start") or "").strip()
        conf_exten = str(payload.get("conf_exten") or "").strip()
        ai_agent = str(payload.get("ai_agent") or "").strip()
        if not all((name, connection_id, user_start, conf_exten, ai_agent)):
            raise ValueError(
                "Mapping name, connection, starting user, Remote Agent extension, and AAVA Agent are required"
            )
        if not user_start.isdigit():
            raise ValueError("Remote Agent starting user must be numeric")
        if not _DIALPLAN_TOKEN_RE.fullmatch(conf_exten):
            raise ValueError("Remote Agent extension contains unsupported characters")
        lines = int(payload.get("number_of_lines") or 1)
        if lines < 1 or lines > 100:
            raise ValueError("Number of lines must be between 1 and 100")
        direction = str(payload.get("direction") or "both").strip()
        if direction not in {"outbound", "inbound", "both"}:
            raise ValueError("Direction must be outbound, inbound, or both")
        dnc_scope = str(payload.get("dnc_scope") or "campaign").strip()
        if dnc_scope not in {"campaign", "system"}:
            raise ValueError("DNC scope must be campaign or system")
        callback_type = str(payload.get("callback_type") or "ANYONE").strip().upper()
        if callback_type not in {"ANYONE", "USERONLY"}:
            raise ValueError("Callback type must be ANYONE or USERONLY")
        trusted_context = str(payload.get("trusted_context") or "from-vicidial-ra").strip()
        trusted_endpoint = str(payload.get("trusted_endpoint") or "").strip() or None
        if not _DIALPLAN_TOKEN_RE.fullmatch(trusted_context):
            raise ValueError("Trusted dialplan context contains unsupported characters")
        if trusted_endpoint and not _DIALPLAN_TOKEN_RE.fullmatch(trusted_endpoint):
            raise ValueError("Trusted endpoint contains unsupported characters")
        pbx_setup_mode = str(
            payload.get("pbx_setup_mode") or "generated_registration"
        ).strip().lower()
        if pbx_setup_mode not in {"generated_registration", "existing_endpoint"}:
            raise ValueError("PBX setup mode must be generated_registration or existing_endpoint")
        pbx_technology = str(payload.get("pbx_technology") or "PJSIP").strip().upper()
        if pbx_technology not in {"PJSIP", "SIP"}:
            raise ValueError("PBX technology must be PJSIP or SIP")
        if pbx_setup_mode == "generated_registration" and pbx_technology != "PJSIP":
            raise ValueError(
                "Generated registration setup supports PJSIP only; use an existing endpoint for SIP"
            )
        pbx_trunk_name = str(payload.get("pbx_trunk_name") or "").strip() or None
        if pbx_trunk_name and (len(pbx_trunk_name) > 80 or any(ord(ch) < 32 for ch in pbx_trunk_name)):
            raise ValueError("PBX trunk name must be 80 printable characters or fewer")
        if not trusted_endpoint:
            raise ValueError("Exact Asterisk endpoint ID is required before setup artifacts can be generated")
        if pbx_setup_mode == "generated_registration" and not pbx_trunk_name:
            raise ValueError("PBX trunk name is required for generated registration setup")

        def sip_identity(field_name: str, fallback: Optional[str]) -> Optional[str]:
            value = str(payload.get(field_name) or fallback or "").strip() or None
            if value and (len(value) > 128 or not _SIP_IDENTITY_RE.fullmatch(value)):
                raise ValueError(f"{field_name} contains unsupported characters")
            return value

        sip_username = sip_identity("sip_username", conf_exten)
        sip_auth_username = sip_identity("sip_auth_username", sip_username)
        sip_contact_user = sip_identity("sip_contact_user", conf_exten)
        sip_transport = str(payload.get("sip_transport") or "udp").strip().lower()
        if sip_transport not in {"udp", "tcp", "tls"}:
            raise ValueError("SIP transport must be udp, tcp, or tls")
        static_agent_user = str(payload.get("static_agent_user") or "").strip() or None
        if static_agent_user and (lines != 1 or static_agent_user != user_start):
            raise ValueError("The one-line fallback user must equal the starting user and requires exactly one line")

        campaign_id = str(payload.get("campaign_id") or "").strip() or None
        closer_campaigns = [
            str(value).strip()
            for value in list(payload.get("closer_campaigns") or [])
            if str(value).strip()
        ]
        if not campaign_id:
            raise ValueError(
                "VICIdial action/outbound campaign ID is required for every mapping"
            )
        if campaign_id.upper() == "CLOSER":
            raise ValueError(
                "VICIdial action/outbound campaign ID must be a real campaign, not CLOSER"
            )
        if direction in {"inbound", "both"} and not closer_campaigns:
            raise ValueError("At least one closer campaign is required for inbound mappings")
        for field_name, value in [
            ("campaign_id", campaign_id),
            *(("closer_campaigns", value) for value in closer_campaigns),
        ]:
            if value and (len(value) > 32 or not _VICIDIAL_ID_RE.fullmatch(value)):
                raise ValueError(f"{field_name} contains unsupported characters")

        dispositions: Dict[str, str] = {}
        for key, value in dict(payload.get("dispositions") or {}).items():
            semantic = str(key or "").strip().lower()
            if semantic:
                dispositions[semantic] = validate_status(
                    value, field_name=f"dispositions.{semantic}"
                )
        statuses: Dict[str, str] = {}
        default_statuses = {
            "ai_hangup": "AIHU",
            "caller_hangup": "AICU",
            "ai_ingroup_transfer": "AIXFR",
            "ai_extension_transfer": "AIEXT",
            "ai_failure": "AIFAIL",
            "dnc": "DNC",
            "callback": "CALLBK",
        }
        for key, default in default_statuses.items():
            statuses[key] = validate_status(
                dict(payload.get("statuses") or {}).get(key) or default,
                field_name=f"statuses.{key}",
            )

        destinations: Dict[str, Dict[str, Any]] = {}
        for key, raw in dict(payload.get("destinations") or {}).items():
            if not isinstance(raw, Mapping):
                raise ValueError(f"Destination {key} must be an object")
            kind = str(raw.get("type") or "").strip().lower()
            target = str(raw.get("target") or "").strip()
            if kind not in {"ingroup", "extension"} or not target:
                raise ValueError(f"Destination {key} requires type ingroup/extension and a target")
            destinations[str(key)] = {
                "type": kind,
                "target": target,
                "description": str(raw.get("description") or key).strip(),
                "status": validate_status(
                    raw.get("status")
                    or statuses["ai_ingroup_transfer" if kind == "ingroup" else "ai_extension_transfer"],
                    field_name=f"destinations.{key}.status",
                ),
            }

        return {
            "connection_id": connection_id,
            "name": name,
            "enabled": bool(payload.get("enabled", True)),
            "direction": direction,
            "campaign_id": campaign_id,
            "closer_campaigns": closer_campaigns,
            "user_start": user_start,
            "number_of_lines": lines,
            "conf_exten": conf_exten,
            "static_agent_user": static_agent_user,
            "ai_agent": ai_agent,
            "trusted_context": trusted_context,
            "trusted_endpoint": trusted_endpoint,
            "pbx_setup_mode": pbx_setup_mode,
            "pbx_technology": pbx_technology,
            "pbx_trunk_name": pbx_trunk_name,
            "sip_username": sip_username,
            "sip_auth_username": sip_auth_username,
            "sip_contact_user": sip_contact_user,
            "sip_transport": sip_transport,
            "dispositions": dispositions,
            "statuses": statuses,
            "destinations": destinations,
            "dnc_scope": dnc_scope,
            "callback_type": callback_type,
        }

    def list_connections(self) -> List[Dict[str, Any]]:
        with self._lock, self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM vicidial_connections ORDER BY name COLLATE NOCASE, id"
            ).fetchall()
        return [self._connection_dict(row) for row in rows]

    def get_connection(self, connection_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM vicidial_connections WHERE id=?", (connection_id,)
            ).fetchone()
        return self._connection_dict(row) if row else None

    def save_connection(
        self, payload: Mapping[str, Any], connection_id: Optional[str] = None
    ) -> Dict[str, Any]:
        data = self.validate_connection(payload)
        connection_id = connection_id or str(uuid.uuid4())
        now = _now()
        with self._lock, self._connection() as conn:
            exists = conn.execute(
                "SELECT * FROM vicidial_connections WHERE id=?", (connection_id,)
            ).fetchone()
            previous = self._connection_dict(exists) if exists else None
            material_changed = bool(
                previous
                and any(
                    previous.get(key) != value
                    for key, value in data.items()
                    if key != "name"
                )
            )
            conn.execute(
                """
                INSERT INTO vicidial_connections (
                    id,name,enabled,base_url,agent_api_url,non_agent_api_url,source,
                    username_env,password_env,verify_ssl,timeout_ms,topology,vicidial_host,
                    sip_port,rtp_start,rtp_end,timezone,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,enabled=excluded.enabled,base_url=excluded.base_url,
                    agent_api_url=excluded.agent_api_url,non_agent_api_url=excluded.non_agent_api_url,
                    source=excluded.source,username_env=excluded.username_env,
                    password_env=excluded.password_env,verify_ssl=excluded.verify_ssl,
                    timeout_ms=excluded.timeout_ms,topology=excluded.topology,
                    vicidial_host=excluded.vicidial_host,sip_port=excluded.sip_port,
                    rtp_start=excluded.rtp_start,rtp_end=excluded.rtp_end,
                    timezone=excluded.timezone,updated_at=excluded.updated_at
                """,
                (
                    connection_id,
                    data["name"], int(data["enabled"]), data["base_url"],
                    data["agent_api_url"], data["non_agent_api_url"], data["source"],
                    data["username_env"], data["password_env"], int(data["verify_ssl"]),
                    data["timeout_ms"], data["topology"], data["vicidial_host"],
                    data["sip_port"], data["rtp_start"], data["rtp_end"], data["timezone"],
                    str(exists["created_at"]) if exists else now, now,
                ),
            )
            if material_changed:
                conn.execute(
                    """
                    UPDATE vicidial_connections
                       SET last_verification_json=NULL, last_verified_at=NULL
                     WHERE id=?
                    """,
                    (connection_id,),
                )
                conn.execute(
                    """
                    UPDATE vicidial_mappings
                       SET last_verification_json=NULL, last_verified_at=NULL, updated_at=?
                     WHERE connection_id=?
                    """,
                    (now, connection_id),
                )
        return self.get_connection(connection_id) or {}

    def delete_connection(self, connection_id: str) -> bool:
        with self._lock, self._connection() as conn:
            cur = conn.execute("DELETE FROM vicidial_connections WHERE id=?", (connection_id,))
            return cur.rowcount > 0

    def list_mappings(self, connection_id: Optional[str] = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM vicidial_mappings"
        params: tuple[Any, ...] = ()
        if connection_id:
            query += " WHERE connection_id=?"
            params = (connection_id,)
        query += " ORDER BY name COLLATE NOCASE, id"
        with self._lock, self._connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._mapping_dict(row) for row in rows]

    def get_mapping(self, mapping_id: str) -> Optional[Dict[str, Any]]:
        with self._lock, self._connection() as conn:
            row = conn.execute(
                "SELECT * FROM vicidial_mappings WHERE id=?", (mapping_id,)
            ).fetchone()
        return self._mapping_dict(row) if row else None

    def get_enabled_mapping(self, mapping_id: str) -> Optional[Dict[str, Any]]:
        mapping = self.get_mapping(mapping_id)
        if not mapping or not mapping.get("enabled"):
            return None
        connection = self.get_connection(str(mapping.get("connection_id") or ""))
        if not connection or not connection.get("enabled"):
            return None
        mapping["connection"] = connection
        return mapping

    def save_mapping(
        self, payload: Mapping[str, Any], mapping_id: Optional[str] = None
    ) -> Dict[str, Any]:
        data = self.validate_mapping(payload)
        if not self.get_connection(data["connection_id"]):
            raise ValueError("VICIdial connection does not exist")
        mapping_id = mapping_id or str(uuid.uuid4())
        now = _now()
        with self._lock, self._connection() as conn:
            if data["enabled"]:
                start = int(data["user_start"])
                end = start + int(data["number_of_lines"]) - 1
                for row in conn.execute(
                    "SELECT * FROM vicidial_mappings WHERE enabled=1 AND id<>?",
                    (mapping_id,),
                ).fetchall():
                    other = self._mapping_dict(row)
                    if str(other.get("connection_id") or "") == data["connection_id"]:
                        other_start = int(str(other.get("user_start") or "0"))
                        other_end = (
                            other_start + int(other.get("number_of_lines") or 1) - 1
                        )
                        if start <= other_end and other_start <= end:
                            raise ValueError(
                                "Remote Agent user range overlaps enabled mapping "
                                f"{other.get('name')}"
                            )
                    if (
                        str(other.get("trusted_context") or "")
                        == data["trusted_context"]
                        and str(other.get("conf_exten") or "")
                        == data["conf_exten"]
                    ):
                        raise ValueError(
                            "Asterisk dialplan context and extension are already used by "
                            f"enabled mapping {other.get('name')}"
                        )
                    endpoint = str(data.get("trusted_endpoint") or "")
                    if (
                        endpoint
                        and endpoint == str(other.get("trusted_endpoint") or "")
                        and data["pbx_technology"]
                        == str(other.get("pbx_technology") or "PJSIP").upper()
                    ):
                        raise ValueError(
                            f"Asterisk endpoint is already used by enabled mapping {other.get('name')}"
                        )
            exists = conn.execute(
                "SELECT * FROM vicidial_mappings WHERE id=?", (mapping_id,)
            ).fetchone()
            previous = self._mapping_dict(exists) if exists else None
            material_changed = bool(
                previous
                and any(
                    previous.get(key) != value
                    for key, value in data.items()
                    if key != "name"
                )
            )
            conn.execute(
                """
                INSERT INTO vicidial_mappings (
                    id,connection_id,name,enabled,direction,campaign_id,closer_campaigns_json,
                    user_start,number_of_lines,conf_exten,static_agent_user,ai_agent,
                    trusted_context,trusted_endpoint,pbx_setup_mode,pbx_technology,pbx_trunk_name,
                    sip_username,sip_auth_username,sip_contact_user,sip_transport,
                    dispositions_json,statuses_json,
                    destinations_json,dnc_scope,callback_type,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    connection_id=excluded.connection_id,name=excluded.name,enabled=excluded.enabled,
                    direction=excluded.direction,campaign_id=excluded.campaign_id,
                    closer_campaigns_json=excluded.closer_campaigns_json,user_start=excluded.user_start,
                    number_of_lines=excluded.number_of_lines,conf_exten=excluded.conf_exten,
                    static_agent_user=excluded.static_agent_user,ai_agent=excluded.ai_agent,
                    trusted_context=excluded.trusted_context,trusted_endpoint=excluded.trusted_endpoint,
                    pbx_setup_mode=excluded.pbx_setup_mode,pbx_technology=excluded.pbx_technology,
                    pbx_trunk_name=excluded.pbx_trunk_name,sip_username=excluded.sip_username,
                    sip_auth_username=excluded.sip_auth_username,
                    sip_contact_user=excluded.sip_contact_user,sip_transport=excluded.sip_transport,
                    dispositions_json=excluded.dispositions_json,statuses_json=excluded.statuses_json,
                    destinations_json=excluded.destinations_json,dnc_scope=excluded.dnc_scope,
                    callback_type=excluded.callback_type,updated_at=excluded.updated_at
                """,
                (
                    mapping_id, data["connection_id"], data["name"], int(data["enabled"]),
                    data["direction"], data["campaign_id"], _json(data["closer_campaigns"], []),
                    data["user_start"], data["number_of_lines"], data["conf_exten"],
                    data["static_agent_user"], data["ai_agent"], data["trusted_context"],
                    data["trusted_endpoint"], data["pbx_setup_mode"], data["pbx_technology"],
                    data["pbx_trunk_name"], data["sip_username"], data["sip_auth_username"],
                    data["sip_contact_user"], data["sip_transport"],
                    _json(data["dispositions"], {}),
                    _json(data["statuses"], {}), _json(data["destinations"], {}),
                    data["dnc_scope"], data["callback_type"],
                    str(exists["created_at"]) if exists else now, now,
                ),
            )
            if material_changed:
                conn.execute(
                    """
                    UPDATE vicidial_mappings
                       SET last_verification_json=NULL, last_verified_at=NULL
                     WHERE id=?
                    """,
                    (mapping_id,),
                )
        return self.get_mapping(mapping_id) or {}

    def delete_mapping(self, mapping_id: str) -> bool:
        with self._lock, self._connection() as conn:
            cur = conn.execute("DELETE FROM vicidial_mappings WHERE id=?", (mapping_id,))
            return cur.rowcount > 0

    def record_verification(
        self,
        *,
        kind: str,
        record_id: str,
        result: Mapping[str, Any],
        expected_revision: Optional[str] = None,
    ) -> bool:
        if kind not in {"connection", "mapping"}:
            raise ValueError("Verification kind must be connection or mapping")
        table = "vicidial_connections" if kind == "connection" else "vicidial_mappings"
        with self._lock, self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if expected_revision:
                row = conn.execute(
                    f"SELECT * FROM {table} WHERE id=?", (record_id,)
                ).fetchone()
                if not row:
                    raise ValueError(f"VICIdial {kind} does not exist")
                if kind == "connection":
                    current_revision = vicidial_configuration_revision(
                        {}, self._connection_dict(row)
                    )
                else:
                    current_mapping = self._mapping_dict(row)
                    connection_row = conn.execute(
                        "SELECT * FROM vicidial_connections WHERE id=?",
                        (str(current_mapping.get("connection_id") or ""),),
                    ).fetchone()
                    if not connection_row:
                        raise ValueError("VICIdial connection does not exist")
                    current_revision = vicidial_configuration_revision(
                        current_mapping, self._connection_dict(connection_row)
                    )
                if current_revision != expected_revision:
                    return False
            conn.execute(
                f"UPDATE {table} SET last_verification_json=?, last_verified_at=?, updated_at=? WHERE id=?",
                (_json(dict(result), {}), _now(), _now(), record_id),
            )
        return True

    def record_mapping_verification(
        self,
        *,
        mapping_id: str,
        mapping_revision: str,
        result: Mapping[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Atomically merge live-call evidence into a mapping verification result.

        ``BEGIN IMMEDIATE`` is required because Admin UI and ai-engine have
        separate process-local locks and SQLite connections. Acquiring the
        database write reservation before the read serializes both services'
        readiness read-modify-write cycles.
        """
        with self._lock, self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM vicidial_mappings WHERE id=?",
                (mapping_id,),
            ).fetchone()
            if not row:
                raise ValueError("VICIdial mapping does not exist")
            current_mapping = self._mapping_dict(row)
            connection_row = conn.execute(
                "SELECT * FROM vicidial_connections WHERE id=?",
                (str(current_mapping.get("connection_id") or ""),),
            ).fetchone()
            if not connection_row:
                raise ValueError("VICIdial connection does not exist")
            if vicidial_configuration_revision(
                current_mapping, self._connection_dict(connection_row)
            ) != str(mapping_revision or "").strip():
                return None

            current = _decode(row["last_verification_json"], {})
            if not isinstance(current, dict):
                current = {}
            current_real_calls = current.get("real_calls")
            if not isinstance(current_real_calls, dict):
                current_real_calls = {}

            merged = dict(result)
            # The database value is authoritative here. ``result`` was built from
            # a pre-request mapping snapshot and may be stale after API/PBX awaits.
            # Using only the value read inside this transaction also avoids
            # resurrecting evidence cleared by a concurrent mapping edit.
            real_calls = dict(current_real_calls)

            configured_direction = str(row["direction"] or "both").strip().lower()
            required_directions = (
                ["inbound", "outbound"]
                if configured_direction == "both"
                else [configured_direction]
            )
            live_call_ready = all(
                bool(dict(real_calls.get(direction) or {}).get("verified"))
                for direction in required_directions
            )
            merged["real_calls"] = real_calls
            merged["real_call"] = {
                "verified": live_call_ready,
                "required_directions": required_directions,
                "note": "Each configured direction requires a correlated call with confirmed VICIdial terminal control",
            }
            merged["ready"] = bool(
                merged.get("configuration_ready")
                and merged.get("pbx_ready")
                and live_call_ready
            )

            now = _now()
            conn.execute(
                """
                UPDATE vicidial_mappings
                   SET last_verification_json=?, last_verified_at=?, updated_at=?
                 WHERE id=?
                """,
                (_json(merged, {}), now, now, mapping_id),
            )
        return merged

    def record_real_call_verification(
        self,
        *,
        mapping_id: str,
        mapping_revision: str,
        direction: str,
        external_call_id: str,
        status: str,
        operation: str,
    ) -> bool:
        """Merge live-call evidence only if its admitted configuration is current."""
        normalized_direction = str(direction or "").strip().lower()
        if normalized_direction not in {"inbound", "outbound"}:
            raise ValueError("Real-call direction must be inbound or outbound")
        expected_revision = str(mapping_revision or "").strip()
        if not expected_revision:
            raise ValueError("Real-call mapping revision is required")
        with self._lock, self._connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM vicidial_mappings WHERE id=?",
                (mapping_id,),
            ).fetchone()
            if not row:
                raise ValueError("VICIdial mapping does not exist")
            current_mapping = self._mapping_dict(row)
            connection_row = conn.execute(
                "SELECT * FROM vicidial_connections WHERE id=?",
                (str(current_mapping.get("connection_id") or ""),),
            ).fetchone()
            if not connection_row:
                raise ValueError("VICIdial connection does not exist")
            current_connection = self._connection_dict(connection_row)
            if vicidial_configuration_revision(
                current_mapping, current_connection
            ) != expected_revision:
                return False
            verification = _decode(row["last_verification_json"], {})
            if not isinstance(verification, dict):
                verification = {}
            real_calls = verification.get("real_calls")
            if not isinstance(real_calls, dict):
                real_calls = {}
            real_calls[normalized_direction] = {
                "verified": True,
                "verified_at": _now(),
                "external_call_id": str(external_call_id or "").strip(),
                "status": validate_status(status),
                "operation": str(operation or "hangup").strip(),
            }
            verification["real_calls"] = real_calls
            configured_direction = str(row["direction"] or "both").strip().lower()
            required_directions = (
                ["inbound", "outbound"]
                if configured_direction == "both"
                else [configured_direction]
            )
            live_call_ready = all(
                bool(dict(real_calls.get(required) or {}).get("verified"))
                for required in required_directions
            )
            verification["real_call"] = {
                "verified": live_call_ready,
                "required_directions": required_directions,
                "note": "Each configured direction requires a correlated call with confirmed VICIdial terminal control",
            }
            verification["ready"] = bool(
                verification.get("configuration_ready")
                and verification.get("pbx_ready")
                and live_call_ready
            )
            now = _now()
            conn.execute(
                """
                UPDATE vicidial_mappings
                   SET last_verification_json=?, last_verified_at=?, updated_at=?
                 WHERE id=?
                """,
                (_json(verification, {}), now, now, mapping_id),
            )
        return True


_store: Optional[VicidialStore] = None
_store_lock = threading.Lock()


def get_vicidial_store() -> VicidialStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = VicidialStore()
        return _store
