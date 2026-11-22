#!/usr/bin/env bash
# Apply the same pinned Android release profile on producer and consumer.
set -euo pipefail
: "${EXACT:?Set EXACT to the pinned SDK checkout}"
expected=a55c5581c7e1a62cdee1f09367ea2909c74c7292
[[ $(git -C "$EXACT" rev-parse HEAD) == "$expected" ]] || { echo 'Unsupported Exact revision for Android fast profile' >&2; exit 1; }
python3 - "$EXACT" <<'PY'
import os, pathlib, subprocess, sys
root = pathlib.Path(sys.argv[1])
path = root / 'scripts/cargo-check.mjs'
original = subprocess.check_output(['git', '-C', str(root), 'show', 'HEAD:scripts/cargo-check.mjs'])
old = b"return ['apple','ios','macos','tvos','android'].includes(kind)"
new = b'''if (kind === 'android' && profile === 'release') return ['--config','profile.release.lto="off"','--config','profile.release.codegen-units=16'];\n  ''' + old
if original.count(old) != 1:
    raise SystemExit('Unsupported Android release profile source')
patched = original.replace(old, new)
if path.read_bytes() not in (original, patched):
    raise SystemExit('Android release profile has unexpected edits')
if path.read_bytes() != patched:
    path.write_bytes(patched)
# Cargo records mtimes as freshness evidence. Normalize only pinned SDK sources,
# not app inputs. Give each tracked build.rs a distinct, later timestamp.
raw = subprocess.check_output(['git', '-C', str(root), 'ls-files', '-z'])
files = [root / os.fsdecode(x) for x in raw.split(b'\0') if x]
dirs = {root}
for file in files:
    if file.is_symlink() or not file.is_file():
        continue
    dirs.update(p for p in file.parents if p == root or root in p.parents)
    os.utime(file, (1577836800, 1577836800))
for directory in dirs:
    os.utime(directory, (1577836800, 1577836800))
for i, file in enumerate(sorted(f for f in files if f.name == 'build.rs' and not f.is_symlink() and f.is_file())):
    stamp = 1577923200 + i
    os.utime(file, (stamp, stamp))
PY
