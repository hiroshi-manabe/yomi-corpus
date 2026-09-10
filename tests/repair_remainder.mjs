import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync(new URL('../web/review/app.js', import.meta.url), 'utf8');
const context = vm.createContext({});
for (const name of ['katakanaToHiragana', 'inferStrongRepairRemainder', 'strongRepairPreviousSegmentsAtSamePosition']) {
  const match = source.match(new RegExp(`function ${name}\\([\\s\\S]*?\n}`));
  assert.ok(match, name);
  vm.runInContext(match[0], context);
}
const infer = (c, w) => JSON.parse(JSON.stringify(context.inferStrongRepairRemainder(c, w)));
assert.deepEqual(infer([['にしお'], []], ['ニシオミコ']), ['にしお', 'みこ']);
assert.deepEqual(infer([[], ['みこ']], ['にしおみこ']), ['にしお', 'みこ']);
assert.deepEqual(infer([['しりつ'], [], ['ちゅうがっこう']], ['しりつじょうほくちゅうがっこう']), ['しりつ', 'じょうほく', 'ちゅうがっこう']);
assert.equal(infer([[], []], ['にしおみこ']), null);
assert.deepEqual(infer([['にし', 'にしお'], []], ['にしおみこ']), ['にし', 'おみこ']);
assert.deepEqual(infer([['にしお'], []], ['にしおみこ', 'にしおみれん']), ['にしお', 'みこ']);
assert.equal(infer([['やまだ'], []], ['にしおみこ']), null);
assert.equal(infer([['にしお'], []], ['にしお']), null);
assert.equal(infer([['にしお'], []], ['にしおABC']), null);
assert.equal(infer([['にしお'], ['']], ['にしおみこ']), null);
assert.deepEqual(infer([['ふたじゅうはち'], ['ばん']], ['にじゅうはちばん']), ['にじゅうはち', 'ばん']);
assert.deepEqual(infer([['にしお'], ['びれん']], ['にしおみこ']), ['にしお', 'みこ']);
assert.deepEqual(infer([['にし', 'にしお'], ['みこ']], ['にしおみこ']), ['にし', 'おみこ']);
assert.equal(context.inferStrongRepairRemainder([['やまだ'], ['みこ']], ['にしおみこ'], [true, false]), null);
const previous = [{surface: '甲', reading: 'こう', edited: true}, {surface: '甲乙', reading: 'こうおつ'}];
const aligned = context.strongRepairPreviousSegmentsAtSamePosition(['甲', '甲', '乙'], previous);
assert.equal(aligned[0], previous[0]);
assert.equal(aligned[1], null);
console.log('Repair remainder inference: prefix, suffix, middle, ambiguity, empty/manual, and positional matching passed.');
context.strongRepairKnownWholeReadings = region => region.whole || [];
context.strongRepairReadingCycleCandidates = (region, surface) => region.reading_candidates?.[surface] || [];
context.defaultStrongRepairReadingForSegment = () => '';
for (const name of ['matchStrongRepairSegmentReadings', 'defaultStrongRepairReadingsForSegments']) {
  vm.runInContext(source.match(new RegExp(`function ${name}\\([\\s\\S]*?\n}`))[0], context);
}
const defaults = (r, s, p) => Array.from(context.defaultStrongRepairReadingsForSegments(r, s, p));
assert.deepEqual(defaults({reading_candidates: {'西尾': ['にしお']}}, ['西尾', '美恋'],
  [{surface: '西尾美恋', reading: 'にしおみこ'}]), ['にしお', 'みこ']);
assert.deepEqual(defaults({whole: ['こうおつへい'], reading_candidates: {'甲': ['かん'], '丙': ['へい']}},
  ['甲', '乙', '丙'], [{surface: '甲', reading: 'こう', edited: true}, {surface: '乙丙', reading: 'おつへい'}]),
  ['こう', 'おつ', 'へい']);
assert.deepEqual(defaults({whole: ['こうおつ'], reading_candidates: {'甲': ['こう']}},
  ['甲', '乙'], [{surface: '甲', reading: '', edited: true}, {surface: '乙', reading: ''}]), ['', '']);
console.log('Default-reading integration and preservation of manual readings passed.');
assert.deepEqual(defaults({reading_candidates: {'二十八': ['ふたじゅうはち'], '番': ['ばん']}},
  ['二十八', '番'], [{surface: '二十八番', reading: 'にじゅうはちばん'}]), ['にじゅうはち', 'ばん']);
// A full candidate match wins over the first prefix's inferred remainder.
assert.deepEqual(defaults({reading_candidates: {'西尾': ['にし', 'にしお'], '美恋': ['みこ']}},
  ['西尾', '美恋'], [{surface: '西尾美恋', reading: 'にしおみこ'}]), ['にしお', 'みこ']);
// The current full reading takes precedence over an older proposal.
assert.deepEqual(defaults({whole: ['にしおみれん'], reading_candidates: {'西尾': ['にしお'], '美恋': ['みれん']}},
  ['西尾', '美恋'], [{surface: '西尾美恋', reading: 'にしおみこ'}]), ['にしお', 'みこ']);
