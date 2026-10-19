export function eligibleUnits(doc) {
  return (doc.units || []).filter(unit => !unit.skipped && !unit.excluded);
}

export function playbackBookmark(frame) {
  return frame ? {unitId:frame.unit.unit_id, revision:frame.unit.revision, tokenIndex:frame.start} : null;
}

export function restorePlaybackCursor(frames, bookmark) {
  if(!bookmark) return 0;
  const sameSentence=frames.findIndex(frame=>frame.unit.unit_id === bookmark.unitId);
  if(sameSentence < 0) return 0;
  if(frames[sameSentence].unit.revision !== bookmark.revision) return sameSentence;
  const exact=frames.findIndex(frame=>frame.unit.unit_id === bookmark.unitId &&
    frame.start <= bookmark.tokenIndex && bookmark.tokenIndex < frame.end);
  return exact < 0 ? sameSentence : exact;
}

export function receiptMatches(doc, record) {
  const units=eligibleUnits(doc), revisions=record.sentences || {};
  return units.length > 0 && Object.keys(revisions).length === units.length &&
    units.every(unit => revisions[unit.unit_id] === unit.revision);
}

export async function revision(unit) {
  const bytes = new TextEncoder().encode(JSON.stringify([unit.text, unit.yomi_tokens]));
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))]
    .map(n => n.toString(16).padStart(2, "0")).join("");
}

export function framesFor(unit, width) {
  const frames = [];
  let start = 0, length = 0;
  for (const [index, token] of unit.yomi_tokens.entries()) {
    const size = [...token[0]].length;
    if (length && length + size > width) {
      frames.push({start, end: index});
      start = index;
      length = 0;
    }
    length += size;
  }
  if (unit.yomi_tokens.length) frames.push({start, end: unit.yomi_tokens.length});
  return frames;
}

export function isCovered(unit, coverage) {
  const entry = coverage[unit.unit_id];
  return entry?.revision === unit.revision &&
    unit.yomi_tokens.every((_, i) => entry.seen.includes(i));
}

export function markFrame(unit, frame, coverage) {
  let entry = coverage[unit.unit_id];
  if (entry?.revision !== unit.revision) entry = {revision: unit.revision, seen: []};
  const seen = new Set(entry.seen);
  for (let i = frame.start; i < frame.end; i++) seen.add(i);
  coverage[unit.unit_id] = {...entry, seen: [...seen]};
}
