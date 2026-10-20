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

export function frameDelay(frame, baseMs, rubyCharacterMs) {
  const rubyCharacters = (frame?.unit.ruby_tokens || []).slice(frame?.start, frame?.end)
    .flatMap(token=>token.nodes || [])
    .filter(node=>node.type === "ruby")
    .reduce((count,node)=>count + [...node.reading].length,0);
  return baseMs + rubyCharacters * rubyCharacterMs;
}

export function isCovered(unit, coverage) {
  const entry = coverage[unit.unit_id];
  const count = unit.tokenCount ?? unit.yomi_tokens.length;
  const seen = new Set(entry?.seen || []);
  return entry?.revision === unit.revision &&
    Array.from({length:count},(_,i)=>i).every(i=>seen.has(i));
}

export function coverageSummary(doc) {
  return {doc_id:doc.doc_id, track_doc_seq:doc.track_doc_seq,
    units:eligibleUnits(doc).map(unit=>({unit_id:unit.unit_id, revision:unit.revision,
      tokenCount:unit.yomi_tokens.length}))};
}

export function completedDocumentCount(catalog, coverage) {
  return completedDocuments(catalog,coverage).length;
}

export function completedDocuments(catalog, coverage) {
  return Object.values(catalog).filter(doc=>doc.units.length && doc.units.every(unit=>isCovered(unit,coverage)))
    .sort((a,b)=>a.track_doc_seq-b.track_doc_seq);
}

export function markFrame(unit, frame, coverage) {
  let entry = coverage[unit.unit_id];
  if (entry?.revision !== unit.revision) entry = {revision: unit.revision, seen: []};
  const seen = new Set(entry.seen);
  for (let i = frame.start; i < frame.end; i++) seen.add(i);
  coverage[unit.unit_id] = {...entry, seen: [...seen]};
}
