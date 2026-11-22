#!/usr/bin/env python3
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest

SCRIPT = Path(__file__).with_name('ios-host-graph.py')
spec = importlib.util.spec_from_file_location('graph', SCRIPT)
graph = importlib.util.module_from_spec(spec)
spec.loader.exec_module(graph)

def target(name, deps=(), **kw):
    return {'name': name, 'dependencies': list(deps), 'path': 'Sources/' + name, 'settings': [], **kw}

def dep(name, kind='byName', condition=None):
    return {kind: [name, condition]}

class Unit(unittest.TestCase):
    def setUp(self):
        self.package = {'products': [{'name':'App','targets':['App']}], 'targets': [
            target('App', [dep('ExactComposition'), dep('CExactLaunch', 'target')]),
            target('ExactComposition', [dep('Kit')]), target('Kit', [dep('CExact')]),
            target('CExact', type='system'), target('CExactLaunch', type='regular'), target('Unreachable')]}

    def test_product_closure_and_system_c_target(self):
        got = graph.extract(self.package, product='App')
        self.assertEqual(set(got['targets']), {'App','ExactComposition','Kit','CExact','CExactLaunch'})
        self.assertNotIn('Unreachable', got['targets'])

    def test_conditional_edges_and_complete_settings_are_preserved(self):
        self.package['targets'][1]['dependencies'].append(dep('Unreachable', condition={'platformNames':['ios']}))
        self.package['targets'][1]['settings'] = [{'tool':'swift','kind':{'define':{'_0':'EXACT_LINK_VIDEO'}}}]
        got = graph.extract(self.package, targets=['App'])
        self.assertIn('Unreachable', got['targets'])
        self.assertEqual(got['composition_defines'], ['EXACT_LINK_VIDEO'])
        self.assertEqual(got['targets']['ExactComposition'], self.package['targets'][1])

    def test_changes_to_path_settings_and_dependency_are_observable(self):
        base = graph.extract(self.package, targets=['App'])['targets']['Kit']
        for field, value in [('path','Other'), ('settings',[{'tool':'swift','kind':{'define':{'_0':'X'}}}]), ('dependencies',[])]:
            changed = json.loads(json.dumps(self.package)); changed['targets'][2][field] = value
            self.assertNotEqual(base, graph.extract(changed, targets=['App'])['targets']['Kit'])

    def test_fail_closed_on_unknown_and_external_dependencies(self):
        for d in [dep('Missing'), {'product':['External', 'pkg', None, None]}, {'plugin':['X']}, {}]:
            changed = json.loads(json.dumps(self.package)); changed['targets'][0]['dependencies'].append(d)
            with self.assertRaises(graph.GraphError): graph.extract(changed, targets=['App'])

    def test_fail_closed_on_duplicate_and_missing_roots(self):
        with self.assertRaises(graph.GraphError): graph.extract(self.package, product='Missing')
        with self.assertRaises(graph.GraphError): graph.extract(self.package)
        self.package['targets'].append(self.package['targets'][0])
        with self.assertRaises(graph.GraphError): graph.extract(self.package, targets=['App'])

class SwiftPMDifferential(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('EXACT_GRAPH_SDK'), 'set EXACT_GRAPH_SDK to an Exact checkout')
    def test_real_environment_variants_change_expected_records(self):
        package = Path(os.environ['EXACT_GRAPH_SDK']) / 'host/apple'
        base = dict(EXACT_APPLE_LINK='all', EXACT_APP_COMPOSITION='updating', EXACT_UPDATE_TRUST='production', EXACT_WIDGET_GALLERY='0', EXACT_APPLE_INTENT_FILES='0', EXACT_WATCH_SYNC='0')
        def dump(**changes):
            env={k:v for k,v in os.environ.items() if not k.startswith('EXACT_')}; env.update(base); env.update(changes)
            return graph.extract(json.loads(subprocess.check_output(['swift','package','dump-package'], cwd=package, env=env)), product='ExactIOS')
        standard=dump()
        self.assertIn('CExactLaunch', standard['targets'])
        self.assertIn('ExactLaunchParts', standard['targets'])
        self.assertNotEqual(standard['targets']['ExactKit'], dump(EXACT_APPLE_INTENT_FILES='1')['targets']['ExactKit'])
        self.assertNotEqual(standard['targets']['ExactKit'], dump(EXACT_UPDATE_TRUST='development')['targets']['ExactKit'])
        self.assertNotEqual(standard['targets']['ExactComposition'], dump(EXACT_APP_COMPOSITION='embedded')['targets']['ExactComposition'])
        self.assertNotEqual(standard['targets']['ExactComposition'], dump(EXACT_WATCH_SYNC='1')['targets']['ExactComposition'])
        self.assertNotEqual(standard['targets']['ExactLaunchParts'], dump(EXACT_LAUNCH_PARTS_TARGET='arm64-apple-ios17.0')['targets']['ExactLaunchParts'])

    @unittest.skipUnless(os.environ.get('EXACT_GRAPH_SDK'), 'set EXACT_GRAPH_SDK to an Exact checkout')
    def test_combinations_match_singleton_unions(self):
        import random, re
        package = Path(os.environ['EXACT_GRAPH_SDK']) / 'host/apple'
        roster = re.findall(r'^    \("([a-z_]+)", "Exact[^"\n]+"\),?$', (package / 'Package.swift').read_text(), re.M)
        self.assertEqual(len(roster), len(set(roster)))
        self.assertGreater(len(roster), 30)
        def dump(links):
            env={k:v for k,v in os.environ.items() if not k.startswith('EXACT_')}; env.update(EXACT_APPLE_LINK=links, EXACT_APP_COMPOSITION='updating', EXACT_UPDATE_TRUST='production', EXACT_WIDGET_GALLERY='0', EXACT_APPLE_INTENT_FILES='0', EXACT_WATCH_SYNC='0')
            result=json.loads(subprocess.check_output(['swift','package','dump-package'], cwd=package, env=env))
            return graph.extract(result, targets=['ExactComposition'])
        empty=dump('')
        single={name:dump(name) for name in roster + ['io', 'modules', 'format']}
        def assert_union(label, names, result):
            union=dict(empty['targets']); definitions=set(empty['composition_defines'])
            for name in names:
                g=single[name]; definitions.update(g['composition_defines'])
                for key, value in g['targets'].items():
                    if key == 'ExactComposition': continue
                    if key in union: self.assertEqual(union[key], value, (label,key))
                    union[key]=value
            actual=dict(result['targets']); actual.pop('ExactComposition')
            union.pop('ExactComposition')
            self.assertEqual(actual, union, label)
            self.assertEqual(set(result['composition_defines']), definitions, label)
        self.assertEqual(empty['roots'], ['ExactComposition'])
        assert_union('all', roster, dump('all'))
        cases={
            'paperstack':['markdown','io'], 'trail':['io','modules','native_views'],
            'aperture-field':['effects','io','modules','svg','images','native_views'],
            'voice':['format','io','modules','native_views','video'],
            'tools-markdown-sound':['tools','markdown','sound'],
            'auth-notifications-drag':['auth_session','notifications','drag'],
            'surfaces-effects-navigation':['surfaces','effects','navigation'],
        }
        rng=random.Random(19317)
        for i in range(6): cases['random-'+str(i)]=rng.sample(roster, rng.randint(2,9))
        for label,names in cases.items():
            with self.subTest(label=label, capabilities=names): assert_union(label,names,dump(','.join(names)))

    @unittest.skipUnless(os.environ.get('EXACT_GRAPH_SDK'), 'set EXACT_GRAPH_SDK to an Exact checkout')
    def test_every_capability_against_universal_graph(self):
        sdk = Path(os.environ['EXACT_GRAPH_SDK'])
        package = sdk / 'host/apple'
        source = (package / 'Package.swift').read_text()
        import re
        roster = re.findall(r'^    \("([a-z_]+)", "Exact[^"\n]+"\),?$', source, re.M)
        self.assertGreater(len(roster), 30, 'SDK capability roster not recognized')
        def dump(links):
            env = {k:v for k,v in os.environ.items() if not k.startswith('EXACT_')}; env.update(EXACT_APPLE_LINK=links, EXACT_APP_COMPOSITION='updating', EXACT_UPDATE_TRUST='production', EXACT_WIDGET_GALLERY='0', EXACT_APPLE_INTENT_FILES='0', EXACT_WATCH_SYNC='0')
            return json.loads(subprocess.check_output(['swift','package','dump-package'], cwd=package, env=env))
        universal = graph.extract(dump('all'), targets=['ExactComposition'])
        for capability in roster:
            with self.subTest(capability=capability):
                current = graph.extract(dump(capability), targets=['ExactComposition'])
                for name, record in current['targets'].items():
                    if name != 'ExactComposition':
                        self.assertEqual(universal['targets'].get(name), record, name)
                expected = {s['kind']['define']['_0'] for s in current['targets']['ExactComposition']['settings'] if s.get('tool')=='swift' and 'define' in s.get('kind',{})}
                self.assertEqual(set(current['composition_defines']), expected)
        for alias in ['motion','drag','timelines','transitions']:
            with self.subTest(alias=alias):
                current=graph.extract(dump(alias), targets=['ExactComposition'])
                self.assertIn('EXACT_LINK_ANIMATIONS', current['composition_defines'])
                self.assertEqual(universal['targets']['ExactAnimations'], current['targets']['ExactAnimations'])

if __name__ == '__main__': unittest.main()
