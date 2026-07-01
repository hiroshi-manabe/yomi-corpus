const manifestUrl = "./manifest.json";
const submissionSchemaVersion = 1;

const state = {
  manifest: null,
  currentStageId: null,
  currentPackMeta: null,
  currentPack: null,
  currentDraft: null,
};

const el = {
  currentTrackList: document.querySelector("#current-track-list"),
  stageSelect: document.querySelector("#stage-select"),
  packList: document.querySelector("#pack-list"),
  historyCount: document.querySelector("#history-count"),
  packTitle: document.querySelector("#pack-title"),
  packBadge: document.querySelector("#pack-badge"),
  packMeta: document.querySelector("#pack-meta"),
  rangeSummary: document.querySelector("#range-summary"),
  itemsContainer: document.querySelector("#items-container"),
  itemsSummary: document.querySelector("#items-summary"),
  statusBanner: document.querySelector("#status-banner"),
  submissionPreview: document.querySelector("#submission-preview"),
  openLatest: document.querySelector("#open-latest"),
  clearRange: document.querySelector("#clear-range"),
  resetDraft: document.querySelector("#reset-draft"),
  copyJson: document.querySelector("#copy-json"),
  downloadJson: document.querySelector("#download-json"),
  reviewerName: document.querySelector("#reviewer-name"),
  itemTemplate: document.querySelector("#item-template"),
};

const settingsKey = "yomi-corpus:review-ui:settings:v1";

boot().catch((error) => {
  showStatus(`Failed to load review workspace: ${error.message}`, true);
  console.error(error);
});

async function boot() {
  loadSettings();
  bindEvents();
  const manifest = await fetchJson(manifestUrl);
  state.manifest = manifest;
  const stageIds = Object.keys(manifest.stages || {});
  if (stageIds.length === 0) {
    throw new Error("No review stages were published.");
  }
  populateStageSelect(stageIds);
  const initialTarget = resolveInitialTarget(stageIds);
  await openStage(initialTarget.stageId, {
    preferLatest: !initialTarget.packId,
    preferredPackId: initialTarget.packId,
  });
}

function bindEvents() {
  el.stageSelect.addEventListener("change", async (event) => {
    await openStage(event.target.value, { preferLatest: true });
  });

  el.openLatest.addEventListener("click", async () => {
    if (!state.currentStageId) {
      return;
    }
    await openStage(state.currentStageId, { preferLatest: true });
  });

  el.clearRange.addEventListener("click", () => {
    if (!isEditable()) {
      return;
    }
    state.currentDraft.from_seq = null;
    state.currentDraft.to_seq = null;
    touchDraft();
    render();
  });

  el.resetDraft.addEventListener("click", () => {
    if (!isEditable()) {
      return;
    }
    if (!window.confirm("Reset all local changes for this pack?")) {
      return;
    }
    state.currentDraft = createEmptyDraft(state.currentPack);
    saveDraft();
    render();
  });

  el.copyJson.addEventListener("click", async () => {
    const payload = JSON.stringify(buildSubmissionPayload(), null, 2);
    try {
      await navigator.clipboard.writeText(payload);
      showStatus("Submission JSON copied to clipboard.");
    } catch (error) {
      showStatus("Clipboard copy failed. Use the download button instead.", true);
    }
  });

  el.downloadJson.addEventListener("click", () => {
    const payload = JSON.stringify(buildSubmissionPayload(), null, 2);
    const blob = new Blob([payload], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${state.currentPack.pack_id || "review_submission"}.json`;
    a.click();
    URL.revokeObjectURL(url);
  });

  el.reviewerName.addEventListener("input", () => {
    saveSettings();
    renderSubmissionPreview();
  });
}

function resolveInitialTarget(stageIds) {
  const params = new URLSearchParams(window.location.search);
  const requested = params.get("stage");
  const requestedPackId = params.get("pack");
  if (requested && stageIds.includes(requested)) {
    return { stageId: requested, packId: requestedPackId };
  }

  if (requestedPackId) {
    for (const stageId of stageIds) {
      const stage = state.manifest.stages?.[stageId];
      if (stage?.packs?.some((pack) => pack.pack_id === requestedPackId)) {
        return { stageId, packId: requestedPackId };
      }
    }
  }

  const defaultStageId = stageIds.includes(state.manifest.default_stage)
    ? state.manifest.default_stage
    : stageIds[0];
  const defaultTrack = Object.values(state.manifest.current_tracks || {}).find(
    (track) => track.review_stage === defaultStageId,
  );
  if (defaultTrack) {
    return { stageId: defaultTrack.review_stage, packId: defaultTrack.pack_id };
  }
  return { stageId: defaultStageId, packId: null };
}

async function openStage(stageId, { preferLatest = false, preferredPackId = null } = {}) {
  const stage = state.manifest.stages?.[stageId];
  if (!stage) {
    throw new Error(`Unknown review stage: ${stageId}`);
  }
  state.currentStageId = stageId;
  el.stageSelect.value = stageId;

  const params = new URLSearchParams(window.location.search);
  const requestedPackId = params.get("pack");
  let packMeta = null;
  if (preferredPackId) {
    packMeta = stage.packs.find((pack) => pack.pack_id === preferredPackId) || null;
  }
  if (!packMeta && !preferLatest && requestedPackId) {
    packMeta = stage.packs.find((pack) => pack.pack_id === requestedPackId) || null;
  }
  if (!packMeta) {
    packMeta =
      stage.packs.find((pack) => pack.pack_id === stage.latest_pack_id) ||
      stage.packs[0] ||
      null;
  }
  if (!packMeta) {
    throw new Error(`No packs found for stage ${stageId}.`);
  }
  await openPack(stageId, packMeta.pack_id);
}

async function openPack(stageId, packId) {
  const stage = state.manifest.stages[stageId];
  const packMeta = stage.packs.find((pack) => pack.pack_id === packId);
  if (!packMeta) {
    throw new Error(`Pack ${packId} not found.`);
  }

  const pack = await fetchJson(packMeta.path);
  state.currentPackMeta = packMeta;
  state.currentPack = pack;
  state.currentDraft = loadDraft(pack);
  updateLocation(stageId, packId);
  render();
}

function populateStageSelect(stageIds) {
  el.stageSelect.innerHTML = "";
  for (const stageId of stageIds) {
    const option = document.createElement("option");
    option.value = stageId;
    option.textContent = state.manifest.stages[stageId].label || stageId;
    el.stageSelect.append(option);
  }
}

function render() {
  renderCurrentTracks();
  renderPackList();
  renderPackSummary();
  renderRangeSummary();
  renderItems();
  renderControlState();
  renderSubmissionPreview();
}

function renderCurrentTracks() {
  const currentTracks = state.manifest.current_tracks || {};
  el.currentTrackList.innerHTML = "";
  const cards = [];
  if (currentTracks.working) {
    cards.push({ ...currentTracks.working, track_name: "working", emphasis: "primary-track" });
  }
  if (currentTracks.dev) {
    cards.push({ ...currentTracks.dev, track_name: "dev", emphasis: "secondary-track" });
  }

  if (cards.length === 0) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "No active track packs were published.";
    el.currentTrackList.append(p);
    return;
  }

  for (const card of cards) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `track-card ${card.emphasis}`;
    button.innerHTML = `
      <div class="track-card-header">
        <strong>${escapeHtml(card.track_name === "working" ? "Current Working Review" : "Dev Review")}</strong>
        <span class="badge ${escapeHtml(card.track_name)}">${escapeHtml(card.track_name)}</span>
      </div>
      <div class="track-card-stage">${escapeHtml(card.label || card.review_stage)}</div>
      <div class="pack-meta-line">${escapeHtml(card.title)} · ${card.item_count} item(s)</div>
    `;
    button.addEventListener("click", () => {
      openStage(card.review_stage, {
        preferLatest: false,
        preferredPackId: card.pack_id,
      }).catch((error) => {
        showStatus(`Failed to open pack: ${error.message}`, true);
      });
    });
    el.currentTrackList.append(button);
  }
}

function renderPackList() {
  const stage = state.manifest.stages[state.currentStageId];
  el.historyCount.textContent = `${stage.packs.length} pack(s)`;
  el.packList.innerHTML = "";
  for (const pack of stage.packs) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "pack-button";
    if (pack.pack_id === state.currentPackMeta?.pack_id) {
      button.classList.add("active-pack");
    }
    if (!String(pack.status || "").startsWith("active")) {
      button.classList.add("readonly-pack");
    }
    button.innerHTML = `
      <div class="pack-title-line">
        <strong>${escapeHtml(pack.title || pack.pack_id)}</strong>
        <span class="badge ${escapeHtml(pack.status || "archived")}">${escapeHtml(pack.status || "archived")}</span>
      </div>
      <div class="pack-meta-line">${pack.item_count} item(s) · ${escapeHtml(pack.track_name || "working")}</div>
    `;
    button.addEventListener("click", () => {
      openPack(state.currentStageId, pack.pack_id).catch((error) => {
        showStatus(`Failed to open pack: ${error.message}`, true);
      });
    });
    el.packList.append(button);
  }
}

function renderPackSummary() {
  const stage = state.manifest.stages[state.currentStageId];
  const pack = state.currentPack;
  const packMeta = state.currentPackMeta;
  const editable = isEditable();
  el.packTitle.textContent = packMeta.title || pack.pack_id;
  const trackName = packMeta.track_name || "working";
  el.packBadge.textContent = editable ? `${trackName} / active` : `${trackName} / read-only`;
  el.packBadge.className = `badge ${editable ? "active" : "archived"} ${trackName}`;

  const draft = state.currentDraft;
  const { fromSeq, toSeq, includedCount } = getEffectiveRange();
  const overrides = getSubmissionOverridesForCurrentStage();
  const cards = [
    ["Stage", stage.label || stage.review_stage],
    ["Track", trackName],
    ["Pack ID", pack.pack_id],
    ["Items", String(pack.item_count)],
    ["Range", `${fromSeq}-${toSeq} (${includedCount} item(s))`],
    ["Overrides", String(overrides.length)],
    ["Draft Saved", draft.updated_at_epoch ? formatDate(draft.updated_at_epoch) : "Not yet"],
  ];
  el.packMeta.innerHTML = cards
    .map(
      ([label, value]) => `
        <div class="meta-card">
          <dt>${escapeHtml(label)}</dt>
          <dd>${escapeHtml(value)}</dd>
        </div>
      `
    )
    .join("");
}

function renderRangeSummary() {
  const { fromSeq, toSeq, includedCount } = getEffectiveRange();
  const overrides = getSubmissionOverridesForCurrentStage();
  const defaultAcceptCount = Math.max(includedCount - overrides.length, 0);
  const summaryCards = [
    makeSummaryCard("From", String(fromSeq)),
    makeSummaryCard("To", String(toSeq)),
    makeSummaryCard("Included", String(includedCount)),
    makeSummaryCard("Default Accept", String(defaultAcceptCount)),
    makeSummaryCard("Overrides", String(overrides.length)),
  ];
  el.rangeSummary.innerHTML = summaryCards.join("");
}

function makeSummaryCard(label, value) {
  return `
    <div class="meta-card">
      <dt>${escapeHtml(label)}</dt>
      <dd>${escapeHtml(value)}</dd>
    </div>
  `;
}

function renderItems() {
  const pack = state.currentPack;
  const { fromSeq, toSeq } = getEffectiveRange();
  const editable = isEditable();
  el.itemsSummary.textContent = `${pack.items.length} total item(s)`;
  el.itemsContainer.innerHTML = "";

  for (const item of pack.items) {
    const node = el.itemTemplate.content.firstElementChild.cloneNode(true);
    const inRange = item.seq >= fromSeq && item.seq <= toSeq;
    const override = state.currentDraft.overrides[item.item_id] || null;
    const isFrom = state.currentDraft.from_seq === item.seq;
    const isTo = state.currentDraft.to_seq === item.seq;

    node.classList.toggle("out-of-range", !inRange);
    node.classList.toggle("has-override", Boolean(override));
    node.classList.toggle("marker-start", isFrom);
    node.classList.toggle("marker-end", isTo);

    node.querySelector(".item-seq").textContent = `#${item.seq}`;
    if (pack.review_stage === "yomi_final_review") {
      renderYomiItem({ node, item, override, editable, isFrom, isTo });
      el.itemsContainer.append(node);
      continue;
    }
    if (pack.review_stage === "yomi_strong_repair_review") {
      renderStrongRepairItem({ node, item, override, editable, isFrom, isTo });
      el.itemsContainer.append(node);
      continue;
    }
    node.querySelector(".item-title").textContent = item.entity_key;

    const proposedBadge = node.querySelector(".proposed-badge");
    proposedBadge.textContent = item.proposed_action;
    proposedBadge.classList.add(item.proposed_action);

    const markerBadge = node.querySelector(".marker-badge");
    if (isFrom && isTo) {
      markerBadge.textContent = "from + to";
      markerBadge.classList.remove("hidden");
    } else if (isFrom) {
      markerBadge.textContent = "from";
      markerBadge.classList.remove("hidden");
    } else if (isTo) {
      markerBadge.textContent = "to";
      markerBadge.classList.remove("hidden");
    } else {
      markerBadge.classList.add("hidden");
    }

    const overrideBadge = node.querySelector(".override-badge");
    if (override) {
      overrideBadge.textContent = override.decision;
      overrideBadge.classList.remove("hidden");
    } else {
      overrideBadge.classList.add("hidden");
    }

    const meta = node.querySelector(".item-meta");
    meta.innerHTML = [
      ["Surface Forms", (item.surface_forms || []).join(" | ") || "None"],
      ["Support", `${item.evidence.supporting_observations} obs / ${item.evidence.supporting_batch_count} batch(es)`],
      ["Oppose", `${item.evidence.opposing_observations} obs / ${item.evidence.opposing_batch_count} batch(es)`],
      ["Confidence", formatConfidenceCounts(item.evidence.confidence_counts)],
    ]
      .map(
        ([label, value]) => `
          <div>
            <dt>${escapeHtml(label)}</dt>
            <dd>${escapeHtml(value)}</dd>
          </div>
        `
      )
      .join("");

    const examples = node.querySelector(".example-list");
    examples.innerHTML = "";
    for (const text of item.example_texts || []) {
      const li = document.createElement("li");
      li.textContent = text;
      examples.append(li);
    }

    const notes = node.querySelector(".note-list");
    notes.innerHTML = "";
    const noteSamples = item.note_samples || [];
    if (noteSamples.length === 0) {
      const li = document.createElement("li");
      li.className = "muted";
      li.textContent = "No note samples.";
      notes.append(li);
    } else {
      for (const text of noteSamples) {
        const li = document.createElement("li");
        li.textContent = text;
        notes.append(li);
      }
    }

    const editableSections = node.querySelectorAll(".editable-only");
    const readonlySections = node.querySelectorAll(".readonly-only");
    editableSections.forEach((section) => section.classList.toggle("hidden", !editable));
    readonlySections.forEach((section) => section.classList.toggle("hidden", editable));

    if (editable) {
      node.querySelector(".set-from").addEventListener("click", () => {
        state.currentDraft.from_seq = item.seq;
        touchDraft();
        render();
      });
      node.querySelector(".set-to").addEventListener("click", () => {
        state.currentDraft.to_seq = item.seq;
        touchDraft();
        render();
      });

      node.querySelector(".accept-default").addEventListener("click", () => {
        delete state.currentDraft.overrides[item.item_id];
        touchDraft();
        render();
      });

      node.querySelector(".reject-item").addEventListener("click", () => {
        setOverride(item.item_id, "reject");
      });

      node.querySelector(".defer-item").addEventListener("click", () => {
        setOverride(item.item_id, "defer");
      });

      const noteField = node.querySelector(".override-note");
      noteField.value = override?.note || "";
      noteField.addEventListener("input", () => {
        if (!state.currentDraft.overrides[item.item_id]) {
          state.currentDraft.overrides[item.item_id] = { decision: "defer", note: "" };
        }
        state.currentDraft.overrides[item.item_id].note = noteField.value;
        touchDraft();
        renderSubmissionPreview();
      });
    }

    el.itemsContainer.append(node);
  }
}

function renderStrongRepairItem({ node, item, override, editable, isFrom, isTo }) {
  node.innerHTML = "";
  node.classList.add("strong-repair-card");
  node.classList.toggle("has-override", Boolean(override));
  node.classList.toggle("marker-start", isFrom);
  node.classList.toggle("marker-end", isTo);

  const header = document.createElement("header");
  header.className = "item-header";
  const titleWrap = document.createElement("div");
  const titleRow = document.createElement("div");
  titleRow.className = "item-title-row";
  const seq = document.createElement("span");
  seq.className = "item-seq";
  seq.textContent = `#${item.seq}`;
  const title = document.createElement("h3");
  title.className = "item-title";
  title.textContent = item.rejected_span || item.item_id;
  titleRow.append(seq, title);

  const badges = document.createElement("div");
  badges.className = "item-badges";
  const statusBadge = document.createElement("span");
  statusBadge.className = "badge proposed-badge";
  statusBadge.textContent = item.repair_status || "pending";
  badges.append(statusBadge);
  if (item.used_web_search) {
    const webBadge = document.createElement("span");
    webBadge.className = "badge";
    webBadge.textContent = "web";
    badges.append(webBadge);
  }
  if (override?.decision) {
    const overrideBadge = document.createElement("span");
    overrideBadge.className = "badge override-badge";
    overrideBadge.textContent = "edited";
    badges.append(overrideBadge);
  }
  titleWrap.append(titleRow, badges);
  header.append(titleWrap);
  node.append(header);

  const afterLine = document.createElement("p");
  afterLine.className = "ruby-line strong-repair-after";
  afterLine.append(...renderStrongRepairAfterLine(item, override, editable));
  node.append(afterLine);

  const details = document.createElement("details");
  details.className = "strong-repair-debug";
  const summary = document.createElement("summary");
  summary.textContent = "Details";
  details.append(summary);
  const grid = document.createElement("dl");
  grid.className = "strong-repair-grid";
  for (const [label, value] of [
    ["Text", item.text || ""],
    ["Rejected", formatRejectedReadings(item)],
    ["Proposal", formatRepairProposal(item.llm_parsed || [])],
    ["Before", item.rendered_yomi_before || ""],
    ["After", item.rendered_yomi_after || ""],
  ]) {
    const wrap = document.createElement("div");
    const dt = document.createElement("dt");
    dt.textContent = label;
    const dd = document.createElement("dd");
    dd.textContent = value;
    wrap.append(dt, dd);
    grid.append(wrap);
  }
  details.append(grid);
  node.append(details);

  if (editable) {
    const noteLabel = document.createElement("label");
    noteLabel.className = "note-field strong-repair-note";
    const noteTitle = document.createElement("span");
    noteTitle.textContent = "Note";
    const note = document.createElement("textarea");
    note.className = "override-note";
    note.rows = 2;
    note.value = override?.note || "";
    note.addEventListener("input", () => {
      const current = state.currentDraft.overrides[item.item_id] || { decision: "accept", note: "" };
      current.note = note.value;
      state.currentDraft.overrides[item.item_id] = current;
      touchDraft();
      renderSubmissionPreview();
    });
    noteLabel.append(noteTitle, note);
    node.append(noteLabel);
  }
}

function formatRejectedReadings(item) {
  const readings = item.rejected_readings || [];
  if (!readings.length) {
    return item.rejected_span || "";
  }
  return readings
    .map((row) => `${row.surface || ""}=${row.reading || ""}`)
    .join("; ");
}

function formatRepairProposal(rows) {
  if (!rows.length) {
    return "";
  }
  return rows
    .map((row) => `${row.surface || ""}/${row.reading || ""}`)
    .join(" + ");
}

function renderStrongRepairAfterLine(item, override, editable) {
  const tokens = parseRenderedYomiTokens(item.rendered_yomi_after || "");
  const span = item.rejected_span || "";
  const match = findRenderedTokenSpan(tokens, span);
  if (!span || !match) {
    return renderReadonlyRubyFromRendered(item.rendered_yomi_after || "");
  }
  const nodes = [];
  for (let index = 0; index < tokens.length; index += 1) {
    if (index === match.start) {
      nodes.push(renderStrongRepairSpanEditor(item, override, editable));
      index = match.end - 1;
      continue;
    }
    nodes.push(...renderReadonlyRubyFromToken(item, tokens[index], index));
  }
  return nodes;
}

function findRenderedTokenSpan(tokens, surfaceSpan) {
  const matches = [];
  for (let start = 0; start < tokens.length; start += 1) {
    let surface = "";
    for (let end = start + 1; end <= tokens.length; end += 1) {
      surface += tokens[end - 1].surface || "";
      if (surface === surfaceSpan) {
        matches.push({ start, end });
        break;
      }
      if (!surfaceSpan.startsWith(surface)) {
        break;
      }
    }
  }
  return matches.length === 1 ? matches[0] : null;
}

function renderStrongRepairSpanEditor(item, override, editable) {
  const manualSegments = override?.manual_segments || null;
  const segments = manualSegments?.length ? manualSegments : defaultStrongRepairSegments(item);
  const wrapper = document.createElement("span");
  wrapper.className = "strong-repair-span-editor";
  wrapper.classList.toggle("changed", Boolean(manualSegments?.length));

  const preview = document.createElement("span");
  preview.className = "strong-repair-span-preview";
  preview.append(...renderStrongRepairSegmentRuby(item, segments, editable));
  wrapper.append(preview);

  if (!editable) {
    return wrapper;
  }

  const editor = document.createElement("span");
  editor.className = "span-editor strong-repair-boundary-editor";
  editor.append(renderStrongRepairSplitControls(item, segments));

  const fields = document.createElement("span");
  fields.className = "span-reading-fields";
  for (const [index, segment] of segments.entries()) {
    const label = document.createElement("label");
    label.className = "span-reading-field";
    const surface = document.createElement("span");
    surface.textContent = segment.surface || "";
    const input = document.createElement("input");
    input.type = "text";
    input.value = segment.reading || "";
    input.placeholder = "reading";
    input.addEventListener("input", () => {
      const current = ensureStrongRepairOverride(item.item_id);
      const currentSegments = current.manual_segments?.length
        ? current.manual_segments
        : defaultStrongRepairSegments(item);
      currentSegments[index].reading = input.value;
      currentSegments[index].edited = true;
      current.manual_segments = currentSegments;
      touchDraft();
      renderSubmissionPreview();
    });
    label.append(surface, input);
    fields.append(label);
  }
  editor.append(fields);
  wrapper.append(editor);
  return wrapper;
}

function renderStrongRepairSegmentRuby(item, segments, editable) {
  const nodes = [];
  for (const [index, segment] of segments.entries()) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "strong-repair-segment-token";
    button.disabled = !editable;
    button.title = editable ? "Cycle reading candidate" : "";
    if (segment.reading) {
      const ruby = document.createElement("ruby");
      ruby.append(document.createTextNode(segment.surface || ""));
      const rt = document.createElement("rt");
      rt.textContent = segment.reading;
      ruby.append(rt);
      button.append(ruby);
    } else {
      button.append(document.createTextNode(segment.surface || ""));
    }
    if (editable) {
      button.addEventListener("click", () => cycleStrongRepairSegmentReading(item, index));
    }
    nodes.push(button);
  }
  return nodes;
}

function cycleStrongRepairSegmentReading(item, index) {
  const current = ensureStrongRepairOverride(item.item_id);
  const segments = current.manual_segments?.length
    ? current.manual_segments
    : defaultStrongRepairSegments(item);
  const segment = segments[index];
  if (!segment) {
    return;
  }
  const candidates = strongRepairReadingCycleCandidates(item, segment.surface || "");
  const values = [...candidates, ""];
  const currentIndex = values.indexOf(segment.reading || "");
  const next = values[(currentIndex + 1) % values.length];
  segments[index] = {
    ...segment,
    reading: next,
    edited: true,
  };
  current.manual_segments = segments;
  touchDraft();
  render();
}

function strongRepairReadingCycleCandidates(item, surface) {
  const values = [];
  for (const reading of item.reading_candidates?.[surface] || []) {
    if (reading && !values.includes(reading)) {
      values.push(reading);
    }
  }
  const hint = item.reading_hints?.[surface];
  if (hint && !values.includes(hint)) {
    values.push(hint);
  }
  const targetReading = readingFromStrongRepairTargets(item.target_escalations || [], surface);
  if (targetReading && !values.includes(targetReading)) {
    values.push(targetReading);
  }
  for (const row of item.llm_parsed || []) {
    if (row?.surface === surface && row.reading) {
      const reading = katakanaToHiragana(String(row.reading));
      if (!values.includes(reading)) {
        values.push(reading);
      }
    }
  }
  for (const row of item.repair_log?.replacement || []) {
    if (row?.surface === surface && row.reading) {
      const reading = katakanaToHiragana(String(row.reading));
      if (!values.includes(reading)) {
        values.push(reading);
      }
    }
  }
  return values;
}

function renderStrongRepairSplitControls(item, segments) {
  const wrap = document.createElement("span");
  wrap.className = "split-controls";
  const chars = Array.from(item.rejected_span || "");
  const indexes = strongRepairSplitIndexes(segments);
  for (let index = 0; index < chars.length; index += 1) {
    const charSpan = document.createElement("span");
    charSpan.className = "split-char";
    charSpan.textContent = chars[index];
    wrap.append(charSpan);
    if (index < chars.length - 1) {
      const boundaryIndex = index + 1;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "split-toggle";
      button.textContent = indexes.has(boundaryIndex) ? "/" : "=";
      button.title = "Toggle split";
      button.addEventListener("click", () => updateStrongRepairSplit(item, boundaryIndex));
      wrap.append(button);
    }
  }
  return wrap;
}

function strongRepairSplitIndexes(segments) {
  const indexes = new Set();
  let cursor = 0;
  for (const segment of segments || []) {
    cursor += Array.from(segment.surface || "").length;
    indexes.add(cursor);
  }
  indexes.delete(0);
  indexes.delete(
    (segments || []).reduce((total, segment) => total + Array.from(segment.surface || "").length, 0)
  );
  return indexes;
}

function updateStrongRepairSplit(item, boundaryIndex) {
  const current = ensureStrongRepairOverride(item.item_id);
  const previousSegments = current.manual_segments?.length
    ? current.manual_segments
    : defaultStrongRepairSegments(item);
  const hasUserEditedReadings = previousSegments.some((segment) => segment.edited);
  if (
    hasUserEditedReadings &&
    !window.confirm("Changing this split will rebuild reading fields for this span. Continue?")
  ) {
    return;
  }
  const indexes = strongRepairSplitIndexes(previousSegments);
  if (indexes.has(boundaryIndex)) {
    indexes.delete(boundaryIndex);
  } else {
    indexes.add(boundaryIndex);
  }
  const ordered = [...indexes].sort((a, b) => a - b);
  const chars = Array.from(item.rejected_span || "");
  let start = 0;
  current.manual_segments = [];
  for (const end of [...ordered, chars.length]) {
    const surface = chars.slice(start, end).join("");
    current.manual_segments.push({
      surface,
      reading: defaultStrongRepairReadingForSegment(item, surface, previousSegments),
      edited: false,
    });
    start = end;
  }
  touchDraft();
  render();
}

function ensureStrongRepairOverride(itemId) {
  const current = state.currentDraft.overrides[itemId] || {};
  state.currentDraft.overrides[itemId] = {
    decision: "accept",
    note: current.note || "",
    manual_segments: current.manual_segments || null,
  };
  return state.currentDraft.overrides[itemId];
}

function defaultStrongRepairSegments(item) {
  const parsed = item.llm_parsed || [];
  if (parsed.length) {
    return parsed
      .filter((row) => row && row.surface)
      .map((row) => ({
        surface: String(row.surface || ""),
        reading: katakanaToHiragana(String(row.reading || "")),
      }));
  }
  const replacement = item.repair_log?.replacement || [];
  if (replacement.length) {
    return replacement
      .filter((row) => row && row.surface)
      .map((row) => ({
        surface: String(row.surface || ""),
        reading: katakanaToHiragana(String(row.reading || "")),
      }));
  }
  return [
    {
      surface: item.rejected_span || "",
      reading: "",
    },
  ];
}

function defaultStrongRepairReadingForSegment(item, surface, previousSegments) {
  const previous = (previousSegments || []).find(
    (segment) => segment.surface === surface && segment.reading
  );
  if (previous) {
    return previous.reading;
  }
  for (const row of item.llm_parsed || []) {
    if (row?.surface === surface && row.reading) {
      return katakanaToHiragana(String(row.reading));
    }
  }
  for (const row of item.repair_log?.replacement || []) {
    if (row?.surface === surface && row.reading) {
      return katakanaToHiragana(String(row.reading));
    }
  }
  const readingCandidates = item.reading_candidates?.[surface] || [];
  if (readingCandidates.length) {
    return readingCandidates[0];
  }
  const readingHint = item.reading_hints?.[surface];
  if (readingHint) {
    return readingHint;
  }
  const targetReading = readingFromStrongRepairTargets(item.target_escalations || [], surface);
  if (targetReading) {
    return targetReading;
  }
  return "";
}

function readingFromStrongRepairTargets(targets, surface) {
  for (let start = 0; start < targets.length; start += 1) {
    let joinedSurface = "";
    let joinedReading = "";
    for (let end = start; end < targets.length; end += 1) {
      joinedSurface += targets[end].surface || "";
      joinedReading += targets[end].current_reading_hiragana || "";
      if (joinedSurface === surface) {
        return joinedReading;
      }
      if (!surface.startsWith(joinedSurface)) {
        break;
      }
    }
  }
  return "";
}

function renderReadonlyRubyFromRendered(rendered) {
  return renderReadonlyRubyFromTokens(parseRenderedYomiTokens(rendered));
}

function renderReadonlyRubyFromTokens(tokens) {
  const nodes = [];
  for (const [index, token] of tokens.entries()) {
    nodes.push(...renderReadonlyRubyFromToken(null, token, index));
  }
  if (!nodes.length) {
    nodes.push(document.createTextNode(""));
  }
  return nodes;
}

function renderReadonlyRubyFromToken(item, token, index) {
  const tokenNodes = item?.rendered_yomi_after_ruby_tokens?.[index]?.nodes || null;
  if (tokenNodes?.length) {
    return renderRubyDisplayNodes(tokenNodes);
  }
  if (!shouldDisplayRuby(token.surface, token.reading)) {
    return [document.createTextNode(token.surface)];
  }
  const span = document.createElement("span");
  span.className = "ruby-token readonly-ruby-token";
  const ruby = document.createElement("ruby");
  ruby.append(document.createTextNode(token.surface));
  const rt = document.createElement("rt");
  rt.textContent = katakanaToHiragana(token.reading);
  ruby.append(rt);
  span.append(ruby);
  return [span];
}

function renderRubyDisplayNodes(displayNodes) {
  const nodes = [];
  for (const displayNode of displayNodes || []) {
    if (displayNode?.type === "ruby" && displayNode.text && displayNode.reading) {
      const span = document.createElement("span");
      span.className = "ruby-token readonly-ruby-token";
      const ruby = document.createElement("ruby");
      ruby.append(document.createTextNode(displayNode.text));
      const rt = document.createElement("rt");
      rt.textContent = displayNode.reading;
      ruby.append(rt);
      span.append(ruby);
      nodes.push(span);
      continue;
    }
    if (displayNode?.text) {
      nodes.push(document.createTextNode(displayNode.text));
    }
  }
  return nodes;
}

function parseRenderedYomiTokens(rendered) {
  return String(rendered || "")
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((token) => {
      const separator = token.lastIndexOf("/");
      if (separator < 0) {
        return { surface: token, reading: "" };
      }
      return {
        surface: token.slice(0, separator),
        reading: token.slice(separator + 1),
      };
    });
}

function shouldDisplayRuby(surface, reading) {
  if (!surface || !reading || surface === reading) {
    return false;
  }
  return /[一-龯々〆ヵヶA-Za-z]/u.test(surface);
}

function katakanaToHiragana(text) {
  return String(text || "").replace(/[ァ-ン]/g, (char) =>
    String.fromCharCode(char.charCodeAt(0) - 0x60)
  );
}

function setOverride(itemId, decision) {
  const current = state.currentDraft.overrides[itemId] || { note: "" };
  state.currentDraft.overrides[itemId] = { decision, note: current.note || "" };
  touchDraft();
  render();
}

function renderYomiItem({ node, item, override, editable, isFrom, isTo }) {
  node.innerHTML = "";
  node.classList.add("yomi-card");
  node.classList.toggle("all-safe", item.unresolved_target_count === 0);
  node.classList.toggle("has-unresolved", item.unresolved_target_count > 0);

  const controls = document.createElement("div");
  controls.className = "yomi-controls";

  const skipLabel = document.createElement("label");
  skipLabel.className = "yomi-control yomi-flag yomi-skip-flag";
  skipLabel.title = "Skip this sentence";
  const skipCheckbox = document.createElement("input");
  skipCheckbox.type = "checkbox";
  skipCheckbox.disabled = !editable;
  skipCheckbox.checked = override?.skip ?? item.skip_default ?? false;
  skipCheckbox.setAttribute("aria-label", "Skip this sentence");
  const skipGlyph = document.createElement("span");
  skipGlyph.className = "control-glyph";
  skipGlyph.setAttribute("aria-hidden", "true");
  skipGlyph.textContent = "×";
  skipLabel.append(skipCheckbox, skipGlyph);
  controls.append(skipLabel);

  const menu = document.createElement("details");
  menu.className = "yomi-menu";
  const summary = document.createElement("summary");
  summary.title = "Range markers";
  summary.setAttribute("aria-label", "Range markers");
  summary.textContent = "⋯";
  menu.append(summary);
  const menuBody = document.createElement("div");
  menuBody.className = "yomi-menu-body";
  const fromButton = document.createElement("button");
  fromButton.type = "button";
  fromButton.className = "secondary-button";
  fromButton.textContent = isFrom ? "From here ✓" : "From here";
  fromButton.disabled = !editable;
  const toButton = document.createElement("button");
  toButton.type = "button";
  toButton.className = "secondary-button";
  toButton.textContent = isTo ? "To here ✓" : "To here";
  toButton.disabled = !editable;
  menuBody.append(fromButton, toButton);
  menu.append(menuBody);
  controls.append(menu);
  node.append(controls);

  const rubyLine = document.createElement("p");
  rubyLine.className = "ruby-line";
  rubyLine.append(...renderRubySegments(item, override, editable));
  node.append(rubyLine);

  if (!editable) {
    return;
  }
  skipCheckbox.addEventListener("change", () => {
    const draft = ensureYomiOverride(item.item_id);
    draft.skip = skipCheckbox.checked;
    touchDraft();
    renderSubmissionPreview();
  });
  fromButton.addEventListener("click", () => {
    state.currentDraft.from_seq = item.seq;
    touchDraft();
    render();
  });
  toButton.addEventListener("click", () => {
    state.currentDraft.to_seq = item.seq;
    touchDraft();
    render();
  });
}

function renderRubySegments(item, override, editable) {
  const nodes = [];
  const targetsById = Object.fromEntries((item.targets || []).map((target) => [target.item_id, target]));
  const groups = buildYomiSpanGroups(item);
  const groupByFirstTargetId = Object.fromEntries(groups.map((group) => [group.targetIds[0], group]));
  const hiddenTargetIds = new Set(groups.flatMap((group) => group.targetIds.slice(1)));
  for (const segment of item.ruby_segments || [{ type: "text", text: item.text || "" }]) {
    if (segment.type !== "ruby") {
      nodes.push(document.createTextNode(segment.text || ""));
      continue;
    }
    if (hiddenTargetIds.has(segment.target_item_id)) {
      continue;
    }
    const group = groupByFirstTargetId[segment.target_item_id];
    if (group) {
      nodes.push(renderYomiSpanGroup(item, group, override, editable));
      continue;
    }
    const target = targetsById[segment.target_item_id];
    if (!target) {
      nodes.push(document.createTextNode(segment.text || ""));
      continue;
    }
    nodes.push(renderRubySpan(item, target, override, editable));
  }
  return nodes;
}

function buildYomiSpanGroups(item) {
  const targets = (item.targets || [])
    .filter(
      (target) =>
        target &&
        Number.isInteger(target.target_start) &&
        Number.isInteger(target.target_end)
    )
    .sort((a, b) => a.target_start - b.target_start || a.target_end - b.target_end);
  const groups = [];
  const consumed = new Set();
  for (let index = 0; index < targets.length; index += 1) {
    const target = targets[index];
    if (consumed.has(target.item_id) || target.is_safe) {
      continue;
    }
    let endIndex = index;
    while (
      endIndex + 1 < targets.length &&
      targets[endIndex].target_end === targets[endIndex + 1].target_start
    ) {
      endIndex += 1;
    }
    const groupTargets = targets.slice(index, endIndex + 1);
    groupTargets.forEach((row) => consumed.add(row.item_id));
    groups.push(makeYomiSpanGroup(groupTargets, item.reading_hints || {}));
  }
  return groups;
}

function makeYomiSpanGroup(targets, readingHints) {
  const targetIds = targets.map((target) => target.item_id);
  const originalSurface = targets.map((target) => target.surface || "").join("");
  const id = targetIds.join("|");
  return {
    id,
    targetIds,
    targets,
    originalSurface,
    readingHints,
    unresolved: targets.some((target) => !target.is_safe),
  };
}

function renderYomiSpanGroup(item, group, override, editable) {
  const spanDraft = override?.span_overrides?.[group.id] || null;
  const mode = spanDraft?.decision || "ok";
  const wrapper = document.createElement("span");
  wrapper.className = "yomi-span-group";
  wrapper.classList.toggle("changed", Boolean(spanDraft));
  wrapper.classList.toggle("unresolved", group.unresolved);
  wrapper.append(renderYomiSpanPreview(item, group, spanDraft, editable));
  if (editable && mode !== "ok") {
    wrapper.append(renderYomiSpanEditor(item, group, spanDraft, mode));
  }
  return wrapper;
}

function renderYomiSpanPreview(item, group, spanDraft, editable) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "ruby-token span-token";
  button.classList.toggle("changed", Boolean(spanDraft));
  button.disabled = !editable;
  button.title = "Span review: OK / fix readings / fix segmentation";
  const segments = spanSegmentsForDisplay(group, spanDraft);
  for (const segment of segments) {
    if (segment.reading) {
      const ruby = document.createElement("ruby");
      ruby.append(document.createTextNode(segment.surface));
      const rt = document.createElement("rt");
      rt.textContent = segment.reading;
      ruby.append(rt);
      button.append(ruby);
    } else {
      button.append(document.createTextNode(segment.surface));
    }
  }
  if (editable) {
    button.addEventListener("click", () => cycleYomiSpanMode(item, group));
  }
  return button;
}

function spanSegmentsForDisplay(group, spanDraft) {
  if (spanDraft?.segments?.length) {
    return spanDraft.segments;
  }
  return group.targets.map((target) => {
    const candidate = selectedCandidate(target, null);
    return {
      surface: target.surface || "",
      reading: candidate?.reading || null,
    };
  });
}

function cycleYomiSpanMode(item, group) {
  const draft = ensureYomiOverride(item.item_id);
  if (!draft.span_overrides) {
    draft.span_overrides = {};
  }
  const current = draft.span_overrides[group.id] || null;
  const currentMode = current?.decision || "ok";
  const nextMode =
    currentMode === "ok" ? "reading" : currentMode === "reading" ? "segmentation" : "ok";
  if (nextMode === "ok") {
    delete draft.span_overrides[group.id];
    cleanupYomiOverride(item.item_id);
  } else {
    const nextSegments =
      nextMode === "reading"
        ? readingModeSegments(group, current)
        : segmentationModeSegments(group, current);
    draft.span_overrides[group.id] = {
      id: group.id,
      decision: nextMode,
      target_item_ids: group.targetIds,
      original_surface: group.originalSurface,
      segments: nextSegments,
    };
  }
  touchDraft();
  render();
}

function readingModeSegments(group, current) {
  if (current?.decision === "reading" && current.segments?.length) {
    return current.segments;
  }
  return group.targets.map((target) => {
    const candidate = selectedCandidate(target, null);
    return {
      surface: target.surface || "",
      reading: candidate?.reading || "",
    };
  });
}

function segmentationModeSegments(group, current) {
  if (current?.decision === "segmentation" && current.segments?.length) {
    return current.segments;
  }
  return [
    {
      surface: group.originalSurface,
      reading: defaultReadingForSpanSegment(group, group.originalSurface, current?.segments || []),
    },
  ];
}

function joinedGroupReading(group) {
  return group.targets
    .map((target) => selectedCandidate(target, null)?.reading || "")
    .join("");
}

function renderYomiSpanEditor(item, group, spanDraft, mode) {
  const panel = document.createElement("span");
  panel.className = "span-editor";

  const modeLabel = document.createElement("span");
  modeLabel.className = "span-editor-mode";
  modeLabel.textContent = mode === "reading" ? "Fix readings" : "Fix segmentation";
  panel.append(modeLabel);

  if (mode === "segmentation") {
    panel.append(renderSplitControls(item, group, spanDraft));
  }

  const fieldList = document.createElement("span");
  fieldList.className = "span-reading-fields";
  for (const [index, segment] of (spanDraft.segments || []).entries()) {
    const label = document.createElement("label");
    label.className = "span-reading-field";
    const surface = document.createElement("span");
    surface.textContent = segment.surface || "";
    const input = document.createElement("input");
    input.type = "text";
    input.value = segment.reading || "";
    input.placeholder = "reading";
    input.addEventListener("input", () => {
      const draft = ensureYomiOverride(item.item_id);
      const current = draft.span_overrides?.[group.id];
      if (!current) {
        return;
      }
      current.segments[index].reading = input.value;
      touchDraft();
      renderSubmissionPreview();
    });
    label.append(surface, input);
    fieldList.append(label);
  }
  panel.append(fieldList);
  return panel;
}

function renderSplitControls(item, group, spanDraft) {
  const wrap = document.createElement("span");
  wrap.className = "split-controls";
  const chars = Array.from(group.originalSurface);
  for (let index = 0; index < chars.length; index += 1) {
    const charSpan = document.createElement("span");
    charSpan.className = "split-char";
    charSpan.textContent = chars[index];
    wrap.append(charSpan);
    if (index < chars.length - 1) {
      const boundaryIndex = index + 1;
      const button = document.createElement("button");
      button.type = "button";
      button.className = "split-toggle";
      button.textContent = splitIndexes(spanDraft).has(boundaryIndex) ? "|" : "·";
      button.title = "Toggle split";
      button.addEventListener("click", () => {
        updateSegmentationSplit(item, group, boundaryIndex);
      });
      wrap.append(button);
    }
  }
  return wrap;
}

function splitIndexes(spanDraft) {
  const indexes = new Set();
  let cursor = 0;
  for (const segment of spanDraft?.segments || []) {
    cursor += Array.from(segment.surface || "").length;
    indexes.add(cursor);
  }
  indexes.delete(0);
  indexes.delete(Array.from(spanDraft?.original_surface || "").length);
  return indexes;
}

function updateSegmentationSplit(item, group, boundaryIndex) {
  const draft = ensureYomiOverride(item.item_id);
  const spanDraft = draft.span_overrides?.[group.id];
  if (!spanDraft || spanDraft.decision !== "segmentation") {
    return;
  }
  const existingReadings = (spanDraft.segments || []).some((segment) => segment.reading);
  if (
    existingReadings &&
    !window.confirm("Changing this split will rebuild reading fields for this span. Continue?")
  ) {
    return;
  }
  const indexes = splitIndexes(spanDraft);
  if (indexes.has(boundaryIndex)) {
    indexes.delete(boundaryIndex);
  } else {
    indexes.add(boundaryIndex);
  }
  const ordered = [...indexes].sort((a, b) => a - b);
  const chars = Array.from(group.originalSurface);
  let start = 0;
  const previousSegments = spanDraft.segments || [];
  spanDraft.segments = [];
  for (const end of [...ordered, chars.length]) {
    const surface = chars.slice(start, end).join("");
    spanDraft.segments.push({
      surface,
      reading: defaultReadingForSpanSegment(group, surface, previousSegments),
    });
    start = end;
  }
  touchDraft();
  render();
}

function defaultReadingForSpanSegment(group, surface, previousSegments) {
  const previous = (previousSegments || []).find(
    (segment) => segment.surface === surface && segment.reading
  );
  if (previous) {
    return previous.reading;
  }
  if (group.readingHints?.[surface]) {
    return group.readingHints[surface];
  }
  const targetReading = readingFromConsecutiveTargets(group.targets, surface);
  if (targetReading) {
    return targetReading;
  }
  if (surface === group.originalSurface) {
    return joinedGroupReading(group);
  }
  return "";
}

function readingFromConsecutiveTargets(targets, surface) {
  for (let start = 0; start < targets.length; start += 1) {
    let joinedSurface = "";
    let joinedReading = "";
    for (let end = start; end < targets.length; end += 1) {
      joinedSurface += targets[end].surface || "";
      joinedReading += selectedCandidate(targets[end], null)?.reading || "";
      if (joinedSurface === surface) {
        return joinedReading;
      }
      if (!surface.startsWith(joinedSurface)) {
        break;
      }
    }
  }
  return "";
}

function renderRubySpan(item, target, override, editable) {
  const targetDraft = override?.targets?.[target.item_id] || null;
  const candidate = selectedCandidate(target, targetDraft);
  const button = document.createElement("button");
  button.type = "button";
  button.className = "ruby-token";
  button.classList.toggle("unresolved", !target.is_safe);
  button.classList.toggle("safe", Boolean(target.is_safe));
  button.classList.toggle("changed", Boolean(targetDraft));
  button.disabled = !editable;
  button.title = rubyTitle(target, candidate);

  if (candidate?.reading) {
    const ruby = document.createElement("ruby");
    ruby.append(document.createTextNode(target.surface));
    const rt = document.createElement("rt");
    rt.textContent = candidate.reading;
    ruby.append(rt);
    button.append(ruby);
  } else {
    button.textContent = target.surface;
  }

  if (editable) {
    button.addEventListener("click", () => {
      cycleYomiTarget(item, target, candidate);
    });
  }
  return button;
}

function selectedCandidate(target, targetDraft) {
  const candidates = target.candidates || [];
  if (targetDraft?.choice_source) {
    return (
      candidates.find((candidate) => candidate.source === targetDraft.choice_source) ||
      candidates[0] ||
      null
    );
  }
  const defaultSource = target.default_choice_source || "current";
  return (
    candidates.find((candidate) => candidate.source === defaultSource) ||
    candidates.find((candidate) => candidate.source === "current") ||
    candidates[0] ||
    null
  );
}

function rubyTitle(target, candidate) {
  const reading = candidate?.reading ? ` / ${candidate.reading}` : " / no ruby";
  return `${target.surface}${reading}`;
}

function cycleYomiTarget(item, target, currentCandidate) {
  const candidates = target.candidates || [];
  if (candidates.length === 0) {
    return;
  }
  const currentIndex = Math.max(
    candidates.findIndex((candidate) => candidate.source === currentCandidate?.source),
    0
  );
  const next = candidates[(currentIndex + 1) % candidates.length];
  const draft = ensureYomiOverride(item.item_id);
  if (next.source === "current" && (target.default_choice_source || "current") === "current") {
    delete draft.targets[target.item_id];
    cleanupYomiOverride(item.item_id);
  } else {
    draft.targets[target.item_id] = {
      choice_source: next.source,
      selected_reading: next.reading ?? null,
      custom_reading: null,
    };
  }
  touchDraft();
  render();
}

function ensureYomiOverride(itemId) {
  if (!state.currentDraft.overrides[itemId]) {
    state.currentDraft.overrides[itemId] = { skip: false, targets: {}, span_overrides: {}, note: "" };
  }
  if (!state.currentDraft.overrides[itemId].targets) {
    state.currentDraft.overrides[itemId].targets = {};
  }
  if (!state.currentDraft.overrides[itemId].span_overrides) {
    state.currentDraft.overrides[itemId].span_overrides = {};
  }
  return state.currentDraft.overrides[itemId];
}

function cleanupYomiOverride(itemId) {
  const draft = state.currentDraft.overrides[itemId];
  if (!draft) {
    return;
  }
  const hasTargets = Object.keys(draft.targets || {}).length > 0;
  const hasSpanOverrides = Object.keys(draft.span_overrides || {}).length > 0;
  if (!hasTargets && !hasSpanOverrides && !draft.skip && !draft.note) {
    delete state.currentDraft.overrides[itemId];
  }
}

function renderSubmissionPreview() {
  if (!isEditable()) {
    el.submissionPreview.value =
      "Archived pack. Submission export is disabled for read-only history views.";
    return;
  }
  const payload = buildSubmissionPayload();
  el.submissionPreview.value = JSON.stringify(payload, null, 2);
}

function renderControlState() {
  const editable = isEditable();
  el.clearRange.disabled = !editable;
  el.resetDraft.disabled = !editable;
  el.copyJson.disabled = !editable;
  el.downloadJson.disabled = !editable;
}

function buildSubmissionPayload() {
  const pack = state.currentPack;
  const { fromSeq, toSeq } = getEffectiveRange();
  const reviewer = el.reviewerName.value.trim();
  const overrides = getSubmissionOverridesForCurrentStage();
  const now = Date.now();

  return {
    schema_version: submissionSchemaVersion,
    submission_type: "review_patch",
    review_stage: pack.review_stage,
    pack_id: pack.pack_id,
    submission_id: `${pack.pack_id}__${new Date(now).toISOString()}`,
    reviewer,
    generated_at_epoch: Math.floor(now / 1000),
    reviewed_ranges: pack.item_count > 0 ? [{ from_seq: fromSeq, to_seq: toSeq }] : [],
    overrides,
  };
}

function getSubmissionOverridesForCurrentStage() {
  const pack = state.currentPack;
  if (pack.review_stage === "yomi_final_review") {
    return getActiveYomiOverrides();
  }
  if (pack.review_stage === "yomi_strong_repair_review") {
    return getActiveStrongRepairOverrides();
  }
  return getActiveOverrides().map((item) => ({
    item_id: item.item_id,
    decision: item.decision,
    ...(item.note ? { note: item.note } : {}),
  }));
}

function getActiveYomiOverrides() {
  const { fromSeq, toSeq } = getEffectiveRange();
  return Object.entries(state.currentDraft.overrides)
    .map(([itemId, override]) => {
      const item = state.currentPack.items.find((row) => row.item_id === itemId);
      if (!item || item.seq < fromSeq || item.seq > toSeq) {
        return null;
      }
      return {
        item_id: itemId,
        ...(typeof override.skip === "boolean" ? { skip: override.skip } : {}),
        targets: Object.entries(override.targets || {}).map(([targetItemId, target]) => ({
          item_id: targetItemId,
          choice_source: target.choice_source,
          selected_reading: target.selected_reading ?? null,
        })),
        span_overrides: Object.values(override.span_overrides || {}).map((span) => ({
          id: span.id,
          decision: span.decision,
          target_item_ids: span.target_item_ids || [],
          original_surface: span.original_surface,
          segments: (span.segments || []).map((segment) => ({
            surface: segment.surface,
            reading: segment.reading || "",
          })),
        })),
        ...(override.note ? { note: String(override.note).trim() } : {}),
      };
    })
    .filter(Boolean)
    .filter(
      (row) => row.targets.length > 0 || row.span_overrides.length > 0 || "skip" in row || row.note
    );
}

function getActiveOverrides() {
  const { fromSeq, toSeq } = getEffectiveRange();
  return Object.entries(state.currentDraft.overrides)
    .map(([itemId, override]) => {
      const item = state.currentPack.items.find((row) => row.item_id === itemId);
      if (!item) {
        return null;
      }
      if (item.seq < fromSeq || item.seq > toSeq) {
        return null;
      }
      return {
        item_id: itemId,
        decision: override.decision,
        note: (override.note || "").trim(),
      };
    })
    .filter(Boolean);
}

function getActiveStrongRepairOverrides() {
  const { fromSeq, toSeq } = getEffectiveRange();
  return Object.entries(state.currentDraft.overrides)
    .map(([itemId, override]) => {
      const item = state.currentPack.items.find((row) => row.item_id === itemId);
      if (!item || item.seq < fromSeq || item.seq > toSeq) {
        return null;
      }
      const row = {
        item_id: itemId,
        decision: override.decision || "accept",
        ...(override.note ? { note: String(override.note).trim() } : {}),
      };
      if (override.manual_segments?.length) {
        row.manual_segments = override.manual_segments.map((segment) => ({
          surface: segment.surface || "",
          reading: segment.reading || "",
        }));
      }
      return row;
    })
    .filter(Boolean)
    .filter(
      (row) =>
        row.decision === "reject" ||
        row.note ||
        (row.manual_segments && row.manual_segments.length > 0)
    );
}

function getEffectiveRange() {
  const itemCount = state.currentPack?.item_count || 0;
  if (itemCount === 0) {
    return { fromSeq: 0, toSeq: 0, includedCount: 0 };
  }
  let fromSeq = state.currentDraft?.from_seq ?? 1;
  let toSeq = state.currentDraft?.to_seq ?? itemCount;
  fromSeq = clamp(fromSeq, 1, itemCount);
  toSeq = clamp(toSeq, 1, itemCount);
  if (fromSeq > toSeq) {
    [fromSeq, toSeq] = [toSeq, fromSeq];
  }
  return { fromSeq, toSeq, includedCount: toSeq - fromSeq + 1 };
}

function isEditable() {
  return String(state.currentPackMeta?.status || "").startsWith("active");
}

function createEmptyDraft(pack) {
  return {
    schema_version: 1,
    review_stage: pack.review_stage,
    pack_id: pack.pack_id,
    from_seq: null,
    to_seq: null,
    overrides: {},
    updated_at_epoch: null,
  };
}

function loadDraft(pack) {
  const key = draftStorageKey(pack.review_stage, pack.pack_id);
  const raw = window.localStorage.getItem(key);
  if (!raw) {
    return createEmptyDraft(pack);
  }
  try {
    const parsed = JSON.parse(raw);
    return {
      ...createEmptyDraft(pack),
      ...parsed,
      overrides: parsed.overrides || {},
    };
  } catch {
    return createEmptyDraft(pack);
  }
}

function touchDraft() {
  state.currentDraft.updated_at_epoch = Math.floor(Date.now() / 1000);
  saveDraft();
}

function saveDraft() {
  const key = draftStorageKey(state.currentPack.review_stage, state.currentPack.pack_id);
  window.localStorage.setItem(key, JSON.stringify(state.currentDraft));
}

function draftStorageKey(reviewStage, packId) {
  return `yomi-corpus:draft:${reviewStage}:${packId}:v1`;
}

function loadSettings() {
  try {
    const raw = window.localStorage.getItem(settingsKey);
    if (!raw) {
      return;
    }
    const parsed = JSON.parse(raw);
    el.reviewerName.value = parsed.reviewer_name || "";
  } catch {
    // ignore
  }
}

function saveSettings() {
  window.localStorage.setItem(
    settingsKey,
    JSON.stringify({
      reviewer_name: el.reviewerName.value.trim(),
    })
  );
}

function updateLocation(stageId, packId) {
  const params = new URLSearchParams(window.location.search);
  params.set("stage", stageId);
  params.set("pack", packId);
  window.history.replaceState({}, "", `${window.location.pathname}?${params.toString()}`);
}

function formatConfidenceCounts(counts) {
  const entries = Object.entries(counts || {});
  if (entries.length === 0) {
    return "None";
  }
  return entries.map(([key, value]) => `${key}:${value}`).join(", ");
}

function formatDate(epochSeconds) {
  if (!epochSeconds) {
    return "Unknown";
  }
  return new Date(epochSeconds * 1000).toLocaleString();
}

function showStatus(message, isError = false) {
  el.statusBanner.textContent = message;
  el.statusBanner.classList.remove("hidden");
  el.statusBanner.style.color = isError ? "var(--danger)" : "var(--warning)";
}

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status} for ${url}`);
  }
  return response.json();
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}
