#!/usr/bin/env bash
# Apply the app-only profile to Exact's production Apple staticlib invocation.
# Other Cargo commands and shared SDK dependency profiles pass through intact.
set -euo pipefail
: "${EXACT_IOS_REAL_CARGO:?}"
args=("$@")
if [[ ${1:-} == rustc ]]; then
  pkg='' profile='' target='' archive=0
  for ((i=0;i<${#args[@]};i++)); do
    case "${args[i]}" in
      -p) pkg=${args[i+1]:-} ;;
      --profile) profile=${args[i+1]:-} ;;
      --target) target=${args[i+1]:-} ;;
      staticlib) archive=1 ;;
    esac
  done
  if [[ $archive == 1 && $profile == balanced && $target == aarch64-apple-ios && $pkg =~ ^[a-zA-Z0-9_-]+-apple$ ]]; then
    echo "Using 16 codegen units for the iOS app crate." >&2
    config="profile.balanced.package.\"$pkg\".codegen-units=16"
    for ((i=0;i<${#args[@]};i++)); do
      if [[ ${args[i]} == -- ]]; then
        args=("${args[@]:0:i}" --config "$config" "${args[@]:i}")
        break
      fi
    done
    (( i < ${#args[@]} )) || args+=(--config "$config")
  fi
fi
exec "$EXACT_IOS_REAL_CARGO" "${args[@]}"
