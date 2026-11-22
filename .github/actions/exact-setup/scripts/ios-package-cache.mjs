// Build the public SDK's package modules once. Exact computes the same cache
// keys during each app build and selects modules using that app's own receipt.
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, readdirSync, realpathSync, writeFileSync } from 'node:fs';
import { basename, dirname, resolve } from 'node:path';

const { EXACT: sdk, CARGO_TARGET_DIR: target, RUNNER_TEMP: temp } = process.env;
const appDir = process.cwd();
const manifest = JSON.parse(readFileSync('app.json', 'utf8'));
if (manifest.id !== 'dev.disposabl.publicseed' || manifest.deploy?.signing?.keys?.mobile !== 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=') {
  throw new Error('The iPhone package publisher requires the public SDK fixture');
}

const sdkPackages = resolve(sdk, 'packages');
const names = readdirSync(sdkPackages).filter(name => {
  const path = resolve(sdkPackages, name, 'apple/exact.json');
  return existsSync(path) && JSON.parse(readFileSync(path, 'utf8')).targets.includes('ios');
});
if (!names.length) throw new Error('Pinned SDK declares no iOS packages');
const dependencies = JSON.parse(readFileSync('package.json', 'utf8'));
for (const name of names) dependencies.dependencies['@exact/' + name] = 'file:.exact/checkout/packages/' + name;
writeFileSync('package.json', JSON.stringify(dependencies));
execFileSync('bun', ['install'], { stdio: 'inherit' });

const { appleImplementations } = await import(resolve(sdk, 'js/bake/src/typescript.mjs'));
const { packageCompiler } = await import(resolve(sdk, 'host/apple/compose.mjs'));
const sdkName = 'iphoneos';
const xcrun = (...args) => execFileSync('xcrun', args, { encoding: 'utf8' });
const swiftc = xcrun('--sdk', sdkName, 'swiftc', '--version');
const sdkPath = xcrun('--sdk', sdkName, '--show-sdk-path').trim();
const minimum = manifest.host.ios.minimumOS;
const packagesRoot = resolve(target, 'apple-swift/ios-' + minimum + '-balanced');
const app = { dir: appDir, manifest };
const extensions = Object.fromEntries(names.map(name => [name, '@exact/' + name]));
const packages = appleImplementations(appDir, 'ios', extensions);
if (packages.length !== names.length) throw new Error('Not every iOS implementation resolved');
for (const pkg of packages) {
  if (pkg.activity === 'alarmkit') pkg.sources.push(resolve(sdk, 'host/apple/widget/AlarmAttributes.swift'));
  const packageDir = resolve(temp, 'catalog-build', pkg.module, 'packages');
  const copies = pkg.sources.map((source, index) => {
    const file = resolve(packageDir, 'src-' + pkg.module, index + '-' + basename(source));
    mkdirSync(dirname(file), { recursive: true });
    writeFileSync(file, '#sourceLocation(file: ' + JSON.stringify(realpathSync(source)) + ', line: 1)\n' + readFileSync(source, 'utf8'));
    return file;
  });
  const triple = 'arm64-apple-ios' + minimum;
  const packageTargets = new Map([[packageDir, [sdkName, ['-target', triple, '-sdk', sdkPath]]]]);
  packageCompiler({
    packages: [pkg], packageDir, packageTargets, packageCopies: [copies], packagesRoot, swiftc,
    seam: resolve(sdk, 'host/apple/modules/ExactPackage.swift'), production: true, distribution: true,
    ios: true, tv: false, app, extensions, minimum: { ios: minimum, macos: manifest.host.macos.minimumOS },
  }).place(packageDir);
  const dylib = resolve(packageDir, pkg.module + '.dylib');
  xcrun('lipo', '-verify_arch', 'arm64', dylib);
  if (!/platform\s+(ios|2)\b/i.test(xcrun('vtool', '-show-build', dylib))) throw new Error('Invalid iOS module ' + pkg.module);
}
console.log('Precompiled ' + packages.length + ' public iPhone package modules');
