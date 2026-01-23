#!/bin/sh
set -eu

if [ "$(id -u)" != "0" ]; then
  exec "$@"
fi

ensure_user_in_gid() {
  user="$1"
  gid="$2"
  name_hint="${3:-}"

  if [ -z "${gid:-}" ] || ! echo "$gid" | grep -Eq '^[0-9]+$'; then
    return 0
  fi

  # Skip if already a member of this GID.
  if id -G "$user" 2>/dev/null | tr ' ' '\n' | grep -qx "$gid"; then
    return 0
  fi

  existing_group="$(awk -F: -v gid="$gid" '$3==gid{print $1; exit}' /etc/group 2>/dev/null || true)"
  group_name="${existing_group:-${name_hint:-gid$gid}}"
  if [ -z "${existing_group:-}" ]; then
    groupadd -g "$gid" "$group_name" 2>/dev/null || true
  fi
  usermod -aG "$group_name" "$user" 2>/dev/null || true
}

detect_gid_for_path() {
  path="$1"
  stat -c '%g' "$path" 2>/dev/null || stat -f '%g' "$path" 2>/dev/null || echo ""
}

if [ -S /var/run/docker.sock ]; then
  sock_gid="$(detect_gid_for_path /var/run/docker.sock)"
  ensure_user_in_gid appuser "$sock_gid" dockersock
fi

# Ensure the Admin UI runtime user can validate/write to the media directory (used by health checks).
# Some distros use a non-default Asterisk group GID (e.g., 996), and the directory can be owned by that group.
if [ -d /mnt/asterisk_media/ai-generated ]; then
  media_gid="$(detect_gid_for_path /mnt/asterisk_media/ai-generated)"
  ensure_user_in_gid appuser "$media_gid" asteriskmedia
elif [ -d /mnt/asterisk_media ]; then
  media_gid="$(detect_gid_for_path /mnt/asterisk_media)"
  ensure_user_in_gid appuser "$media_gid" asteriskmedia
fi

exec gosu appuser "$@"
