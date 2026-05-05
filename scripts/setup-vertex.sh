#!/usr/bin/env bash
#
# setup-vertex.sh — One-command Google Vertex AI setup for AAVA
#
# Run this on the AAVA host (the box where docker compose runs), NOT your laptop.
# It writes ./secrets/gcp-service-account.json and patches ./.env, both of which
# need to be on the host that runs the ai_engine container.
#
# On a headless server (typical FreePBX box over SSH), the script will print a
# URL — open it on your laptop, sign in to Google, then paste the verification
# code back into the terminal.
#
# Usage:
#   ./scripts/setup-vertex.sh                    # interactive (prompts for project)
#   ./scripts/setup-vertex.sh my-gcp-project-id  # non-interactive
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SECRETS_DIR="$REPO_ROOT/secrets"
SA_NAME="aava-vertex"
SA_KEY_FILE="$SECRETS_DIR/gcp-service-account.json"
ENV_FILE="$REPO_ROOT/.env"
DEFAULT_LOCATION="us-central1"

# ── Colors ──────────────────────────────────────────────────────────────────
red()   { printf '\033[0;31m%s\033[0m\n' "$*"; }
green() { printf '\033[0;32m%s\033[0m\n' "$*"; }
cyan()  { printf '\033[0;36m%s\033[0m\n' "$*"; }
bold()  { printf '\033[1m%s\033[0m\n' "$*"; }

# ── Preflight ───────────────────────────────────────────────────────────────
if ! command -v gcloud &>/dev/null; then
  red "Error: gcloud CLI not found."
  echo "Install it from: https://cloud.google.com/sdk/docs/install"
  exit 1
fi

if ! gcloud auth print-access-token &>/dev/null 2>&1; then
  echo "Not logged in to gcloud."
  if [[ -n "${DISPLAY:-}" ]] || [[ "$(uname)" == "Darwin" ]]; then
    echo "Opening browser to authenticate..."
    gcloud auth login --brief
  else
    echo "Headless host detected. gcloud will print a URL — open it on your"
    echo "laptop, sign in, then paste the verification code back here."
    echo
    gcloud auth login --no-launch-browser --brief
  fi
fi

# ── Resolve project ─────────────────────────────────────────────────────────
PROJECT=""

if [[ -n "${1:-}" ]]; then
  PROJECT="$1"
else
  CURRENT_PROJECT=$(gcloud config get-value project 2>/dev/null || true)
  if [[ -n "$CURRENT_PROJECT" && "$CURRENT_PROJECT" != "(unset)" ]]; then
    read -rp "Use GCP project \"$CURRENT_PROJECT\"? [Y/n] " yn
    if [[ ! "$yn" =~ ^[Nn] ]]; then
      PROJECT="$CURRENT_PROJECT"
    fi
  fi

  if [[ -z "$PROJECT" ]]; then
    echo
    echo "Listing your GCP projects..."
    mapfile -t EXISTING < <(gcloud projects list --format="value(projectId)" 2>/dev/null || true)

    if [[ ${#EXISTING[@]} -gt 0 ]]; then
      echo "Available projects:"
      for i in "${!EXISTING[@]}"; do
        printf "  %d) %s\n" "$((i+1))" "${EXISTING[$i]}"
      done
      printf "  %d) Create a new project\n" "$(( ${#EXISTING[@]} + 1 ))"
      echo
      read -rp "Pick a number, or type a project ID: " choice

      if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#EXISTING[@]} )); then
        PROJECT="${EXISTING[$((choice-1))]}"
      elif [[ "$choice" =~ ^[0-9]+$ ]] && (( choice == ${#EXISTING[@]} + 1 )); then
        PROJECT=""  # fall through to create flow
      else
        PROJECT="$choice"
      fi
    else
      echo "No existing projects found on this account."
    fi
  fi

  if [[ -z "$PROJECT" ]]; then
    echo
    echo "Let's create a new GCP project for AAVA."
    echo "Project IDs must be 6-30 chars, lowercase letters/digits/hyphens, globally unique."
    read -rp "New project ID (e.g. aava-vertex-$(date +%s | tail -c 5)): " PROJECT
    if [[ -z "$PROJECT" ]]; then
      red "Error: project ID required."
      exit 1
    fi
    cyan "Creating project $PROJECT..."
    gcloud projects create "$PROJECT" --name="AAVA Vertex" --quiet
    green "  ✓ Project created"

    echo
    echo "Vertex AI requires a billing account linked to the project."
    mapfile -t BILLING < <(gcloud billing accounts list --filter="open=true" --format="value(name,displayName)" 2>/dev/null || true)
    if [[ ${#BILLING[@]} -eq 0 ]]; then
      red "  No active billing accounts found on this Google account."
      echo "  Set one up at: https://console.cloud.google.com/billing"
      echo "  Then re-run this script with your new project ID:"
      echo "    ./scripts/setup-vertex.sh $PROJECT"
      exit 1
    fi
    echo "  Available billing accounts:"
    for i in "${!BILLING[@]}"; do
      printf "    %d) %s\n" "$((i+1))" "${BILLING[$i]}"
    done
    read -rp "  Pick one: " bchoice
    if [[ "$bchoice" =~ ^[0-9]+$ ]] && (( bchoice >= 1 && bchoice <= ${#BILLING[@]} )); then
      BILLING_ID=$(echo "${BILLING[$((bchoice-1))]}" | awk '{print $1}' | sed 's|billingAccounts/||')
      gcloud billing projects link "$PROJECT" --billing-account="$BILLING_ID" --quiet
      green "  ✓ Billing linked"
    else
      red "  Invalid choice. Link billing manually then re-run."
      exit 1
    fi
  fi
fi

if [[ -z "$PROJECT" ]]; then
  red "Error: No project ID provided."
  exit 1
fi

if ! gcloud projects describe "$PROJECT" &>/dev/null; then
  red "Error: Project \"$PROJECT\" not found or not accessible."
  echo "Check the ID at https://console.cloud.google.com/cloud-resource-manager"
  exit 1
fi

bold "Setting up Vertex AI for project: $PROJECT"
echo

# ── Step 1: Enable Vertex AI API ────────────────────────────────────────────
cyan "[1/5] Enabling Vertex AI API..."
gcloud services enable aiplatform.googleapis.com --project="$PROJECT" --quiet
green "  ✓ Vertex AI API enabled"

# ── Step 2: Create service account ──────────────────────────────────────────
cyan "[2/5] Creating service account..."
SA_EMAIL="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"

if gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT" &>/dev/null 2>&1; then
  green "  ✓ Service account already exists: $SA_EMAIL"
else
  gcloud iam service-accounts create "$SA_NAME" \
    --project="$PROJECT" \
    --display-name="AAVA Vertex AI" \
    --quiet
  green "  ✓ Created service account: $SA_EMAIL"
fi

# ── Step 3: Grant Vertex AI User role ───────────────────────────────────────
cyan "[3/5] Granting Vertex AI User role..."
gcloud projects add-iam-policy-binding "$PROJECT" \
  --member="serviceAccount:$SA_EMAIL" \
  --role="roles/aiplatform.user" \
  --condition=None \
  --quiet >/dev/null 2>&1
green "  ✓ Role granted"

# ── Step 4: Download JSON key ───────────────────────────────────────────────
cyan "[4/5] Downloading service account key..."
mkdir -p "$SECRETS_DIR"

if [[ -f "$SA_KEY_FILE" ]]; then
  # Detect existing key's project so we can warn if user picked a different one.
  EXISTING_KEY_PROJECT="$(sed -n 's/.*"project_id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$SA_KEY_FILE" | head -n1)"
  if [[ -n "$EXISTING_KEY_PROJECT" && "$EXISTING_KEY_PROJECT" != "$PROJECT" ]]; then
    red "  ⚠ Existing key belongs to project \"$EXISTING_KEY_PROJECT\", not \"$PROJECT\"."
    red "    Keeping it and writing GOOGLE_CLOUD_PROJECT=$PROJECT would cause confusing auth failures."
    read -rp "  Overwrite with a fresh key for \"$PROJECT\"? [y/N] " overwrite
  else
    read -rp "  Key file already exists. Overwrite? [y/N] " overwrite
  fi
  if [[ ! "$overwrite" =~ ^[Yy] ]]; then
    green "  ✓ Keeping existing key"
    # Don't silently overwrite GOOGLE_CLOUD_PROJECT to a value that doesn't
    # match the kept key — pin the .env to the key's actual project instead.
    if [[ -n "$EXISTING_KEY_PROJECT" && "$EXISTING_KEY_PROJECT" != "$PROJECT" ]]; then
      cyan "  → Pinning GOOGLE_CLOUD_PROJECT to existing key's project: $EXISTING_KEY_PROJECT"
      PROJECT="$EXISTING_KEY_PROJECT"
    fi
  else
    gcloud iam service-accounts keys create "$SA_KEY_FILE" \
      --iam-account="$SA_EMAIL" \
      --project="$PROJECT" \
      --quiet
    chmod 600 "$SA_KEY_FILE"
    green "  ✓ Key saved to secrets/gcp-service-account.json"
  fi
else
  gcloud iam service-accounts keys create "$SA_KEY_FILE" \
    --iam-account="$SA_EMAIL" \
    --project="$PROJECT" \
    --quiet
  chmod 600 "$SA_KEY_FILE"
  green "  ✓ Key saved to secrets/gcp-service-account.json"
fi

# ── Step 5: Patch .env ──────────────────────────────────────────────────────
cyan "[5/5] Updating .env..."

patch_env_var() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    sed -i.bak "s|^${key}=.*|${key}=${val}|" "$ENV_FILE"
  elif grep -q "^# *${key}=" "$ENV_FILE" 2>/dev/null; then
    sed -i.bak "s|^# *${key}=.*|${key}=${val}|" "$ENV_FILE"
  else
    echo "${key}=${val}" >> "$ENV_FILE"
  fi
}

if [[ ! -f "$ENV_FILE" ]]; then
  cp "$REPO_ROOT/.env.example" "$ENV_FILE" 2>/dev/null || touch "$ENV_FILE"
fi

patch_env_var "GOOGLE_CLOUD_PROJECT" "$PROJECT"
patch_env_var "GOOGLE_CLOUD_LOCATION" "$DEFAULT_LOCATION"
rm -f "$ENV_FILE.bak"

green "  ✓ GOOGLE_CLOUD_PROJECT=$PROJECT"
green "  ✓ GOOGLE_CLOUD_LOCATION=$DEFAULT_LOCATION"

# ── Summary ─────────────────────────────────────────────────────────────────
echo
bold "Done! One thing left — enable Vertex AI in your config."
echo
echo "  In config/ai-agent.yaml, under providers → google_live, add:"
echo
cyan "    use_vertex_ai: true"
cyan "    vertex_project: \${GOOGLE_CLOUD_PROJECT}"
cyan "    vertex_location: \${GOOGLE_CLOUD_LOCATION:-us-central1}"
cyan "    llm_model: gemini-live-2.5-flash-native-audio"
echo
echo "  Then restart:"
echo
cyan "    docker compose restart ai_engine"
echo
echo "  Verify with:"
echo
cyan "    docker logs ai_engine 2>&1 | grep -i vertex"
echo
