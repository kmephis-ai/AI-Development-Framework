import fs from 'node:fs';
const phase = process.argv[2] || 'unknown';
const html = fs.readFileSync('index.html', 'utf8');
const pkg = JSON.parse(fs.readFileSync('package.json', 'utf8'));
if (!html.includes('data-adwf-reference="ready"')) process.exit(2);
if (!pkg.dependencies || !Object.prototype.hasOwnProperty.call(pkg.dependencies, 'react')) process.exit(3);
if (!['lint', 'test', 'build'].includes(phase)) process.exit(4);
console.log(`WEBREF ${phase} PASS`);
