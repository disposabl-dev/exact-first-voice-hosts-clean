#!/usr/bin/env python3
"""Regression tests for source changes that must disable the host fast path.

Run with IOS_GENERAL_CANDIDATE pointing at the implementation under review.
"""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPTS=Path(__file__).parent
candidate=Path(os.environ.get('IOS_GENERAL_CANDIDATE', SCRIPTS/'ios-general-link.py'))
spec=importlib.util.spec_from_file_location('candidate', candidate)
# The reviewed implementation loads helpers relative to its own location.
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class SourceIsolation(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.sdk=Path(self.temp.name)/'sdk'; self.sdk.mkdir(); self.app=Path(self.temp.name)/'app'; self.app.mkdir()
        self.receipt=Path(self.temp.name)/'receipt.json'
        self.receipt.write_text('{"binary":{"sourceInputs":{"files":[]}}}')
        for p in ['host/apple/Package.swift','host/apple/Sources/CExact/module.modulemap','host/apple/Sources/CExactLaunch/include/CExactLaunch.h','host/apple/Sources/ExactLaunchParts/Registry.swift','host/apple/Sources/ExactComposition/Composition.swift','host/apple/Sources/ExactIOS/main.swift','host/apple/Sources/ExactKit/Kit.swift','host/apple/include/exact.h']:
            f=self.sdk/p; f.parent.mkdir(parents=True,exist_ok=True); f.write_text('original\n')
        (self.sdk/'.gitignore').write_text('host/apple/Sources/ExactComposition/injected.swift\n')
        subprocess.run(['git','init','-q',str(self.sdk)],check=True)
        subprocess.run(['git','-C',str(self.sdk),'add','.'],check=True)
        subprocess.run(['git','-C',str(self.sdk),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','-qm','base'],check=True)

    def check(self): module.check_sources(self.sdk,self.app,self.receipt,['ExactKit'])

    def test_clean_sources_pass(self):
        self.check()

    def test_modified_c_header_is_rejected(self):
        (self.sdk/'host/apple/include/exact.h').write_text('modified ABI\n')
        with self.assertRaises(ValueError): self.check()

    def test_ignored_extra_swift_source_is_rejected(self):
        (self.sdk/'host/apple/Sources/ExactComposition/injected.swift').write_text('import Foundation\n')
        with self.assertRaises(ValueError): self.check()

if __name__=='__main__': unittest.main()
