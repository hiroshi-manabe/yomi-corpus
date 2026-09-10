import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../web/review/app.js', import.meta.url), 'utf8');
function item(id, surfaces, prefix = '', doc = 'doc') {
  let offset = [...prefix].length;
  return {item_id: id, doc_id: doc, text: prefix + surfaces.join(''), targets: surfaces.map((surface, i) => {
    const start = offset;
    offset += [...surface].length;
    return {item_id: `${id}:${i}`, surface, target_start: start, target_end: offset};
  })};
}
const split = item('source', ['薩', '摩']);
const same = item('same', ['薩', '摩'], '旧');
const whole = item('whole', ['薩摩']);
const otherDoc = item('other', ['薩', '摩'], '', 'other');
const state = {currentPack: {items: [split, same, whole, otherDoc]}, currentDraft: {overrides: {}},
  repeatCancellation: {targetIds: new Set(split.targets.map(t => t.item_id))}};
const context = vm.createContext({state, reviewActionTargets: i => i.targets,
  itemReviewStage: () => 'yomi_final_review', candidateForId: () => ({}),
  selectedCandidate: () => ({}), isUnresolvedNoRubyCandidate: () => false});
for (const name of ['findRepeatedCancellationMatches', 'targetMatchesCancellationSpec', 'mergeOperationFitsItem']) {
  vm.runInContext(source.match(new RegExp(`function ${name}\\([\\s\\S]*?\n}`))[0], context);
}
function matches(origin) {
  const base = origin.targets[0].target_start;
  state.repeatCancellation.targetIds = new Set(origin.targets.map(t => t.item_id));
  return Array.from(context.findRepeatedCancellationMatches(origin, {surface: '薩摩', mergeOps: [],
    targetSpecs: origin.targets.map(t => ({surface: t.surface, offsetStart: t.target_start - base, offsetEnd: t.target_end - base}))}), m => m.item.item_id);
}
assert.deepEqual(matches(split), ['same']);
assert.deepEqual(matches(whole), []);
console.log('Repeated cancellation only matches identical token boundaries, within the same document.');
