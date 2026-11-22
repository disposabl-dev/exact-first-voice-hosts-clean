#!/usr/bin/env python3
"""Capture/restore only Exact's app-independent, content-keyed iOS arms."""
import argparse, hashlib, importlib.util, json, os, re, shutil, subprocess
from pathlib import Path

pattern=re.compile(r'(?:[0-9a-f]{16}-libexact_(?:web|video|sound|midi)\.dylib|sound_(?:mixer-c|render-c|engine-m)-[0-9a-f]{16}\.o)')
def digest(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''): h.update(block)
    return h.hexdigest()
def apple_toolchain():
    spec=importlib.util.spec_from_file_location('linkkit',Path(__file__).with_name('ios-link-kit.py')); mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod.toolchain()
def macho(path):
    subprocess.run(['xcrun','lipo','-verify_arch','arm64',str(path)],check=True,stdout=subprocess.DEVNULL)
    output=subprocess.check_output(['xcrun','vtool','-show-build',str(path)],text=True)
    if not re.search(r'^\s*platform\s+(?:ios|2)\s*$',output,re.I|re.M): raise ValueError('Not an iOS arm: '+str(path))
parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['capture','restore']);parser.add_argument('--kit',required=True);parser.add_argument('--target',required=True);args=parser.parse_args()
kit=Path(args.kit).resolve(); target=Path(args.target).resolve(); armcache=target/'apple-swift/ios-17.0-balanced/arms'; stored=kit/'arms'; info=json.loads((kit/'kit.json').read_text())
if info['toolchain']!=apple_toolchain(): raise ValueError('Apple toolchain mismatch; use normal native build')
if args.mode=='capture':
    if stored.exists() or not armcache.is_dir() or armcache.is_symlink(): raise ValueError('Arm source missing or destination exists')
    chosen=sorted(p for p in armcache.iterdir() if pattern.fullmatch(p.name))
    if not chosen: raise ValueError('No public iOS arms found')
    stored.mkdir()
    files=[]
    for src in chosen:
        if src.is_symlink() or not src.is_file(): raise ValueError('Arm must be a regular file')
        macho(src); dest=stored/src.name; shutil.copyfile(src,dest)
        if digest(src)!=digest(dest): raise ValueError('Copied arm digest mismatch')
        files.append({'name':src.name,'sha256':digest(dest)})
    (stored/'manifest.json').write_text(json.dumps({'version':1,'files':files},sort_keys=True)+'\n')
    print('Captured',len(files),'public iOS arms')
else:
    manifest=json.loads((stored/'manifest.json').read_text()); files=manifest.get('files')
    if manifest.get('version')!=1 or not isinstance(files,list) or not 1<=len(files)<=32: raise ValueError('Invalid arm manifest')
    seen=set(); checked=[]
    for item in files:
        name=item.get('name',''); expected=item.get('sha256','')
        if name in seen or not pattern.fullmatch(name) or not re.fullmatch('[0-9a-f]{64}',expected): raise ValueError('Invalid arm entry')
        seen.add(name); src=stored/name
        if src.is_symlink() or not src.is_file() or digest(src)!=expected: raise ValueError('Arm digest mismatch: '+name)
        macho(src); checked.append((src,expected))
    armcache.mkdir(parents=True,exist_ok=True)
    if armcache.is_symlink(): raise ValueError('Arm cache is a symlink')
    for src,expected in checked:
        dest=armcache/src.name
        if dest.exists() or dest.is_symlink():
            if dest.is_symlink() or not dest.is_file() or digest(dest)!=expected: raise ValueError('Existing arm differs: '+src.name)
        else: shutil.copyfile(src,dest)
    print('Restored',len(checked),'verified content-keyed iOS arms')
