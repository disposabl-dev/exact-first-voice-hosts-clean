#!/usr/bin/env bash
# Publish only outputs from a public synthetic app built in fresh cache homes.
set -euo pipefail
: "${EXACT:?}" "${RUNNER_TEMP:?}" "${GITHUB_WORKSPACE:?}" "${GITHUB_SHA:?}"
[[ ${GITHUB_REPOSITORY:-} == disposabl-dev/disposabl-exact ]] || { echo 'Android cache production is restricted to the canonical public repository' >&2; exit 1; }
[[ "$EXACT" == "$HOME/work/_temp/exact-sdk" ]] || { echo 'Unexpected SDK path' >&2; exit 1; }
root="$HOME/exact-native"
target="$HOME/.cache/exact-native/cargo"
gradle="$RUNNER_TEMP/exact-public-gradle"
out="$RUNNER_TEMP/kit-out"
[[ ! -e "$root" && ! -e "$target" && ! -e "$gradle" ]] || { echo 'Synthetic app and cache paths must be fresh' >&2; exit 1; }
mkdir -p "$(dirname "$target")" "$out" "$gradle"
export CARGO_TARGET_DIR="$target" GRADLE_USER_HOME="$gradle"
unset CARGO_INCREMENTAL EXACT_UPDATE_SEED ANDROID_APK_SIGNING ANDROID_PLAY_UPLOAD_SIGNING
bash "$GITHUB_WORKSPACE/.github/actions/exact-setup/scripts/android-fast-profile.sh"
git clone -q --local --no-hardlinks "$GITHUB_WORKSPACE" "$root"
git -C "$root" checkout -q --detach "$(git -C "$GITHUB_WORKSPACE" rev-parse HEAD)"
cd "$root/apps/apps"
mkdir -p .exact
ln -s "$EXACT" .exact/checkout
bun install --frozen-lockfile
export EXACT_KEYSTORE="$RUNNER_TEMP/public-fixture-signing.p12" EXACT_KEY_ALIAS=android
# This random, disposable key never enters the archives.
export EXACT_KEYSTORE_PASSWORD="$(openssl rand -hex 24)"
export EXACT_KEY_PASSWORD="$EXACT_KEYSTORE_PASSWORD"
trap 'rm -f "$EXACT_KEYSTORE"' EXIT
keytool -genkeypair -noprompt -storetype PKCS12 -keystore "$EXACT_KEYSTORE" -storepass:env EXACT_KEYSTORE_PASSWORD -keypass:env EXACT_KEY_PASSWORD -alias android -keyalg RSA -dname CN=PublicFixture -validity 2 >/dev/null 2>&1
export CONVEX_ORIGIN=wss://public-fixture.invalid EXACT_MOBILE_CHANNEL=base-1
export EXACT_UPDATE_SEED="$(openssl rand -base64 32)" EXACT_UPDATE_TRUST=production EXACT_UPDATE_GENESIS=1
export EXACT_BUILD_NUMBER=1 EXACT_BAKE_OUTPUT="$RUNNER_TEMP/public-fixture-bake"
export GITHUB_ENV="$RUNNER_TEMP/public-fixture-env"
: > "$GITHUB_ENV"
export EXACT_REF=$(git -C "$EXACT" rev-parse HEAD)
bun "$GITHUB_WORKSPACE/.github/actions/exact-setup/scripts/mobile-updates.mjs" configure android
unset EXACT_UPDATE_SEED
export EXACT_VERSION=$(bun -p 'JSON.parse(await Bun.file("app.json").text()).version')
printf 'org.gradle.caching=true\n' > "$gradle/gradle.properties"
bun exact.mjs android --release --no-install --json > "$RUNNER_TEMP/public-fixture-result.json"
# Do not package the fake app's binary, build script outputs, receipts, Gradle
# project output, or incremental compilation state. The cache is disposable.
tar -cf - --exclude='incremental' --exclude='apps-android-*' --exclude='libapps_android*' --exclude='apps_android*' \
  -C "$target" release aarch64-linux-android/release | zstd -T0 -3 -o "$out/cargo-android.zst"
gradle_paths=()
for path in caches/build-cache-1 caches/modules-2 caches/9.8.0 wrapper/dists; do
  [[ ! -e "$gradle/$path" ]] || gradle_paths+=("$path")
done
(( ${#gradle_paths[@]} )) || { echo 'Missing public Gradle caches' >&2; exit 1; }
tar -cf - --exclude='*.lock' --exclude='*.lck' --exclude='*.part' -C "$gradle" "${gradle_paths[@]}" | zstd -T0 -3 -o "$out/cargo-gradle.zst"
