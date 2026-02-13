# Telephony Providers

This document describes how to configure telephony providers (SIP trunks) for the Asterisk AI Voice Agent.

## Telnyx

Telnyx is a global communications platform offering SIP trunking, programmable voice, and SMS services. It is a reliable, low-latency alternative for telephony infrastructure.

### Setup

1.  **Create Connection**: Log in to the [Telnyx Mission Control Portal](https://portal.telnyx.com) and create a new Connection.
2.  **Get Credentials**: Note the **SIP Login**, **SIP Password**, and **SIP Realm** (usually `telnyx.com`).
3.  **Assign Number**: Purchase or assign a phone number to the Connection.

### Asterisk Configuration

Add the following to your `pjsip.conf` (or `sip.conf` for legacy):

```ini
[transport-tls]
type=transport
protocol=tls
bind=0.0.0.0:5061
cert_file=/etc/asterisk/keys/asterisk.crt
priv_key_file=/etc/asterisk/keys/asterisk.key
method=tlsv1_2

[telnyx]
type=endpoint
context=from-telnyx
disallow=all
allow=ulaw
media_encryption=sdes
transport=transport-tls

[telnyx]
type=registration
expiry=3600
client_uri=sip:<span style="color: orange">SIP_LOGIN</span>@telnyx.com
server_uri=sip:telnyx.com:5061
transport=transport-tls
outbound_auth=telnyx_auth

[telnyx_auth]
type=auth
username=<span style="color: orange">SIP_LOGIN</span>
password=<span style="color: orange">SIP_PASSWORD</span>

[telnyx]
type=aor
contact=sip:<span style="color: orange">DESTINATION_NUMBER</span>@telnyx.com:5061
```

*Note: The configuration relies on SIP registration. Ensure your Asterisk server is reachable from the internet (port 5061/UDP) or use Telnyx's IP authentication if you have a static IP.*

### Why Telnyx?

-   **Global Coverage**: 100+ countries.
-   **Programmable Voice & SMS**: robust APIs.
-   **High Performance**: low latency T1/fiber network.
-   **AI-Ready**: Integrated inference API with 53 AI models.

### Resources

-   **Dashboard**: https://portal.telnyx.com
-   **Documentation**: https://developers.telnyx.com/docs/voice