export function eligibleUnits(doc) {
  return (doc.units || []).filter(unit => !unit.skipped && !unit.excluded);
}

export function playbackBookmark(frame) {
  return frame ? {unitId:frame.unit.unit_id, revision:frame.unit.revision, tokenIndex:frame.start,
    offset:frameOffsets(frame.unit,frame).start} : null;
}

export function restorePlaybackCursor(frames, bookmark) {
  if(!bookmark) return 0;
  const sameSentence=frames.findIndex(frame=>frame.unit.unit_id === bookmark.unitId);
  if(sameSentence < 0) return 0;
  if(Number.isInteger(bookmark.offset)) {
    const index=frames.findIndex(frame=>frame.unit.unit_id === bookmark.unitId &&
      frameOffsets(frame.unit,frame).start <= bookmark.offset && bookmark.offset < frameOffsets(frame.unit,frame).end);
    return index < 0 ? sameSentence : index;
  }
  if(frames[sameSentence].unit.revision !== bookmark.revision) return sameSentence;
  const exact=frames.findIndex(frame=>frame.unit.unit_id === bookmark.unitId &&
    frame.start <= bookmark.tokenIndex && bookmark.tokenIndex < frame.end);
  return exact < 0 ? sameSentence : exact;
}

export function receiptMatches(doc, record) {
  const units=eligibleUnits(doc), revisions=record.sentences || {};
  return units.length > 0 && units.every(unit => Object.hasOwn(revisions,unit.unit_id));
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
  if(!entry) return false;
  if(entry.complete) return true;
  if(entry.schema === 2) {
    const length=unit.textLength ?? unit.text.length;
    const seen=new Set(entry.seen);
    return Array.from({length},(_,i)=>i).every(i=>seen.has(i));
  }
  const count = unit.tokenCount ?? unit.yomi_tokens.length;
  const seen = new Set(entry?.seen || []);
  return seen.size === count &&
    Array.from({length:count},(_,i)=>i).every(i=>seen.has(i));
}

export function migrateCoverage(unit, coverage) {
  const entry=coverage[unit.unit_id];
  if(!entry || entry.schema === 2) return;
  if(isCovered(unit,coverage)) {
    coverage[unit.unit_id]={schema:2,complete:true,seen:[]};
  } else if(unit.yomi_tokens && entry.revision === unit.revision) {
    const seen=[]; let offset=0;
    for(const [index,[surface]] of unit.yomi_tokens.entries()) {
      if(entry.seen.includes(index)) for(let i=0;i<surface.length;i++) seen.push(offset+i);
      offset+=surface.length;
    }
    coverage[unit.unit_id]={schema:2,seen};
  }
}

function frameOffsets(unit, frame) {
  return {start:unit.yomi_tokens.slice(0,frame.start).reduce((n,t)=>n+t[0].length,0),
    end:unit.yomi_tokens.slice(0,frame.end).reduce((n,t)=>n+t[0].length,0)};
}

export function isFrameCovered(unit, frame, coverage) {
  if(isCovered(unit,coverage)) return true;
  const entry=coverage[unit.unit_id];
  if(!entry) return false;
  if(entry.schema !== 2) return entry.revision === unit.revision &&
    Array.from({length:frame.end-frame.start},(_,i)=>i+frame.start).every(i=>entry.seen.includes(i));
  const {start,end}=frameOffsets(unit,frame), seen=new Set(entry.seen);
  return Array.from({length:end-start},(_,i)=>i+start).every(i=>seen.has(i));
}

export function coverageSummary(doc) {
  return {doc_id:doc.doc_id, track_doc_seq:doc.track_doc_seq,
    units:eligibleUnits(doc).map(unit=>({unit_id:unit.unit_id, revision:unit.revision, textLength:unit.text.length,
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
  migrateCoverage(unit,coverage);
  const previous=coverage[unit.unit_id];
  let entry = previous?.schema === 2 ? previous : {schema:2,seen:[], ...(previous ? {legacy:previous} : {})};
  if(entry.complete) return;
  const seen = new Set(entry.seen);
  const {start,end}=frameOffsets(unit,frame);
  for (let i = start; i < end; i++) seen.add(i);
  coverage[unit.unit_id] = {...entry, complete:seen.size === unit.text.length, seen: [...seen]};
}
