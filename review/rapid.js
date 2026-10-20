const {revision, framesFor, isCovered, markFrame, eligibleUnits, receiptMatches, playbackBookmark, restorePlaybackCursor, coverageSummary, completedDocumentCount, completedDocuments, frameDelay, migrateCoverage, isFrameCovered} =
  await import(`./rapid-core.mjs${new URL(import.meta.url).search}`);

const $ = id => document.getElementById(id);
const key = "yomi-corpus:rapid-review:v1";
let saved, index, server = {}, docs = [], frames = [], cursor = 0, timer = null, busy = false;
let positionKey="", partialMode=false;
let catalogLoading=false;
let preparing=false;
let database, dirty=false, writing=false, storageFailed=false, persistence=Promise.resolve();
const defaults = {speed:700,rubyTime:0,width:18,size:52,kanji:115,ruby:65,gothic:true,red:true};
function status(text, error = false) { if(storageFailed && !error) return; $("status").textContent = text; $("status").classList.toggle("error", error); }
function persist() {
  if(storageFailed) { pause(); return false; }
  dirty=true;
  if(!writing) {
    writing=true;
    persistence=(async () => {
      try {
        while(dirty) {
          dirty=false;
          await new Promise((resolve,reject) => {
            const tx=database.transaction("state","readwrite");
            tx.objectStore("state").put(saved,key);
            tx.oncomplete=resolve; tx.onerror=tx.onabort=()=>reject(tx.error);
          });
        }
      } catch {
        storageFailed=true; pause();
        status("記録を保存できません。ブラウザの空き容量を確認してください。",true);
      } finally { writing=false; }
    })();
  }
  return true;
}
async function openStorage() {
  database=await new Promise((resolve,reject) => {
    const request=indexedDB.open("yomi-corpus-rapid-review",1);
    request.onupgradeneeded=()=>request.result.createObjectStore("state");
    request.onsuccess=()=>resolve(request.result); request.onerror=()=>reject(request.error);
  });
  return new Promise((resolve,reject) => {
    const request=database.transaction("state").objectStore("state").get(key);
    request.onsuccess=()=>resolve(request.result);request.onerror=()=>reject(request.error);
  });
}
async function json(path) {
  const response = await fetch(path, {cache:"no-cache"});
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}
function pause() { clearTimeout(timer); timer = null; $("play").textContent = "再生"; }
function readyDocs() { return docs.filter(doc => doc.units.length && doc.units.every(u => isCovered(u, saved.coverage))); }
function refreshCoverage() {
  const complete = readyDocs();
  const count = docs.reduce((n,d) => n + d.units.filter(u => isCovered(u,saved.coverage)).length, 0);
  $("coverage").textContent = `この範囲: ${count}文を確認 · 提出可能 ${complete.length}文書`;
  $("global-coverage").textContent = `全範囲の提出可能: ${completedDocumentCount(saved.catalog || {},saved.coverage)}文書${catalogLoading ? "（以前の閲覧分を再集計中）" : ""}`;
  $("pending").textContent = `提出待ち・確認待ち: ${saved.pending.length}件${saved.pending.length ? "（確認されるまで記録を保持します）" : ""}`;
  const selected = $("pending-select").value;
  $("pending-select").replaceChildren(...saved.pending.map(s => {
    const option = document.createElement("option"); option.value=s.submission_id;
    option.textContent=`文書 ${s.documents.map(d=>d.track_doc_seq).join(",")}`; return option;
  }));
  if(saved.pending.some(s=>s.submission_id === selected)) $("pending-select").value=selected;
  $("resend").disabled = !saved.pending.length;
  $("confirm").hidden = !saved.draft;
  $("cancel-draft").hidden = !saved.draft;
  $("submit").disabled = $("download").disabled = preparing || catalogLoading ||
    (!saved.draft && !completedDocumentCount(saved.catalog || {},saved.coverage));
}
function render() {
  const frame = frames[cursor];
  $("line").replaceChildren();
  $("play").disabled = !frame; $("previous").disabled = !frame || cursor === 0; $("next").disabled = !frame || cursor === frames.length-1;
  if (!frame) { $("position").textContent = "表示対象がありません"; refreshCoverage(); return; }
  const {unit, doc} = frame;
  $("position").textContent = `文書 ${doc.track_doc_seq} · 文 ${unit.unit_seq} · コマ ${cursor+1}/${frames.length} · 通読 ${server.documents?.[doc.doc_id]?.count || 0}回`;
  for (let i = frame.start; i < frame.end; i++) {
    const token = unit.yomi_tokens[i];
    const nodes = unit.ruby_tokens?.[i]?.nodes || [{type:"text",text:token[0]}];
    for (const node of nodes) {
      const base = document.createElement("span");
      for (const char of node.text) {
        const span = document.createElement("span"); span.textContent = char;
        if (/\p{Script=Han}/u.test(char)) span.className = "han";
        base.append(span);
      }
      if (node.type === "ruby") {
        const ruby = document.createElement("ruby"), rt = document.createElement("rt");
        rt.textContent = node.reading; ruby.append(base,rt); $("line").append(ruby);
      } else $("line").append(base);
    }
  }
  fitLine();
  if (!document.hidden) markFrame(unit,frame,saved.coverage);
  saved.visitedDocuments ||= {};
  saved.visitedDocuments[doc.track_doc_seq] = true;
  saved.positions ||= {};
  if(positionKey) saved.positions[positionKey] = playbackBookmark(frame);
  persist();
  refreshCoverage();
}
function fitLine() {
  const line=$("line");
  let size=Number($("size").value);
  line.style.fontSize=`${size}px`;
  // Keep every displayed token visible, including on a zoomed tablet screen.
  for(let attempt=0;attempt<3 && line.scrollWidth>line.clientWidth;attempt++) {
    size *= line.clientWidth/line.scrollWidth;
    line.style.fontSize=`${size}px`;
  }
}
window.addEventListener("resize",fitLine);
function rebuild(partial = partialMode, bookmark = null) {
  pause();
  const old = frames[cursor];
  frames = docs.flatMap(doc => {
    const partiallyViewed = doc.units.some(u => Boolean(saved.coverage[u.unit_id]));
    if (partial && (!partiallyViewed || doc.units.every(u => isCovered(u,saved.coverage)))) return [];
    return doc.units.flatMap(unit => framesFor(unit, Number($("width").value))
      .filter(f => !partial || !isFrameCovered(unit,f,saved.coverage))
      .map(f => ({...f,doc,unit})));
  });
  cursor = restorePlaybackCursor(frames, bookmark || playbackBookmark(old));
  render();
}
async function load(partial = false) {
  pause(); if (busy) return;
  const from = Number($("from").value), to = Number($("to").value);
  if (!Number.isSafeInteger(from) || !Number.isSafeInteger(to) || from < 1 || to < from) return status("文書の開始・終了番号を確認してください。",true);
  busy = true; $("load").disabled = true; status("文書を読み込んでいます…");
  try {
    const loaded = [];
    const wanted = seq => partial ? Boolean(saved.visitedDocuments?.[seq]) : seq >= from && seq <= to;
    for (const shard of index.tracks.dev.shards.filter(s => partial
      ? Object.keys(saved.visitedDocuments || {}).some(n => Number(n) >= s.start_track_doc_seq && Number(n) <= s.end_track_doc_seq)
      : s.end_track_doc_seq >= from && s.start_track_doc_seq <= to)) {
      const payload = await json(shard.path);
      for (const doc of payload.documents.filter(d => wanted(d.track_doc_seq))) {
        doc.units = eligibleUnits(doc);
        for (const unit of doc.units) { unit.revision = await revision(unit); migrateCoverage(unit,saved.coverage); }
        saved.catalog ||= {};
        saved.catalog[doc.doc_id] = coverageSummary(doc);
        loaded.push(doc);
      }
    }
    if(saved.draft?.documents.some(record => {
      const doc=loaded.find(d=>d.doc_id === record.doc_id);
      return doc && !receiptMatches(doc,record);
    })) {
      saved.draft=null;
      alert("対象の文が増減したため、未提出のチェック記録を作り直します。閲覧済みの記録は保持します。");
    }
    docs = loaded.filter(doc=>doc.units.length).sort((a,b) => a.track_doc_seq-b.track_doc_seq);
    partialMode=partial;
    positionKey=partial ? "partial" : `range:${from}:${to}`;
    saved.range = [from,to]; saved.mode=partial ? "partial" : "range";
    const bookmark=saved.positions?.[positionKey];
    persist(); frames=[]; cursor=0; rebuild(partial,bookmark);
    status(`${docs.length}文書を読み込みました。非表示のタブでは再生・記録を停止します。`);
  } catch (e) { status(`読み込みに失敗しました: ${e.message}`,true); }
  finally { busy = false; $("load").disabled = false; }
}
function tick() {
  timer = setTimeout(() => {
    if (document.hidden || cursor >= frames.length-1) { pause(); return; }
    cursor++; render();
    if (timer !== null) tick();
  },frameDelay(frames[cursor],Number($("speed").value),Number($("rubyTime").value)));
}
function settings() {
  for (const name of Object.keys(defaults)) {
    const input = $(name);
    saved.settings[name] = input.type === "checkbox" ? input.checked : Number(input.value);
    const output = input.parentElement.querySelector("output");
    if (output) output.textContent = input.value + (name === "speed" ? " ms" : name === "rubyTime" ? " ms/字" : name === "size" ? " px" : ["kanji","ruby"].includes(name) ? "%" : "");
  }
  document.documentElement.style.setProperty("--size",`${$("size").value}px`);
  document.documentElement.style.setProperty("--kanji", Number($("kanji").value)/100);
  document.documentElement.style.setProperty("--ruby", Number($("ruby").value)/100);
  document.body.classList.toggle("gothic",$("gothic").checked);
  document.body.classList.toggle("red",$("red").checked); persist();
  fitLine();
}
async function prepare() {
  pause();
  if(preparing || catalogLoading) throw new Error("文書の確認が終わるまでお待ちください。");
  preparing=true; refreshCoverage();
  try {
    await backfillCatalog();
    const ready=completedDocuments(saved.catalog || {},saved.coverage);
    const draftIds=new Set(saved.draft?.documents.map(doc=>doc.doc_id) || []);
    const extra=ready.filter(doc=>!draftIds.has(doc.doc_id));
    let replace=false;
    if(saved.draft && extra.length) {
      replace=confirm(`保存済みの提出案に含まれない文書が${extra.length}件あります。全範囲の提出案を作り直しますか？\nまだIssueを作成していない場合だけOKを選んでください。作成済みならキャンセルし、先に「提出済みにする」を押してください。二重提出を防ぐためです。`);
      if(!replace) throw new Error("保存済みの提出案を保持しました。作成済みのIssueを先に提出済みとして記録してください。");
    }
    if(!saved.draft || replace) {
      if(!ready.length) throw new Error("提出できる文書がありません。");
      status(`全範囲の${ready.length}文書を確認しています…`);
      const currentIndex=await json("./archive/index.json"), shards=new Map(), documents=[];
      let stale=false;
      for(const summary of ready) {
        const shard=currentIndex.tracks.dev.shards.find(s=>summary.track_doc_seq>=s.start_track_doc_seq && summary.track_doc_seq<=s.end_track_doc_seq);
        if(!shard) { stale=true; delete saved.catalog[summary.doc_id]; continue; }
        if(!shards.has(shard.path)) shards.set(shard.path,await json(shard.path));
        const doc=shards.get(shard.path).documents.find(d=>d.doc_id === summary.doc_id && d.track_doc_seq === summary.track_doc_seq);
        if(!doc) { stale=true; delete saved.catalog[summary.doc_id]; continue; }
        doc.units=eligibleUnits(doc);
        for(const unit of doc.units) { unit.revision=await revision(unit); migrateCoverage(unit,saved.coverage); }
        saved.catalog[doc.doc_id]=coverageSummary(doc);
        if(!doc.units.length || !doc.units.every(u=>isCovered(u,saved.coverage))) { stale=true; continue; }
        documents.push({doc_id:doc.doc_id,track_doc_seq:doc.track_doc_seq,
          sentences:Object.fromEntries(doc.units.map(u=>[u.unit_id,true]))});
      }
      persist();
      if(stale) throw new Error("対象の文が増減しました。提出可能数を更新しました。未閲覧の文を確認し、残りの提出はもう一度操作してください。");
      saved.draft={submission_type:"rapid_review_receipt",schema_version:2,track_name:"dev",
        submission_id:`rapid_review__${crypto.randomUUID()}`,documents};
    }
    if (!persist()) throw new Error("提出データを保存できません。");
    await persistence;
    if(storageFailed) throw new Error("提出データを保存できません。");
    return JSON.stringify(saved.draft,null,2);
  } finally { preparing=false; refreshCoverage(); }
}
$("load").onclick = () => load();
$("partial").onclick = () => load(true);
$("previous").onclick = () => { pause(); if (cursor>0) cursor--; render(); };
$("restart").onclick = () => { pause(); cursor=0; render(); };
$("next").onclick = () => { pause(); if (cursor<frames.length-1) cursor++; render(); };
$("play").onclick = () => { if(timer !== null) pause(); else if(frames.length) { $("play").textContent="停止"; tick(); } };
document.addEventListener("visibilitychange",pause);
document.addEventListener("keydown",e => {
  if (/INPUT|TEXTAREA|BUTTON/.test(e.target.tagName)) return;
  if (e.code === "Space") { e.preventDefault(); $("play").click(); }
  if (e.code === "ArrowRight") $("next").click();
  if (e.code === "ArrowLeft") $("previous").click();
});
for(const name of Object.keys(defaults)) $(name).oninput = () => {
  settings();
  if(name === "width") rebuild();
  else if(timer !== null && ["speed","rubyTime"].includes(name)) { clearTimeout(timer); tick(); }
};
$("submit").onclick = async () => {
  try {
    const body = await prepare(); await navigator.clipboard.writeText(body);
    const title = `[通読チェック] 文書 ${saved.draft.documents.map(d=>d.track_doc_seq).join(",")}`;
    const issue = new URL("https://github.com/hiroshi-manabe/yomi-corpus/issues/new");
    issue.searchParams.set("title",title.length > 180 ? "[通読チェック] 複数文書" : title);
    window.open(issue,"_blank","noopener");
    status("JSONをコピーしました。Issue本文に貼り付けて作成してください。大きい場合はJSONをダウンロードして添付してください。");
  } catch(e) { status(e.message,true); }
};
$("download").onclick = async () => {
  try { const url = URL.createObjectURL(new Blob([await prepare()],{type:"application/json"}));
    const a=document.createElement("a"); a.href=url; a.download="rapid-review.json"; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
  } catch(e) { status(e.message,true); }
};
$("confirm").onclick = () => {
  if (!saved.draft) return;
  saved.pending.push(saved.draft);
  clearSubmittedCoverage(saved.draft);
  saved.draft=null; persist(); refreshCoverage(); status("提出済みとして保存しました。サーバーの確認後、通読回数に反映されます。");
};
function clearSubmittedCoverage(receipt) {
  for(const doc of receipt.documents) for(const id of Object.keys(doc.sentences)) delete saved.coverage[id];
}
async function synchronize() {
  try {
    server=await json("./rapid-review-state.json");
    const accepted=new Set(server.accepted_submission_ids || []);
    saved.pending=saved.pending.filter(s=>!accepted.has(s.submission_id));
    if(saved.draft && accepted.has(saved.draft.submission_id)) {
      clearSubmittedCoverage(saved.draft); saved.draft=null;
    }
    persist(); refreshCoverage();
    if(server.errors?.length) status(`確認が必要な通読Issue: ${server.errors.map(e=>`#${e.issue_number}: ${e.error}`).join(" / ")}`,true);
  } catch { /* Keep durable local receipts if the server is temporarily unavailable. */ }
}
$("resend").onclick = async () => {
  try {
    const receipt=saved.pending.find(s=>s.submission_id === $("pending-select").value);
    if(receipt) { await navigator.clipboard.writeText(JSON.stringify(receipt,null,2)); status("同じ提出IDでコピーしました。元のIssueを確認し、必要な場合のみ再提出してください。"); }
  } catch(e) { status(e.message,true); }
};
$("cancel-draft").onclick = () => {
  if(confirm("まだIssueを作成していない提出案だけ取り消してください。閲覧記録は残ります。")) {
    saved.draft=null; persist(); refreshCoverage();
  }
};
async function boot() {
  try {
    saved = await openStorage() || {coverage:{},pending:[],settings:{}};
    for(const doc of Object.values(saved.catalog || {})) for(const unit of doc.units) migrateCoverage(unit,saved.coverage);
    saved.settings = {...defaults,...saved.settings};
    for(const [name,value] of Object.entries(saved.settings)) if($(name)) {
      if($(name).type === "checkbox") $(name).checked=value; else $(name).value=value;
    }
    if(saved.range) [$("from").value,$("to").value]=saved.range;
    settings();
    index = await json("./archive/index.json");
    await synchronize();
    await load(saved.mode === "partial");
    await backfillCatalog();
    setInterval(() => { if(!document.hidden) synchronize(); },60000);
  } catch(e) { status(`初期化できません: ${e.message}`,true); }
}
async function backfillCatalog() {
  const known=new Set(Object.values(saved.catalog || {}).map(doc=>doc.track_doc_seq));
  const missing=new Set(Object.keys(saved.visitedDocuments || {}).map(Number).filter(seq=>!known.has(seq)));
  if(!missing.size) return;
  catalogLoading=true; refreshCoverage();
  try {
    for(const shard of index.tracks.dev.shards.filter(s=>[...missing].some(seq=>seq>=s.start_track_doc_seq && seq<=s.end_track_doc_seq))) {
      const payload=await json(shard.path);
      for(const doc of payload.documents.filter(d=>missing.has(d.track_doc_seq))) {
        doc.units=eligibleUnits(doc);
        for(const unit of doc.units) { unit.revision=await revision(unit); migrateCoverage(unit,saved.coverage); }
        saved.catalog ||= {};
        saved.catalog[doc.doc_id]=coverageSummary(doc);
      }
      persist(); refreshCoverage();
    }
  } catch(e) { status(`以前の閲覧分を再集計できませんでした。再読み込みで再試行できます: ${e.message}`,true); }
  finally { catalogLoading=false; refreshCoverage(); }
}
boot();
