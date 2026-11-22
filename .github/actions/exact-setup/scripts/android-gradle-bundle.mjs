import { readFileSync, copyFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { readCohort } from './android-cohort-compare.mjs';

const [capture, output, apk] = process.argv.slice(2);
readCohort(process.env.EXACT_BAKE_OUTPUT);
let bundle;
if (capture === '--verify') {
  bundle = output;
} else {
  const params = JSON.parse(readFileSync(capture, 'utf8'));
  if (!params.properties?.appBuildDir || !params.projectCacheDir) throw Error('Incomplete Gradle capture');
  bundle = resolve(params.properties.appBuildDir, 'outputs/bundle/release/app-release.aab');
}
const check = spawnSync('python3', ['-c', `
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as a, zipfile.ZipFile(sys.argv[2]) as b:
    al = {n: a.read(n) for n in a.namelist() if n.startswith('lib/') and n.endswith('.so')}
    bl = {n[5:]: b.read(n) for n in b.namelist() if n.startswith('base/lib/') and n.endswith('.so')}
    if not al or al != bl:
        raise SystemExit('APK and AAB native libraries differ')
`, apk, bundle], { stdio: 'inherit' });
if (check.status !== 0) throw Error('APK and AAB native library verification failed');
if (capture !== '--verify') copyFileSync(bundle, output);
