#!/usr/bin/env bash
set -euo pipefail
platform=$1
artifacts="$RUNNER_TEMP/${platform}-artifacts"
if [[ "$platform" == android-link || "$platform" == ios-link ]]; then
  platform=${platform%-link}
  private_dir=$(mktemp -d "$RUNNER_TEMP/appdrop.XXXXXX")
  trap 'rm -rf "$private_dir"' EXIT
  case "$platform" in
    android) file="$RUNNER_TEMP/android-artifacts/android-production.apk" ;;
    ios) file="$RUNNER_TEMP/ios-artifacts/apps.ipa" ;;
  esac
  url=''
  if [[ "$platform" == android && "${EXACT_PUBLIC_APK:-}" == true ]]; then
    if ! url=$(python3 "$(dirname "$0")/release-apk.py" publish "$file"); then
      echo '::warning::GitHub APK publication failed. Uploading the verified installer to AppDrop.'
      url=''
    fi
  fi
  if [[ -z "$url" ]]; then
    if ! env -u GH_TOKEN bunx --bun appdrop@0.8.0 "$file" --json > "$private_dir/result.json" 2> "$private_dir/log"; then
      echo '::error::AppDrop upload failed'; exit 1
    fi
    url=$(jq -er '.url | select(test("^https://(www\\.)?appdrop\\.app/d/[A-Za-z0-9]+/?$"))' "$private_dir/result.json")
  fi
  # Persist only the share link. AppDrop's management credential stays on this runner.
  jq -nc --arg url "$url" --arg scope "$EXACT_INSTALL_SCOPE" '{url:$url,scope:$scope}' > "$RUNNER_TEMP/installer-$platform.json"
  exit
fi
[[ "$CONVEX_ORIGIN" == wss://* ]] || { echo '::error::Invalid Convex WebSocket origin'; exit 1; }
mkdir -p "$artifacts/bake"
if [[ "$platform" == ios ]]; then
  if [[ -n "${EXACT_IOS_SOURCE_TIMES:-}" ]]; then
    # Non-template native workspaces use Cargo's normal freshness checks.
    if ! SETUP_EXACT_REF="$EXACT_REF" IOS_RESTORE_CARGO_SOURCE_MTIMES=1 bun "$(dirname "$0")/ios-source-times.mjs" apply "$EXACT_IOS_SOURCE_TIMES" "$PWD"; then
      unset EXACT_LINK_KIT_PROOF EXACT_LINK_KIT
      export CARGO_TARGET_DIR="$RUNNER_TEMP/ios-full-build"
      echo 'Using the normal Exact iOS build for this workspace.'
    fi
  fi
  # This pinned SDK compiles its app archive independently of its shared SDK
  # dependencies. Keep their original profiles and their Cargo fingerprints.
  if [[ "${EXACT_REF:-}" == a55c5581c7e1a62cdee1f09367ea2909c74c7292 && "${EXACT_IOS_APP_CGU:-16}" == 16 && -z "${CARGO_PROFILE_BALANCED_CODEGEN_UNITS:-}" && ! -f data/Cargo.toml ]] && jq -e '.game == null' app.json >/dev/null; then
    export EXACT_IOS_REAL_CARGO="$(command -v cargo)"
    wrapper="$RUNNER_TEMP/ios-app-cargo"
    mkdir -p "$wrapper"
    scripts=$(cd "$(dirname "$0")" && pwd)
    ln -sf "$scripts/ios-cargo-app-profile.sh" "$wrapper/cargo"
    export PATH="$wrapper:$PATH"
  fi
  export EXACT_GENERAL_PROOF="$artifacts/swift-host.json"
  rm -f "$EXACT_GENERAL_PROOF"
  bun exact.mjs ios --device --release --unsigned --archive "$artifacts/apps.ipa" --json > "$artifacts/build.json"
  if [[ -f "$EXACT_GENERAL_PROOF" ]]; then
    jq -r '"Reused Swift targets: " + (.targets | join(", "))' "$EXACT_GENERAL_PROOF"
  else
    echo "Exact completed the iOS build using the stock link kit or SwiftPM."
  fi
  unzip -tq "$artifacts/apps.ipa" >/dev/null
  unzip -q "$artifacts/apps.ipa" -d "$RUNNER_TEMP/iphone-archive"
  apps=("$RUNNER_TEMP"/iphone-archive/Payload/*.app)
  [[ ${#apps[@]} == 1 && -f "${apps[0]}/Info.plist" ]]
  executable=$(python3 -c 'import plistlib,sys; print(plistlib.load(open(sys.argv[1],"rb"))["CFBundleExecutable"])' "${apps[0]}/Info.plist")
  [[ "$executable" != */* && "$executable" != . && "$executable" != .. ]]
  macho="${apps[0]}/$executable"
  xcrun lipo -verify_arch arm64 "$macho"
  xcrun vtool -show-build "$macho" > "$RUNNER_TEMP/ios-load-commands"
  grep -Eiq '^[[:space:]]*platform[[:space:]]+(ios|2)[[:space:]]*$' "$RUNNER_TEMP/ios-load-commands"
  receipt="$artifacts/apps.receipt.json"
  bake="$EXACT_BAKE_OUTPUT/ios-aarch64-apple-ios.build.json"
  jq -e --slurpfile bake "$bake" '
    .platform == "ios" and .target == "aarch64-apple-ios" and
    .build.trust == "production" and .compatibilityId == $bake[0].compat.id and
    .build == $bake[0] and $bake[0].compat.target == "aarch64-apple-ios" and
    $bake[0].compat.inputs.platform == "ios"
  ' "$receipt" >/dev/null
  jq -e --slurpfile receipt "$receipt" '
    .platform == "ios" and .target == "aarch64-apple-ios" and
    .compatibilityId == $receipt[0].compatibilityId and .build.trust == "production"
  ' "${apps[0]}/receipt.json" >/dev/null
  exit
fi
[[ "$platform" == android ]]
if [[ -z "${ANDROID_APK_SIGNING:-}" ]]; then
  echo '::error::Production Android signing is not configured'; exit 1
fi
signing_dir=$(mktemp -d "$RUNNER_TEMP/android-signing.XXXXXX")
trap 'rm -rf "$signing_dir"' EXIT
build() (
  signing=$1 output=$2
  shift 2
  export EXACT_KEYSTORE="$signing_dir/signing.p12" EXACT_KEY_ALIAS=android
  EXACT_KEYSTORE_PASSWORD=$(jq -er '.password' <<< "$signing")
  echo "::add-mask::$EXACT_KEYSTORE_PASSWORD"
  export EXACT_KEYSTORE_PASSWORD EXACT_KEY_PASSWORD="$EXACT_KEYSTORE_PASSWORD"
  jq -er '.keystore' <<< "$signing" | base64 --decode > "$EXACT_KEYSTORE"
  bun exact.mjs android "$@" --json > "$signing_dir/build.json" || return
  cp "$(jq -er '.path' "$signing_dir/build.json")" "$output" || return
)
verify_apk() {
"$ANDROID_HOME/build-tools/$ANDROID_BUILD_TOOLS_VERSION/apksigner" verify --print-certs "$artifacts/android-production.apk" > "$signing_dir/apk-signature.txt"
cat "$signing_dir/apk-signature.txt"
APK_VERIFY_PASSWORD=$(jq -er '.password' <<< "$ANDROID_APK_SIGNING")
export APK_VERIFY_PASSWORD
expected_apk_cert=$(keytool -list -v -keystore "$signing_dir/signing.p12" -storepass:env APK_VERIFY_PASSWORD -alias android | sed -n 's/^[[:space:]]*SHA256: //p' | head -1 | tr -d ':' | tr '[:upper:]' '[:lower:]')
python3 - "$expected_apk_cert" "$signing_dir/apk-signature.txt" <<'PYCODE'
import re, sys
expected = sys.argv[1].lower()
lines = open(sys.argv[2], encoding='utf-8').read().splitlines()
digest_lines = [line for line in lines if 'certificate SHA-256 digest:' in line]
digests = [re.search(r'certificate SHA-256 digest:\s*([0-9a-fA-F]{64})\s*$', line) for line in digest_lines]
if (not re.fullmatch(r'[0-9a-f]{64}', expected) or not digests or
        any(match is None or match.group(1).lower() != expected for match in digests)):
    raise SystemExit('::error::APK signer does not match the selected key')
PYCODE
unset APK_VERIFY_PASSWORD
echo 'apk_verified=true' >> "$GITHUB_OUTPUT"
}
# Sideloading needs only the signed APK. Build the Play bundle when connected.
if [[ "${ANDROID_PLAY_CONNECTED:-}" != true ]]; then
  build "$ANDROID_APK_SIGNING" "$artifacts/android-production.apk" --release --no-install
  verify_apk
  exit
fi
: "${ANDROID_PLAY_UPLOAD_SIGNING:?Play upload signing is not configured}"
# Keep Gradle's existing caches and configuration, but add our capture in an isolated
# user home. This avoids modifying the runner's shared Gradle configuration.
gradle_home="$signing_dir/gradle-home"
mkdir -p "$gradle_home/init.d"
original_gradle_home="${GRADLE_USER_HOME:-$HOME/.gradle}"
if [[ -d "$original_gradle_home" ]]; then
  for entry in "$original_gradle_home"/* "$original_gradle_home"/.[!.]*; do
    [[ -e "$entry" ]] || continue
    case "${entry##*/}" in
      init.d) cp -R "$entry"/. "$gradle_home/init.d/" ;;
      *) ln -s "$entry" "$gradle_home/${entry##*/}" ;;
    esac
  done
fi
cp "$(dirname "$0")/android-gradle-capture.init.gradle" "$gradle_home/init.d/exact-capture.gradle"
export GRADLE_USER_HOME="$gradle_home" EXACT_GRADLE_CAPTURE="$signing_dir/gradle.json"
if ! build "$ANDROID_APK_SIGNING" "$artifacts/android-production.apk" --release --no-install; then
  # A failing AAB task must not lose an otherwise buildable APK. Re-run the
  # supported Exact APK-only build; never trust an output left by a failed task.
  echo '::warning::Combined Android build failed; retrying APK only'
  export EXACT_ANDROID_APK_ONLY=1
  rm -f "$EXACT_GRADLE_CAPTURE"
  if build "$ANDROID_APK_SIGNING" "$artifacts/android-production.apk" --release --no-install; then
    verify_apk
  fi
  echo '::error::Android bundle build failed; only a verified APK may be uploaded'
  exit 1
fi
verify_apk
aab_signing=$ANDROID_PLAY_UPLOAD_SIGNING
# Exact has already built both artifacts from the same staged inputs. If capture
# was unavailable, retain the supported two-build route and cohort comparison.
export EXACT_KEYSTORE="$signing_dir/bundle.p12" EXACT_KEY_ALIAS=android
EXACT_KEYSTORE_PASSWORD=$(jq -er '.password' <<< "$aab_signing")
echo "::add-mask::$EXACT_KEYSTORE_PASSWORD"
export EXACT_KEYSTORE_PASSWORD EXACT_KEY_PASSWORD="$EXACT_KEYSTORE_PASSWORD"
jq -er '.keystore' <<< "$aab_signing" | base64 --decode > "$EXACT_KEYSTORE"
expected_cert=$(keytool -list -v -keystore "$EXACT_KEYSTORE" -storepass:env EXACT_KEYSTORE_PASSWORD -alias android | sed -n 's/^[[:space:]]*SHA256: //p' | head -1)
expected_bundle_normalized=$(printf '%s' "$expected_cert" | tr -d ':' | tr '[:upper:]' '[:lower:]')
[[ -n "$expected_bundle_normalized" ]] || { echo '::error::Cannot read AAB signing key'; exit 1; }
if [[ ! -s "$EXACT_GRADLE_CAPTURE" ]]; then
  EXACT_BAKE_OUTPUT="$RUNNER_TEMP/play-bake" build "$aab_signing" "$signing_dir/play.aab" --bundle
  bun "$(dirname "$0")/android-cohort-compare.mjs" "$EXACT_BAKE_OUTPUT" "$RUNNER_TEMP/play-bake"
  bun "$(dirname "$0")/android-gradle-bundle.mjs" --verify "$signing_dir/play.aab" "$artifacts/android-production.apk"
else
  bun "$(dirname "$0")/android-gradle-bundle.mjs" "$EXACT_GRADLE_CAPTURE" "$signing_dir/play.aab" "$artifacts/android-production.apk"
  if [[ "$expected_bundle_normalized" != "$expected_apk_cert" ]]; then
    # Gradle used the APK key; remove its JAR signature before signing for Play.
    zip -qd "$signing_dir/play.aab" 'META-INF/*.SF' 'META-INF/*.RSA' 'META-INF/*.DSA' 'META-INF/*.EC' 'META-INF/SIG-*' || true
    python3 - "$signing_dir/play.aab" <<'PYCODE'
import re, sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as bundle:
    if any(re.fullmatch(r'META-INF/(?:[^/]+\.(?:SF|RSA|DSA|EC)|SIG-[^/]+)', name, re.I) for name in bundle.namelist()):
        raise SystemExit('Old AAB signature remains')
PYCODE
    jarsigner -keystore "$EXACT_KEYSTORE" -storepass:env EXACT_KEYSTORE_PASSWORD -keypass:env EXACT_KEY_PASSWORD "$signing_dir/play.aab" android >/dev/null
  fi
fi
jarsigner -verify "$signing_dir/play.aab" >/dev/null
actual_cert=$(keytool -printcert -jarfile "$signing_dir/play.aab" | sed -n 's/^[[:space:]]*SHA256: //p' | head -1)
[[ "$expected_cert" == "$actual_cert" ]] || { echo '::error::AAB signer does not match the selected key'; exit 1; }
mv "$signing_dir/play.aab" "$artifacts/android-production.aab"
