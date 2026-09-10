import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../web/review/app.js', import.meta.url), 'utf8');
const state = {archiveSearchLoads: new Map(), archiveSearchIndex: null, archiveSearchIndexPath: ''};
const requests = [];
let finish;
const context = vm.createContext({state, fetchJson: path => {
  requests.push(path);
  return new Promise(resolve => { finish = resolve; });
}});
for (const name of ['loadArchiveSearchIndex', 'ensureArchiveSearchIndex']) {
  vm.runInContext(source.match(new RegExp(`(?:async )?function ${name}\\([\\s\\S]*?\n}`))[0], context);
}
const background = context.ensureArchiveSearchIndex('index');
const foreground = context.ensureArchiveSearchIndex('index');
assert.equal(background, foreground);
assert.deepEqual(requests, ['index']);
finish({documents: [{doc_id: 'one'}]});
const index = await foreground;
assert.equal(await context.ensureArchiveSearchIndex('index'), index);
assert.equal(requests.length, 1);
assert.equal(state.archiveSearchLoads.size, 0);

context.fetchJson = async () => { throw new Error('offline'); };
await assert.rejects(context.ensureArchiveSearchIndex('retry'), /offline/);
assert.equal(state.archiveSearchLoads.size, 0);
const shardRequests = [];
context.fetchJson = async path => {
  shardRequests.push(path);
  return path === 'retry'
    ? {shards: [{path: 'one'}, {path: 'two'}]}
    : {documents: [{doc_id: path}]};
};
const retried = await context.ensureArchiveSearchIndex('retry');
assert.deepEqual(shardRequests, ['retry', 'one', 'two']);
assert.deepEqual(Array.from(retried.documents, doc => doc.doc_id), ['one', 'two']);
console.log('Search preload: shared request, cache reuse, failure retry, and silent shard loading passed.');
