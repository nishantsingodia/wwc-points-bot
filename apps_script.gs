/**
 * WWC T20 Points — in-sheet "Refresh" button.
 * Paste this into the Sheet's Apps Script (Extensions ▸ Apps Script), then set
 * Script Properties (Project Settings ▸ Script Properties):
 *   GH_OWNER    = your GitHub username
 *   GH_REPO     = wwc-points-bot
 *   GH_PAT      = a GitHub fine-grained PAT with "Actions: Read and write" on the repo
 *   GH_WORKFLOW = wwc-points.yml   (optional; this is the default)
 * Save, reload the sheet, and use the "🏏 WWC" menu.
 */
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('🏏 WWC')
    .addItem('Refresh points now', 'refreshNow')
    .addToUi();
}

function refreshNow() {
  const p = PropertiesService.getScriptProperties();
  const owner = p.getProperty('GH_OWNER');
  const repo = p.getProperty('GH_REPO');
  const pat = p.getProperty('GH_PAT');
  const wf = p.getProperty('GH_WORKFLOW') || 'wwc-points.yml';
  const ui = SpreadsheetApp.getUi();

  if (!owner || !repo || !pat) {
    ui.alert('Set GH_OWNER, GH_REPO and GH_PAT in Script Properties first.');
    return;
  }

  const url = 'https://api.github.com/repos/' + owner + '/' + repo +
              '/actions/workflows/' + wf + '/dispatches';
  const res = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    headers: { Authorization: 'Bearer ' + pat, Accept: 'application/vnd.github+json' },
    payload: JSON.stringify({ ref: 'main' }),
    muteHttpExceptions: true,
  });

  const code = res.getResponseCode();
  if (code === 204) {
    ui.alert('✅ Refresh triggered. The sheet updates in ~1–2 minutes (watch the GitHub Actions run).');
  } else {
    ui.alert('⚠️ Trigger failed (HTTP ' + code + '):\n' + res.getContentText());
  }
}
