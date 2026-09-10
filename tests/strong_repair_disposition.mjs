import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync(new URL('../web/review/app.js', import.meta.url), 'utf8');
const state = {currentDraft: {overrides: {}}, currentPack: {items: [{item_id: 'u'}]}};
const context = vm.createContext({state,
  isItemIncludedInSubmission: () => true,
  itemReviewStage: () => 'yomi_strong_repair_review',
  originalItemId: item => item.item_id,
});
for (const name of ['ensureStrongRepairOverride', 'cleanupStrongRepairOverride', 'getActiveStrongRepairOverrides']) {
  vm.runInContext(source.match(new RegExp(`function ${name}\\([\\s\\S]*?\n}`))[0], context);
}
for (const disposition of ['Skip', 'Exclude', 'Keep']) {
  context.ensureStrongRepairOverride('u').disposition = disposition;
  context.ensureStrongRepairOverride('u').manual_correction_required = true;
  context.cleanupStrongRepairOverride('u');
  assert.equal(state.currentDraft.overrides.u.disposition, disposition);
  assert.equal(context.getActiveStrongRepairOverrides()[0].disposition, disposition);
}
console.log('Escalated disposition survives other edits, cleanup, and submission.');
