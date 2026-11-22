#!/usr/bin/env python3
"""Extract the reachable SwiftPM target graph without guessing capability aliases.

Input is `swift package dump-package` from the exact environment used to build.
The complete target records are intentionally retained: callers must compare
records and separately validate source contents, compiler inputs and link flags.
"""
import argparse
import json
from pathlib import Path

class GraphError(ValueError):
    pass

def extract(package, product=None, targets=()):
    if not isinstance(package, dict) or not isinstance(package.get('targets'), list):
        raise GraphError('invalid SwiftPM package')
    indexed = {}
    for item in package['targets']:
        if not isinstance(item, dict) or not isinstance(item.get('name'), str) or item['name'] in indexed:
            raise GraphError('invalid or duplicate target')
        indexed[item['name']] = item
    roots = list(targets)
    if product is not None:
        matches = [p for p in package.get('products', []) if p.get('name') == product]
        if len(matches) != 1 or not isinstance(matches[0].get('targets'), list):
            raise GraphError('unknown or ambiguous product: ' + product)
        roots.extend(matches[0]['targets'])
    if not roots or any(not isinstance(x, str) or not x for x in roots):
        raise GraphError('at least one target or product is required')
    roots = sorted(set(roots))
    selected = {}
    pending = list(roots)
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        if name not in indexed:
            raise GraphError('unresolved target: ' + name)
        target = indexed[name]
        deps = target.get('dependencies')
        if not isinstance(deps, list):
            raise GraphError('invalid dependencies: ' + name)
        selected[name] = target
        for dep in deps:
            if not isinstance(dep, dict) or len(dep) != 1:
                raise GraphError('unknown dependency shape in ' + name)
            kind, value = next(iter(dep.items()))
            # Conservatively traverse conditioned edges too; the entire edge is
            # retained in the signature, so conditions can never be overlooked.
            if kind not in ('target', 'byName') or not isinstance(value, list) or not value or not isinstance(value[0], str):
                raise GraphError('unsupported dependency in ' + name + ': ' + kind)
            pending.append(value[0])
    comp = selected.get('ExactComposition', {})
    defines = []
    for setting in comp.get('settings', []):
        kind = setting.get('kind', {}) if isinstance(setting, dict) else {}
        if 'define' in kind and setting.get('tool') == 'swift':
            value = kind['define']
            if not isinstance(value, dict) or not isinstance(value.get('_0'), str):
                raise GraphError('invalid composition define')
            defines.append(value['_0'])
    return {'schema': 1, 'roots': roots, 'targets': {k: selected[k] for k in sorted(selected)}, 'composition_defines': sorted(defines)}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package-dump', required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--product')
    group.add_argument('--target', action='append', dest='targets')
    parser.add_argument('--output')
    args = parser.parse_args()
    try:
        graph = extract(json.loads(Path(args.package_dump).read_text()), args.product, args.targets or ())
        data = json.dumps(graph, sort_keys=True, separators=(',', ':'), ensure_ascii=True) + '\n'
        if args.output:
            Path(args.output).write_text(data)
        else:
            print(data, end='')
    except (OSError, ValueError, TypeError, KeyError) as e:
        parser.exit(1, f'ios-host-graph: {e}\n')

if __name__ == '__main__':
    main()
