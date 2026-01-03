# Outbound Prompt Assets (en-US)

This folder contains **small, versioned telephony prompt assets** shipped with Asterisk AI Voice Agent for the outbound campaign dialer.

## Files

- `aava-consent-default.ulaw`
  - Default consent prompt (DTMF 1 accept / 2 deny).
- `aava-voicemail-default.ulaw`
  - Default voicemail drop message.

These are **8kHz μ-law (G.711 ulaw)** raw audio files intended to be placed in the runtime media directory:

- Host path: `./asterisk_media/ai-generated/`
- Container path: `/mnt/asterisk_media/ai-generated/`
- Asterisk sounds path (symlink): `/var/lib/asterisk/sounds/ai-generated/`

Media URIs used by Asterisk:

- `sound:ai-generated/aava-consent-default`
- `sound:ai-generated/aava-voicemail-default`

## Licensing

These prompt assets are provided under this repository’s license (same as the codebase).
