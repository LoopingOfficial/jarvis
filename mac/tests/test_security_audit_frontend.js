/* Offline behavior tests for Coding policy events: no browser/network needed. */
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../ui/js/code_env.js'), 'utf8');
const context = {
  window: {}, console, Date, Map, Set,
  document: { readyState: 'loading', addEventListener() {}, getElementById() { return null; } },
  toast() {}, J: { post() { throw Error('unexpected write request'); } },
};
// Expose the closure only in this test VM, without changing application exports.
const end = source.lastIndexOf('})();');
const seam = 'window.auditTest = {models: _models, setEditor(e) {_editor=e;}, save: _saveCurrent, force: _forceWrite};';
vm.runInNewContext(source.slice(0, end) + seam + source.slice(end), context);
const { CodeEnv, DocumentStore, auditTest } = context.window;
CodeEnv.renderFiles = () => {};
const logs = [];
CodeEnv._log = (...args) => logs.push(args);
const updates = [];
auditTest.setEditor({ updateOptions(o) { updates.push(o); } });
const doc = { id: 'doc1', content: 'original source', dirty: true, read_only: false };
DocumentStore.add(doc);
DocumentStore.open('doc1');
const entry = { readOnly: false };
auditTest.models.set('doc1', entry);
CodeEnv._onEvent('code.document.policy', {document_id:'doc1', read_only:true});
assert.equal(doc.read_only, true);
assert.equal(entry.readOnly, true);
assert.equal(updates.at(-1).readOnly, true);
assert.equal(updates.at(-1).domReadOnly, true);
assert.equal(doc.content, 'original source');
assert.equal(doc.dirty, true);
(async () => {
  await auditTest.save(); // would throw on network access
  assert.equal(await auditTest.force(entry, 'report text'), false);
  CodeEnv._onEvent('security.analysis.progress',
    {task_id:'job1', label:'Analyse 2/8', file:'marketplace.php', size:153522});
  assert.match(logs.at(-1)[1], /Analyse 2\/8/);
  CodeEnv._onEvent('code.document.policy', {document_id:'doc1', read_only:false});
  assert.equal(doc.read_only, false);
  assert.equal(entry.readOnly, false);
  assert.equal(updates.at(-1).readOnly, false);
  assert.equal(doc.content, 'original source');
  console.log('Coding audit policy: lock, save refusal, progress, unlock, content preservation OK');
})().catch(err => { console.error(err); process.exitCode = 1; });
