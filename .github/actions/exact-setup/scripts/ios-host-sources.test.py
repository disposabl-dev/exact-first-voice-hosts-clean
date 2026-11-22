#!/usr/bin/env python3
import importlib.util,tempfile
from pathlib import Path
p=Path(__file__).with_name('ios-host-sources.py');s=importlib.util.spec_from_file_location('host_sources',p);m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
with tempfile.TemporaryDirectory() as d:
 root=Path(d); core=root/'Sources/ExactCore';headers=root/'include';core.mkdir(parents=True);headers.mkdir()
 generated=core/'GeneratedBatch.swift';generated.write_text('one'); (headers/'exact.h').write_text('header')
 records={'ExactCore':{'path':'Sources/ExactCore'}}
 first=m.source_hashes(root,records); assert 'GeneratedBatch.swift' in first['ExactCore'] and 'exact.h' in first['public_headers']
 generated.write_text('two'); assert m.source_hashes(root,records)!=first
 (core/'new.swift').write_text('extra'); assert 'new.swift' in m.source_hashes(root,records)['ExactCore']
 (core/'escape.h').symlink_to(headers/'exact.h')
 try: m.source_hashes(root,records)
 except ValueError: pass
 else: raise AssertionError('symlink accepted')
print('generated source, inventory changes and symlink rejection verified')
