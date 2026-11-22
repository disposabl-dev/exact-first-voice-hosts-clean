#!/usr/bin/env bash
# Every input is produced from the pinned public Exact source on a clean runner.
set -euo pipefail
out="$RUNNER_TEMP/kit-out"
mkdir -p "$out"
base="exact-$EXACT_REF-$PLATFORM-$RUNNER_OS-$RUNNER_ARCH-bun-$BUN_VERSION.tar.zst"
source_dir="$RUNNER_TEMP"
if [[ ! -f "$source_dir/$base" ]]; then source_dir="$RUNNER_TEMP/exact-bundles"; fi
cp "$source_dir/$base" "$source_dir/$base.sha256" "$out/"
(cd "$out" && shasum -a 256 -c "$base.sha256")
case "$PLATFORM" in
  android|ios) bash "$(dirname "$0")/$PLATFORM-compiler-cache.sh" ;;
  web)
    scratch=$(mktemp -d "$RUNNER_TEMP/exact-source.XXXXXX")
    trap 'rm -rf "$scratch"' EXIT
    git clone --quiet --no-checkout --no-tags --depth=1 "file://$EXACT" "$scratch/sdk"
    [[ $(git -C "$scratch/sdk" rev-parse HEAD) == "$EXACT_REF" ]]
    git -C "$scratch/sdk" remote remove origin
    tar -C "$scratch/sdk" -cf - .git | zstd -T0 -1 -o "$out/sdk.tar.zst"
    ;;
  *) exit 2 ;;
esac
cd "$out"
for file in *.zst *.json; do
  if [[ -f "$file" && ! -f "$file.sha256" ]]; then shasum -a 256 "$file" > "$file.sha256"; fi
done
