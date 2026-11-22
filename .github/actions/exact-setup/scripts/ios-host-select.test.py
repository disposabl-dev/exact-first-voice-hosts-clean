#!/usr/bin/env python3
import os,shutil,subprocess,sys,tempfile
from pathlib import Path
with tempfile.TemporaryDirectory() as d:
 p=Path(d); shutil.copy(Path(__file__).with_name('ios-host-select.py'),p/'ios-host-select.py')
 (p/'ios-general-link.py').write_text('import sys; sys.exit(1)\n')
 (p/'ios-link-kit.py').write_text('print("stock fallback")\n')
 out=p/'result'; out.write_text('partial')
 env={**os.environ,'EXACT_HOST_CATALOG':str(p/'catalog')}
 r=subprocess.run([sys.executable,str(p/'ios-host-select.py'),'link','--kit',str(p/'kit'),'--out',str(out)],env=env,text=True,capture_output=True)
 assert r.returncode==0 and 'stock fallback' in r.stdout and not out.exists()
 (p/'ios-general-link.py').write_text('print("linked")\n')
 r=subprocess.run([sys.executable,str(p/'ios-host-select.py'),'link','--kit',str(p/'kit'),'--out',str(out)],env=env,text=True,capture_output=True)
 assert r.returncode==0 and 'verified fresh-archive link' in r.stdout and 'stock fallback' not in r.stdout
print('host catalog selection and fallback passed')
