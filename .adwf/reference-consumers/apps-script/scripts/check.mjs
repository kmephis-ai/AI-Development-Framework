import fs from 'node:fs';
import vm from 'node:vm';

const phase = process.argv[2] || 'unknown';
if (!['lint', 'test', 'build'].includes(phase)) process.exit(2);

const manifest = JSON.parse(fs.readFileSync('appsscript.json', 'utf8'));
const pkg = JSON.parse(fs.readFileSync('package.json', 'utf8'));
const source = fs.readFileSync('Code.gs', 'utf8');
if (manifest.runtimeVersion !== 'V8') process.exit(3);
if (!pkg.scripts || !pkg.scripts.lint || !pkg.scripts.test || !pkg.scripts.build) process.exit(4);
if (!source.includes('SpreadsheetApp.getActiveSpreadsheet()')) process.exit(5);
if (/\b(clasp|googleapis|gcloud|curl|wget|npx)\b|https?:\/\//i.test(JSON.stringify(pkg))) process.exit(6);

if (phase === 'test') {
  const fixture = JSON.parse(fs.readFileSync('fixtures/operations.json', 'utf8'));
  const spreadsheet = {
    getSheetByName(name) {
      if (name !== fixture.sheet) return null;
      return { getDataRange() { return { getValues() { return fixture.rows; } }; } };
    }
  };
  const context = vm.createContext({
    SpreadsheetApp: { getActiveSpreadsheet() { return spreadsheet; } },
    Error,
    Number,
    String
  });
  vm.runInContext(source, context, { filename: 'Code.gs' });
  const actual = vm.runInContext('buildSummaryFromActiveSpreadsheet()', context);
  if (Number(actual.income) !== fixture.expected.income || Number(actual.expense) !== fixture.expected.expense) process.exit(7);
}

console.log(`ASREF ${phase} PASS`);
