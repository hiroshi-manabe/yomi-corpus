const manifestUrl = "./manifest.json";
const submissionSchemaVersion = 1;
const githubNewIssueUrl = "https://github.com/hiroshi-manabe/yomi-corpus/issues/new";

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
  taskPickerPanel: document.querySelector("#task-picker-panel"),
  taskDocList: document.querySelector("#task-doc-list"),
  taskDraftList: document.querySelector("#task-draft-list"),
  taskSummary: document.querySelector("#task-summary"),
  selectAllDocs: document.querySelector("#select-all-docs"),
  clearDocSelection: document.querySelector("#clear-doc-selection"),
  startTask: document.querySelector("#start-task"),
  backToTaskPicker: document.querySelector("#back-to-task-picker"),
  completeTask: document.querySelector("#complete-task"),
  taskWorkPanels: document.querySelectorAll(".task-work-panel"),
  rangeSummary: document.querySelector("#range-summary"),
  itemsContainer: document.querySelector("#items-container"),
  itemsSummary: document.querySelector("#items-summary"),
  statusBanner: document.querySelector("#status-banner"),
  submissionPreview: document.querySelector("#submission-preview"),
  issueUrlSummary: document.querySelector("#issue-url-summary"),
  openLatest: document.querySelector("#open-latest"),
  clearRange: document.querySelector("#clear-range"),
  resetDraft: document.querySelector("#reset-draft"),
  openIssueTitle: document.querySelector("#open-issue-title"),
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

  el.selectAllDocs.addEventListener("click", () => {
    if (!isEditable()) {
      return;
    }
    selectAllDocumentTasks();
  });

  el.clearDocSelection.addEventListener("click", () => {
    if (!isEditable()) {
      return;
    }
    clearTaskSelection();
  });

  el.startTask.addEventListener("click", () => {
    if (!isEditable()) {
      return;
    }
    startReviewTask();
  });

  el.backToTaskPicker.addEventListener("click", () => {
    if (!isEditable()) {
      return;
    }
    deferCurrentTask();
  });

  el.completeTask.addEventListener("click", () => {
    if (!isEditable()) {
      return;
    }
    completeCurrentTask();
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
      showStatus("Submission JSON copied. Open an issue and paste it into the body.");
    } catch (error) {
      el.submissionPreview.focus();
      el.submissionPreview.select();
      showStatus("Clipboard copy failed. The JSON text is selected; copy it manually, then open an issue.", true);
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

  el.openIssueTitle.addEventListener("click", () => {
    const urls = buildIssueUrls();
    openUrlInNewTab(urls.issue.url);
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
  renderTaskSelector();
  renderRangeSummary();
  renderItems();
  renderControlState();
  renderSubmissionPreview();
}

function renderCurrentTracks() {
  const currentTracks = state.manifest.current_tracks || {};
  el.currentTrackList.innerHTML = "";
  const cards = [];
  if (currentTracks.dev) {
    cards.push({ ...currentTracks.dev, track_name: "dev", emphasis: "secondary-track" });
  }

  if (cards.length === 0) {
    const p = document.createElement("p");
    p.className = "muted";
    p.textContent = "No active dev review packs were published.";
    el.currentTrackList.append(p);
    return;
  }

  for (const card of cards) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `track-card ${card.emphasis}`;
    button.innerHTML = `
      <div class="track-card-header">
        <strong>Dev Review</strong>
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
      <div class="pack-meta-line">${pack.item_count} item(s) · ${escapeHtml(pack.track_name || "dev")}</div>
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
  const trackName = packMeta.track_name || "dev";
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

function renderTaskSelector() {
  if (!el.taskPickerPanel || !el.taskDocList || !el.taskSummary) {
    return;
  }
  const docs = buildDocumentTasks(state.currentPack);
  const task = normalizeTask(state.currentDraft.task, state.currentPack);
  const editable = isEditable();
  const started = isTaskStarted();

  el.taskPickerPanel.classList.toggle("hidden", !editable || started);
  el.taskWorkPanels.forEach((panel) => {
    panel.classList.toggle("hidden", editable && !started);
  });
  if (!editable) {
    el.taskSummary.textContent = "Archived packs are read-only.";
    return;
  }

  renderSavedTaskDrafts(docs);
  el.taskDocList.innerHTML = "";
  for (const doc of docs) {
    el.taskDocList.append(renderTaskDocumentRow(doc, task));
  }
  const selectedCount = task.doc_ids.length;
  const selectedItems = itemsForTask(task);
  el.taskSummary.textContent =
    selectedCount > 0
      ? `${selectedCount} document(s), ${selectedItems.length} item(s) selected.`
      : "Choose documents, then start a review task.";
  el.startTask.disabled = docs.length === 0 || selectedCount === 0;
  el.clearDocSelection.disabled = selectedCount === 0;
  el.selectAllDocs.disabled = docs.length === 0 || selectedCount === docs.length;
}

function renderSavedTaskDrafts(docs) {
  if (!el.taskDraftList) {
    return;
  }
  const savedTasks = listSavedTaskDrafts();
  el.taskDraftList.innerHTML = "";
  if (savedTasks.length === 0) {
    return;
  }

  const heading = document.createElement("div");
  heading.className = "task-draft-heading muted";
  heading.textContent = "Deferred local tasks";
  el.taskDraftList.append(heading);

  for (const record of savedTasks) {
    const row = document.createElement("article");
    row.className = "task-draft-row";

    const body = document.createElement("div");
    body.className = "task-draft-body";
    const title = document.createElement("strong");
    title.textContent = record.task_label || record.task_id || "Deferred Task";
    const meta = document.createElement("div");
    meta.className = "task-draft-meta";
    meta.textContent = formatTaskDraftMeta(record, docs);
    body.append(title, meta);

    const button = document.createElement("button");
    button.type = "button";
    button.textContent = `Return to ${record.task_label || "Task"}`;
    button.addEventListener("click", () => {
      resumeTaskDraft(record.task_id);
    });

    row.append(body, button);
    el.taskDraftList.append(row);
  }
}

function renderTaskDocumentRow(doc, task) {
  const row = document.createElement("article");
  row.className = "task-doc-row";
  row.classList.toggle("selected", task.doc_ids.includes(doc.doc_id));

  const label = document.createElement("label");
  label.className = "task-doc-check";
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = task.doc_ids.includes(doc.doc_id);
  checkbox.addEventListener("change", () => {
    toggleDocumentTask(doc.doc_id, checkbox.checked);
  });
  const title = document.createElement("span");
  title.className = "task-doc-title";
  title.textContent = `Doc ${doc.doc_seq}`;
  label.append(checkbox, title);

  const meta = document.createElement("div");
  meta.className = "task-doc-meta";
  const itemSeq = doc.item_count > 0 ? `seq ${doc.from_seq}-${doc.to_seq}` : "no review cards";
  meta.textContent =
    `${doc.item_count} item(s) · ${doc.unresolved_count} review target(s) · ${doc.unit_count || 0} sentence(s) · ${itemSeq}`;

  const preview = document.createElement("div");
  preview.className = "task-doc-preview";
  preview.textContent = doc.preview;

  const actions = document.createElement("div");
  actions.className = "task-doc-actions";
  for (const [labelText, handler] of [
    ["From", () => setDocumentRangeBoundary(doc.doc_id, "start")],
    ["To", () => setDocumentRangeBoundary(doc.doc_id, "end")],
    ["Only", () => selectOnlyDocumentTask(doc.doc_id)],
  ]) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary-button";
    button.textContent = labelText;
    button.addEventListener("click", handler);
    actions.append(button);
  }

  row.append(label, meta, preview, actions);
  return row;
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
  const visibleItems = getVisibleItems();
  el.itemsSummary.textContent = `${visibleItems.length} shown / ${pack.items.length} total item(s)`;
  el.itemsContainer.innerHTML = "";

  let lastDocId = null;
  for (const item of visibleItems) {
    if (item.doc_id && item.doc_id !== lastDocId) {
      el.itemsContainer.append(renderDocumentSeparator(item));
      lastDocId = item.doc_id;
    }
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

function renderDocumentSeparator(item) {
  const separator = document.createElement("div");
  separator.className = "document-separator";
  const left = document.createElement("strong");
  left.textContent = `Document ${item.doc_seq || ""}`;
  const right = document.createElement("span");
  right.textContent = item.doc_id || "";
  separator.append(left, right);
  return separator;
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
  title.textContent = item.text || item.rejected_span || item.item_id;
  titleRow.append(seq, title);

  const badges = document.createElement("div");
  badges.className = "item-badges";
  const statusBadge = document.createElement("span");
  statusBadge.className = "badge proposed-badge";
  statusBadge.textContent = item.region_count
    ? `${item.region_count} region(s)`
    : item.repair_status || "pending";
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
    ["Rejected", strongRepairRegions(item).map(formatRejectedReadings).join(" | ")],
    ["Proposal", strongRepairRegions(item).map((region) => formatRepairProposal(region.llm_parsed || [])).join(" | ")],
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
      cleanupStrongRepairOverride(item.item_id);
      touchDraft();
      if (!state.currentDraft.overrides[item.item_id]) {
        render();
      } else {
        renderSubmissionPreview();
      }
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
  const matches = [];
  for (const region of strongRepairRegions(item)) {
    const span = region.rejected_span || "";
    const match = span ? findRenderedTokenSpan(tokens, span) : null;
    if (match) {
      matches.push({ ...match, region });
    }
  }
  matches.sort((left, right) => left.start - right.start || left.end - right.end);
  const usableMatches = [];
  let cursor = -1;
  for (const match of matches) {
    if (match.start >= cursor) {
      usableMatches.push(match);
      cursor = match.end;
    }
  }
  if (!usableMatches.length) {
    return renderReadonlyRubyFromRendered(item.rendered_yomi_after || "");
  }
  const nodes = [];
  const byStart = new Map(usableMatches.map((match) => [match.start, match]));
  for (let index = 0; index < tokens.length; index += 1) {
    const match = byStart.get(index);
    if (match) {
      nodes.push(renderStrongRepairSpanEditor(item, match.region, override, editable));
      index = match.end - 1;
      continue;
    }
    nodes.push(...renderReadonlyRubyFromToken(item, tokens[index], index));
  }
  return nodes;
}

function strongRepairRegions(item) {
  return item.regions?.length ? item.regions : [item];
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

function renderStrongRepairSpanEditor(item, region, override, editable) {
  const regionOverride = strongRepairRegionOverride(override, region);
  const manualSegments = regionOverride?.manual_segments || null;
  const segments = manualSegments?.length ? manualSegments : defaultStrongRepairSegments(region);
  const wrapper = document.createElement("span");
  wrapper.className = "strong-repair-span-editor";
  wrapper.classList.toggle("changed", Boolean(manualSegments?.length));

  const preview = document.createElement("span");
  preview.className = "strong-repair-span-preview";
  preview.append(...renderStrongRepairSegmentRuby(item, region, segments, editable));
  wrapper.append(preview);

  if (!editable) {
    return wrapper;
  }

  const editor = document.createElement("span");
  editor.className = "span-editor strong-repair-boundary-editor";
  editor.append(renderStrongRepairSplitControls(item, region, segments));

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
      const current = ensureStrongRepairRegionOverride(item.item_id, region.region_id || region.item_id);
      const currentSegments = current.manual_segments?.length
        ? current.manual_segments
        : defaultStrongRepairSegments(region);
      const hadManualSegments = Boolean(current.manual_segments?.length);
      currentSegments[index].reading = input.value;
      currentSegments[index].edited = true;
      setStrongRepairManualSegments(item, region, currentSegments);
      touchDraft();
      const nextRegion = strongRepairRegionOverride(state.currentDraft.overrides[item.item_id], region);
      if (hadManualSegments !== Boolean(nextRegion?.manual_segments?.length)) {
        render();
      } else {
        renderSubmissionPreview();
      }
    });
    label.append(surface, input);
    fields.append(label);
  }
  editor.append(fields);
  wrapper.append(editor);
  return wrapper;
}

function strongRepairRegionOverride(override, region) {
  const regionId = region.region_id || region.item_id;
  if (!override || !regionId) {
    return null;
  }
  return override.regions?.[regionId] || null;
}

function renderStrongRepairSegmentRuby(item, region, segments, editable) {
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
      button.addEventListener("click", () => cycleStrongRepairSegmentReading(item, region, index));
    }
    nodes.push(button);
  }
  return nodes;
}

function cycleStrongRepairSegmentReading(item, region, index) {
  const current = ensureStrongRepairRegionOverride(item.item_id, region.region_id || region.item_id);
  const segments = current.manual_segments?.length
    ? current.manual_segments
    : defaultStrongRepairSegments(region);
  const segment = segments[index];
  if (!segment) {
    return;
  }
  const candidates = strongRepairReadingCycleCandidates(region, segment.surface || "");
  const values = [...candidates, ""];
  const currentIndex = values.indexOf(segment.reading || "");
  const next = values[(currentIndex + 1) % values.length];
  segments[index] = {
    ...segment,
    reading: next,
    edited: true,
  };
  setStrongRepairManualSegments(item, region, segments);
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

function renderStrongRepairSplitControls(item, region, segments) {
  const wrap = document.createElement("span");
  wrap.className = "split-controls";
  const chars = Array.from(region.rejected_span || "");
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
      button.addEventListener("click", () => updateStrongRepairSplit(item, region, boundaryIndex));
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

function updateStrongRepairSplit(item, region, boundaryIndex) {
  const current = ensureStrongRepairRegionOverride(item.item_id, region.region_id || region.item_id);
  const previousSegments = current.manual_segments?.length
    ? current.manual_segments
    : defaultStrongRepairSegments(region);
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
  const chars = Array.from(region.rejected_span || "");
  let start = 0;
  const nextSegments = [];
  for (const end of [...ordered, chars.length]) {
    const surface = chars.slice(start, end).join("");
    nextSegments.push({
      surface,
      reading: defaultStrongRepairReadingForSegment(region, surface, previousSegments),
      edited: false,
    });
    start = end;
  }
  setStrongRepairManualSegments(item, region, nextSegments);
  touchDraft();
  render();
}

function ensureStrongRepairOverride(itemId) {
  const current = state.currentDraft.overrides[itemId] || {};
  state.currentDraft.overrides[itemId] = {
    decision: "accept",
    note: current.note || "",
    regions: current.regions || {},
  };
  return state.currentDraft.overrides[itemId];
}

function ensureStrongRepairRegionOverride(itemId, regionId) {
  const current = ensureStrongRepairOverride(itemId);
  if (!current.regions) {
    current.regions = {};
  }
  if (!current.regions[regionId]) {
    current.regions[regionId] = { region_id: regionId, manual_segments: null };
  }
  return current.regions[regionId];
}

function setStrongRepairManualSegments(item, region, segments) {
  const current = ensureStrongRepairOverride(item.item_id);
  const regionId = region.region_id || region.item_id;
  if (!current.regions) {
    current.regions = {};
  }
  const normalized = normalizeStrongRepairSegments(segments);
  if (strongRepairSegmentsEqual(normalized, defaultStrongRepairSegments(region))) {
    delete current.regions[regionId];
  } else {
    current.regions[regionId] = {
      region_id: regionId,
      manual_segments: normalized,
    };
  }
  cleanupStrongRepairOverride(item.item_id);
}

function cleanupStrongRepairOverride(itemId) {
  const current = state.currentDraft.overrides[itemId];
  if (!current) {
    return;
  }
  const note = String(current.note || "").trim();
  if (note) {
    current.note = note;
    return;
  }
  if (Object.keys(current.regions || {}).length > 0) {
    return;
  }
  delete state.currentDraft.overrides[itemId];
}

function normalizeStrongRepairSegments(segments) {
  return (segments || [])
    .filter((segment) => segment && segment.surface)
    .map((segment) => ({
      surface: String(segment.surface || ""),
      reading: String(segment.reading || ""),
      ...(segment.edited ? { edited: true } : {}),
    }));
}

function strongRepairSegmentsEqual(left, right) {
  const normalizedLeft = normalizeStrongRepairSegments(left);
  const normalizedRight = normalizeStrongRepairSegments(right);
  if (normalizedLeft.length !== normalizedRight.length) {
    return false;
  }
  return normalizedLeft.every(
    (segment, index) =>
      segment.surface === normalizedRight[index].surface &&
      segment.reading === normalizedRight[index].reading
  );
}

function defaultStrongRepairSegments(region) {
  const parsed = region.llm_parsed || [];
  if (parsed.length) {
    return parsed
      .filter((row) => row && row.surface)
      .map((row) => ({
        surface: String(row.surface || ""),
        reading: katakanaToHiragana(String(row.reading || "")),
      }));
  }
  const replacement = region.repair_log?.replacement || [];
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
      surface: region.rejected_span || "",
      reading: "",
    },
  ];
}

function defaultStrongRepairReadingForSegment(region, surface, previousSegments) {
  const previous = (previousSegments || []).find(
    (segment) => segment.surface === surface && segment.reading
  );
  if (previous) {
    return previous.reading;
  }
  for (const row of region.llm_parsed || []) {
    if (row?.surface === surface && row.reading) {
      return katakanaToHiragana(String(row.reading));
    }
  }
  for (const row of region.repair_log?.replacement || []) {
    if (row?.surface === surface && row.reading) {
      return katakanaToHiragana(String(row.reading));
    }
  }
  const readingCandidates = region.reading_candidates?.[surface] || [];
  if (readingCandidates.length) {
    return readingCandidates[0];
  }
  const readingHint = region.reading_hints?.[surface];
  if (readingHint) {
    return readingHint;
  }
  const targetReading = readingFromStrongRepairTargets(region.target_escalations || [], surface);
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
  const segments = item.ruby_segments || [{ type: "text", text: item.text || "" }];
  for (let index = 0; index < segments.length; index += 1) {
    const segment = segments[index];
    if (segment.type !== "ruby") {
      nodes.push(...renderYomiTextSegmentWithNumericMerge(item, segment, segments[index - 1], segments[index + 1], override, editable, targetsById));
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

function renderYomiTextSegmentWithNumericMerge(
  item,
  segment,
  previousSegment,
  nextSegment,
  override,
  editable,
  targetsById,
) {
  const text = segment.text || "";
  const nodes = [];
  const previousTarget = targetForRubySegment(previousSegment, targetsById);
  const nextTarget = targetForRubySegment(nextSegment, targetsById);
  const previousNoRuby = previousTarget && isNoRubyTarget(previousTarget, override);
  const nextNoRuby = nextTarget && isNoRubyTarget(nextTarget, override);
  const previousMergeEligible = previousNoRuby || isNumericMergeEligibleTarget(previousTarget);
  const nextMergeEligible = nextNoRuby || isNumericMergeEligibleTarget(nextTarget);
  let remaining = text;

  const trailing = nextMergeEligible ? remaining.match(/([0-9０-９]+)$/)?.[1] || "" : "";
  const leading = previousMergeEligible ? remaining.match(/^([0-9０-９]+)/)?.[1] || "" : "";
  if (trailing && trailing.length < remaining.length) {
    nodes.push(document.createTextNode(remaining.slice(0, -trailing.length)));
    remaining = trailing;
  }
  if (trailing && nextTarget) {
    nodes.push(renderNumericMergeButton(item, nextTarget, trailing, "before", override, editable));
    remaining = "";
  }
  if (leading && previousTarget) {
    nodes.push(renderNumericMergeButton(item, previousTarget, leading, "after", override, editable));
    remaining = remaining.slice(leading.length);
  }
  if (remaining) {
    nodes.push(document.createTextNode(remaining));
  }
  return nodes;
}

function targetForRubySegment(segment, targetsById) {
  if (!segment || segment.type !== "ruby") {
    return null;
  }
  return targetsById[segment.target_item_id] || null;
}

function isNoRubyTarget(target, override) {
  const targetDraft = override?.targets?.[target.item_id] || null;
  return selectedCandidate(target, targetDraft)?.source === "none";
}

function isNumericMergeEligibleTarget(target) {
  if (!target || !hasNoRubyCandidate(target)) {
    return false;
  }
  return /[A-Za-z]/.test(target.surface || "");
}

function hasNoRubyCandidate(target) {
  return (target?.candidates || []).some((candidate) => candidate.source === "none");
}

function renderNumericMergeButton(item, target, digits, side, override, editable) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = "ruby-token numeric-merge-token";
  const span = numericMergeSpanDraft(target, digits, side);
  const active = Boolean(override?.span_overrides?.[span.id]);
  button.classList.toggle("changed", active);
  button.disabled = !editable;
  button.title = active
    ? "Numeric merge is active; tap to clear."
    : "Merge this number with the no-ruby target for strong repair.";
  button.textContent = digits;
  if (editable) {
    button.addEventListener("click", () => toggleNumericMergeSpan(item, span));
  }
  return button;
}

function numericMergeSpanDraft(target, digits, side) {
  const originalSurface = side === "before"
    ? `${digits}${target.surface || ""}`
    : `${target.surface || ""}${digits}`;
  return {
    id: `numeric-merge:${target.item_id}:${side}:${digits}`,
    decision: "segmentation",
    target_item_ids: [target.item_id],
    original_surface: originalSurface,
    segments: [{ surface: originalSurface, reading: "" }],
    repair_required: true,
    repair_reason: "numeric_merge_no_reading",
  };
}

function toggleNumericMergeSpan(item, span) {
  const draft = ensureYomiOverride(item.item_id);
  if (!draft.span_overrides) {
    draft.span_overrides = {};
  }
  if (draft.span_overrides[span.id]) {
    delete draft.span_overrides[span.id];
  } else {
    for (const targetItemId of span.target_item_ids || []) {
      draft.targets[targetItemId] = {
        choice_source: "none",
        selected_reading: null,
        custom_reading: null,
      };
    }
    draft.span_overrides[span.id] = span;
  }
  cleanupYomiOverride(item.item_id);
  touchDraft();
  render();
}

function buildYomiSpanGroups(item) {
  return [];
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

  if (candidate?.ruby_nodes?.length) {
    button.append(...renderRubyDisplayNodes(candidate.ruby_nodes));
  } else if (candidate?.reading) {
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
  if (targetDraft?.choice_source) {
    return candidateForSource(target, targetDraft.choice_source);
  }
  return defaultCandidate(target);
}

function candidateForSource(target, source) {
  const candidates = target.candidates || [];
  return candidates.find((candidate) => candidate.source === source) || candidates[0] || null;
}

function defaultCandidate(target) {
  const candidates = target.candidates || [];
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
  if (next.source === defaultCandidate(target)?.source) {
    delete draft.targets[target.item_id];
    cleanupYomiOverride(item.item_id);
  } else {
    draft.targets[target.item_id] = {
      choice_source: next.source,
      selected_reading: next.reading ?? null,
      custom_reading: null,
    };
  }
  if (next.source !== "none") {
    removeNumericMergeSpansForTarget(draft, target.item_id);
    cleanupYomiOverride(item.item_id);
  }
  touchDraft();
  render();
}

function removeNumericMergeSpansForTarget(draft, targetItemId) {
  for (const [spanId, span] of Object.entries(draft.span_overrides || {})) {
    if (
      span?.repair_reason === "numeric_merge_no_reading" &&
      (span.target_item_ids || []).includes(targetItemId)
    ) {
      delete draft.span_overrides[spanId];
    }
  }
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
    renderIssueUrlSummary(null);
    return;
  }
  const payload = buildSubmissionPayload();
  el.submissionPreview.value = JSON.stringify(payload, null, 2);
  renderIssueUrlSummary(buildIssueUrls(payload), payload);
}

function renderControlState() {
  const editable = isEditable();
  const started = isTaskStarted();
  el.backToTaskPicker.disabled = !editable || !started;
  el.completeTask.disabled = !editable || !started;
  el.clearRange.disabled = !editable;
  el.resetDraft.disabled = !editable;
  el.openIssueTitle.disabled = !editable;
  el.copyJson.disabled = !editable;
  el.downloadJson.disabled = !editable;
}

function buildIssueUrls(payload = buildSubmissionPayload()) {
  const title = buildIssueTitle(payload);
  const issueUrl = buildGithubIssueUrl(title);
  return {
    issue: {
      url: issueUrl,
      length: issueUrl.length,
      enabled: true,
    },
  };
}

function buildGithubIssueUrl(title) {
  const params = new URLSearchParams();
  params.set("title", title);
  return `${githubNewIssueUrl}?${params.toString()}`;
}

function openUrlInNewTab(url) {
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.target = "_blank";
  anchor.rel = "noopener noreferrer";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

function buildIssueTitle(payload) {
  const packId = payload.pack_id || "review";
  const ranges = payload.reviewed_ranges || [];
  const range = ranges.length === 1
    ? formatSeqRange(ranges[0].from_seq, ranges[0].to_seq)
    : `${ranges.length} ranges`;
  const task = payload.task?.mode === "documents" ? ` docs ${formatDocSeqs(payload.task.doc_seqs || [])}` : "";
  return `[yomi-review] ${packId}${task} ${range}`;
}

function formatSeqRange(fromSeq, toSeq) {
  return fromSeq === toSeq ? `seq ${fromSeq}` : `seq ${fromSeq}-${toSeq}`;
}

function formatDocSeqs(docSeqs) {
  if (!docSeqs.length) {
    return "";
  }
  const sorted = [...docSeqs].sort((a, b) => a - b);
  const ranges = [];
  let start = sorted[0];
  let end = sorted[0];
  for (const seq of sorted.slice(1)) {
    if (seq === end + 1) {
      end = seq;
      continue;
    }
    ranges.push(start === end ? String(start) : `${start}-${end}`);
    start = seq;
    end = seq;
  }
  ranges.push(start === end ? String(start) : `${start}-${end}`);
  return ranges.join(",");
}

function renderIssueUrlSummary(urls, payload = null) {
  if (!el.issueUrlSummary) {
    return;
  }
  if (!urls) {
    el.issueUrlSummary.textContent = "Issue export is disabled for read-only history views.";
    return;
  }
  const jsonLength = payload ? JSON.stringify(payload, null, 2).length : 0;
  el.issueUrlSummary.textContent =
    `Issue URL: ${urls.issue.length} chars. Copy JSON separately (${jsonLength} chars).`;
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
    task: buildSubmissionTaskMetadata(),
    reviewed_ranges: buildReviewedRanges(),
    overrides,
  };
}

function buildSubmissionTaskMetadata() {
  const task = normalizeTask(state.currentDraft.task, state.currentPack);
  if (task.mode !== "documents") {
    return { mode: "full_pack" };
  }
  const docs = buildDocumentTasks(state.currentPack).filter((row) => task.doc_ids.includes(row.doc_id));
  return {
    mode: "documents",
    task_id: state.currentDraft.active_task_id || null,
    task_label: state.currentDraft.active_task_label || null,
    doc_ids: docs.map((doc) => doc.doc_id),
    doc_seqs: docs.map((doc) => doc.doc_seq),
    doc_ranges: buildReviewedDocumentRanges(docs),
    item_count: itemsForTask(task).length,
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
  return Object.entries(state.currentDraft.overrides)
    .map(([itemId, override]) => {
      const item = state.currentPack.items.find((row) => row.item_id === itemId);
      if (!item || !isItemIncludedInSubmission(item)) {
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
          ...(span.repair_required ? { repair_required: true } : {}),
          ...(span.repair_reason ? { repair_reason: span.repair_reason } : {}),
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
  return Object.entries(state.currentDraft.overrides)
    .map(([itemId, override]) => {
      const item = state.currentPack.items.find((row) => row.item_id === itemId);
      if (!item) {
        return null;
      }
      if (!isItemIncludedInSubmission(item)) {
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
  return Object.entries(state.currentDraft.overrides)
    .map(([itemId, override]) => {
      const item = state.currentPack.items.find((row) => row.item_id === itemId);
      if (!item || !isItemIncludedInSubmission(item)) {
        return null;
      }
      const row = {
        item_id: itemId,
        decision: override.decision || "accept",
        ...(override.note ? { note: String(override.note).trim() } : {}),
      };
      const regions = Object.values(override.regions || {})
        .filter((region) => region?.region_id && region.manual_segments?.length)
        .map((region) => ({
          region_id: region.region_id,
          manual_segments: region.manual_segments.map((segment) => ({
            surface: segment.surface || "",
            reading: segment.reading || "",
          })),
        }));
      if (regions.length) {
        row.regions = regions;
      }
      return row;
    })
    .filter(Boolean)
    .filter(
      (row) =>
        row.decision === "reject" ||
        row.note ||
        (row.regions && row.regions.length > 0)
    );
}

function getEffectiveRange() {
  const itemCount = state.currentPack?.item_count || 0;
  if (itemCount === 0) {
    return { fromSeq: 0, toSeq: 0, includedCount: 0 };
  }
  const base = getTaskRange();
  let fromSeq = state.currentDraft?.from_seq ?? base.fromSeq;
  let toSeq = state.currentDraft?.to_seq ?? base.toSeq;
  fromSeq = clamp(fromSeq, base.fromSeq, base.toSeq);
  toSeq = clamp(toSeq, base.fromSeq, base.toSeq);
  if (fromSeq > toSeq) {
    [fromSeq, toSeq] = [toSeq, fromSeq];
  }
  const includedCount = getVisibleItems().filter(
    (item) => item.seq >= fromSeq && item.seq <= toSeq
  ).length;
  return { fromSeq, toSeq, includedCount };
}

function getTaskRange() {
  const selected = getVisibleItems();
  if (selected.length > 0) {
    return {
      fromSeq: Math.min(...selected.map((item) => item.seq)),
      toSeq: Math.max(...selected.map((item) => item.seq)),
    };
  }
  const itemCount = state.currentPack?.item_count || 0;
  return { fromSeq: itemCount ? 1 : 0, toSeq: itemCount };
}

function getVisibleItems() {
  const task = normalizeTask(state.currentDraft?.task, state.currentPack);
  return itemsForTask(task);
}

function itemsForTask(task) {
  const items = state.currentPack?.items || [];
  if (task.mode !== "documents" || task.doc_ids.length === 0) {
    return items;
  }
  const docIds = new Set(task.doc_ids);
  return items.filter((item) => docIds.has(item.doc_id));
}

function isItemIncludedInSubmission(item) {
  const { fromSeq, toSeq } = getEffectiveRange();
  return (
    item.seq >= fromSeq &&
    item.seq <= toSeq &&
    getVisibleItems().some((row) => row.item_id === item.item_id)
  );
}

function buildReviewedRanges() {
  const items = getIncludedItems()
    .map((item) => item.seq)
    .filter((seq) => Number.isInteger(seq))
    .sort((a, b) => a - b);
  if (items.length === 0) {
    return [];
  }
  const ranges = [];
  let fromSeq = items[0];
  let toSeq = items[0];
  for (const seq of items.slice(1)) {
    if (seq === toSeq + 1) {
      toSeq = seq;
      continue;
    }
    ranges.push({ from_seq: fromSeq, to_seq: toSeq });
    fromSeq = seq;
    toSeq = seq;
  }
  ranges.push({ from_seq: fromSeq, to_seq: toSeq });
  return ranges;
}

function buildReviewedDocumentRanges(docs = null) {
  const sourceDocs = docs || buildDocumentTasks(state.currentPack).filter((doc) =>
    normalizeTask(state.currentDraft.task, state.currentPack).doc_ids.includes(doc.doc_id)
  );
  const seqs = sourceDocs
    .map((doc) => doc.doc_seq)
    .filter((seq) => Number.isInteger(seq))
    .sort((a, b) => a - b);
  if (!seqs.length) {
    return [];
  }
  const ranges = [];
  let fromDocSeq = seqs[0];
  let toDocSeq = seqs[0];
  for (const seq of seqs.slice(1)) {
    if (seq === toDocSeq + 1) {
      toDocSeq = seq;
      continue;
    }
    ranges.push({ from_doc_seq: fromDocSeq, to_doc_seq: toDocSeq });
    fromDocSeq = seq;
    toDocSeq = seq;
  }
  ranges.push({ from_doc_seq: fromDocSeq, to_doc_seq: toDocSeq });
  return ranges;
}

function getIncludedItems() {
  const { fromSeq, toSeq } = getEffectiveRange();
  return getVisibleItems().filter((item) => item.seq >= fromSeq && item.seq <= toSeq);
}

function buildDocumentTasks(pack) {
  if (pack?.documents?.length) {
    const itemStats = new Map();
    for (const item of pack.items || []) {
      const docId = item.doc_id || "";
      if (!docId) {
        continue;
      }
      if (!itemStats.has(docId)) {
        itemStats.set(docId, {
          from_seq: item.seq,
          to_seq: item.seq,
          item_count: 0,
          unresolved_count: 0,
        });
      }
      const stats = itemStats.get(docId);
      stats.from_seq = Math.min(stats.from_seq, item.seq);
      stats.to_seq = Math.max(stats.to_seq, item.seq);
      stats.item_count += 1;
      stats.unresolved_count += Number(item.unresolved_target_count ?? item.region_count ?? 0);
    }
    return pack.documents
      .map((doc) => {
        const stats = itemStats.get(doc.doc_id) || {};
        return {
          doc_id: doc.doc_id || "",
          doc_seq: doc.doc_seq || 0,
          from_seq: stats.from_seq ?? 0,
          to_seq: stats.to_seq ?? 0,
          item_count: stats.item_count ?? Number(doc.item_count || 0),
          unresolved_count: stats.unresolved_count ?? Number(doc.region_count || 0),
          unit_count: Number(doc.unit_count || 0),
          preview: doc.preview || "",
        };
      })
      .sort((left, right) => left.doc_seq - right.doc_seq);
  }
  const docs = [];
  const byId = new Map();
  for (const item of pack?.items || []) {
    const docId = item.doc_id || "";
    if (!docId) {
      continue;
    }
    if (!byId.has(docId)) {
      const doc = {
        doc_id: docId,
        doc_seq: item.doc_seq || docs.length + 1,
        from_seq: item.seq,
        to_seq: item.seq,
        item_count: 0,
        unresolved_count: 0,
        preview: item.text || "",
      };
      byId.set(docId, doc);
      docs.push(doc);
    }
    const doc = byId.get(docId);
    doc.from_seq = Math.min(doc.from_seq, item.seq);
    doc.to_seq = Math.max(doc.to_seq, item.seq);
    doc.item_count += 1;
    doc.unresolved_count += Number(item.unresolved_target_count || 0);
  }
  return docs.sort((left, right) => left.from_seq - right.from_seq);
}

function normalizeTask(task, pack) {
  const docs = buildDocumentTasks(pack);
  const validDocIds = new Set(docs.map((doc) => doc.doc_id));
  let docIds = [];
  if (task?.mode === "document" && task.doc_id) {
    docIds = [task.doc_id];
  } else if (task?.mode === "documents" && Array.isArray(task.doc_ids)) {
    docIds = task.doc_ids;
  }
  docIds = [...new Set(docIds.map(String))].filter((docId) => validDocIds.has(docId));
  return {
    mode: docIds.length > 0 ? "documents" : "all",
    doc_ids: docIds,
    started: Boolean(task?.started),
    range_start_doc_id: validDocIds.has(task?.range_start_doc_id) ? task.range_start_doc_id : null,
    range_end_doc_id: validDocIds.has(task?.range_end_doc_id) ? task.range_end_doc_id : null,
  };
}

function listSavedTaskDrafts() {
  const records = Object.values(state.currentDraft?.saved_tasks || {});
  return records
    .filter((record) => record?.task_id)
    .sort((left, right) => {
      const leftNumber = Number(left.task_number || 0);
      const rightNumber = Number(right.task_number || 0);
      if (leftNumber !== rightNumber) {
        return leftNumber - rightNumber;
      }
      return String(left.task_id).localeCompare(String(right.task_id));
    });
}

function findSavedTaskDraftByDocIds(docIds) {
  const key = canonicalDocIdKey(docIds);
  return listSavedTaskDrafts().find((record) => canonicalDocIdKey(record.task?.doc_ids || []) === key) || null;
}

function canonicalDocIdKey(docIds) {
  const order = new Map(buildDocumentTasks(state.currentPack).map((doc, index) => [doc.doc_id, index]));
  return [...new Set((docIds || []).map(String))]
    .filter((docId) => order.has(docId))
    .sort((left, right) => order.get(left) - order.get(right))
    .join("\u001f");
}

function allocateTaskIdentity() {
  const number = Math.max(1, Number(state.currentDraft.next_task_number || 1));
  state.currentDraft.next_task_number = number + 1;
  return {
    task_id: `task_${number}`,
    task_label: `Task ${number}`,
    task_number: number,
  };
}

function currentTaskDraftRecord() {
  const existingId = state.currentDraft.active_task_id;
  const existing = existingId ? state.currentDraft.saved_tasks?.[existingId] : null;
  const identity = existing
    ? {
        task_id: existing.task_id,
        task_label: existing.task_label,
        task_number: existing.task_number,
      }
    : existingId
      ? {
          task_id: existingId,
          task_label: state.currentDraft.active_task_label || existingId,
          task_number: taskNumberFromId(existingId),
        }
      : allocateTaskIdentity();
  state.currentDraft.active_task_id = identity.task_id;
  state.currentDraft.active_task_label = identity.task_label;
  return {
    ...identity,
    task: {
      ...normalizeTask(state.currentDraft.task, state.currentPack),
      started: false,
    },
    from_seq: state.currentDraft.from_seq ?? null,
    to_seq: state.currentDraft.to_seq ?? null,
    overrides: cloneJson(state.currentDraft.overrides || {}),
    updated_at_epoch: Math.floor(Date.now() / 1000),
  };
}

function taskNumberFromId(taskId) {
  const match = String(taskId || "").match(/^task_(\d+)$/);
  return match ? Number(match[1]) : null;
}

function formatTaskDraftMeta(record, docs) {
  const docIds = new Set(record.task?.doc_ids || []);
  const selectedDocs = docs.filter((doc) => docIds.has(doc.doc_id));
  const docSeqs = selectedDocs.map((doc) => doc.doc_seq);
  const itemCount = selectedDocs.reduce((sum, doc) => sum + Number(doc.item_count || 0), 0);
  const parts = [];
  if (docSeqs.length) {
    parts.push(`docs ${formatDocSeqs(docSeqs)}`);
  }
  parts.push(`${itemCount} item(s)`);
  if (record.updated_at_epoch) {
    parts.push(`saved ${formatDate(record.updated_at_epoch)}`);
  }
  return parts.join(" · ");
}

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value));
}

function toggleDocumentTask(docId, selected) {
  const task = normalizeTask(state.currentDraft.task, state.currentPack);
  const docIds = new Set(task.doc_ids);
  if (selected) {
    docIds.add(docId);
  } else {
    docIds.delete(docId);
  }
  state.currentDraft.task = {
    ...task,
    mode: docIds.size > 0 ? "documents" : "all",
    doc_ids: [...docIds],
    started: false,
  };
  state.currentDraft.from_seq = null;
  state.currentDraft.to_seq = null;
  touchDraft();
  render();
}

function selectOnlyDocumentTask(docId) {
  state.currentDraft.task = {
    mode: "documents",
    doc_ids: [docId],
    started: false,
  };
  state.currentDraft.from_seq = null;
  state.currentDraft.to_seq = null;
  touchDraft();
  render();
}

function selectAllDocumentTasks() {
  const docs = buildDocumentTasks(state.currentPack);
  state.currentDraft.task = {
    mode: "documents",
    doc_ids: docs.map((doc) => doc.doc_id),
    started: false,
  };
  state.currentDraft.from_seq = null;
  state.currentDraft.to_seq = null;
  touchDraft();
  render();
}

function setDocumentRangeBoundary(docId, side) {
  const task = normalizeTask(state.currentDraft.task, state.currentPack);
  const next = {
    ...task,
    mode: "documents",
    started: false,
    [side === "start" ? "range_start_doc_id" : "range_end_doc_id"]: docId,
  };
  const docs = buildDocumentTasks(state.currentPack);
  const startId = next.range_start_doc_id;
  const endId = next.range_end_doc_id;
  if (startId && endId) {
    const startIndex = docs.findIndex((doc) => doc.doc_id === startId);
    const endIndex = docs.findIndex((doc) => doc.doc_id === endId);
    const fromIndex = Math.min(startIndex, endIndex);
    const toIndex = Math.max(startIndex, endIndex);
    next.doc_ids = docs.slice(fromIndex, toIndex + 1).map((doc) => doc.doc_id);
  } else {
    next.doc_ids = [docId];
  }
  state.currentDraft.task = next;
  state.currentDraft.from_seq = null;
  state.currentDraft.to_seq = null;
  touchDraft();
  render();
}

function startReviewTask() {
  const task = normalizeTask(state.currentDraft.task, state.currentPack);
  if (task.doc_ids.length === 0) {
    showStatus("Select at least one document before starting a task.", true);
    return;
  }
  const matchingDraft = findSavedTaskDraftByDocIds(task.doc_ids);
  if (matchingDraft) {
    resumeTaskDraft(matchingDraft.task_id);
    showStatus(`Returned to ${matchingDraft.task_label || "deferred task"}.`);
    return;
  }
  const identity = allocateTaskIdentity();
  state.currentDraft.task = {
    ...task,
    mode: "documents",
    started: true,
  };
  state.currentDraft.active_task_id = identity.task_id;
  state.currentDraft.active_task_label = identity.task_label;
  state.currentDraft.from_seq = null;
  state.currentDraft.to_seq = null;
  touchDraft();
  render();
}

function isTaskStarted() {
  return Boolean(normalizeTask(state.currentDraft?.task, state.currentPack).started);
}

function clearTaskSelection() {
  state.currentDraft.task = { mode: "documents", doc_ids: [], started: false };
  state.currentDraft.active_task_id = null;
  state.currentDraft.active_task_label = null;
  state.currentDraft.from_seq = null;
  state.currentDraft.to_seq = null;
  touchDraft();
  render();
}

function deferCurrentTask() {
  if (!isTaskStarted()) {
    clearTaskSelection();
    return;
  }
  const record = currentTaskDraftRecord();
  state.currentDraft.saved_tasks[record.task_id] = record;
  clearActiveTaskState();
  touchDraft();
  showStatus(`${record.task_label || "Task"} deferred locally.`);
  render();
}

function completeCurrentTask() {
  if (!isTaskStarted()) {
    clearTaskSelection();
    return;
  }
  if (
    !window.confirm(
      "Mark this local task complete and discard its draft? Copy or submit the JSON first if needed.",
    )
  ) {
    return;
  }
  const taskId = state.currentDraft.active_task_id;
  if (taskId && state.currentDraft.saved_tasks?.[taskId]) {
    delete state.currentDraft.saved_tasks[taskId];
  }
  clearActiveTaskState();
  touchDraft();
  showStatus("Task marked complete locally.");
  render();
}

function resumeTaskDraft(taskId) {
  const record = state.currentDraft.saved_tasks?.[taskId];
  if (!record) {
    showStatus("Deferred task was not found in local storage.", true);
    return;
  }
  state.currentDraft.active_task_id = record.task_id;
  state.currentDraft.active_task_label = record.task_label || record.task_id;
  state.currentDraft.task = {
    ...normalizeTask(record.task, state.currentPack),
    started: true,
  };
  state.currentDraft.from_seq = record.from_seq ?? null;
  state.currentDraft.to_seq = record.to_seq ?? null;
  state.currentDraft.overrides = cloneJson(record.overrides || {});
  touchDraft();
  render();
}

function clearActiveTaskState() {
  state.currentDraft.active_task_id = null;
  state.currentDraft.active_task_label = null;
  state.currentDraft.task = { mode: "documents", doc_ids: [], started: false };
  state.currentDraft.from_seq = null;
  state.currentDraft.to_seq = null;
  state.currentDraft.overrides = {};
}

function isEditable() {
  return String(state.currentPackMeta?.status || "").startsWith("active");
}

function createEmptyDraft(pack) {
  return {
    schema_version: 2,
    review_stage: pack.review_stage,
    pack_id: pack.pack_id,
    active_task_id: null,
    active_task_label: null,
    next_task_number: 1,
    saved_tasks: {},
    task: { mode: "documents", doc_ids: [], started: false },
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
    return normalizeReviewDraft(parsed, pack);
  } catch {
    return createEmptyDraft(pack);
  }
}

function normalizeReviewDraft(parsed, pack) {
  const base = createEmptyDraft(pack);
  const draft = {
    ...base,
    ...parsed,
    schema_version: 2,
    task: normalizeTask(parsed?.task, pack),
    overrides: parsed?.overrides || {},
    saved_tasks: {},
    next_task_number: Math.max(1, Number(parsed?.next_task_number || 1)),
  };

  const rawSavedTasks = parsed?.saved_tasks || {};
  let maxTaskNumber = 0;
  for (const [taskId, rawRecord] of Object.entries(rawSavedTasks)) {
    const task = normalizeTask(rawRecord?.task, pack);
    if (task.mode !== "documents" || task.doc_ids.length === 0) {
      continue;
    }
    const taskNumber = Number(rawRecord?.task_number || taskNumberFromId(taskId) || 0);
    maxTaskNumber = Math.max(maxTaskNumber, taskNumber);
    draft.saved_tasks[taskId] = {
      task_id: taskId,
      task_label: rawRecord?.task_label || (taskNumber ? `Task ${taskNumber}` : taskId),
      task_number: taskNumber || null,
      task: { ...task, started: false },
      from_seq: rawRecord?.from_seq ?? null,
      to_seq: rawRecord?.to_seq ?? null,
      overrides: rawRecord?.overrides || {},
      updated_at_epoch: rawRecord?.updated_at_epoch || null,
    };
  }

  draft.active_task_id = parsed?.active_task_id || null;
  draft.active_task_label = parsed?.active_task_label || draft.active_task_id || null;
  if (!draft.task.started) {
    draft.active_task_id = null;
    draft.active_task_label = null;
  }
  if (draft.active_task_id) {
    maxTaskNumber = Math.max(maxTaskNumber, taskNumberFromId(draft.active_task_id) || 0);
  }
  draft.next_task_number = Math.max(draft.next_task_number, maxTaskNumber + 1);
  return draft;
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
