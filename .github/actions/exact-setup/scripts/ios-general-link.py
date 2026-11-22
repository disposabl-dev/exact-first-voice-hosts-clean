#!/usr/bin/env python3
"""Link verified SDK Swift targets against this app's native archive and bake."""
import argparse
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SDK_REF = 'a55c5581c7e1a62cdee1f09367ea2909c74c7292'
TRIPLE = 'arm64-apple-ios17.0'
CONDITIONS = {'intent_files': False, 'widget_gallery': False, 'watch_sync': False, 'composition': 'updating'}


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


kit_tools = load_script('ios-link-kit')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def output(args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def check_build_args(args, apple_sdk, bake):
    # Compare the flags Exact gave SwiftPM, including every product-specific flag.
    # A new product shape goes back to SwiftPM until its recipe is understood.
    require(args[:4] == ['build', '-c', 'release', '--scratch-path'] and len(args) > 5,
            'unsupported Swift profile')
    fixed = ['--triple', TRIPLE, '--sdk', apple_sdk, '-Xcc', '-isysroot', '-Xcc', apple_sdk,
             '-Xlinker', '-syslibroot', '-Xlinker', apple_sdk, '-Xswiftc', '-Xclang-linker',
             '-Xswiftc', '-isysroot', '-Xswiftc', '-Xclang-linker', '-Xswiftc', apple_sdk,
             '-Xlinker', '-needed_framework', '-Xlinker', 'SafariServices',
             '-Xlinker', '-exported_symbol', '-Xlinker', '_exact_advance*']
    rest = args[5:]
    require(rest[:len(fixed)] == fixed, 'unsupported Swift or linker flags')
    rest = rest[len(fixed):]
    for section, filename in zip(('__exact_plan', '__exact_compat', '__exact_hbc'), kit_tools.PAYLOAD):
        require(rest[:7] == ['-Xlinker', '-sectcreate', '-Xlinker', '__TEXT', '-Xlinker', section, '-Xlinker']
                and len(rest) >= 8, 'unexpected linked section')
        path = Path(rest[7])
        require(path.name == filename and kit_tools.digest(path) == kit_tools.digest(bake / filename),
                'section input differs from bake')
        rest = rest[8:]
    require(rest == ['-Xlinker', '-sectalign', '-Xlinker', '__TEXT', '-Xlinker', '__exact_hbc',
                     '-Xlinker', '0x10'], 'unexpected final linker inputs')


def verified_archive(receipt, bake, app):
    facts = kit_tools.facts(receipt, bake, app)
    record = kit_tools.load(receipt)
    identity = kit_tools.load(app / 'app.json')
    inputs = record['compat']['inputs']
    require(inputs['app'] == (identity.get('app', {}).get('id') or identity.get('id')) and
            inputs['keys'] == identity.get('deploy', {}).get('signing', {}).get('keys'),
            'receipt identity or update keys differ')
    archives = [p for p in record['products'] if p['path'].endswith('.a')]
    require(len(archives) == 1, 'expected one fresh Cargo archive')
    archive = Path(archives[0]['path']).resolve()
    require(archive.is_relative_to(Path(os.environ['CARGO_TARGET_DIR']).resolve()) and
            kit_tools.digest(archive) == facts['archive'], 'Cargo archive differs from receipt')
    captured = Path(os.environ['EXACT_LIB_DIR']) / ('lib' + os.environ['EXACT_LIB'] + '.a')
    require(captured.name == archive.name and kit_tools.digest(captured) == facts['archive'],
            'SwiftPM archive differs from receipt')
    links = inputs.get('linked')
    require(isinstance(links, list) and all(isinstance(v, str) and re.fullmatch('[a-z0-9_]+', v) for v in links)
            and os.environ['EXACT_APPLE_LINK'] == (','.join(dict.fromkeys(links)) or 'none'),
            'Swift graph differs from production bake')
    return archive, links


def compile_record(record):
    # Cargo's -L directory and library name are resolved against the fresh
    # archive above. All other flags, including every Swift flag, must match.
    if record['name'] != 'ExactCore':
        return record
    record = dict(record)
    settings = list(record['settings'])
    require(len(settings) >= 3 and settings[1].get('tool') == 'linker' and
            settings[2].get('tool') == 'linker' and
            settings[1]['kind']['unsafeFlags']['_0'][0] == '-L' and
            len(settings[1]['kind']['unsafeFlags']['_0']) == 2 and
            isinstance(settings[2]['kind']['linkedLibrary']['_0'], str), 'unknown Cargo linkage')
    record['settings'] = settings[:1] + settings[3:]
    return record


def prepare_graph(package, manifest):
    current = json.loads(output(['/usr/bin/xcrun', 'swift', 'package', '--package-path', str(package), 'dump-package']))
    graph = load_script('ios-host-graph').extract(current, product='ExactIOS')
    targets = graph['targets']
    require(targets['ExactLaunchParts']['path'] == 'Sources/ExactLaunchParts' and
            targets['ExactIOS']['path'] == 'Sources/ExactIOS', 'custom launch objects')
    composition = targets['ExactComposition']
    require(composition['path'] == 'Sources/ExactComposition' and all(
        set(s) == {'tool', 'kind'} and s['tool'] == 'swift' and set(s['kind']) == {'define'}
        for s in composition['settings']), 'unsupported composition settings')
    require(not targets['ExactLaunchParts']['settings'] and not targets['ExactIOS']['settings'],
            'unsupported entry point flags')
    rebuilt = {'ExactIOS', 'ExactLaunchParts', 'ExactComposition'}
    for name in sorted(targets.keys() - rebuilt):
        require(name in manifest['targets'] and
                compile_record(targets[name]) == compile_record(manifest['targets'][name]),
                'target settings changed: ' + name)
    modules = sorted(targets.keys() - rebuilt - {'CExact', 'CExactLaunch'})
    require(all(re.fullmatch('Exact[A-Za-z0-9]+', name) for name in modules), 'non-SDK target')
    return modules, graph['composition_defines']


def check_sources(sdk, app, receipt, modules):
    # The catalog and runtime recipe use only source owned by the immutable SDK.
    roots = ['host/apple/Package.swift', 'host/apple/include', *['host/apple/Sources/' + n for n in
             [*modules, 'CExact', 'CExactLaunch', 'ExactLaunchParts', 'ExactComposition', 'ExactIOS']]]
    require(not output(['git', '-C', str(sdk), 'status', '--porcelain', '--untracked-files=all', '--', *roots]),
            'SDK host sources changed')
    # SwiftPM picks up source files even if Git ignores them. The three
    # rebuilt SDK targets have no generated files, so their trees must match.
    for name in ('ExactLaunchParts', 'ExactComposition', 'ExactIOS'):
        rel = 'host/apple/Sources/' + name
        tracked = set(output(['git', '-C', str(sdk), 'ls-tree', '-r', '--name-only', 'HEAD', '--', rel]).splitlines())
        root = sdk / rel
        actual = {p.relative_to(sdk).as_posix() for p in root.rglob('*') if p.is_file() or p.is_symlink()}
        require(actual == tracked and not any((sdk / n).is_symlink() for n in actual),
                'rebuilt SDK source inventory changed')
    for item in kit_tools.load(receipt)['binary']['sourceInputs']['files']:
        root = {'exact': sdk, 'app': app}[item['root']]
        rel = Path(item['path'])
        require(not rel.is_absolute() and '..' not in rel.parts and
                kit_tools.digest(root / rel) == item['sha256'], 'source changed after bake')


def select_objects(catalog, manifest, modules, include):
    files = manifest['files']
    objects = []
    for name in modules:
        object_path = 'host-objects/' + name + '.o'
        prefix = 'host-objects/' + name + '.swiftmodule/'
        matches = [k for k in files if k.startswith(prefix)]
        require(object_path in files and any(k.endswith('.swiftmodule') for k in matches),
                'incomplete cached target: ' + name)
        module_dir = include / (name + '.swiftmodule')
        module_dir.mkdir()
        for key in [object_path, *matches]:
            path = catalog / key
            require('..' not in Path(key).parts and path.resolve().is_relative_to(catalog)
                    and kit_tools.digest(path) == files[key], 'invalid cached target: ' + name)
            if key.startswith(prefix) and path.suffix in ('.swiftmodule', '.swiftdoc'):
                require(key == prefix + path.name, 'unexpected Swift module layout')
                shutil.copyfile(path, module_dir / path.name)
        objects.append(catalog / object_path)
    return objects


def compile_swift(name, sources, flags, scratch, defines=(), main=False):
    obj = scratch / (name + '.o')
    cmd = [*flags, '-package-name', 'apple', '-module-name', name, '-emit-object', '-emit-module', '-emit-module-path',
           str(scratch / (name + '.swiftmodule'))]
    if not main:
        cmd.append('-parse-as-library')
    for define in defines:
        cmd.extend(['-D', define])
    subprocess.run([*cmd, *map(str, sources), '-o', str(obj)], check=True)
    return obj


def link(args):
    sdk = Path(os.environ['EXACT']).resolve()
    app = Path(args.app).resolve()
    bake = Path(args.bake).resolve()
    kit = Path(args.kit).resolve()
    catalog = Path(os.environ['EXACT_HOST_CATALOG']).resolve()
    destination = Path(args.out).resolve()
    require(not destination.exists(), 'output exists')
    require(output(['git', '-C', str(sdk), 'rev-parse', 'HEAD']) == SDK_REF, 'SDK revision differs')
    original = kit_tools.load(kit / 'kit.json')
    manifest = kit_tools.load(catalog / 'manifest.json')
    toolchain = kit_tools.toolchain()
    require(manifest['ref'] == SDK_REF and manifest['target'] == TRIPLE and manifest['profile'] == 'release-balanced'
            and manifest['conditions'] == CONDITIONS and original['toolchain'] == manifest['toolchain'] == toolchain,
            'unsupported SDK or compiler configuration')
    expected = {'EXACT_APP_COMPOSITION':'updating', 'EXACT_UPDATE_TRUST':'production',
                'EXACT_APPLE_INTENT_FILES':'0', 'EXACT_WIDGET_GALLERY':'0', 'EXACT_WATCH_SYNC':'0',
                'EXACT_IOS_DEPLOYMENT':'17.0'}
    require(all(os.environ.get(k) == v for k, v in expected.items()) and
            not any(os.environ.get(k) for k in ('EXACT_LAUNCH_PARTS','EXACT_CORE_FRAMEWORK','EXACT_LAUNCH_PARTS_TARGET')) and
            all(os.environ.get(k) != '1' for k in ('EXACT_TESTS','EXACT_WIDGET_CORE')), 'unsupported native settings')
    check_build_args(json.loads(os.environ['EXACT_HOST_PRODUCT_ARGS']), toolchain[2], bake)
    archive, links = verified_archive(args.receipt, bake, app)
    package = sdk / 'host/apple'
    modules, defines = prepare_graph(package, manifest)
    check_sources(sdk, app, args.receipt, modules)
    reused = {name: manifest['targets'][name] for name in [*modules, 'CExact', 'CExactLaunch']}
    inventories = load_script('ios-host-sources').source_hashes(package, reused)
    require(all(manifest['source_hashes'].get(name) == files for name, files in inventories.items()),
            'cached SDK source or header inventory differs')
    sources = package / 'Sources'
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.exact-link-', dir=destination.parent) as tmp:
        scratch = Path(tmp)
        objects = select_objects(catalog, manifest, modules, scratch)
        launch = scratch / 'CExactLaunch.o'
        c_source = sources / 'CExactLaunch'
        subprocess.run(['/usr/bin/xcrun', '--sdk', 'iphoneos', 'clang', '-target', TRIPLE, '-isysroot',
                        toolchain[2], '-O2', '-I', str(c_source / 'include'), '-c', str(c_source / 'launch.c'),
                        '-o', str(launch)], check=True)
        modulemap = scratch / 'CExactLaunch.modulemap'
        modulemap.write_text('module CExactLaunch { umbrella header "' + str(sources / 'CExactLaunch/include/CExactLaunch.h') + '" export * }\n')
        flags = ['/usr/bin/xcrun', '--sdk', 'iphoneos', 'swiftc', '-target', TRIPLE, '-sdk', toolchain[2],
                 '-swift-version', '5', '-O', '-whole-module-optimization', '-D', 'SWIFT_PACKAGE',
                 '-I', str(scratch), '-I', str(sources / 'CExact'), '-Xcc', '-fmodule-map-file=' + str(modulemap)]
        for name in ('ExactLaunchParts', 'ExactComposition', 'ExactIOS'):
            objects.append(compile_swift(name, sorted((sources / name).glob('*.swift')), flags, scratch,
                                         defines if name == 'ExactComposition' else (), main=name == 'ExactIOS'))
        response = scratch / 'objects.rsp'
        response.write_text('\n'.join(shlex.quote(str(obj)) for obj in [launch, *objects]))
        linked = scratch / 'app'
        replacements = {'@@KIT@/link.txt': '@' + str(response), '@KIT@/ExactIOS': str(linked),
                        '@KIT@/ExactIOS.swiftmodule': str(scratch / 'ExactIOS.swiftmodule'),
                        '-lapps_apple': str(archive), '@KIT@/dependency.dat': str(scratch / 'dependency.dat'),
                        '@KIT@/lto.o': str(scratch / 'lto.o')}
        replacements.update({'@KIT@/' + n: str(bake / n) for n in kit_tools.PAYLOAD})
        require(all(original['commands'].count(key) == 1 for key in replacements), 'unsupported public link recipe')
        cmd = [replacements.get(arg, arg.replace('@KIT@', str(kit))) for arg in original['commands']]
        subprocess.run(cmd, check=True)
        kit_tools.verify_sections(linked, bake)
        linked.replace(destination)
    if marker := os.environ.get('EXACT_GENERAL_PROOF'):
        Path(marker).write_text(json.dumps({'graph': links, 'targets': modules, 'executable': kit_tools.digest(destination)}))
    print(f'iOS host catalog reused {len(modules)} Swift targets')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['link'])
    for key in ('kit', 'receipt', 'bake', 'out'):
        parser.add_argument('--' + key, required=True)
    parser.add_argument('--app', default=os.getcwd())
    link(parser.parse_args())
