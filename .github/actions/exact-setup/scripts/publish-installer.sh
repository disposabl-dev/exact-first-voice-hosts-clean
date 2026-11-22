#!/usr/bin/env bash
set -euo pipefail
platform=$1
scripts=$(dirname "$0")
status="$RUNNER_TEMP/install-$platform"
if [[ ${2:-} == stage ]]; then
  # Both native commands build one device ABI. Read only a complete target receipt.
  case "$platform" in
    android) target=aarch64-linux-android ;;
    ios) target=aarch64-apple-ios ;;
    *) exit 1 ;;
  esac
  receipt="$EXACT_BAKE_OUTPUT/$platform-$target.build.json"
  until [[ -s "$receipt" ]] && jq -e --arg target "$target" '.trust == "production" and .compat.target == $target' "$receipt" >/dev/null 2>&1; do
    if [[ -e "$status.ready" ]]; then echo retry > "$status.result"; exit; fi
    sleep .2
  done
  export EXACT_EARLY_RECEIPT="$receipt"
  if bun "$scripts/mobile-updates.mjs" seed "$platform"; then echo ok > "$status.result"; else echo retry > "$status.result"; fi
  exit
fi

env -u EXACT_UPDATE_SEED bash "$scripts/native-build.sh" "$platform-link"
# The provider accepted this exact APK/IPA. The staged publisher may now commit its signed record and head.
touch "$status.ready"
if [[ -s "$status.pid" ]]; then
  pid=$(cat "$status.pid")
  while [[ ! -s "$status.result" ]] && kill -0 "$pid" 2>/dev/null; do sleep .1; done
fi
if [[ ! -s "$status.result" || $(cat "$status.result") != ok ]]; then
  # A retried native build can legitimately replace a receipt while packaging.
  env -u GH_TOKEN bun "$scripts/mobile-updates.mjs" seed "$platform"
fi
url=$(jq -er .url "$RUNNER_TEMP/installer-$platform.json")
case "$platform" in android) label=Android ;; ios) label=iPhone ;; esac
printf '[Install on %s](%s)\n' "$label" "$url" >> "$GITHUB_STEP_SUMMARY"
