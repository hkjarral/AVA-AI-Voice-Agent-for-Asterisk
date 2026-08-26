"""Effective audio-chain resolution and alignment findings.

Single source of truth, mirrored for the Admin UI:

- The **audio profile** owns the Asterisk wire contract (``transport_out`` /
  ``transport_in``), the internal processing rate, and the provider-boundary
  preference for pipeline Agents (``provider_pref``).
- A **provider instance** owns only its native API boundary
  (``provider_input_*`` / ``output_*``), constrained by the adapter's declared
  capabilities.
- Every wire-facing provider field (``input_encoding``/``input_sample_rate_hz``
  on modern full agents, ``target_encoding``/``target_sample_rate_hz`` on all)
  is **derived per call** from the resolved profile by the engine
  (``session.provider_overrides`` in ``src/engine.py``); YAML values for them
  are legacy fallbacks, not an edit point.

This module re-derives that resolution from the merged config alone — without
importing provider adapters — so the Admin UI backend can show the effective
chain per Agent and flag misalignments *before* a call, instead of operators
discovering them from call logs.

Provider format capabilities come from ``PROVIDER_AUDIO_CAPABILITIES``
(``src/config/audio_baselines.py``) — the same registry each adapter's
``get_capabilities()`` builds from — so runtime and alignment can never drift.
"""

from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.audio.audiosocket_protocol import normalize_slin_format
from src.config.audio_baselines import (
    PROVIDER_AUDIO_BASELINES,
    PROVIDER_AUDIO_CAPABILITIES,
)
from src.config.provider_instances import FULL_AGENT_KINDS, provider_kind

MULAW_TOKENS = frozenset({"ulaw", "mulaw", "mu-law", "g711_ulaw", "g711ulaw"})
ALAW_TOKENS = frozenset({"alaw", "a-law", "g711_alaw", "g711alaw"})
COMPANDED_TOKENS = MULAW_TOKENS | ALAW_TOKENS

# Kinds whose adapter config declares a distinct provider_input_* boundary.
# For them input_encoding/input_sample_rate_hz are wire-facing (derived per
# call); for legacy contracts (Deepgram) input_* IS the provider boundary.
_KINDS_WITH_PROVIDER_INPUT = frozenset(
    {"openai_realtime", "google_live", "grok", "elevenlabs_agent"}
)


# Codecs the ExternalMedia RTP server can actually decode and stamp
# (see src/rtp_server.py _decode_payload/_payload_type_byte).
RTP_SUPPORTED_CODECS = frozenset({"ulaw", "alaw", "slin16"})

# Wire rate is fixed by the RTP codec: G.711 is 8 kHz, slin16 is 16 kHz.
_RTP_CODEC_RATES = {"ulaw": 8000, "alaw": 8000, "slin16": 16000}


def resolve_external_media_wire(external_media: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Resolve the process-wide ExternalMedia RTP wire contract.

    ExternalMedia runs ONE RTP codec per engine process, in BOTH directions,
    shared by every Agent and profile: ``external_media.codec`` owns it (the
    Audio Transport page). Audio-profile transport legs govern the AudioSocket
    wire only. Returns ``{"codec", "encoding", "sample_rate_hz", "source",
    "configured"}`` — ``configured`` echoes the raw YAML token so callers can
    flag unsupported values; source is ``external_media.codec`` or
    ``builtin-default``.
    """
    em = external_media if isinstance(external_media, Mapping) else {}
    raw = str(em.get("codec") or "").strip().lower()
    if raw in MULAW_TOKENS:
        codec = "ulaw"
    elif raw in ALAW_TOKENS:
        codec = "alaw"
    elif raw in {"slin16", "linear16", "pcm16"}:
        codec = "slin16"
    else:
        codec = ""
    if codec in RTP_SUPPORTED_CODECS:
        source = "external_media.codec"
    else:
        codec, source = "ulaw", "builtin-default"
    return {
        "codec": codec,
        "encoding": codec,
        "sample_rate_hz": _RTP_CODEC_RATES[codec],
        "source": source,
        "configured": raw,
    }


def resolve_external_media_transit_rate(
    external_media: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    """Resolve the engine-side RTP transit rate for ExternalMedia.

    Inbound RTP is decoded to PCM16 and resampled to this rate before it
    reaches the engine (``src/rtp_server.py``). ``external_media.sample_rate``
    owns it; when unset it is inferred from ``external_media.format``
    (slin16-family → 16000, otherwise 8000) exactly like the engine does.
    Setting it to the provider's native rate (e.g. 8000 for G.711 providers)
    removes an avoidable resample round trip.
    """
    em = external_media if isinstance(external_media, Mapping) else {}
    fmt = str(em.get("format") or "slin16").strip().lower()
    raw_rate = em.get("sample_rate")
    try:
        rate = int(raw_rate) if raw_rate else 0
    except (TypeError, ValueError):
        rate = 0
    if rate > 0:
        return {"sample_rate_hz": rate, "source": "external_media.sample_rate"}
    inferred = 16000 if fmt in ("slin16", "linear16", "pcm16") else 8000
    return {"sample_rate_hz": inferred, "source": f"format:{fmt}"}


def normalize_encoding(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in MULAW_TOKENS:
        return "mulaw"
    if token in ALAW_TOKENS:
        return "alaw"
    if token in {"linear16", "pcm16", "slin", "slin16", "linear", "pcm"}:
        return "linear16"
    return token


def _finding(
    severity: str,
    code: str,
    title: str,
    detail: str,
    **scope: Optional[str],
) -> Dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "title": title,
        "detail": detail,
        "scope": {key: value for key, value in scope.items() if value},
    }


def resolve_wire_leg(
    declared: Mapping[str, Any],
    *,
    fallback_encoding: str = "slin",
    fallback_rate: Optional[int] = 8000,
) -> Dict[str, Any]:
    """Resolve one AudioSocket wire leg from a declared ``{encoding, sample_rate_hz}``.

    This is the per-call resolution TransportOrchestrator applies — the runtime
    calls this same function, so the Admin UI alignment view cannot drift from
    what actually goes on the wire. AudioSocket legs are signed-linear: valid
    declarations normalize to their canonical slin format; companded
    declarations ride the lossless slin@8000 compatibility carrier (Asterisk
    owns the trunk codec); anything else keeps the caller's fallback. The
    ExternalMedia wire never comes from profile legs — see
    ``resolve_external_media_wire``.
    """
    declared_enc = str(declared.get("encoding") or "").strip().lower()
    declared_rate = declared.get("sample_rate_hz")
    carrier = False
    try:
        enc, rate = normalize_slin_format(
            declared_enc,
            int(declared_rate) if declared_rate is not None else None,
        )
    except (TypeError, ValueError):
        if declared_enc in COMPANDED_TOKENS:
            enc, rate, carrier = "slin", 8000, True
        else:
            enc, rate = fallback_encoding, fallback_rate
            if not rate:
                token = str(enc or "").strip().lower()
                rate = 16000 if token in {"slin16", "linear16", "pcm16"} else 8000
    return {
        "encoding": enc,
        "sample_rate_hz": rate,
        "carrier": carrier,
        "declared_encoding": declared_enc or str(enc or ""),
    }


def resolve_wire_contract(
    profile: Mapping[str, Any],
    audio_transport: str,
    external_media: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Resolve both wire legs exactly like the runtime does.

    AudioSocket: the profile owns the wire (``transport_out`` /
    ``transport_in``). ExternalMedia: the Audio Transport RTP settings own it —
    one codec, both directions, identical for every profile and Agent.
    """
    if audio_transport != "audiosocket":
        wire = resolve_external_media_wire(external_media)
        leg = {
            "encoding": wire["encoding"],
            "sample_rate_hz": wire["sample_rate_hz"],
            "carrier": False,
            "declared_encoding": wire["codec"],
            "source": "external_media",
        }
        out_leg = dict(leg)
        in_leg = dict(leg)
        in_leg["declared"] = False
        return {"out": out_leg, "in": in_leg}

    transport_out = profile.get("transport_out")
    out_leg = resolve_wire_leg(
        transport_out if isinstance(transport_out, Mapping) else {},
    )
    out_leg["source"] = "profile"
    transport_in = profile.get("transport_in")
    if isinstance(transport_in, Mapping) and transport_in:
        # An explicit inbound leg falls back to the resolved outbound leg,
        # mirroring TransportOrchestrator._negotiate_formats.
        in_leg = resolve_wire_leg(
            transport_in,
            fallback_encoding=str(out_leg["encoding"]),
            fallback_rate=out_leg["sample_rate_hz"],
        )
        in_leg["declared"] = True
        in_leg["source"] = "profile"
    else:
        in_leg = dict(out_leg)
        in_leg["declared"] = False
    return {"out": out_leg, "in": in_leg}


def _select(preferred: Any, supported: Sequence[Any], *, encoding: bool) -> Any:
    if not supported:
        return preferred
    if encoding:
        supported_norm = [normalize_encoding(item) for item in supported]
        if normalize_encoding(preferred) in supported_norm:
            return preferred
    else:
        try:
            if int(preferred) in [int(item) for item in supported]:
                return int(preferred)
        except (TypeError, ValueError):
            pass
    return supported[0]


def _provider_boundary_preferences(
    kind: str,
    provider_cfg: Mapping[str, Any],
    provider_pref: Mapping[str, Any],
) -> Dict[str, Any]:
    """Mirror the runtime preference chain for the provider API boundary."""
    baseline = PROVIDER_AUDIO_BASELINES.get(kind, {})

    def _value(*keys: str, fallback: Any) -> Any:
        for key in keys:
            value = provider_cfg.get(key)
            if value not in (None, ""):
                return value
        for key in keys:
            value = baseline.get(key)
            if value not in (None, ""):
                return value
        return fallback

    if kind in _KINDS_WITH_PROVIDER_INPUT:
        in_enc = _value("provider_input_encoding", fallback="linear16")
        in_rate = _value("provider_input_sample_rate_hz", fallback=16000)
    elif kind in FULL_AGENT_KINDS and kind != "local":
        # Legacy contract (Deepgram): input_* IS the provider boundary.
        in_enc = _value("input_encoding", fallback="linear16")
        in_rate = _value("input_sample_rate_hz", fallback=16000)
    else:
        in_enc = provider_pref.get("input_encoding", "linear16")
        in_rate = provider_pref.get("input_sample_rate_hz", 16000)
    out_enc = _value(
        "provider_output_encoding", "output_encoding",
        fallback=provider_pref.get("output_encoding", "linear16"),
    )
    out_rate = _value(
        "provider_output_sample_rate_hz", "output_sample_rate_hz",
        fallback=provider_pref.get("output_sample_rate_hz", 16000),
    )
    return {
        "input_encoding": in_enc,
        "input_sample_rate_hz": in_rate,
        "output_encoding": out_enc,
        "output_sample_rate_hz": out_rate,
    }


def _resolve_chain(
    *,
    agent: Mapping[str, Any],
    config: Mapping[str, Any],
    profiles: Mapping[str, Any],
    default_profile_name: str,
    findings: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    agent_name = str(agent.get("display_name") or agent.get("slug") or "default")
    explicit_profile = str(agent.get("audio_profile") or "").strip()
    profile_name = explicit_profile or default_profile_name
    profile = profiles.get(profile_name)
    if not isinstance(profile, Mapping):
        findings.append(_finding(
            "error", "profile_missing",
            f"Audio profile '{profile_name}' not found",
            f"Agent '{agent_name}' resolves to profile '{profile_name}', which is "
            "not defined under profiles: — calls for this Agent fail closed.",
            agent=agent_name, profile=profile_name,
        ))
        return None

    audio_transport = str(config.get("audio_transport") or "audiosocket")
    wire = resolve_wire_contract(
        profile, audio_transport, config.get("external_media")
    )

    providers = config.get("providers") if isinstance(config.get("providers"), Mapping) else {}
    pipelines = config.get("pipelines") if isinstance(config.get("pipelines"), Mapping) else {}

    # An Agent selects EITHER a monolithic provider OR a modular pipeline
    # (the editor clears one when the other is chosen). A pipeline Agent's
    # provider column is empty, and its boundary comes from the profile's
    # provider_pref — never from default_provider.
    provider_key = str(agent.get("provider") or "").strip()
    pipeline_name = str(agent.get("pipeline") or "").strip()
    if provider_key and provider_key not in providers and provider_key in pipelines:
        # Robustness: a name that only exists under pipelines: is a pipeline,
        # whichever column it arrived in.
        pipeline_name, provider_key = provider_key, ""
    if not provider_key and not pipeline_name:
        # System default, mirroring full_agent_default(): a full-agent
        # default_provider wins, otherwise the active pipeline runs.
        default_provider = str(config.get("default_provider") or "").strip()
        default_cfg = providers.get(default_provider)
        default_kind = (
            provider_kind(default_provider, default_cfg)
            if isinstance(default_cfg, Mapping)
            else None
        )
        if default_kind in FULL_AGENT_KINDS:
            provider_key = default_provider
        elif default_provider in pipelines:
            pipeline_name = default_provider
        else:
            pipeline_name = str(config.get("active_pipeline") or "").strip()

    provider_cfg = providers.get(provider_key) if provider_key else None
    provider_cfg = provider_cfg if isinstance(provider_cfg, Mapping) else {}
    kind = provider_kind(provider_key, provider_cfg) if provider_key else None
    is_full_agent = bool(kind and kind in FULL_AGENT_KINDS)
    if pipeline_name and not is_full_agent:
        provider_key = ""
        provider_cfg = {}
        kind = None

    pipeline_components: Optional[Dict[str, str]] = None
    if pipeline_name:
        entry = pipelines.get(pipeline_name)
        if isinstance(entry, str):
            pipeline_components = {"stt": entry, "llm": entry, "tts": entry}
        elif isinstance(entry, Mapping):
            pipeline_components = {
                role: str(entry.get(role) or "") for role in ("stt", "llm", "tts")
            }
        else:
            findings.append(_finding(
                "error", "pipeline_missing",
                f"Pipeline '{pipeline_name}' not found",
                f"Agent '{agent_name}' selects pipeline '{pipeline_name}', which is "
                "not defined under pipelines: — calls for this Agent fail closed.",
                agent=agent_name, profile=profile_name,
            ))

    provider_pref = profile.get("provider_pref")
    provider_pref = provider_pref if isinstance(provider_pref, Mapping) else {}
    prefs = _provider_boundary_preferences(kind or "", provider_cfg, provider_pref)

    caps = PROVIDER_AUDIO_CAPABILITIES.get(kind or "")
    negotiated = dict(prefs)
    boundary_source = "provider" if is_full_agent else "profile"
    if caps:
        wideband_selected = (
            audio_transport == "audiosocket"
            and normalize_encoding(wire["out"]["encoding"]) == "linear16"
            and int(wire["out"]["sample_rate_hz"] or 0) >= 16000
            and all(
                caps.get(key) is not None
                for key in (
                    "wideband_input_encoding",
                    "wideband_input_sample_rate_hz",
                    "wideband_output_encoding",
                    "wideband_output_sample_rate_hz",
                )
            )
        )
        if wideband_selected:
            negotiated = {
                "input_encoding": caps["wideband_input_encoding"],
                "input_sample_rate_hz": caps["wideband_input_sample_rate_hz"],
                "output_encoding": caps["wideband_output_encoding"],
                "output_sample_rate_hz": caps["wideband_output_sample_rate_hz"],
            }
            boundary_source = "provider-wideband-capability"
        else:
            negotiated = {
                "input_encoding": _select(
                    prefs["input_encoding"], caps["input_encodings"], encoding=True
                ),
                "input_sample_rate_hz": _select(
                    prefs["input_sample_rate_hz"], caps["input_sample_rates_hz"], encoding=False
                ),
                "output_encoding": _select(
                    prefs["output_encoding"], caps["output_encodings"], encoding=True
                ),
                "output_sample_rate_hz": _select(
                    prefs["output_sample_rate_hz"], caps["output_sample_rates_hz"], encoding=False
                ),
            }

    chain = {
        "agent": agent_name,
        "agent_slug": agent.get("slug"),
        "is_default_agent": bool(agent.get("is_default")),
        "profile": profile_name,
        # Whether the Agent selected the profile itself or inherited
        # profiles.default — Agents only point at a profile, they never carry
        # audio format settings of their own.
        "profile_source": "agent" if explicit_profile else "default",
        "provider": provider_key or None,
        "provider_kind": kind,
        "pipeline": pipeline_name or None,
        "pipeline_components": pipeline_components,
        "boundary_source": boundary_source,
        "audio_transport": audio_transport,
        "wire_out": wire["out"],
        "wire_in": wire["in"],
        "rtp_transit_rate_hz": (
            resolve_external_media_transit_rate(config.get("external_media"))["sample_rate_hz"]
            if audio_transport == "externalmedia"
            else None
        ),
        "provider_boundary": negotiated,
        "internal_rate_hz": profile.get("internal_rate_hz", 8000),
        "output_resampler": profile.get("output_resampler", "linear"),
    }

    _collect_chain_findings(
        chain=chain,
        config=config,
        profile=profile,
        provider_cfg=provider_cfg,
        prefs=prefs,
        caps=caps,
        findings=findings,
    )
    return chain


def _collect_chain_findings(
    *,
    chain: Mapping[str, Any],
    config: Mapping[str, Any],
    profile: Mapping[str, Any],
    provider_cfg: Mapping[str, Any],
    prefs: Mapping[str, Any],
    caps: Optional[Mapping[str, Any]],
    findings: List[Dict[str, Any]],
) -> None:
    agent = str(chain["agent"])
    profile_name = str(chain["profile"])
    provider_key = chain.get("provider")
    kind = chain.get("provider_kind")
    audio_transport = str(chain["audio_transport"])
    wire_out = chain["wire_out"]
    wire_in = chain["wire_in"]

    # 1) Companded profile on AudioSocket rides the slin carrier — the exact
    #    situation operators read as "I picked alaw but it negotiates slin".
    if wire_out.get("carrier"):
        findings.append(_finding(
            "info", "audiosocket_slin_carrier",
            f"'{profile_name}' ({wire_out['declared_encoding']}) rides the AudioSocket slin carrier",
            "AudioSocket frames are signed-linear PCM, so a companded profile "
            f"({wire_out['declared_encoding']}) uses the lossless slin@8000 carrier on the "
            "engine leg. Asterisk transcodes to the trunk codec "
            f"({wire_out['declared_encoding']} stays intact toward the caller). This is the "
            "expected contract, not a misconfiguration.",
            agent=agent, profile=profile_name,
        ))

    # 2) ExternalMedia: the Audio Transport RTP settings own the wire — one
    #    codec, both directions, identical for every profile and Agent.
    if audio_transport == "externalmedia":
        em_wire = resolve_external_media_wire(config.get("external_media"))
        rtp_codec = em_wire["codec"]
        if em_wire["configured"] and em_wire["source"] == "builtin-default":
            findings.append(_finding(
                "warning", "rtp_codec_unsupported",
                f"external_media.codec={em_wire['configured']} is not a supported RTP codec",
                "The RTP server can carry ulaw, alaw, and slin16. The configured "
                f"value falls back to {rtp_codec}@{em_wire['sample_rate_hz']}. "
                "Pick a supported codec on the Audio Transport page.",
            ))
        transport_out = profile.get("transport_out")
        declared_out = (
            str(transport_out.get("encoding") or "").strip().lower()
            if isinstance(transport_out, Mapping)
            else ""
        )
        profile_declares_in = bool(profile.get("transport_in"))
        if (
            declared_out
            and normalize_encoding(declared_out) != normalize_encoding(rtp_codec)
        ) or profile_declares_in:
            findings.append(_finding(
                "info", "profile_transport_ignored_on_rtp",
                f"'{profile_name}' transport settings do not apply on ExternalMedia",
                "ExternalMedia runs one process-wide RTP codec from the Audio "
                f"Transport settings ({rtp_codec}@{em_wire['sample_rate_hz']}, "
                "both directions, shared by every Agent). The profile's "
                "Transport Output/Input govern the AudioSocket wire only; the "
                "profile still owns the internal rate and provider preferences.",
                agent=agent, profile=profile_name,
            ))
        if int(wire_out.get("sample_rate_hz") or 0) > 8000:
            findings.append(_finding(
                "warning", "wideband_rtp_unsupported",
                "external_media.codec=slin16 selects 16 kHz RTP",
                "16 kHz RTP is outside the supported release baseline "
                "(ulaw/alaw at 8 kHz). For wideband audio use AudioSocket with "
                "a wideband profile instead.",
            ))

    # 3) Declared asymmetric inbound leg on AudioSocket is advisory.
    if audio_transport == "audiosocket" and wire_in.get("declared"):
        findings.append(_finding(
            "info", "transport_in_advisory",
            f"'{profile_name}' declares transport_in on AudioSocket",
            "AudioSocket announces the inbound format per frame, so the engine "
            "decodes what actually arrives; the declared transport_in is used for "
            "diagnostics and RTP fallbacks only.",
            agent=agent, profile=profile_name,
        ))

    if not provider_key:
        return

    # 4) Wire-facing provider YAML fields are derived from the profile per call.
    derived_checks = [
        ("target_encoding", wire_out["encoding"], True),
        ("target_sample_rate_hz", wire_out["sample_rate_hz"], False),
    ]
    if kind in _KINDS_WITH_PROVIDER_INPUT:
        derived_checks.extend([
            ("input_encoding", wire_in["encoding"], True),
            ("input_sample_rate_hz", wire_in["sample_rate_hz"], False),
        ])
    for field, derived_value, is_encoding in derived_checks:
        configured = provider_cfg.get(field)
        if configured in (None, ""):
            continue
        differs = (
            normalize_encoding(configured) != normalize_encoding(derived_value)
            if is_encoding
            else str(configured) != str(derived_value)
        )
        if differs:
            carrier_note = ""
            if is_encoding and wire_out.get("carrier") and normalize_encoding(
                configured
            ) == normalize_encoding(wire_out.get("declared_encoding")):
                carrier_note = (
                    f" ({wire_out['declared_encoding']} is preserved on the trunk; "
                    "slin is only the lossless AudioSocket carrier)"
                )
            findings.append(_finding(
                "warning", "provider_wire_field_derived",
                f"providers.{provider_key}.{field}={configured} is overridden per call",
                f"This field is wire-facing and derived from audio profile "
                f"'{profile_name}' at call setup: the call uses "
                f"{derived_value}{carrier_note}. Edit the wire contract on the "
                "Audio Profile; the provider card only owns the provider-native "
                "boundary. Remove the YAML value to silence this warning.",
                agent=agent, profile=profile_name, provider=str(provider_key),
            ))

    # 5) Provider-native boundary must fit the adapter's capabilities.
    if caps and chain["boundary_source"] != "provider-wideband-capability":
        negotiated = chain["provider_boundary"]
        for pref_key, caps_key, negotiated_key, is_encoding in (
            ("input_encoding", "input_encodings", "input_encoding", True),
            ("input_sample_rate_hz", "input_sample_rates_hz", "input_sample_rate_hz", False),
            ("output_encoding", "output_encodings", "output_encoding", True),
            ("output_sample_rate_hz", "output_sample_rates_hz", "output_sample_rate_hz", False),
        ):
            preferred = prefs[pref_key]
            selected = negotiated[negotiated_key]
            same = (
                normalize_encoding(preferred) == normalize_encoding(selected)
                if is_encoding
                else str(preferred) == str(selected)
            )
            if not same:
                findings.append(_finding(
                    "warning", "provider_boundary_renegotiated",
                    f"{provider_key}: {pref_key}={preferred} is outside the adapter's supported set",
                    f"Supported: {caps[caps_key]}. The call negotiates "
                    f"{selected} instead. Fix the value on the provider card (it "
                    "must match the provider-side/dashboard audio settings).",
                    agent=agent, provider=str(provider_key),
                ))

    # 6) provider_pref on a profile is a pipeline-only preference.
    if (
        chain.get("provider_kind") in FULL_AGENT_KINDS
        and isinstance(profile.get("provider_pref"), Mapping)
        and profile.get("provider_pref")
        and chain["boundary_source"].startswith("provider")
    ):
        findings.append(_finding(
            "info", "provider_pref_ignored_for_full_agent",
            f"'{profile_name}' provider_pref does not apply to monolithic '{provider_key}'",
            "A monolithic (full-agent) provider takes its API boundary from the "
            "provider card; the profile's Provider Preferences apply to pipeline "
            "Agents only.",
            agent=agent, profile=profile_name, provider=str(provider_key),
        ))


def evaluate_audio_alignment(
    config: Mapping[str, Any],
    agents: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Compute effective audio chains and alignment findings for the Admin UI."""
    profiles_cfg = config.get("profiles")
    profiles: Dict[str, Any] = (
        {k: v for k, v in profiles_cfg.items() if k != "default" and isinstance(v, Mapping)}
        if isinstance(profiles_cfg, Mapping)
        else {}
    )
    configured_default = (
        profiles_cfg.get("default") if isinstance(profiles_cfg, Mapping) else None
    )
    default_profile_name = (
        str(configured_default).strip()
        if isinstance(configured_default, str) and str(configured_default).strip()
        else "telephony_ulaw_8k"
    )

    rows = [dict(agent) for agent in (agents or []) if isinstance(agent, Mapping)]
    if not rows:
        rows = [{
            "slug": None,
            "display_name": "Default",
            "provider": config.get("default_provider"),
            "audio_profile": None,
            "is_default": True,
        }]

    findings: List[Dict[str, Any]] = []
    chains: List[Dict[str, Any]] = []
    for agent in rows:
        chain = _resolve_chain(
            agent=agent,
            config=config,
            profiles=profiles,
            default_profile_name=default_profile_name,
            findings=findings,
        )
        if chain:
            chains.append(chain)

    # De-duplicate identical findings raised for multiple Agents on the same
    # profile/provider pair, keeping the first agent as the representative.
    seen: Dict[tuple, Dict[str, Any]] = {}
    for finding in findings:
        key = (
            finding["code"],
            finding["title"],
            finding["scope"].get("profile"),
            finding["scope"].get("provider"),
        )
        if key in seen:
            existing = seen[key]
            agents_list = existing.setdefault("agents", [existing["scope"].get("agent")])
            agent_name = finding["scope"].get("agent")
            if agent_name and agent_name not in agents_list:
                agents_list.append(agent_name)
        else:
            seen[key] = finding
            if finding["scope"].get("agent"):
                finding["agents"] = [finding["scope"]["agent"]]
    deduped = list(seen.values())

    severity_rank = {"error": 0, "warning": 1, "info": 2}
    deduped.sort(key=lambda item: severity_rank.get(item["severity"], 3))

    return {
        "audio_transport": str(config.get("audio_transport") or "audiosocket"),
        "default_profile": default_profile_name,
        "chains": chains,
        "findings": deduped,
    }
