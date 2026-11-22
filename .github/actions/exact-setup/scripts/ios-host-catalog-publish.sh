#!/usr/bin/env bash
# Build SDK-owned Swift targets only, for the pinned public 17.0 iOS toolchain.
set -euo pipefail
: "${EXACT:?}" "${EXACT_REF:?}" "${CARGO_TARGET_DIR:?}" "${RUNNER_TEMP:?}"
[[ "$EXACT_REF" == a55c5581c7e1a62cdee1f09367ea2909c74c7292 && $(git -C "$EXACT" rev-parse HEAD) == "$EXACT_REF" ]]
scripts=$(cd "$(dirname "$0")" && pwd)
kit="$HOME/.cache/exact/ios-link-kit/$EXACT_REF"
scratch="$CARGO_TARGET_DIR/apple-swift/ios-17.0-balanced"
product="$scratch/out/Products/Release-iphoneos"
sdk=$(xcrun --sdk iphoneos --show-sdk-path)
cd "$EXACT/host/apple"
export EXACT_APP_COMPOSITION=updating EXACT_APPLE_LINK=all EXACT_UPDATE_TRUST=production
export EXACT_APPLE_INTENT_FILES=0 EXACT_WATCH_SYNC=0 EXACT_WIDGET_GALLERY=0 IPHONEOS_DEPLOYMENT_TARGET=17.0
export EXACT_LIB_DIR="$CARGO_TARGET_DIR/aarch64-apple-ios/balanced" EXACT_LIB=apps_apple
unset SDKROOT
args=(build -c release --scratch-path "$scratch" --triple arm64-apple-ios17.0 --sdk "$sdk" -Xcc -isysroot -Xcc "$sdk" -Xlinker -syslibroot -Xlinker "$sdk" -Xswiftc -Xclang-linker -Xswiftc -isysroot -Xswiftc -Xclang-linker -Xswiftc "$sdk")
# Match Exact's own host preparation, including its ignored generated Swift.
bun "$EXACT/kernel/build/apple-batch.mjs"
swift package describe --type json > "$RUNNER_TEMP/ios-host-package.json"
swift package dump-package > "$RUNNER_TEMP/ios-host-dump.json"
python3 "$scripts/ios-host-graph.py" --package-dump "$RUNNER_TEMP/ios-host-dump.json" --target ExactComposition --output "$RUNNER_TEMP/ios-host-graph.json"
python3 "$scripts/ios-host-sources.py" --sdk "$EXACT" --graph "$RUNNER_TEMP/ios-host-graph.json" --output "$RUNNER_TEMP/ios-host-sources-before.json"
# Compile the full SDK capability closure once; only reusable library targets
# are exported, never the application composition, linked host or SDK PCMs.
swift "${args[@]}" --target ExactComposition
python3 "$scripts/ios-host-sources.py" --sdk "$EXACT" --graph "$RUNNER_TEMP/ios-host-graph.json" --output "$RUNNER_TEMP/ios-host-sources-after.json"
cmp -s "$RUNNER_TEMP/ios-host-sources-before.json" "$RUNNER_TEMP/ios-host-sources-after.json"
out="$RUNNER_TEMP/ios-host-catalog"
[[ ! -e "$out" ]]
mkdir -p "$out/host-objects"
python3 - "$RUNNER_TEMP/ios-host-package.json" "$RUNNER_TEMP/ios-host-graph.json" "$product" "$out" <<'PY_TARGETS'
import json,shutil,sys
from pathlib import Path
package=json.load(open(sys.argv[1])); graph=json.load(open(sys.argv[2])); product=Path(sys.argv[3]); out=Path(sys.argv[4])/'host-objects'
targets={x['name']:x for x in package['targets']}; seen=set(); pending=['ExactComposition']
while pending:
 name=pending.pop()
 if name in seen: continue
 if name not in targets: raise ValueError('Unknown SwiftPM target '+name)
 seen.add(name); pending.extend(targets[name].get('target_dependencies',[]))
if set(graph['targets']) != seen: raise ValueError('SwiftPM target graphs differ')
for name in sorted(seen-{'ExactComposition'}):
 item=targets[name]
 if item['module_type'] in ('SystemLibraryTarget','ClangTarget'): continue
 if item['module_type']!='SwiftTarget' or not name.startswith('Exact'): raise ValueError('Unsupported target '+name)
 obj=product/(name+'.o'); module=product/(name+'.swiftmodule')
 if not obj.is_file() or obj.is_symlink() or not module.is_dir() or module.is_symlink(): raise ValueError('Missing '+name)
 shutil.copyfile(obj,out/obj.name); dst=out/module.name;dst.mkdir()
 for f in module.iterdir():
  if f.is_file() and not f.is_symlink() and f.suffix in ('.swiftmodule','.swiftdoc'):
   shutil.copyfile(f,dst/f.name)
 if not list(dst.glob('*.swiftmodule')): raise ValueError('Missing module interface '+name)
PY_TARGETS
python3 - "$scripts" "$kit" "$out" "$EXACT_REF" "$RUNNER_TEMP/ios-host-graph.json" "$RUNNER_TEMP/ios-host-sources-after.json" <<'PY'
import hashlib,importlib.util,json,sys
from pathlib import Path
scripts,kit,out,ref=Path(sys.argv[1]),Path(sys.argv[2]),Path(sys.argv[3]),sys.argv[4]
spec=importlib.util.spec_from_file_location('proof',scripts/'ios-link-kit.py'); proof=importlib.util.module_from_spec(spec);spec.loader.exec_module(proof)
r=json.loads((kit/'kit.json').read_text()); assert r['toolchain']==proof.toolchain()
for name in ('ExactCore','ExactKit','ExactUpdates'):
 candidates=[x for x in r['digests'] if x.endswith('-'+name+'.o')]; assert len(candidates)==1
 assert proof.digest(out/'host-objects'/(name+'.o'))==r['digests'][candidates[0]],name
files={}
for p in sorted((out/'host-objects').rglob('*')):
 if p.is_dir(): continue
 assert p.is_file() and not p.is_symlink() and p.suffix in ('.o','.swiftmodule','.swiftdoc','.json')
 files[str(p.relative_to(out))]=proof.digest(p)
graph=json.load(open(sys.argv[5])); assert graph['schema']==1 and graph['roots']==['ExactComposition']
(out/'manifest.json').write_text(json.dumps({'ref':ref,'toolchain':proof.toolchain(),'target':'arm64-apple-ios17.0','profile':'release-balanced','conditions':{'intent_files':False,'widget_gallery':False,'watch_sync':False,'composition':'updating'},'targets':graph['targets'],'source_hashes':json.load(open(sys.argv[6])),'composition_defines':graph['composition_defines'],'files':files},sort_keys=True)+'\n')
PY
COPYFILE_DISABLE=1 tar -C "$out" -cf - . | zstd -T0 -3 -o "$RUNNER_TEMP/kit-out/ios-host-catalog.zst"
