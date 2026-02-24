#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SRC_DIR="${PROJECT_ROOT}/assets/outbound_prompts/en-US"
DST_DIR="${PROJECT_ROOT}/asterisk_media/ai-generated"

CONSENT_SRC="${SRC_DIR}/aava-consent-default.ulaw"
VM_SRC="${SRC_DIR}/aava-voicemail-default.ulaw"
CONSENT_DST="${DST_DIR}/aava-consent-default.ulaw"
VM_DST="${DST_DIR}/aava-voicemail-default.ulaw"

if [[ ! -f "${CONSENT_SRC}" || ! -f "${VM_SRC}" ]]; then
  echo "Missing shipped prompt assets in ${SRC_DIR}."
  echo "Expected: ${CONSENT_SRC} and ${VM_SRC}"
  exit 1
fi

mkdir -p "${DST_DIR}"

install -m 0664 "${CONSENT_SRC}" "${CONSENT_DST}"
install -m 0664 "${VM_SRC}" "${VM_DST}"

echo "Installed outbound prompt assets:"
echo " - ${CONSENT_DST}"
echo " - ${VM_DST}"
echo
echo "Asterisk media URIs:"
echo " - sound:ai-generated/aava-consent-default"
echo " - sound:ai-generated/aava-voicemail-default"
