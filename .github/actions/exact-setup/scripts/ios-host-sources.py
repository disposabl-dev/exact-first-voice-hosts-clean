#!/usr/bin/env python3
"""Hash SDK-owned host source trees, including generated files and headers."""
import argparse,hashlib,json,os
from pathlib import Path
EXTENSIONS={'.swift','.c','.cc','.cpp','.m','.mm','.h','.hpp','.modulemap'}
def source_hashes(package_directory:Path,target_records:dict)->dict:
 apple=Path(package_directory).resolve()
 if not isinstance(target_records,dict): raise ValueError('Missing target graph')
 def inventory(root):
  if not root.is_dir() or root.is_symlink() or not root.resolve().is_relative_to(apple): raise ValueError('Unsafe SDK source root: '+str(root))
  result={}
  for directory,dirs,files in os.walk(root,followlinks=False):
   dirs.sort(); files.sort(); directory=Path(directory)
   if any((directory/name).is_symlink() for name in dirs): raise ValueError('Symlinked SDK source directory')
   for name in files:
    path=directory/name
    if path.suffix not in EXTENSIONS: continue
    if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(apple): raise ValueError('Unsafe SDK source file: '+str(path))
    h=hashlib.sha256()
    with path.open('rb') as stream:
     for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    result[str(path.relative_to(root))]=h.hexdigest()
  return result
 out={}
 for name,target in sorted(target_records.items()):
  rel=Path(target.get('path') or '')
  if rel.is_absolute() or not rel.parts or '..' in rel.parts: raise ValueError('Invalid SDK target path')
  out[name]=inventory(apple/rel)
 out['public_headers']=inventory(apple/'include')
 return out
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--sdk',required=True);p.add_argument('--graph',required=True);p.add_argument('--output',required=True);p.add_argument('--target',action='append');a=p.parse_args()
 graph=json.loads(Path(a.graph).read_text());targets=graph.get('targets')
 if not isinstance(targets,dict):raise ValueError('Missing target graph')
 if a.target:
  if not set(a.target).issubset(targets):raise ValueError('Unknown target')
  targets={name:targets[name] for name in a.target}
 out=source_hashes(Path(a.sdk)/'host/apple',targets)
 Path(a.output).write_text(json.dumps(out,sort_keys=True,separators=(',',':'))+'\n')
