# Google Vertex AI Setup Guide

## Overview

The `google_live` provider can connect via **two authentication modes**:

| Feature | Developer API (default) | Vertex AI |
|---------|------------------------|-----------|
| **Auth** | API key (`GOOGLE_API_KEY`) | OAuth2 service account JSON |
| **Endpoint** | `generativelanguage.googleapis.com` | `{location}-aiplatform.googleapis.com` |
| **Models** | Preview (`*-preview-*`) | GA (`gemini-live-2.5-flash-native-audio`) |
| **Function calling** | 1008 bug (~1 in 5–10 calls) | Fixed in GA model |
| **Enterprise** | No SLA | SLA, VPC-SC, audit logging |

This guide walks you through obtaining the service account JSON and enabling Vertex AI mode. For dialplan, pipeline, and advanced configuration, see [Provider-Google-Setup.md](Provider-Google-Setup.md).

## Quick Setup (recommended)

**Run this on your AAVA host** (the FreePBX / Asterisk box where `docker compose` runs) — not on your laptop. The script writes `secrets/gcp-service-account.json` and patches `.env`, both of which need to be on the host that runs the `ai_engine` container.

If you have the `gcloud` CLI installed there ([install guide](https://cloud.google.com/sdk/docs/install)), run:

```bash
./scripts/setup-vertex.sh
```

On a headless server over SSH, the script auto-detects there's no display and uses gcloud's code-paste auth flow — it prints a URL, you open it on your laptop, sign in, paste the verification code back into the terminal. No browser needed on the box itself.

This handles everything: enables the Vertex AI API, creates a service account with the right role, downloads the JSON key into `secrets/`, and patches your `.env`.

### What the script asks for

After Google sign-in, the script needs to know which GCP project to use:

- **You already have projects** — the script lists them and lets you pick one by number (or type a project ID directly). You can also pass it up front: `./scripts/setup-vertex.sh my-project-id`.
- **You want a new project** — pick the "Create a new project" option (or run on a brand-new account with no projects). The script will:
  1. Prompt for a new project ID (must be 6–30 chars, lowercase letters/digits/hyphens, globally unique across GCP)
  2. Create it via `gcloud projects create`
  3. List your billing accounts and link one — Vertex AI won't run without active billing.
- **You have no billing account at all** — the script stops with a pointer to `https://console.cloud.google.com/billing`. Setting up billing requires entering payment details in the GCP Console, which can't be automated. Once billing is set up, re-run the script.

### Workspace / org accounts

If your Google account is part of a Workspace organization, your admin may need to allow project creation outside the org's hierarchy. If `gcloud projects create` fails with a policy error, that's the cause — either ask your admin or create the project in the GCP Console first, then re-run with `./scripts/setup-vertex.sh <existing-project-id>`.

After the script finishes, enable Vertex AI in `config/ai-agent.yaml`:

```yaml
providers:
  google_live:
    use_vertex_ai: true
    vertex_project: ${GOOGLE_CLOUD_PROJECT}
    vertex_location: ${GOOGLE_CLOUD_LOCATION:-us-central1}
    llm_model: gemini-live-2.5-flash-native-audio
```

Then restart: `docker compose restart ai_engine`

---

## Manual Setup

If you prefer to set things up manually or don't have `gcloud`, follow the steps below.

### Prerequisites

- A Google Cloud project with billing enabled
- The **Vertex AI API** enabled:
  https://console.cloud.google.com/apis/library/aiplatform.googleapis.com

### Step 1: Create a Service Account

1. Open **IAM & Admin → Service Accounts** in the GCP Console:
   https://console.cloud.google.com/iam-admin/serviceaccounts
2. Click **Create Service Account**
3. Fill in:
   - **Name**: `aava-vertex` (or any descriptive name)
   - **Role**: `Vertex AI User` (`roles/aiplatform.user`)
4. Click **Done**

### Step 2: Download the JSON Key

1. Click the service account you just created
2. Go to the **Keys** tab
3. Click **Add Key → Create new key → JSON**
4. Save the downloaded `.json` file — this is your credential file

The JSON contains `project_id`, `client_email`, and `private_key`. Keep it secure.

### Step 3: Upload Credentials

#### Option A: Admin UI (recommended)

1. Open the Admin UI → **Settings → Vertex AI**
2. Click **Upload Credential JSON** and select your downloaded file
3. The UI saves it to `/app/project/secrets/gcp-service-account.json` and auto-sets `GOOGLE_APPLICATION_CREDENTIALS` in `.env`
4. Click **Verify** to confirm the credentials can acquire an OAuth2 token

#### Option B: Manual

1. Copy the JSON into your project's `secrets/` directory:
   ```bash
   cp ~/Downloads/aava-vertex-*.json ./secrets/gcp-service-account.json
   ```
2. The `docker-compose.yml` volume mount (`./secrets:/app/project/secrets`) makes it available inside the container
3. The engine auto-detects the file at `/app/project/secrets/gcp-service-account.json` on startup — no need to set `GOOGLE_APPLICATION_CREDENTIALS` manually

### Step 4: Set Environment Variables

Add to your `.env` file:

```bash
GOOGLE_CLOUD_PROJECT=my-gcp-project-id
GOOGLE_CLOUD_LOCATION=us-central1
```

### Step 5: Enable Vertex AI in Config

In `config/ai-agent.yaml`:

```yaml
providers:
  google_live:
    use_vertex_ai: true
    vertex_project: ${GOOGLE_CLOUD_PROJECT}
    vertex_location: ${GOOGLE_CLOUD_LOCATION:-us-central1}
    llm_model: gemini-live-2.5-flash-native-audio
```

> **Note**: `api_key` is not used in Vertex AI mode. Authentication is handled entirely via the service account JSON.

### Step 6: Restart & Verify

```bash
docker compose restart ai_engine
docker logs ai_engine 2>&1 | grep -i vertex
```

Look for a log line confirming the Vertex AI endpoint is active. If ADC (Application Default Credentials) fails, the engine gracefully falls back to Developer API mode using `GOOGLE_API_KEY`.

## Available Regions

| Region | Location |
|--------|----------|
| `us-central1` | Iowa (default) |
| `us-east1` | South Carolina |
| `us-east4` | Northern Virginia |
| `us-west1` | Oregon |
| `us-west4` | Las Vegas |
| `europe-west1` | Belgium |
| `europe-west2` | London |
| `europe-west3` | Frankfurt |
| `europe-west4` | Netherlands |
| `asia-east1` | Taiwan |
| `asia-northeast1` | Tokyo |
| `asia-southeast1` | Singapore |
| `australia-southeast1` | Sydney |

Set `vertex_location` to the region closest to your Asterisk server for lowest latency.

## Troubleshooting

### Issue: "ADC failed" — falls back to Developer API

**Cause**: The service account JSON is missing or unreadable inside the container.

**Solution**:
1. Verify the file exists: `docker exec ai_engine ls -la /app/project/secrets/gcp-service-account.json`
2. Verify it's valid JSON: `docker exec ai_engine python3 -c "import json; json.load(open('/app/project/secrets/gcp-service-account.json'))"`
3. Check the `secrets/` directory has correct permissions on the host

### Issue: "vertex_project is required"

**Cause**: `use_vertex_ai: true` is set but `GOOGLE_CLOUD_PROJECT` is empty or missing.

**Solution**: Ensure `GOOGLE_CLOUD_PROJECT` is set in `.env` and matches the `project_id` in your JSON key file.

### Issue: 403 Permission Denied

**Cause**: The service account lacks the `roles/aiplatform.user` role.

**Solution**:
1. Open IAM in GCP Console: https://console.cloud.google.com/iam-admin/iam
2. Find your service account email
3. Add the **Vertex AI User** role

## See Also

- [Provider-Google-Setup.md](Provider-Google-Setup.md) — Dialplan, pipelines, advanced config, Google Live architecture
- [Configuration-Reference.md](Configuration-Reference.md) — Full config reference
- [Transport-Mode-Compatibility.md](Transport-Mode-Compatibility.md) — Audio transport options
