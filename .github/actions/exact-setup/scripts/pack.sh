#!/usr/bin/env bash
set -euo pipefail
cd "$EXACT"
paths=()
if [[ "$SETUP_PLATFORM" == web ]]; then
  paths=(.cache/exact/tools ".cache/exact/publisher/$SETUP_EXACT_REF")
  while IFS= read -r compiler; do paths+=("$compiler"); done < <(cd "$HOME" && find .cargo/hermes-lean-sys -name hermesc -type f)
  (( ${#paths[@]} > 2 )) || { echo 'Missing host Hermes compiler' >&2; exit 1; }
else
  pin=$(bun -p 'Bun.TOML.parse(await Bun.file("rust-toolchain.toml").text()).toolchain.channel')
  host=$(rustc +"$pin" -vV | awk '/^host: / { print $2 }')
  paths=(".rustup/toolchains/$pin-$host" .cargo/hermes-lean-sys .cargo/registry/cache .cargo/registry/index .cargo/git/db)
  if [[ "$SETUP_PLATFORM" == ios ]]; then
    publisher=".cache/exact/publisher/$SETUP_EXACT_REF"
    [[ -x "$HOME/$publisher/exact-bake" && -x "$HOME/$publisher/exact-js-bake" ]] || { echo 'Missing iOS publisher tools' >&2; exit 1; }
    paths+=("$publisher")
  fi
fi
present=()
for path in "${paths[@]}"; do [[ ! -e "$HOME/$path" ]] || present+=("$path"); done
[[ ${#present[@]} -gt 0 ]]
asset="exact-${SETUP_EXACT_REF}-${SETUP_PLATFORM}-${RUNNER_OS}-${RUNNER_ARCH}-bun-${SETUP_BUN_VERSION}.tar.zst"
tar -cf - -C "$HOME" "${present[@]}" | zstd -T0 -1 -o "$RUNNER_TEMP/$asset"
size=$(wc -c < "$RUNNER_TEMP/$asset")
(( size < 2000000000 )) || { echo "Bundle exceeds 2 GB: $size" >&2; exit 1; }
(cd "$RUNNER_TEMP" && shasum -a 256 "$asset" > "$asset.sha256")
