#!/usr/bin/env python3
"""Capture project-owned inputs from a completed Exact SwiftPM link for review.

Experimental. This does not establish source eligibility or publish a kit.
"""
import argparse, hashlib, json, os, shutil, subprocess
from pathlib import Path

p=argparse.ArgumentParser()
for name in ('manifest','receipt','bake','archive','out'): p.add_argument('--'+name,required=True)
a=p.parse_args(); out=Path(a.out).resolve(); receipt=json.loads(Path(a.receipt).read_text()); receipt=receipt.get('build',receipt)
manifest=json.loads(Path(a.manifest).read_text()); candidates=[v for k,v in manifest['commands'].items() if 'Ld ' in k and '/ExactIOS normal' in k]
if len(candidates)!=1 or out.exists(): raise ValueError('Expected one ExactIOS link and an unused destination')
command=candidates[0]; args=command['args']; responses=[x[1:] for x in args if x.startswith('@/')]
if len(responses)!=1: raise ValueError('Expected one link response file')
products={x['sha256'] for x in receipt['products'] if x['path'].endswith('.a')}
def digest(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
if len(products)!=1 or digest(a.archive) not in products or receipt['trust']!='production': raise ValueError('Production archive does not match receipt')
bake=Path(a.bake).resolve()
if json.loads((bake/'compat.json').read_text())!=receipt['compat']: raise ValueError('Bake does not match receipt')
response=Path(responses[0]).resolve()
if 'apple-swift' not in response.parts: raise ValueError('Unexpected Swift build directory')
target=Path(*response.parts[:response.parts.index('apple-swift')])
objects=response.read_text().split()
if not objects or any(not x.endswith('.o') for x in objects): raise ValueError('Unexpected object response')
out.mkdir(parents=True); copied=[]
for i,src in enumerate(objects):
 dest=out/f'{i:02}-{Path(src).name}'; shutil.copyfile(src,dest)
 if digest(src)!=digest(dest): raise ValueError('Copied object mismatch')
 copied.append(dest)
(out/'link.txt').write_text(' '.join(map(str,copied)))
archive=out/Path(a.archive).name; shutil.copyfile(a.archive,archive)
if digest(archive) not in products: raise ValueError('Copied archive mismatch')
for n in ('app.plan','compat.json','app.hbc'): shutil.copyfile(bake/n,out/n)
modules=[x for x in args if x.endswith('/ExactIOS.swiftmodule')]
if len(modules)!=1: raise ValueError('Expected one Swift module')
module=out/'ExactIOS.swiftmodule'; shutil.copyfile(modules[0],module)
if digest(module)!=digest(modules[0]): raise ValueError('Copied Swift module mismatch')
# The narrow prototype admits only normal Exact host executable links, no user frameworks.
developer=os.path.realpath(subprocess.check_output(['/usr/bin/xcrun','--sdk','iphoneos','--show-sdk-path'],text=True).strip()).split('/Platforms/', 1)[0]
allowed=(str(out),developer,'/Applications','/usr','/System','/Library')
new=[]
for x in args:
 if x=='@'+responses[0]: x='@'+str(out/'link.txt')
 elif x.endswith('/ExactIOS_lto.o'): x=str(out/'lto.o')
 elif x.endswith('/ExactIOS_dependency_info.dat'): x=str(out/'dependency.dat')
 elif x==modules[0]: x=str(module)
 elif x.endswith(('/app.plan','/compat.json','/app.hbc')): x=str(out/Path(x).name)
 elif x.startswith(('-L'+str(target)+'/', '-F'+str(target)+'/')): x=x[:2]+str(out)
 elif x.startswith(str(target)+'/') and x.endswith('/capture'): x=str(out)
 elif x.startswith(str(target)+'/') and x.endswith('/ExactIOS'): x=str(out/'ExactIOS')
 elif x.startswith(str(target)+'/') or x.startswith('@'+str(target)+'/'): raise ValueError('Unexpected build input: '+x)
 # The recipe may refer only to captured project data or the local Apple SDK.
 path = x[2:] if x.startswith(('-L/', '-F/')) else x[1:] if x.startswith('@/') else x
 if path.startswith('/') and not any(os.path.realpath(path) == root or os.path.realpath(path).startswith(root+'/') for root in allowed):
  raise ValueError('Uncaptured absolute linker path: '+x)
 new.append(x)
(out/'commands.json').write_text(json.dumps(new,indent=2)); (out/'digests.json').write_text(json.dumps({x.name:digest(x) for x in [*copied,archive,module]},indent=2))
print('captured',out)
