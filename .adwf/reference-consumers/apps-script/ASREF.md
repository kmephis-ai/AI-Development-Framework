# Reference Apps Script Consumer

Minimal consumer-owned Apps Script/data-centric product used only by `ASREF-001` conformance.

`appsscript.json` is the Apps Script Project Pack detection marker. `package.json` is intentionally present so detection must choose `apps-script` before the generic `node` pack.

The mandatory correctness path is local-only: no `clasp`, OAuth, Apps Script API, Drive API, Google deployment or package installation. `scripts/check.mjs` executes the consumer-owned `.gs` functions inside Node `vm` with a narrow fake `SpreadsheetApp` boundary for deterministic adapter testing. This is not a claim that Google Apps Script runtime was executed.
