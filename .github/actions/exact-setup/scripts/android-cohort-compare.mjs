import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
const { cohortReceipt } = await import(resolve(process.env.EXACT, 'scripts/deploy-heads.mjs'));
const { canonicalJson } = await import(resolve(process.env.EXACT, 'scripts/deploy-signing.mjs'));
export function readCohort(dir) {
  const files = readdirSync(dir).filter(name => /^android-.*\.build\.json$/.test(name)).sort();
  if (!files.length) throw Error('Missing Android build receipt');
  return files.map(name => {
    const build = JSON.parse(readFileSync(resolve(dir, name), 'utf8'));
    if (build.trust !== 'production' || build.compat?.inputs?.platform !== 'android') throw Error('Invalid Android production receipt');
    return cohortReceipt(build);
  });
}
if (import.meta.main && canonicalJson(readCohort(process.argv[2])) !== canonicalJson(readCohort(process.argv[3]))) {
  throw Error('APK and AAB update cohorts differ');
}
