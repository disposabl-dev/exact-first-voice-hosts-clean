#!/usr/bin/env python3
"""Try the verified Swift target catalog, then the original stock link proof."""
import os
import subprocess
import sys
from pathlib import Path

scripts = Path(__file__).resolve().parent
args = sys.argv[1:]
if not args or args[0] != 'link':
    raise SystemExit('link only')

if os.environ.get('EXACT_HOST_CATALOG'):
    attempt = subprocess.run([sys.executable, str(scripts / 'ios-general-link.py'), *args],
                             capture_output=True, text=True)
    if attempt.returncode == 0:
        print('host catalog: verified fresh-archive link')
        raise SystemExit(0)
    print('host catalog refused; trying stock link kit', file=sys.stderr)
    if '--out' in args:
        Path(args[args.index('--out') + 1]).unlink(missing_ok=True)

raise SystemExit(subprocess.call([sys.executable, str(scripts / 'ios-link-kit.py'), *args]))
