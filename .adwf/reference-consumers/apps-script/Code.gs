function summarizeOperations_(rows) {
  return rows.slice(1).reduce(function (summary, row) {
    var kind = String(row[0] || '');
    var amount = Number(row[1] || 0);
    if (kind === 'income') summary.income += amount;
    if (kind === 'expense') summary.expense += amount;
    return summary;
  }, { income: 0, expense: 0 });
}

function buildSummaryFromActiveSpreadsheet() {
  var spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  var sheet = spreadsheet.getSheetByName('Operations');
  if (!sheet) throw new Error('OPERATIONS_SHEET_REQUIRED');
  return summarizeOperations_(sheet.getDataRange().getValues());
}
