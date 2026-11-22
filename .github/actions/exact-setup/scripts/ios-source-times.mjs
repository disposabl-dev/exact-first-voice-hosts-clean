#!/usr/bin/env bun
// Cargo fingerprints use source mtimes as well as bytes. A public seed records
// source timestamps; a fresh checkout restores them only after verifying bytes.
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { existsSync, lstatSync, readFileSync, realpathSync, statSync, utimesSync, writeFileSync } from 'node:fs';
import { isAbsolute, relative, resolve, sep } from 'node:path';

const [command, ...args] = process.argv.slice(2);
const sdk = realpathSync(process.env.EXACT || (() => { throw Error('EXACT is required'); })());
const ref = process.env.SETUP_EXACT_REF;
if (!/^[0-9a-f]{40}$/.test(ref || '') || execFileSync('git', ['-C', sdk, 'rev-parse', 'HEAD'], {encoding:'utf8'}).trim() !== ref) throw Error('Exact checkout does not match its pin');
const absolute = p => { if (!p || !isAbsolute(p)) throw Error('expected an absolute path'); return resolve(p); };
const cargo = realpathSync(process.env.CARGO_HOME || resolve(process.env.HOME || '', '.cargo'));
const cargoAllowed = rel => /^(?:registry\/src|git\/checkouts|hermes-lean-sys)\//.test(rel.split(sep).join('/'));
const digest = p => createHash('sha256').update(readFileSync(p)).digest('hex');
const within = (root, path) => path === root || path.startsWith(root + sep);
const safe = (root, rel) => {
  if (typeof rel !== 'string' || !rel || isAbsolute(rel) || rel.split(/[\\/]/).some(x => !x || x === '.' || x === '..')) throw Error('invalid source path');
  const path = resolve(root, rel);
  if (!within(root, path) || !within(root, realpathSync(path)) || !lstatSync(path).isFile()) throw Error(`source escapes its root: ${rel}`);
  return path;
};
if (command === 'record' && args.length === 3) {
  const [receiptPath, appPath, output] = args.map(absolute), app = realpathSync(appPath);
  const receipt = JSON.parse(readFileSync(receiptPath, 'utf8'));
  if (receipt.trust !== 'production' || receipt.compat?.target !== 'aarch64-apple-ios' || receipt.compat?.inputs?.platform !== 'ios' || !Array.isArray(receipt.binary?.inputs)) throw Error('invalid production iOS compiler receipt');
  const entries = new Map();
  for (const input of receipt.binary.inputs) {
    if (!input || !isAbsolute(input.path || '') || !/^[0-9a-f]{64}$/.test(input.sha256 || '')) throw Error('invalid compiler input');
    const path = resolve(input.path), kind = within(app, path) ? 'app' : within(sdk, path) ? 'sdk' : within(cargo, path) && cargoAllowed(relative(cargo, path)) ? 'cargo' : null;
    if (!kind) continue; // registry and toolchain inputs retain their packaged mtimes
    const root = kind === 'app' ? app : kind === 'sdk' ? sdk : cargo, rel = relative(root, path);
    // A symlink into a package cache is not a source owned by either checkout.
    if (!within(root, realpathSync(path))) continue;
    safe(root, rel);
    if (digest(path) !== input.sha256) throw Error(`compiler input changed: ${path}`);
    const info = statSync(path, {bigint:true});
    entries.set(`${kind}/${rel}`, {root:kind, path:rel.split(sep).join('/'), sha256:input.sha256, mtimeNs:info.mtimeNs.toString()});
  }
  if (!entries.size || ![...entries.values()].some(x => x.root === 'app' && x.path.startsWith('.exact/shells/'))) throw Error('receipt has no generated app shell inputs');
  const manifest = {version:1, exactRef:ref, sdkPath:sdk, appPath:app, cargoPath:cargo, files:[...entries.values()].sort((a,b) => `${a.root}/${a.path}`.localeCompare(`${b.root}/${b.path}`))};
  writeFileSync(output, JSON.stringify(manifest) + '\n', {flag:'wx'});
  console.log(`Recorded ${manifest.files.length} public source timestamps`);
} else if (command === 'apply' && args.length === 2) {
  const [manifestPath, appPath] = args.map(absolute), appRoot = realpathSync(appPath);
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'));
  if (manifest.version !== 1 || manifest.exactRef !== ref || manifest.sdkPath !== sdk || manifest.appPath !== appRoot || manifest.cargoPath !== cargo || !Array.isArray(manifest.files) || manifest.files.length > 100000) throw Error('source timestamp manifest does not match checkout');
  // Ask the stock SDK to generate the consumer shell, including Cargo.lock.
  const {resolveApp} = await import(resolve(sdk, 'scripts/app.mjs'));
  const app = resolveApp(appRoot);
  if (!app.generated || !existsSync(resolve(appRoot, 'app.ts')) || existsSync(resolve(appRoot, 'data/Cargo.toml')) || app.manifest.game || app.manifest.library || app.hasGpu || app.manifest.share_target || app.manifest.host?.ios?.appClip || app.manifest.widgets?.length || app.manifest.live_activities?.length) throw Error('only a stock generated TypeScript app is supported');
  app.rust();
  let restored = 0, changed = 0;
  const seen = new Set();
  for (const entry of manifest.files) {
    if (!entry || !['sdk','app','cargo'].includes(entry.root) || !/^[0-9a-f]{64}$/.test(entry.sha256 || '') || !/^\d{1,20}$/.test(entry.mtimeNs || '')) throw Error('invalid source timestamp entry');
    const key = `${entry.root}/${entry.path}`;
    if (seen.has(key)) throw Error('duplicate source timestamp entry');
    seen.add(key);
    if (entry.root === 'cargo' && !cargoAllowed(entry.path)) throw Error('invalid Cargo source path');
    if (entry.root === 'cargo' && process.env.IOS_RESTORE_CARGO_SOURCE_MTIMES !== '1') { changed++; continue; }
    let path;
    try { path = safe(entry.root === 'app' ? appRoot : entry.root === 'sdk' ? sdk : cargo, entry.path); }
    catch (error) { if (error.code === 'ENOENT') { changed++; continue; } throw error; }
    if (digest(path) !== entry.sha256) {
      // Even a tampered file whose mtime was forged to the seed must dirty Cargo.
      const now = new Date();
      utimesSync(path, statSync(path).atime, now);
      changed++; continue;
    }
    const time = Number(BigInt(entry.mtimeNs)) / 1e9;
    if (!Number.isFinite(time) || time > Date.now() / 1000 + 86400) throw Error('invalid source timestamp');
    const atime = statSync(path).atime;
    utimesSync(path, atime, time);
    restored++;
  }
  console.log(`Restored ${restored} matching source timestamps; ${changed} changed or missing files will rebuild`);
} else throw Error('usage: ios-source-times.mjs record <build.json> <app-dir> <new-manifest> | apply <manifest> <app-dir>');
