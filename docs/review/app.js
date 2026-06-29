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
  const currentWorking = state.manifest.current_tracks?.working;
  if (currentWorking) {
    return { stageId: currentWorking.review_stage, packId: currentWorking.pack_id };
  }
  const currentDev = state.manifest.current_tracks?.dev;
  if (currentDev) {
    return { stageId: currentDev.review_stage, packId: currentDev.pack_id };
  }
  return { stageId: state.manifest.default_stage || stageIds[0], packId: null };
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
  const overrides = getActiveOverrides();
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
  const overrides = getActiveOverrides();
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

  const webSearchLabel = document.createElement("label");
  webSearchLabel.className = "yomi-control yomi-flag yomi-web-flag";
  webSearchLabel.title = "Use web search for strong repair";
  const webSearchCheckbox = document.createElement("input");
  webSearchCheckbox.type = "checkbox";
  webSearchCheckbox.disabled = !editable;
  webSearchCheckbox.checked = Boolean(override?.web_search);
  webSearchCheckbox.setAttribute("aria-label", "Use web search for strong repair");
  const webSearchGlyph = document.createElement("span");
  webSearchGlyph.className = "control-glyph";
  webSearchGlyph.setAttribute("aria-hidden", "true");
  webSearchGlyph.textContent = "⌕";
  webSearchLabel.append(webSearchCheckbox, webSearchGlyph);
  controls.append(webSearchLabel);

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
  webSearchCheckbox.addEventListener("change", () => {
    const draft = ensureYomiOverride(item.item_id);
    draft.web_search = webSearchCheckbox.checked;
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
  for (const segment of item.ruby_segments || [{ type: "text", text: item.text || "" }]) {
    if (segment.type !== "ruby") {
      nodes.push(document.createTextNode(segment.text || ""));
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
    state.currentDraft.overrides[itemId] = { skip: false, web_search: false, targets: {}, note: "" };
  }
  if (!state.currentDraft.overrides[itemId].targets) {
    state.currentDraft.overrides[itemId].targets = {};
  }
  return state.currentDraft.overrides[itemId];
}

function cleanupYomiOverride(itemId) {
  const draft = state.currentDraft.overrides[itemId];
  if (!draft) {
    return;
  }
  const hasTargets = Object.keys(draft.targets || {}).length > 0;
  if (!hasTargets && !draft.skip && !draft.web_search && !draft.note) {
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
  const overrides =
    pack.review_stage === "yomi_final_review"
      ? getActiveYomiOverrides()
      : getActiveOverrides().map((item) => ({
          item_id: item.item_id,
          decision: item.decision,
          ...(item.note ? { note: item.note } : {}),
        }));
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
        ...(typeof override.web_search === "boolean" ? { web_search: override.web_search } : {}),
        targets: Object.entries(override.targets || {}).map(([targetItemId, target]) => ({
          item_id: targetItemId,
          choice_source: target.choice_source,
          selected_reading: target.selected_reading ?? null,
        })),
        ...(override.note ? { note: String(override.note).trim() } : {}),
      };
    })
    .filter(Boolean)
    .filter(
      (row) => row.targets.length > 0 || "skip" in row || "web_search" in row || row.note
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
