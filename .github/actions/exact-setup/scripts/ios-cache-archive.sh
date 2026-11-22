#!/usr/bin/env bash
# Package only Cargo's compiled Rust artifacts and fingerprints from a public seed.
# No Swift scratch, Clang module cache, Apple SDK, seed bake, bundle or receipt.
set -euo pipefail
[[ $# == 3 ]] || { echo "usage: $0 TARGET_DIR OUTPUT.tar.zst CARGO_JSON" >&2; exit 2; }
target=$1 output=$2 evidence=$3
[[ "$target" == /* && "$output" == /* && "$evidence" == /* && -d "$target" && -f "$evidence" && ! -e "$output" ]] || { echo 'Use an existing absolute target and a new absolute output' >&2; exit 1; }
list=$(mktemp "${TMPDIR:-/tmp}/exact-ios-files.XXXXXX")
trap 'rm -f "$list"' EXIT
TARGET="$target" LIST="$list" EVIDENCE="$evidence" python3 - <<'PY'
import json
import os
from pathlib import Path
root = Path(os.environ['TARGET'])
files = []
# A cache miss is safe: Cargo rebuilds any missing build-script outputs.
for profile in ('balanced', 'aarch64-apple-darwin/balanced', 'aarch64-apple-ios/balanced'):
    directory = root / profile
    for name in ('deps', '.fingerprint'):
        part = directory / name
        if not part.is_dir() or part.is_symlink():
            continue
        for base, dirs, names in os.walk(part, followlinks=False):
            dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()]
            for filename in names:
                path = Path(base) / filename
                if path.is_symlink() or not path.is_file():
                    continue
                if name == 'deps' and path.suffix not in ('.a', '.d', '.dylib', '.rlib', '.rmeta'):
                    continue
                files.append(os.fsencode(path.relative_to(root)))
    if directory.is_dir():
        for path in directory.glob('lib*.a'):
            if path.is_file() and not path.is_symlink():
                files.append(os.fsencode(path.relative_to(root)))
# Cargo build-script outputs are indispensable for fingerprint reuse. Select
# only build units named by real compiler JSON from the public seed, never the
# entire cache. Do not follow links or copy Apple SDK and module-cache content.
units = set()
profile_roots = [root / p for p in ('balanced', 'aarch64-apple-darwin/balanced', 'aarch64-apple-ios/balanced')]
with open(os.environ['EVIDENCE'], encoding='utf8') as stream:
    messages = [json.loads(line) for line in stream if line.strip()]
if not any(m.get('reason') == 'build-finished' and m.get('success') for m in messages):
    raise SystemExit('Cargo capture has no successful build-finished message')
for message in messages:
    paths = list(message.get('filenames', []))
    if message.get('out_dir'):
        paths.append(message['out_dir'])
    for item in paths:
        path = Path(item)
        if not path.is_absolute():
            raise SystemExit('Cargo capture contains a relative path')
        for profile in profile_roots:
            try:
                rel = path.relative_to(profile / 'build')
            except ValueError:
                continue
            if rel.parts:
                units.add(profile / 'build' / rel.parts[0])
if not units:
    raise SystemExit('Cargo capture names no build-script units')
for unit in sorted(units):
    if unit.is_symlink() or not unit.is_dir():
        raise SystemExit(f'Missing or symlinked captured build unit: {unit}')
    for base, dirs, names in os.walk(unit, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()
                   and not d.endswith(('.sdk', '.framework'))
                   and 'modulecache' not in d.lower() and 'module-cache' not in d.lower()]
        for filename in names:
            path = Path(base) / filename
            if path.is_symlink() or not path.is_file():
                continue
            if filename in ('compat.json', 'app.plan', 'app.hbc', 'receipt.json') or filename.endswith(('.pcm', '.swiftmodule', '.tbd', '.receipt.json', '.build.json')):
                continue
            files.append(os.fsencode(path.relative_to(root)))
# These are only the four generated Rust files used to key an archive, not the bake or its receipt.
compiled = root / 'bake-compiled'
if compiled.is_dir() and not compiled.is_symlink():
    for base, dirs, names in os.walk(compiled, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(base) / d).is_symlink()]
        for filename in names:
            path = Path(base) / filename
            if filename in ('.complete', 'module.rs', 'logic.rs', 'linked.rs', 'host.rs') and path.is_file() and not path.is_symlink():
                files.append(os.fsencode(path.relative_to(root)))
if not files:
    raise SystemExit('No compiled Rust cache files found')
with open(os.environ['LIST'], 'wb') as out:
    out.write(b'\0'.join(sorted(set(files))) + b'\0')
PY
COPYFILE_DISABLE=1 tar -C "$target" --null -T "$list" -cf - | zstd -T0 -1 -o "$output"
