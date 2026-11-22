// GitHub Actions runs this from the Exact app directory.
import { execFileSync, spawn } from 'node:child_process';
import { createHash, createPrivateKey, createPublicKey, hkdfSync, sign } from 'node:crypto';
import { appendFileSync, existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';

const [command, platform] = process.argv.slice(2);
if (command === 'plan' && !platform) {
  const { EXACT, EXACT_UPDATE_SEED, GITHUB_OUTPUT } = process.env;
  if (!EXACT || !EXACT_UPDATE_SEED || !GITHUB_OUTPUT) throw Error('Missing Exact checkout, update seed or output path');
  const cleanEnv = { ...process.env };
  delete cleanEnv.EXACT_UPDATE_SEED;
  const tmp = mkdtempSync(resolve(tmpdir(), 'exact-update-plan-'));
  const worktree = resolve(tmp, 'ios');
  const appDir = resolve(worktree, 'apps/apps');
  let added = false;
  try {
    execFileSync('git', ['worktree', 'add', '--quiet', '--detach', worktree, 'HEAD'], { env: cleanEnv, stdio: 'inherit' });
    added = true;
    mkdirSync(resolve(appDir, '.exact'), { recursive: true });
    symlinkSync(EXACT, resolve(appDir, '.exact/checkout'));
    execFileSync('bun', ['install', '--frozen-lockfile'], { cwd: appDir, env: cleanEnv, stdio: 'inherit' });
    const platforms = [['android', process.cwd()], ['ios', appDir]];
    const results = await Promise.allSettled(platforms.map(([name, cwd]) => new Promise((resolveChild, reject) => {
      const output = resolve(tmp, `${name}.output`);
      writeFileSync(output, '');
      const child = spawn(process.execPath, [import.meta.filename, 'deliver', name], {
        cwd, env: { ...cleanEnv, EXACT_UPDATE_SEED, GITHUB_OUTPUT: output }, stdio: 'inherit',
      });
      child.on('error', reject);
      child.on('close', code => {
        if (code !== 0) return reject(Error(`${name} update delivery failed (${code})`));
        const values = Object.fromEntries(readFileSync(output, 'utf8').trim().split('\n').map(line => line.split('=')));
        if (!['true', 'false'].includes(values[`${name}_native`])) return reject(Error(`${name} did not report its native decision`));
        if (values[`${name}_native`] === 'false') return resolveChild(null);
        const channel = values[`${name}_channel`];
        if (!/^base-[1-9][0-9]*$/.test(channel ?? '')) return reject(Error(`${name} returned an invalid channel`));
        resolveChild({ platform: name, channel });
      });
    })));
    const failed = results.filter(result => result.status === 'rejected');
    if (failed.length) throw new AggregateError(failed.map(result => result.reason), 'Mobile update delivery failed');
    const native = results.map(result => result.value).filter(Boolean);
    const isPublicAndroid = item => process.env.PUBLIC_REPO === 'true' && item.platform === 'android';
    appendFileSync(GITHUB_OUTPUT, `matrix=${JSON.stringify(native.filter(item => !isPublicAndroid(item)))}\npublic_android=${JSON.stringify(native.filter(isPublicAndroid))}\n`);
  } finally {
    if (added) execFileSync('git', ['worktree', 'remove', '--force', worktree], { env: cleanEnv, stdio: 'inherit' });
    rmSync(tmp, { recursive: true, force: true });
  }
  process.exit(0);
}
const probing = command === 'first' && !platform;
if (!probing && (!['configure', 'deliver', 'seed'].includes(command) || !['ios', 'android'].includes(platform)))
  throw Error('expected first, plan or configure|deliver|seed ios|android');

const env = process.env;
const { EXACT, EXACT_REF, GITHUB_REPOSITORY, GITHUB_SHA, CONVEX_ORIGIN } = env;
if ((!probing && !EXACT) || !/^[a-f0-9]{40}$/.test(EXACT_REF) || !GITHUB_REPOSITORY ||
    !/^[a-f0-9]{40}$/.test(GITHUB_SHA) || !/^wss:\/\/[^/]+$/.test(CONVEX_ORIGIN))
  throw Error('Missing pinned SDK, commit, repository or Convex deployment origin');

const manifest = JSON.parse(readFileSync('app.json', 'utf8'));
if (!/^\d+(?:\.\d+){1,2}$/.test(manifest.version)) throw Error('Set a numeric native version in app.json');
env.EXACT_VERSION = manifest.version;
const secret = Buffer.from(env.EXACT_UPDATE_SEED || '', 'base64');
delete env.EXACT_UPDATE_SEED;
if (secret.length !== 32) throw Error('Set EXACT_UPDATE_SEED to a base64-encoded 32-byte key for this environment');

// A different native source tree needs its own baseline even when Exact computes
// the same runtime ABI. Bind it to the signed key so old installs keep their feed.
const iconPaths = new Set(manifest.icons?.map(icon => icon.src));
const nativeFiles = execFileSync('git', ['ls-tree', '-rz', 'HEAD', '--', '.']).toString().split('\0')
  .filter(row => {
    if (!row) return false;
    const path = row.split('\t')[1];
    if (iconPaths.has(path)) return true;
    return !/^(?:assets|deck)\//.test(path) && !/\.(?:ts|contract|md)$/.test(path);
  });
function updateKey(platform) {
  const generation = createHash('sha256').update(JSON.stringify([
    GITHUB_REPOSITORY, 'production', platform, CONVEX_ORIGIN,
    EXACT_REF, manifest.version, nativeFiles,
  ])).digest();
  const keyBytes = Buffer.from(hkdfSync('sha256', secret, generation, 'exact-updates-v1', 32));
  const privateKey = createPrivateKey({
    key: Buffer.concat([Buffer.from('302e020100300506032b657004220420', 'hex'), keyBytes]),
    type: 'pkcs8', format: 'der',
  });
  keyBytes.fill(0);
  const publicKey = createPublicKey(privateKey).export({ format: 'der', type: 'spki' }).subarray(-32).toString('base64');
  return { privateKey, publicKey };
}

if (probing) {
  // This read-only hint starts the three first builds in parallel. All other
  // replies go through Exact's full compatibility and signature checks.
  const empty = await Promise.all(['android', 'ios'].map(async target => {
    const { publicKey } = updateKey(target);
    const keyId = createHash('sha256').update(Buffer.from(publicKey, 'base64')).digest('hex').slice(0, 32);
    try {
      const response = await fetch(`https://appdrop.app/u/${keyId}/.exact/`, {
        redirect: 'error', cache: 'no-store', signal: AbortSignal.timeout(5000),
      });
      if (!response.ok) return false;
      const { names } = await response.json();
      return Array.isArray(names) && names.length === 0;
    } catch { return false; }
  }));
  secret.fill(0);
  appendFileSync(env.GITHUB_OUTPUT, `first=${empty.every(Boolean)}\n`);
  process.exit(0);
}
const { privateKey, publicKey } = updateKey(platform);
secret.fill(0);
const [{ resolveApp, readBuilds, classifyArtifacts }, { openOrigin, appdropOrigin },
  { streams, streamCandidates, publishStream, defaultRelease, readBinaryInputFile }, { canonicalBytes }] = await Promise.all(
  ['app', 'origin', 'publisher', 'deploy-signing'].map(name => import(resolve(EXACT, 'scripts', name + '.mjs'))));
manifest.deploy = { ...manifest.deploy,
  store: { ...manifest.deploy.store, [platform]: 'A' },
  signing: { key: 'mobile', keys: { mobile: publicKey } },
};
const origin = openOrigin(appdropOrigin(manifest));
const signer = {
  keyId: 'mobile', publicKey,
  signBytes: bytes => sign(null, bytes, privateKey),
  sign: head => ({ keyId: 'mobile', ed25519: sign(null, canonicalBytes(head), privateKey).toString('base64') }),
};
origin.useSigner(signer);
let channel = env.EXACT_MOBILE_CHANNEL;
function native(reason, nextChannel) {
  if (command === 'seed') throw Error('Built binary cannot establish its update stream: ' + reason);
  appendFileSync(env.GITHUB_OUTPUT, platform + '_native=true\n' + platform + '_channel=' + nextChannel + '\n');
  console.log(platform + ' needs a binary: ' + reason + ' (' + nextChannel + ')');
}
if (command === 'deliver') {
  const names = await origin.list('.exact');
  if (!Array.isArray(names)) throw Error('AppDrop returned an invalid channel listing');
  const channels = names.filter(name => /^base-[1-9][0-9]*$/.test(name));
  channels.sort((a, b) => Number(a.slice(5)) - Number(b.slice(5)));
  channel = channels.at(-1);
  if (!channel) {
    native('first build of this native version', 'base-1');
    process.exit(0);
  }
}
if (!/^base-[1-9][0-9]*$/.test(channel || '')) throw Error('Missing native update channel');
manifest.deploy.channel = channel;
manifest.deploy.channels = { [channel]: 'appdrop' };
const configured = JSON.stringify(manifest, null, 2) + '\n';
if (command === 'seed' && env.EXACT_EARLY_RECEIPT) {
  if (readFileSync('app.json', 'utf8') !== configured) throw Error('Native build manifest changed during publication');
} else {
  writeFileSync('app.json', configured);
}
const installScope = `${origin.describe()}/${channel}`;
if (command === 'configure') {
  appendFileSync(env.GITHUB_ENV, `EXACT_INSTALL_SCOPE=${installScope}\n`);
  appendFileSync(env.GITHUB_ENV, 'EXACT_VERSION=' + manifest.version + '\n');
  process.exit(0);
}
const app = resolveApp(process.cwd());
// On first installs, stage blobs while native packaging runs. Release history and
// the signed head stay private until the provider confirms the verified installer.
if (command === 'seed' && env.EXACT_EARLY_RECEIPT) {
  const receiptFile = env.EXACT_EARLY_RECEIPT;
  const readyFile = resolve(env.RUNNER_TEMP, `install-${platform}.ready`);
  const receiptDigest = () => createHash('sha256').update(readFileSync(receiptFile)).digest('hex');
  const expectedReceipt = receiptDigest();
  const head = origin.head.bind(origin);
  const put = origin.put.bind(origin);
  const seen = new Map();
  origin.head = async stream => {
    const result = await head(stream);
    seen.set(`${stream.channel}/${stream.compatibilityId}`, result?.sha256 ?? null);
    return result;
  };
  origin.put = async (path, bytes, options) => {
    if (path.startsWith('.exact/blobs/')) return put(path, bytes, options);
    const match = /^\.exact\/([^/]+)\/([^/]+)\/releases\/[^/]+\.json$/.exec(path);
    if (!match) throw Error('Unexpected publication path during native packaging');
    while (!existsSync(readyFile)) await new Promise(done => setTimeout(done, 50));
    if (receiptDigest() !== expectedReceipt) throw Error('Native package used a newer build receipt');
    const [, channel, compatibilityId] = match;
    const current = await head({ channel, compatibilityId });
    if (!seen.has(`${channel}/${compatibilityId}`) ||
        (current?.sha256 ?? null) !== seen.get(`${channel}/${compatibilityId}`)) {
      throw Error('Update stream changed during native packaging');
    }
    return put(path, bytes, options);
  };
}
const builds = command === 'seed' ? readBuilds(app).filter(b => b.trust === 'production' && b.compat.inputs.platform === platform) : [];
if (command === 'seed' && !builds.length) throw Error('Missing production build receipt for ' + platform);
const found = await streams(origin, app, channel, { platforms: [platform], builds });
const selected = command === 'seed' ? found.filter(s => builds.some(b => b.compat.id === s.stream.compatibilityId)) : found;
const publishTools = env.EXACT_PUBLISH_TOOLS;
const tools = publishTools ? { graph: resolve(publishTools, 'exact-bake'), typescript: resolve(publishTools, 'exact-js-bake') } : null;
// The Linux publisher uses the same pinned native inputs as the builder. The SDK
// compares evidence against workspace paths; reading that evidence needs no Cargo.
const workspace = app.generated ? resolve(app.dir, '.exact/shells') : app.workspace;
// Exact spreads this object when inspecting Apple assets. Keep dir enumerable
// without reading the inherited getters that prepare the native workspace.
const candidateApp = Object.create(app, {
  dir: { value: app.dir, enumerable: true },
  workspace: { value: workspace },
});
const candidates = await streamCandidates(candidateApp, origin, selected, { ...env, EXACT_UPDATE_TRUST: 'production', EXACT_GAME_PROOF_PROFILE: 'release' }, { tools });
const compatible = [];
for (const update of candidates) {
  if (update.reason) {
    if (update.needsBinary) continue;
    throw Error(update.reason);
  }
  if (update.note) throw Error(update.note);
  if (update.superseded?.length) continue;
  const checked = classifyArtifacts(update.build, update.row.receipt, { id: signer.keyId, public: publicKey });
  if (!checked.bundle) {
    if (checked.missing.every(reason => checked.needsBinary?.includes(reason))) continue;
    throw Error(checked.missing.join('; '));
  }
  // SDK-generated shell paths differ between the Linux publisher and iOS builder.
  // The signing scope already binds the actual SDK and every tracked native app input.
  // The macOS builder patches only the final linker invocation. Compare both
  // files against the two reviewed variants before admitting a Linux publish.
  const appleBuilder = 'host/apple/build.mjs';
  let sameAppleBuilder = false;
  if (platform === 'ios' && EXACT_REF === 'a55c5581c7e1a62cdee1f09367ea2909c74c7292' &&
      update.build.pendingInputs.includes(`exact/${appleBuilder}`)) {
    const known = new Set([
      '0306f15724f34e8fd3bad29fff511ce16ae829c9625bedddc32e3a2fb684ba6a',
      'd140dc0ab3866a6572ec52c696c7724430e0ad207b1a38df225404638127c99e',
    ]);
    const signed = await readBinaryInputFile(origin, update.row.receipt);
    const recorded = signed && JSON.parse(signed.bytes).files.filter(file => file.root === 'exact' && file.path === appleBuilder);
    const local = createHash('sha256').update(readFileSync(resolve(EXACT, appleBuilder))).digest('hex');
    sameAppleBuilder = recorded?.length === 1 && known.has(recorded[0].sha256) && known.has(local);
  }
  const changed = update.build.pendingInputs.filter(path =>
    !(sameAppleBuilder && path === `exact/${appleBuilder}`) &&
    !(app.generated && /^(?:app\/\.exact\/shells|workspace)\/(?:Cargo\.(?:lock|toml)|rust-toolchain\.toml|(?:android|apple|web|linux|windows)\/(?:Cargo\.toml|build\.rs|src\/(?:main|lib)\.rs))$/.test(path)) &&
    !['exact/host/apple/Sources/ExactCore/GeneratedBatch.swift', 'exact/host/apple/Sources/ExactCore'].includes(path));
  if (changed.length) {
    console.log('Changed native inputs:', changed.join(', '));
    continue;
  }
  compatible.push(update);
}
if (!compatible.length) {
  native('the installed runtime cannot apply this change', 'base-' + (Number(channel.slice(5)) + 1));
} else {
  for (const update of compatible) {
    const result = await publishStream({ origin, ...update, app, signer,
      release: defaultRelease(GITHUB_SHA),
      snapshot: { id: GITHUB_SHA, commit: GITHUB_SHA, dirty: false }, opts: {}, log: () => {} });
    console.log(platform, result.action, 'sequence', result.seq, origin.describe(), update.row.compatibilityId);
  }
  if (command === 'deliver') {
    appendFileSync(env.GITHUB_OUTPUT, platform + '_native=false\n');
    // Reuse the installer only after Exact admits this update for the same stream.
    let saved;
    try { saved = JSON.parse(readFileSync(resolve(env.RUNNER_TEMP, `installer-${platform}.json`), 'utf8')); }
    catch { /* Older deployments may have no saved installer. */ }
    const publicApk = platform === 'android' && env.PUBLIC_REPO === 'true' &&
      typeof saved?.url === 'string' && saved.url.startsWith(`https://github.com/${GITHUB_REPOSITORY}/releases/download/`) &&
      /^exact-android-[1-9][0-9]*-[1-9][0-9]*\/android-production\.apk$/.test(saved.url.slice(`https://github.com/${GITHUB_REPOSITORY}/releases/download/`.length));
    if (saved?.scope === installScope && (publicApk || /^https:\/\/(?:www\.)?appdrop\.app\/d\/[A-Za-z0-9]+\/?$/.test(saved.url))) {
      const label = platform === 'ios' ? 'iPhone' : 'Android';
      appendFileSync(env.GITHUB_STEP_SUMMARY, `[Install on ${label}](${saved.url})\n`);
    }
  }
}
