#!/usr/bin/env bash
# Capture the public synthetic seed's real iOS final-link inputs.
set -euo pipefail
: "${EXACT:?}" "${SETUP_EXACT_REF:?}" "${RUNNER_TEMP:?}"
script_dir=$(cd "$(dirname "$0")" && pwd)
app="${IOS_CACHE_ROOT:-$HOME/exact-native}/apps/apps"
target="${IOS_CACHE_TARGET:-$HOME/.cache/exact-native/cargo}"
bake="$RUNNER_TEMP/ios-artifacts/bake/ios-aarch64-apple-ios.baked"
receipt="$RUNNER_TEMP/ios-artifacts/bake/ios-aarch64-apple-ios.build.json"
archive="$target/aarch64-apple-ios/balanced/libapps_apple.a"
# The public release must contain only the documented, synthetic seed.
python3 - "$receipt" <<'PY_CHECK'
import json, sys
r=json.load(open(sys.argv[1])); i=r['compat']['inputs']; m=r['binary']['metadata']
assert r['trust']=='production' and i['app']=='dev.disposabl.publicseed'
assert i['keys']=={'mobile':'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA='}
assert m['delivery']['origin']=='https://appdrop.app/u/66687aadf862bd776c8fc18b8e9f8e20'
PY_CHECK
manifest=$(python3 - "$target" <<'PY'
import glob, json, os, sys
root=sys.argv[1]+'/apple-swift/*/out/Intermediates.noindex/XCBuildData/*.xcbuilddata/manifest.json'
for path in sorted(glob.glob(root), key=os.path.getmtime, reverse=True):
    with open(path) as f: data=json.load(f)
    commands=[v for k,v in data['commands'].items() if 'Ld ' in k and '/ExactIOS normal' in k]
    if len(commands)==1 and any('/dev.disposabl.publicseed/' in x for x in commands[0].get('inputs', [])):
        print(path); break
else: raise SystemExit('No public seed ExactIOS link command')
PY
)
capture="$RUNNER_TEMP/ios-link-kit-capture"
kit="$HOME/.cache/exact/ios-link-kit/$SETUP_EXACT_REF"
[[ ! -e "$capture" && ! -e "$kit" ]] || { echo 'Link kit output already exists' >&2; exit 1; }
python3 "$script_dir/ios-link-kit-capture.py" --manifest "$manifest" --receipt "$receipt" --bake "$bake" --archive "$archive" --out "$capture"
python3 "$script_dir/ios-link-kit.py" create --kit "$kit" --proof "$capture" --receipt "$receipt" --bake "$bake" --app "$app"
python3 "$script_dir/ios-link-arms.py" capture --kit "$kit" --target "$target"
echo "Public iOS link kit created at $kit"
