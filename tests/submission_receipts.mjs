import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { webcrypto } from 'node:crypto';

const source = fs.readFileSync(new URL('../web/review/app.js', import.meta.url), 'utf8');
const storage = new Map();
const context = vm.createContext({
  crypto: webcrypto,
  window: {localStorage: {
    getItem: key => storage.get(key) ?? null,
    setItem: (key, value) => storage.set(key, value),
  }},
  state: {issueAcknowledgments: {records: []}},
  cloneJson: value => JSON.parse(JSON.stringify(value)),
  localTaskRecordStage: record => record.queue_stage,
  taskDocIdsForStorageTask: task => task.doc_ids || [],
  baseDocIdFromTaskDocId: id => id,
  taskDocKey: doc => doc.doc_id,
  submittedTaskDocIds: () => new Set(),
  taskRecordStatus: record => record.status,
});
vm.runInContext('const submissionReceiptPrefix = "test:";', context);
for (const name of ['submissionReceiptKey', 'readSubmissionReceipt', 'saveTaskSubmissionReceipts',
  'rememberServerSubmissionReceipts', 'docIsSubmittedLocally', 'issueAcknowledgmentsForDoc',
  'consolidateSubmittedTaskRecords', 'persistentTaskIdentity', 'allocateTaskIdentity', 'taskNumberFromId']) {
  const match = source.match(new RegExp(`function ${name}\\([\\s\\S]*?\n}`));
  assert.ok(match, name);
  vm.runInContext(match[0], context);
}
const record = {queue_stage: 'bulk', task: {doc_ids: ['doc1']}, task_id: 'task1'};
const doc = {doc_id: 'doc1', queue_stage: 'bulk'};
context.saveTaskSubmissionReceipts(record);
assert.equal(context.docIsSubmittedLocally(doc), true);
// Other task submissions and absent task drafts cannot undo the receipt.
context.saveTaskSubmissionReceipts({...record, task: {doc_ids: ['doc2']}});
assert.equal(context.docIsSubmittedLocally(doc), true);
assert.equal(context.docIsSubmittedLocally({...doc, queue_stage: 'strong'}), false);
context.saveTaskSubmissionReceipts(record, {reopen: true});
context.saveTaskSubmissionReceipts(record, {migrate: true});
assert.equal(context.docIsSubmittedLocally(doc), false);
context.saveTaskSubmissionReceipts(record);
assert.equal(context.docIsSubmittedLocally(doc), true);
const ack = {review_stage: 'bulk', doc_ids: ['doc1'], submission_id: 's1'};
context.rememberServerSubmissionReceipts({records: [ack]});
context.rememberServerSubmissionReceipts({records: []});
assert.equal(context.issueAcknowledgmentsForDoc(doc).length, 1);
assert.equal(context.issueAcknowledgmentsForDoc({...doc, queue_stage: 'strong'}).length, 0);
storage.clear();
context.rememberServerSubmissionReceipts({receipt_history: [ack], records: []});
assert.equal(context.issueAcknowledgmentsForDoc(doc).length, 1);
console.log('Submission receipts: persistence, other tasks, reopen, stage isolation, stale responses, new-device history passed.');
const submitted = {status: 'submitted', queue_stage: 'bulk', submitted_at_epoch: 100,
  task_label: 'Task 1', task: {doc_ids: ['doc1', 'doc2']}, overrides: {a: 1}};
const duplicates = {original: structuredClone(submitted),
  fragment: {...structuredClone(submitted), task: {doc_ids: ['doc2']}, overrides: {b: 2}},
  other: {...structuredClone(submitted), submitted_at_epoch: 200}};
context.consolidateSubmittedTaskRecords(duplicates);
assert.deepEqual(Object.keys(duplicates), ['original', 'other']);
assert.deepEqual([...duplicates.original.task.doc_ids], ['doc1', 'doc2']);
assert.equal(duplicates.original.overrides.a, 1);
assert.equal(duplicates.original.overrides.b, 2);
context.consolidateSubmittedTaskRecords(duplicates);
assert.equal(Object.keys(duplicates).length, 2);
console.log('Duplicate task fragments consolidate idempotently without losing edits or merging separate submissions.');
const old1 = {task_id: 'task_1', task_label: 'Task 1', submitted_at_epoch: 100, queue_stage: 'bulk'};
const old2 = {...old1, submitted_at_epoch: 200};
const id1 = context.persistentTaskIdentity(old1, 'bulk');
const id2 = context.persistentTaskIdentity(old2, 'bulk');
assert.notEqual(id1.task_id, id2.task_id);
assert.notEqual(id1.task_number, id2.task_number);
assert.equal(context.persistentTaskIdentity(old1, 'bulk').task_id, id1.task_id);
assert.equal(context.persistentTaskIdentity({...old1, task_id:'task_1_2'}, 'bulk').task_id, id1.task_id);
const fresh = context.allocateTaskIdentity();
assert.ok(fresh.task_number > id2.task_number);
assert.equal(context.taskNumberFromId(fresh.task_id), fresh.task_number);
assert.equal(context.persistentTaskIdentity({...old1,...id1}, 'bulk').task_number, id1.task_number);
console.log('Task IDs and browser-wide numbering: pack collisions, fragment aliases, migration stability, and allocation passed.');
