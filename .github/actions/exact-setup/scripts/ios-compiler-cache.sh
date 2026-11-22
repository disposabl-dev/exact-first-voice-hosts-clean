#!/usr/bin/env bash
set -euo pipefail
[[ "$GITHUB_REPOSITORY" == disposabl-dev/disposabl-exact ]] || exit 1
if [[ -d /Applications/Xcode_27.2.app/Contents/Developer ]]; then
  export DEVELOPER_DIR=/Applications/Xcode_27.2.app/Contents/Developer
fi
scripts="$GITHUB_WORKSPACE/.github/actions/exact-setup/scripts"
export SETUP_EXACT_REF="$EXACT_REF"
export CARGO_TARGET_DIR="$HOME/.cache/exact-native/cargo"
export EXACT_BAKE_OUTPUT="$RUNNER_TEMP/ios-artifacts/bake"
export EXACT_MOBILE_CHANNEL=base-1 EXACT_BUILD_NUMBER=1
app="$HOME/exact-native/apps/apps"
git worktree add --quiet --detach "$HOME/exact-native" HEAD
cd "$app"
mkdir -p ".exact" "$RUNNER_TEMP/kit-out" "$RUNNER_TEMP/evidence" "$RUNNER_TEMP/capturebin"
ln -s "$EXACT" .exact/checkout
bun install --frozen-lockfile
# The public canonical template is the fixture. No deployment credentials are used.
export CONVEX_ORIGIN=wss://public-seed.invalid
bun -e '
  const p = "app.json", app = await Bun.file(p).json();
  app.id = "dev.disposabl.publicseed"; app.app.id = app.id;
  app.deploy.store.ios = "A";
  app.deploy.signing = {key:"mobile", keys:{mobile:"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}};
  app.deploy.channel = "base-1"; app.deploy.channels = {"base-1":"appdrop"};
  await Bun.write(p, JSON.stringify(app, null, 2) + "\n");
'
bundle="$HOME/.cache/exact/publisher/$EXACT_REF"
[[ -x "$bundle/exact-js-bake" ]] || { echo 'Shared macOS publisher is missing' >&2; exit 1; }
bun "$scripts/ios-baker.mjs" record "$bundle/exact-js-bake" "$RUNNER_TEMP/kit-out/ios-baker.json"
bun "$scripts/ios-baker.mjs" enable "$RUNNER_TEMP/kit-out/ios-baker.json"
export EXACT_IOS_BAKER_PATH="$bundle/exact-js-bake" EXACT_IOS_BAKER_SHA256="$(shasum -a 256 "$bundle/exact-js-bake" | cut -d ' ' -f1)"
bash "$scripts/ios-link-kit-enable.sh"
(cd "$EXACT" && git ls-files -z | xargs -0 touch -t 202001010000)
find "$EXACT" -type d -not -path '*/.git*' -exec touch -t 202001010000 {} +
export IOS_REAL_CARGO=$(command -v cargo)
export IOS_EVIDENCE_PUBLIC_SEED=1 IOS_EVIDENCE_DIR="$RUNNER_TEMP/evidence"
ln -s "$scripts/ios-cargo-capture.sh" "$RUNNER_TEMP/capturebin/cargo"
export PATH="$RUNNER_TEMP/capturebin:$PATH"
bun exact.mjs ios --device --release --unsigned --archive "$RUNNER_TEMP/public-seed.ipa" --json > "$RUNNER_TEMP/public-seed.json"
captures=("$RUNNER_TEMP"/evidence/cargo-ios.*)
[[ ${#captures[@]} == 1 ]]
bun "$scripts/ios-source-times.mjs" record "$EXACT_BAKE_OUTPUT/ios-aarch64-apple-ios.build.json" "$app" "$RUNNER_TEMP/kit-out/ios-source-times.json"
bash "$scripts/ios-cache-archive.sh" "$CARGO_TARGET_DIR" "$RUNNER_TEMP/kit-out/cargo-ios.zst" "${captures[0]}"
bash "$scripts/ios-link-kit-publish.sh"
# Keep public package outputs separate from app-specific linking and signing.
bun "$scripts/ios-package-cache.mjs"
packages="$CARGO_TARGET_DIR/apple-swift/ios-17.0-balanced/packages-kept"
test -d "$packages"
cp -R "$packages" "$HOME/.cache/exact/ios-link-kit/$EXACT_REF/packages-kept"
COPYFILE_DISABLE=1 tar -C "$HOME/.cache/exact/ios-link-kit/$EXACT_REF" -cf - . | zstd -T0 -3 -o "$RUNNER_TEMP/kit-out/ios-link-kit.zst"

# SDK-only Swift host catalog for production capability combinations.
bash "$scripts/ios-host-catalog-publish.sh"
