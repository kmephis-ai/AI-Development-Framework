import fs from 'node:fs';
import vm from 'node:vm';

const phase = process.argv[2] || 'unknown';
if (!['lint', 'test', 'build'].includes(phase)) process.exit(2);

const manifest = JSON.parse(fs.readFileSync('edge-controller.json', 'utf8'));
const pkg = JSON.parse(fs.readFileSync('package.json', 'utf8'));
const source = fs.readFileSync(manifest.entrypoint, 'utf8');

const expectedScripts = {
  lint: 'node scripts/check.mjs lint',
  test: 'node scripts/check.mjs test',
  build: 'node scripts/check.mjs build'
};
if (manifest.schema_version !== 1 || manifest.runtime !== 'LOCAL_JS_EDGE_CONTROLLER_V1') process.exit(3);
if (manifest.entrypoint !== 'rules/controller.js' || manifest.network !== 'NONE' || manifest.deployment !== 'NONE') process.exit(4);
if (JSON.stringify(pkg.scripts || {}) !== JSON.stringify(expectedScripts)) process.exit(5);
if ((pkg.dependencies && Object.keys(pkg.dependencies).length) || (pkg.devDependencies && Object.keys(pkg.devDependencies).length)) process.exit(6);
const packageText = JSON.stringify(pkg).toLowerCase();
if (/\b(ssh|scp|rsync|curl|wget|npx|deploy)\b|https?:\/\//i.test(packageText)) process.exit(7);
if (/\b(require\s*\(\s*['"](?:http|https|net|dgram|child_process)['"]|fetch\s*\(|websocket|mqtt|ssh|scp|rsync)\b/i.test(source)) process.exit(8);
if (!source.includes('function evaluateEdgeState')) process.exit(9);

if (phase === 'test') {
  const fixture = JSON.parse(fs.readFileSync('fixtures/events.json', 'utf8'));
  if (!Array.isArray(fixture.cases) || fixture.cases.length < 2) process.exit(10);
  const context = vm.createContext({ Number, Array, Object, JSON });
  vm.runInContext(source, context, { filename: manifest.entrypoint });
  for (const item of fixture.cases) {
    context.__input = item.input;
    const actual = vm.runInContext('evaluateEdgeState(__input)', context);
    if (JSON.stringify(actual) !== JSON.stringify(item.expected)) process.exit(11);
  }
}

console.log(`EDGEREF ${phase} PASS`);
