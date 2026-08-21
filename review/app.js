const manifestUrl = "./manifest.json";
const submissionSchemaVersion = 1;
const githubNewIssueUrl = "https://github.com/hiroshi-manabe/yomi-corpus/issues/new";
const yomiLongPressMs = 550;
const repeatCancellationDelayMs = 700;
const repeatCancellationLifetimeMs = 12000;
const archiveSearchHistoryStorageKey = "yomi-review:archive-search-history:v1";
const archiveSearchHistoryLimit = 12;

const state = {
  manifest: null,
  currentStageId: null,
  currentPackMeta: null,
  currentPack: null,
  currentDraft: null,
  unifiedSources: [],
  archiveIndex: null,
  archiveCurrentTrack: "dev",
  archiveCurrentShard: null,
  archiveCurrentShardPath: "",
  archiveShardCache: new Map(),
  archiveSearchIndex: null,
  archiveSearchIndexPath: "",
  archiveSearchQuery: "",
  archiveSearchTimer: null,
  uiMode: "workflow",
  pendingIssueTaskId: null,
  pendingArchiveCorrectionKey: null,
  runtimeStatus: null,
  runtimePollingStarted: false,
  runtimePollTimer: null,
  runtimePollGeneration: 0,
  runtimePollFailures: 0,
  repeatCancellation: null,
};

const el = {
  taskPickerPanel: document.querySelector("#task-picker-panel"),
  taskDocList: document.querySelector("#task-doc-list"),
  taskDraftList: document.querySelector("#task-draft-list"),
  taskSummary: document.querySelector("#task-summary"),
  taskPickerGlobalActions: document.querySelector("#task-picker-global-actions"),
  selectAllDocs: document.querySelector("#select-all-docs"),
  clearDocSelection: document.querySelector("#clear-doc-selection"),
  startTask: document.querySelector("#start-task"),
  backToTaskPicker: document.querySelector("#back-to-task-picker"),
  completeTask: document.querySelector("#complete-task"),
  taskWorkPanels: document.querySelectorAll(".task-work-panel"),
  itemsContainer: document.querySelector("#items-container"),
  itemsSummary: document.querySelector("#items-summary"),
  statusBanner: document.querySelector("#status-banner"),
  serverUpdateBanner: document.querySelector("#server-update-banner"),
  serverUpdateMessage: document.querySelector("#server-update-message"),
  serverUpdateRefresh: document.querySelector("#server-update-refresh"),
  runtimeStatusLine: document.querySelector("#runtime-status-line"),
  issueReturnModal: document.querySelector("#issue-return-modal"),
  issueReturnTitle: document.querySelector("#issue-return-title"),
  issueReturnDescription: document.querySelector("#issue-return-description"),
  markSubmitted: document.querySelector("#mark-submitted"),
  issueNotYet: document.querySelector("#issue-not-yet"),
  submissionPreview: document.querySelector("#submission-preview"),
  issueUrlSummary: document.querySelector("#issue-url-summary"),
  resetDraft: document.querySelector("#reset-draft"),
  openIssueTitle: document.querySelector("#open-issue-title"),
  openIssueBottom: document.querySelector("#open-issue-bottom"),
  copyJson: document.querySelector("#copy-json"),
  downloadJson: document.querySelector("#download-json"),
  uiModeSelect: document.querySelector("#ui-mode-select"),
  workflowPreviewModal: document.querySelector("#workflow-preview-modal"),
  workflowPreviewTitle: document.querySelector("#workflow-preview-title"),
  workflowPreviewMeta: document.querySelector("#workflow-preview-meta"),
  workflowPreviewBody: document.querySelector("#workflow-preview-body"),
  workflowPreviewActions: document.querySelector("#workflow-preview-actions"),
  workflowPreviewClose: document.querySelector("#workflow-preview-close"),
  reviewerName: document.querySelector("#reviewer-name"),
  itemTemplate: document.querySelector("#item-template"),
  repeatCancellationBar: document.querySelector("#repeat-cancellation-bar"),
  repeatCancellationMessage: document.querySelector("#repeat-cancellation-message"),
  repeatCancellationApply: document.querySelector("#repeat-cancellation-apply"),
  repeatCancellationUndo: document.querySelector("#repeat-cancellation-undo"),
  repeatCancellationDismiss: document.querySelector("#repeat-cancellation-dismiss"),
};

const settingsKey = "yomi-corpus:review-ui:settings:v2";
const archiveCorrectionStorageKey = "yomi-corpus:archive-corrections:v1";
const archiveCorrectionStoreSchemaVersion = 2;
const workflowTakeNextCountStorageKey = "yomi-corpus:workflow-take-next-count:v1";
const workflowTakeNextOptions = [5, 10, 15, 20, 25, 30, 35, 40, 45, 50];

boot().catch((error) => {
  showStatus(`レビュー画面を読み込めませんでした: ${error.message}`, true);
  console.error(error);
});

async function boot() {
  loadSettings();
  bindEvents();
  const manifest = await fetchJson(manifestUrl);
  state.manifest = manifest;
  const stageIds = Object.keys(manifest.stages || {});
  if (stageIds.length === 0) {
    throw new Error("公開されたレビューステージがありません。");
  }
  const initialTarget = resolveInitialTarget(stageIds);
  if (initialTarget.stageId === "unified_yomi_review") {
    await openUnifiedReview();
  } else if (initialTarget.stageId === "archive_browser") {
    await openArchiveBrowser();
  } else {
    await openStage(initialTarget.stageId, {
      preferLatest: !initialTarget.packId,
      preferredPackId: initialTarget.packId,
    });
  }
  restorePendingIssueConfirmation();
  startRuntimeStatusPolling();
}

function bindEvents() {
  el.repeatCancellationApply?.addEventListener("click", applyRepeatedCancellation);
  el.repeatCancellationUndo?.addEventListener("click", undoRepeatedCancellation);
  el.repeatCancellationDismiss?.addEventListener("click", dismissRepeatedCancellation);

  el.serverUpdateRefresh.addEventListener("click", () => {
    if (isTaskStarted() && !window.confirm("サーバー側の状態を反映して画面を更新しますか？ ローカル作業は保存され、再開できます。")) {
      return;
    }
    window.location.reload();
  });

  document.addEventListener("visibilitychange", () => {
    if (!state.manifest?.runtime_status?.path) {
      return;
    }
    clearRuntimePollTimer();
    if (document.hidden) {
      scheduleRuntimeStatusPoll();
    } else {
      pollRuntimeStatus();
    }
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

  el.resetDraft.addEventListener("click", () => {
    if (!isEditable()) {
      return;
    }
    if (!window.confirm("現在の作業とローカル編集を破棄しますか？")) {
      return;
    }
    clearActiveTaskState();
    touchDraft();
    render({ scrollToTop: true });
  });

  el.copyJson.addEventListener("click", async () => {
    const copied = await copySubmissionJsonToClipboard();
    if (copied === null) {
      return;
    }
    if (copied) {
      showStatus("提出用JSONをコピーしました。Issueを開き、本文に貼り付けてください。");
    } else {
      showStatus("クリップボードへのコピーに失敗しました。選択されたJSONを手動でコピーしてからIssueを開いてください。", true);
    }
  });

  el.downloadJson.addEventListener("click", () => {
    const submission = buildSubmissionPayload();
    if (!confirmTerminalExclusions(submission)) {
      return;
    }
    const payload = formatSubmissionJson(submission);
    const blob = new Blob([payload], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${state.currentPack.pack_id || "review_submission"}.json`;
    a.click();
    URL.revokeObjectURL(url);
  });

  el.openIssueTitle.addEventListener("click", async () => {
    await openIssueForCurrentTask();
  });

  el.openIssueBottom.addEventListener("click", async () => {
    await openIssueForCurrentTask();
  });

  el.uiModeSelect?.addEventListener("change", () => {
    state.uiMode = normalizeUiMode(el.uiModeSelect.value);
    saveSettings();
    updateLocation(state.currentStageId, state.currentPack?.pack_id || state.currentPackMeta?.pack_id || "");
    render();
  });

  el.workflowPreviewClose?.addEventListener("click", () => requestCloseWorkflowDocumentPreview());
  el.workflowPreviewModal?.addEventListener("click", (event) => {
    if (event.target?.hasAttribute?.("data-preview-close")) {
      requestCloseWorkflowDocumentPreview();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !el.workflowPreviewModal?.classList.contains("hidden")) {
      event.preventDefault();
      requestCloseWorkflowDocumentPreview();
    }
  });

  window.addEventListener("beforeunload", (event) => {
    if (!archiveCorrectionHasUnsavedEdits()) {
      return;
    }
    event.preventDefault();
    event.returnValue = "";
  });

  window.addEventListener("focus", () => {
    if (state.pendingIssueTaskId || state.pendingArchiveCorrectionKey) {
      showIssueReturnModal();
    }
  });

  el.markSubmitted?.addEventListener("click", () => {
    const pendingTaskId = state.pendingIssueTaskId;
    const pendingArchiveCorrectionKey = state.pendingArchiveCorrectionKey;
    state.pendingIssueTaskId = null;
    state.pendingArchiveCorrectionKey = null;
    hideIssueReturnModal();
    clearPendingIssueConfirmation(pendingTaskId);
    if (pendingArchiveCorrectionKey) {
      markArchiveCorrectionSubmitted(pendingArchiveCorrectionKey);
    } else if (pendingTaskId) {
      markSavedTaskSubmitted(pendingTaskId);
    }
  });

  el.issueNotYet?.addEventListener("click", () => {
    const pendingTaskId = state.pendingIssueTaskId;
    state.pendingIssueTaskId = null;
    state.pendingArchiveCorrectionKey = null;
    clearPendingIssueConfirmation(pendingTaskId);
    hideIssueReturnModal();
    showStatus("Issueは提出済みにされませんでした。ローカルの作業内容は残っています。");
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
  if (requested === "archive_browser") {
    return { stageId: "archive_browser", packId: null };
  }
  if (requested === "corpus_map") {
    return { stageId: "archive_browser", packId: null };
  }
  if (requested === "unified_yomi_review") {
    return { stageId: "unified_yomi_review", packId: null };
  }
  if (requested && stageIds.includes(requested)) {
    return { stageId: requested, packId: requestedPackId };
  }

  if (hasDevYomiReviewSources()) {
    return { stageId: "unified_yomi_review", packId: null };
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

function activeDevReviewQueues() {
  return (state.manifest.current_review_queues || []).filter(
    (queue) =>
      queue.track_name === "dev" &&
      ["yomi_final_review", "yomi_strong_repair_review"].includes(queue.review_stage) &&
      String(queue.status || "").startsWith("active"),
  );
}

function hasDevYomiReviewSources() {
  return activeDevYomiReviewSources().length > 0;
}

function hasReviewArchive() {
  return Boolean(state.manifest?.archive?.index_path);
}

async function openStage(stageId, { preferLatest = false, preferredPackId = null } = {}) {
  const stage = state.manifest.stages?.[stageId];
  if (!stage) {
    throw new Error(`不明なレビューステージです: ${stageId}`);
  }
  state.currentStageId = stageId;

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
    throw new Error(`ステージ ${stageId} にパックがありません。`);
  }
  await openPack(stageId, packMeta.pack_id);
  updateRuntimePollingForInteraction();
}

async function openPack(stageId, packId) {
  const stage = state.manifest.stages[stageId];
  const packMeta = stage.packs.find((pack) => pack.pack_id === packId);
  if (!packMeta) {
    throw new Error(`パック ${packId} が見つかりません。`);
  }

  const pack = await fetchJson(packMeta.path);
  state.currentPackMeta = packMeta;
  state.currentPack = pack;
  state.currentDraft = loadDraft(pack);
  syncLocalTaskRecordsForCurrentPack();
  updateLocation(stageId, packId);
  render({ scrollToTop: isTaskStarted() });
}

async function openUnifiedReview() {
  const reviewSources = activeDevYomiReviewSources();
  if (reviewSources.length === 0) {
    const fallbackStage = Object.keys(state.manifest.stages || {})[0];
    await openStage(fallbackStage, { preferLatest: true });
    return;
  }
  state.currentStageId = "unified_yomi_review";
  const sources = [];
  for (const source of reviewSources) {
    const pack = await fetchJson(source.path);
    sources.push({ meta: source, pack });
  }
  const unified = buildUnifiedReviewPack(sources);
  state.currentPackMeta = {
    pack_id: unified.pack_id,
    title: unified.title,
    track_name: "dev",
    status: "active-dev",
  };
  state.currentPack = unified;
  state.unifiedSources = sources;
  state.currentDraft = loadDraft(unified);
  syncLocalTaskRecordsForCurrentPack();
  updateLocation("unified_yomi_review", unified.pack_id);
  render({ scrollToTop: isTaskStarted() });
  updateRuntimePollingForInteraction();
}

async function openArchiveBrowser() {
  if (!hasReviewArchive()) {
    showStatus("確定済みアーカイブはまだ公開されていません。", true);
    return;
  }
  state.currentStageId = "archive_browser";
  if (!state.archiveIndex) {
    state.archiveIndex = await fetchJson(state.manifest.archive.index_path);
  }
  state.archiveCurrentTrack = "dev";
  const track = state.archiveIndex?.tracks?.[state.archiveCurrentTrack] || {};
  state.archiveCurrentShard = { documents: track.documents || [] };
  state.archiveCurrentShardPath = "";
  state.currentPackMeta = {
    pack_id: "archive_browser",
    title: "確定済みコーパス",
    track_name: "dev",
    status: "archive",
  };
  state.currentPack = {
    schema_version: 1,
    review_stage: "archive_browser",
    pack_id: "archive_browser",
    track_name: "dev",
    item_count: state.archiveIndex?.tracks?.dev?.document_count || 0,
    items: [],
    documents: [],
  };
  state.currentDraft = loadDraft(state.currentPack);
  updateLocation("archive_browser", "archive_browser");
  render();
  updateRuntimePollingForInteraction();
}

function activeDevYomiReviewSources() {
  const activeSources = activeDevReviewQueues();
  if (activeSources.length > 0) {
    return activeSources;
  }
  const sources = [];
  for (const stageId of ["yomi_final_review", "yomi_strong_repair_review"]) {
    const stage = state.manifest.stages?.[stageId];
    const packId = stage?.latest_pack_ids_by_track?.dev;
    if (!stage || !packId) {
      continue;
    }
    const pack = (stage.packs || []).find((row) => row.pack_id === packId);
    if (pack) {
      sources.push({ ...pack, review_stage: stageId, label: stage.label || stageId });
    }
  }
  return sources;
}

function buildUnifiedReviewPack(sources) {
  const sourceIds = sources.map(({ pack }) => pack.pack_id);
  const items = [];
  const sourceDocuments = [];
  let nextSeq = 1;
  for (const { pack, meta } of sources) {
    const pendingDocIds = Array.isArray(meta.pending_doc_ids)
      ? new Set(meta.pending_doc_ids.map(String))
      : null;
    for (const item of pack.items || []) {
      if (pendingDocIds && !pendingDocIds.has(String(item.doc_id || ""))) {
        continue;
      }
      const unifiedItem = {
        ...item,
        item_id: `${pack.review_stage}:${pack.pack_id}:${item.item_id}`,
        original_item_id: item.item_id,
        original_seq: item.seq,
        source_review_stage: pack.review_stage,
        source_pack_id: pack.pack_id,
      };
      unifiedItem.seq = nextSeq++;
      items.push(unifiedItem);
    }
    for (const doc of pack.documents || []) {
      const docId = String(doc.doc_id || "");
      if (!docId || (pendingDocIds && !pendingDocIds.has(docId))) {
        continue;
      }
      sourceDocuments.push({
        ...doc,
        awaiting_finalization: Boolean(pendingDocIds),
        queue_stage: pack.review_stage,
        source_pack_id: pack.pack_id,
        task_doc_id: queueDocKey(pack.review_stage, docId),
        queue_member: documentBelongsToQueue(pack.review_stage, doc),
      });
    }
  }
  const statsByDoc = new Map();
  for (const item of items) {
    const docId = String(item.doc_id || "");
    if (!docId) {
      continue;
    }
    const key = queueDocKey(item.source_review_stage, docId);
    if (!statsByDoc.has(key)) {
      statsByDoc.set(key, {
        from_seq: item.seq,
        to_seq: item.seq,
        item_count: 0,
        unresolved_count: 0,
        final_item_count: 0,
        strong_repair_item_count: 0,
      });
    }
    const stats = statsByDoc.get(key);
    stats.from_seq = Math.min(stats.from_seq, item.seq);
    stats.to_seq = Math.max(stats.to_seq, item.seq);
    stats.item_count += 1;
    stats.unresolved_count += Number(item.unresolved_target_count ?? item.region_count ?? 0);
    if (item.source_review_stage === "yomi_final_review") {
      stats.final_item_count += 1;
    }
    if (item.source_review_stage === "yomi_strong_repair_review") {
      stats.strong_repair_item_count += 1;
    }
  }
  const documentRows = sourceDocuments
    .map((doc) => {
      const key = taskDocKey(doc);
      const stats = statsByDoc.get(key) || {};
      return {
        ...doc,
        from_seq: stats.from_seq ?? 0,
        to_seq: stats.to_seq ?? 0,
        item_count: stats.item_count ?? 0,
        unresolved_count: stats.unresolved_count ?? Number(doc.region_count || 0),
        final_item_count: stats.final_item_count ?? 0,
        strong_repair_item_count: stats.strong_repair_item_count ?? 0,
        queue_member: documentBelongsToQueue(doc.queue_stage, doc),
        selectable: unifiedDocumentIsSelectable(doc, stats),
      };
    })
    .sort(
      (left, right) =>
        documentDisplaySeq(left) - documentDisplaySeq(right) ||
        queueStageSort(left.queue_stage) - queueStageSort(right.queue_stage),
    );
  const actionableDocumentRows = documentRows.filter((doc) => doc.selectable !== false);
  return {
    schema_version: 1,
    review_stage: "unified_yomi_review",
    queue_id: "unified_yomi_review",
    pack_id: `unified_${sourceIds.join("__")}`,
    title: "読みレビュー",
    track_name: "dev",
    item_count: items.length,
    summary: {
      document_count: documentRows.length,
      selectable_document_count: documentRows.filter((doc) => doc.selectable !== false).length,
      source_pack_ids: sourceIds,
    },
    source_packs: sources.map(({ pack, meta }) => ({
      review_stage: pack.review_stage,
      pack_id: pack.pack_id,
      title: meta.title || pack.pack_id,
      item_count: pack.item_count,
    })),
    documents: documentRows,
    actionable_documents: actionableDocumentRows,
    items,
  };
}

function queueDocKey(queueStage, docId) {
  return `${queueStage}::${docId}`;
}

function taskDocKey(doc) {
  return doc?.task_doc_id || doc?.doc_id || "";
}

function stableDocumentSeq(value) {
  const explicit = Number(value?.track_doc_seq || 0);
  if (Number.isInteger(explicit) && explicit > 0) {
    return explicit;
  }
  const match = String(value?.doc_id || "").match(/:(\d+)$/);
  return match ? Number(match[1]) : 0;
}

function documentDisplaySeq(doc) {
  return stableDocumentSeq(doc);
}

function itemDisplayDocSeq(item) {
  return stableDocumentSeq(item);
}

function queueStageSort(stage) {
  if (stage === "yomi_final_review") {
    return 0;
  }
  if (stage === "yomi_strong_repair_review") {
    return 1;
  }
  return 99;
}

function unifiedDocumentIsSelectable(doc, stats) {
  const stateName = String(doc.state || "");
  const itemCount = Number(stats?.item_count || 0);
  if (itemCount <= 0) {
    return false;
  }
  if (!documentBelongsToQueue(doc.queue_stage, doc)) {
    return false;
  }
  if (doc.queue_stage === "yomi_final_review") {
    return stateName.startsWith("final_") || (stateName === "" && doc.selectable !== false);
  }
  if (doc.queue_stage === "yomi_strong_repair_review") {
    return stateName.startsWith("strong_");
  }
  return Boolean(doc.selectable);
}

function documentBelongsToQueue(queueStage, doc) {
  if (doc && "queue_member" in doc) {
    return Boolean(doc.queue_member);
  }
  const stateName = String(doc?.state || "");
  if (queueStage === "yomi_final_review") {
    return stateName.startsWith("final_") || (stateName === "" && doc?.selectable !== false);
  }
  if (queueStage === "yomi_strong_repair_review") {
    return stateName.startsWith("strong_");
  }
  return Boolean(doc?.selectable);
}

function render({ scrollToTop = false } = {}) {
  renderTaskSelector();
  renderItems();
  renderControlState();
  renderSubmissionPreview();
  if (scrollToTop) {
    scrollReviewPageToTop();
  }
}

function scrollReviewPageToTop() {
  const reset = () => {
    window.scrollTo({ top: 0, left: 0, behavior: "auto" });
    if (document.scrollingElement) {
      document.scrollingElement.scrollTop = 0;
      document.scrollingElement.scrollLeft = 0;
    }
  };
  if (typeof window.requestAnimationFrame === "function") {
    window.requestAnimationFrame(reset);
  } else {
    reset();
  }
}

function renderTaskSelector() {
  if (!el.taskPickerPanel || !el.taskDocList || !el.taskSummary) {
    return;
  }
  const docs = buildDocumentTasks(state.currentPack);
  const actionableDocs = buildActionableDocumentTasks(state.currentPack);
  const task = normalizeTask(state.currentDraft.task, state.currentPack);
  const editable = isEditable();
  const started = isTaskStarted();
  const usesDedicatedSelectionControls =
    state.currentStageId === "archive_browser" || isUnifiedReviewPack(state.currentPack);
  el.taskPickerGlobalActions?.classList.toggle("hidden", usesDedicatedSelectionControls);

  if (state.currentStageId === "archive_browser") {
    renderArchiveBrowserPanel();
    return;
  }
  el.taskPickerPanel.classList.remove("archive-browser-panel");

  el.taskPickerPanel.classList.toggle("unified-task-picker", isUnifiedReviewPack(state.currentPack));
  el.taskPickerPanel.classList.toggle("hidden", !editable || started);
  el.taskWorkPanels.forEach((panel) => {
    panel.classList.toggle("hidden", editable && !started);
  });
  if (!editable) {
    el.taskSummary.textContent = "過去のレビュー内容は閲覧専用です。";
    return;
  }

  renderSavedTaskDrafts(actionableDocs);
  el.taskDocList.innerHTML = "";
  if (isUnifiedReviewPack(state.currentPack)) {
    renderWorkflowTaskDashboard(withSubmittedProcessingPlaceholders(docs), actionableDocs, task);
    el.taskSummary.textContent = "キューを一つ選び、その中から文書を選択してレビューを開始してください。";
    el.startTask.disabled = true;
    el.clearDocSelection.disabled = true;
    el.selectAllDocs.disabled = true;
    return;
  } else {
    for (const doc of docs) {
      el.taskDocList.append(renderTaskDocumentRow(doc, task));
    }
  }
  const selectableDocs = actionableDocs;
  const selectAllDocs = selectableDocsForCurrentTask(actionableDocs, task);
  const selectedCount = task.doc_ids.length;
  const selectedSelectAllCount = selectAllDocs.filter((doc) => task.doc_ids.includes(taskDocKey(doc))).length;
  const selectedItems = itemsForTask(task);
  el.taskSummary.textContent =
    selectedCount > 0
      ? `${selectedCount}文書、${selectedItems.length}項目を選択中です。`
      : selectableDocs.length
        ? "文書を選択してレビューを開始してください。"
        : "このキューには選択できる文書がありません。";
  el.startTask.disabled = selectableDocs.length === 0 || selectedCount === 0;
  el.clearDocSelection.disabled = selectedCount === 0;
  el.selectAllDocs.disabled = selectAllDocs.length === 0 || selectedSelectAllCount === selectAllDocs.length;
}

function renderArchiveBrowserPanel() {
  el.taskPickerPanel.classList.remove("hidden");
  el.taskPickerPanel.classList.add("archive-browser-panel");
  el.taskWorkPanels.forEach((panel) => panel.classList.add("hidden"));
  el.taskDraftList.innerHTML = "";
  el.taskDocList.innerHTML = "";
  el.selectAllDocs.disabled = true;
  el.clearDocSelection.disabled = true;
  el.startTask.disabled = true;
  const track = state.archiveIndex?.tracks?.[state.archiveCurrentTrack] || {};
  if (!state.archiveCurrentShard) {
    el.taskSummary.textContent = "確定済み文書はまだ公開されていません。";
    return;
  }
  const docs = state.archiveCurrentShard.documents || [];
  el.taskSummary.innerHTML = "";
  const summaryText = document.createElement("span");
  summaryText.textContent = `この範囲には確定済み文書が${docs.length}件あります。タイルをクリックすると確認・修正できます。`;
  el.taskSummary.append(summaryText);
  if (hasDevYomiReviewSources()) {
    const backButton = document.createElement("button");
    backButton.type = "button";
    backButton.className = "secondary-button compact-button";
    backButton.textContent = "作業中のレビューに戻る";
    backButton.addEventListener("click", () => {
      openUnifiedReview().catch((error) => {
        showStatus(`作業中のレビューを開けませんでした: ${error.message}`, true);
      });
    });
    el.taskSummary.append(backButton);
  }
  el.taskDocList.append(renderArchiveSearchPanel(track));
  el.taskDocList.append(renderCorpusMapTileGrid(docs));
}

function renderArchiveSearchPanel(track) {
  const panel = document.createElement("section");
  panel.className = "archive-search-panel";
  const heading = document.createElement("div");
  heading.className = "archive-search-heading";
  heading.innerHTML = `
    <div>
      <strong>コーパスを検索</strong>
      <p class="muted">表記、または「描く/えがく」のような表記/読みで検索します。</p>
    </div>
  `;
  const input = document.createElement("input");
  input.type = "search";
  input.className = "archive-search-input";
  input.placeholder = "表記、または表記/読みを検索";
  input.autocomplete = "off";
  input.value = state.archiveSearchQuery || "";
  input.disabled = !track.search_path;
  heading.append(input);
  panel.append(heading);

  const history = document.createElement("div");
  history.className = "archive-search-history";
  panel.append(history);

  const status = document.createElement("p");
  status.className = "archive-search-status muted";
  status.textContent = track.search_path
    ? "検索語を入力すると検索用インデックスを読み込みます。"
    : "このトラックには検索用インデックスがありません。";
  panel.append(status);

  const results = document.createElement("div");
  results.className = "archive-search-results";
  panel.append(results);
  const nodes = { panel, status, results, history, input, track };
  renderArchiveSearchHistory(track, nodes);

  input.addEventListener("input", () => {
    state.archiveSearchQuery = input.value;
    updateRuntimePollingForInteraction();
    scheduleArchiveSearch(track, nodes);
  });
  input.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.isComposing || event.keyCode === 229) {
      return;
    }
    event.preventDefault();
    state.archiveSearchQuery = input.value;
    rememberArchiveSearchQuery(track, input.value);
    renderArchiveSearchHistory(track, nodes);
    scheduleArchiveSearch(track, nodes, { immediate: true });
  });
  if (state.archiveSearchQuery.trim()) {
    scheduleArchiveSearch(track, nodes, { immediate: true });
  }
  return panel;
}

function archiveSearchHistoryKey(track) {
  return `${archiveSearchHistoryStorageKey}:${String(track?.track_name || "dev")}`;
}

function loadArchiveSearchHistory(track) {
  try {
    const parsed = JSON.parse(
      window.localStorage.getItem(archiveSearchHistoryKey(track)) || "[]",
    );
    return Array.isArray(parsed)
      ? parsed.filter((query) => typeof query === "string" && query.trim()).slice(0, archiveSearchHistoryLimit)
      : [];
  } catch {
    return [];
  }
}

function rememberArchiveSearchQuery(track, value) {
  const query = String(value || "").trim();
  if (!query) {
    return;
  }
  const normalized = normalizeArchiveSearchText(query);
  const history = loadArchiveSearchHistory(track)
    .filter((candidate) => normalizeArchiveSearchText(candidate) !== normalized);
  history.unshift(query);
  try {
    window.localStorage.setItem(
      archiveSearchHistoryKey(track),
      JSON.stringify(history.slice(0, archiveSearchHistoryLimit)),
    );
  } catch {
    // Search remains usable when browser storage is unavailable.
  }
}

function clearArchiveSearchHistory(track) {
  try {
    window.localStorage.removeItem(archiveSearchHistoryKey(track));
  } catch {
    // Search remains usable when browser storage is unavailable.
  }
}

function renderArchiveSearchHistory(track, nodes) {
  const history = loadArchiveSearchHistory(track);
  nodes.history.innerHTML = "";
  nodes.history.classList.toggle("hidden", history.length === 0);
  if (!history.length) {
    return;
  }
  const label = document.createElement("span");
  label.className = "archive-search-history-label";
  label.textContent = "最近の検索";
  nodes.history.append(label);
  for (const query of history) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "archive-search-history-query";
    button.textContent = query;
    button.addEventListener("click", () => {
      nodes.input.value = query;
      state.archiveSearchQuery = query;
      rememberArchiveSearchQuery(track, query);
      renderArchiveSearchHistory(track, nodes);
      scheduleArchiveSearch(track, nodes, { immediate: true });
    });
    nodes.history.append(button);
  }
  const clear = document.createElement("button");
  clear.type = "button";
  clear.className = "archive-search-history-clear";
  clear.textContent = "履歴を消去";
  clear.addEventListener("click", () => {
    clearArchiveSearchHistory(track);
    renderArchiveSearchHistory(track, nodes);
  });
  nodes.history.append(clear);
}

function scheduleArchiveSearch(track, nodes, { immediate = false } = {}) {
  if (state.archiveSearchTimer) {
    window.clearTimeout(state.archiveSearchTimer);
  }
  const query = state.archiveSearchQuery.trim();
  if (!query) {
    nodes.status.textContent = "検索語を入力すると検索用インデックスを読み込みます。";
    nodes.results.innerHTML = "";
    return;
  }
  const run = () => {
    performArchiveSearch(track, query, nodes).catch((error) => {
      if (!nodes.panel.isConnected) {
        return;
      }
      nodes.status.textContent = `検索に失敗しました: ${error.message}`;
      nodes.status.classList.add("error");
    });
  };
  state.archiveSearchTimer = window.setTimeout(run, immediate ? 0 : 180);
}

async function performArchiveSearch(track, query, nodes) {
  const searchPath = String(track.search_path || "");
  if (!searchPath) {
    return;
  }
  nodes.status.classList.remove("error");
  if (!state.archiveSearchIndex || state.archiveSearchIndexPath !== searchPath) {
    nodes.status.textContent = "検索用インデックスを読み込んでいます…";
    state.archiveSearchIndex = await fetchJson(searchPath);
    state.archiveSearchIndexPath = searchPath;
  }
  if (!nodes.panel.isConnected || query !== state.archiveSearchQuery.trim()) {
    return;
  }
  const normalizedQuery = normalizeArchiveSearchText(query);
  const matches = [];
  let totalMatches = 0;
  for (const doc of state.archiveSearchIndex.documents || []) {
    const matchingUnits = archiveSearchUnits(doc)
      .map((unit) => ({
        ...unit,
        search_hit_count: archiveSearchUnitHitCount(unit, normalizedQuery),
      }))
      .filter((unit) => unit.search_hit_count > 0);
    const hitCount = matchingUnits.reduce(
      (total, unit) => total + Number(unit.search_hit_count || 0),
      0,
    );
    if (!hitCount) {
      continue;
    }
    totalMatches += 1;
    if (matches.length < 100) {
      matches.push({
        ...doc,
        search_hit_count: hitCount,
        search_matching_units: matchingUnits,
      });
    }
  }
  renderArchiveSearchResults(matches, totalMatches, query, nodes);
}

function normalizeArchiveSearchText(value) {
  return katakanaToHiragana(
    String(value || "").normalize("NFKC").toLocaleLowerCase("ja"),
  );
}

function archiveSearchUnits(doc) {
  if (Array.isArray(doc?.units)) {
    return doc.units;
  }
  // Compatibility with schema-v2 indexes during rolling publication.
  return String(doc?.text || "")
    .split("\n")
    .filter(Boolean)
    .map((text, index) => ({ unit_seq: index + 1, yomi_tokens: [[text, ""]] }));
}

function archiveSearchUnitTokens(unit) {
  return (unit?.yomi_tokens || [])
    .filter((token) => Array.isArray(token) && token.length >= 2)
    .map(([surface, reading]) => ({
      surface: String(surface || ""),
      reading: String(reading || ""),
    }));
}

function archiveSearchUnitRawText(unit) {
  return archiveSearchUnitTokens(unit).map((token) => token.surface).join("");
}

function archiveSearchUnitYomiText(unit) {
  return archiveSearchUnitTokens(unit)
    .map((token) => `${token.surface}/${token.reading}`)
    .join(" ");
}

function archiveSearchUnitFromReviewItem(item) {
  const canonical = normalizeYomiTokenPairs(item?.yomi_tokens);
  const tokens = canonical.length
    ? canonical
    : parseRenderedYomiTokens(item?.rendered_yomi || "")
      .map((token) => [token.surface, token.reading]);
  return {
    unit_seq: Number(item?.unit_seq || item?.seq || 0),
    yomi_tokens: tokens.length ? tokens : [[String(item?.text || ""), ""]],
  };
}

function archiveSearchUnitHitCount(unit, normalizedQuery) {
  const text = normalizedQuery.includes("/")
    ? archiveSearchUnitYomiText(unit)
    : archiveSearchUnitRawText(unit);
  return countNormalizedSearchHits(text, normalizedQuery);
}

function countNormalizedSearchHits(text, normalizedQuery) {
  if (!normalizedQuery) {
    return 0;
  }
  const normalizedText = normalizeArchiveSearchText(text);
  let count = 0;
  let cursor = 0;
  while (cursor < normalizedText.length) {
    const index = normalizedText.indexOf(normalizedQuery, cursor);
    if (index < 0) {
      break;
    }
    count += 1;
    cursor = index + normalizedQuery.length;
  }
  return count;
}

function renderArchiveSearchResults(matches, totalMatches, query, nodes) {
  nodes.results.innerHTML = "";
  if (!totalMatches) {
    nodes.status.textContent = `「${query}」を含む文書は見つかりませんでした。`;
    return;
  }
  nodes.status.textContent = totalMatches > matches.length
    ? `${totalMatches}文書が見つかりました。先頭の${matches.length}件を表示します。`
    : `${totalMatches}文書が見つかりました。`;
  for (const doc of matches) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "archive-search-result";
    const hitCount = Number(doc.search_hit_count || 0);
    button.innerHTML = `
      <span class="archive-search-result-heading">
        <strong>文書 ${escapeHtml(doc.track_doc_seq)}</strong>
        <span class="archive-search-hit-count">${hitCount}件</span>
      </span>
    `;
    const snippets = document.createElement("span");
    snippets.className = "archive-search-ruby-snippets";
    for (const unit of (doc.search_matching_units || []).slice(0, 3)) {
      snippets.append(renderArchiveSearchRubySnippet(unit, query));
    }
    if ((doc.search_matching_units || []).length > 3) {
      const remaining = document.createElement("small");
      remaining.textContent = `ほか${doc.search_matching_units.length - 3}文`;
      snippets.append(remaining);
    }
    button.append(snippets);
    button.addEventListener("click", () => {
      rememberArchiveSearchQuery(nodes.track, query);
      renderArchiveSearchHistory(nodes.track, nodes);
      openArchiveSearchResult(doc, query).catch((error) => {
        showStatus(`検索結果を開けませんでした: ${error.message}`, true);
      });
    });
    nodes.results.append(button);
  }
}

function renderArchiveSearchRubySnippet(unit, query) {
  const line = document.createElement("span");
  line.className = "archive-search-ruby-snippet";
  const normalizedQuery = normalizeArchiveSearchText(query);
  const useYomi = normalizedQuery.includes("/");
  let highlighted = false;
  for (const [index, token] of archiveSearchUnitTokens(unit).entries()) {
    const wrapper = document.createElement("span");
    const searchable = useYomi
      ? `${token.surface}/${token.reading}`
      : token.surface;
    if (normalizeArchiveSearchText(searchable).includes(normalizedQuery)) {
      wrapper.className = "archive-search-token-match";
      highlighted = true;
    }
    const rubyToken = Array.isArray(unit?.ruby_tokens) ? unit.ruby_tokens[index] : null;
    wrapper.append(
      ...renderReadonlyRubyFromTokensWithNodes([token], rubyToken ? [rubyToken] : []),
    );
    line.append(wrapper);
  }
  if (!highlighted) {
    line.classList.add("search-match");
  }
  return line;
}

async function openArchiveSearchResult(result, query) {
  const packPath = String(result.pack_path || "");
  if (packPath) {
    await openPendingSearchResult(result, query, packPath);
    return;
  }
  const shardPath = String(result.shard_path || "");
  if (!shardPath) {
    throw new Error("検索結果にアーカイブの保存先がありません。");
  }
  const doc = await loadArchiveDocument(result);
  if (!doc) {
    throw new Error("アーカイブ内に文書が見つかりません。");
  }
  openArchiveCorrectionEditor(doc);
  scrollArchiveCorrectionToFirstMatch(doc, query);
}

async function openPendingSearchResult(result, query, packPath) {
  const pack = await fetchJson(packPath);
  const items = (pack.items || []).filter(
    (item) =>
      String(item.doc_id || "") === String(result.doc_id || "") &&
      itemDisplayDocSeq(item) === Number(result.track_doc_seq || 0),
  );
  if (!items.length) {
    throw new Error("レビュー用データ内に文書が見つかりません。");
  }

  el.workflowPreviewTitle.textContent = `文書 ${result.track_doc_seq}`;
  el.workflowPreviewMeta.textContent = "処理中 · 閲覧専用";
  el.workflowPreviewBody.innerHTML = "";
  const originalPack = state.currentPack;
  const originalDraft = state.currentDraft;
  state.currentPack = pack;
  state.currentDraft = { overrides: {} };
  try {
    const sortedItems = items.sort(
      (left, right) =>
        Number(left.unit_seq || left.seq || 0) - Number(right.unit_seq || right.seq || 0),
    );
    for (const item of sortedItems) {
      const node = renderPreviewItem(item);
      const searchUnit = archiveSearchUnitFromReviewItem(item);
      node.dataset.searchText = archiveSearchUnitRawText(searchUnit);
      node.dataset.searchYomi = archiveSearchUnitYomiText(searchUnit);
      el.workflowPreviewBody.append(node);
    }
  } finally {
    state.currentPack = originalPack;
    state.currentDraft = originalDraft;
  }
  el.workflowPreviewActions.innerHTML = "";
  const note = document.createElement("span");
  note.className = "muted";
  note.textContent = "この文書は処理中です。現在のレビュー用データを表示しています。";
  el.workflowPreviewActions.append(note);
  el.workflowPreviewModal.classList.remove("hidden");
  scrollPendingSearchToFirstMatch(query);
  updateRuntimePollingForInteraction();
}

function scrollPendingSearchToFirstMatch(query) {
  const normalizedQuery = normalizeArchiveSearchText(query).trim();
  const target = [...(el.workflowPreviewBody?.querySelectorAll("[data-search-text]") || [])].find(
    (node) => normalizeArchiveSearchText(
      normalizedQuery.includes("/") ? node.dataset.searchYomi : node.dataset.searchText,
    ).includes(normalizedQuery),
  );
  if (!target) {
    return;
  }
  target.classList.add("search-match");
  window.requestAnimationFrame(() => {
    target.scrollIntoView({ block: "center", behavior: "auto" });
  });
}

function scrollArchiveCorrectionToFirstMatch(doc, query) {
  const normalizedQuery = normalizeArchiveSearchText(query).trim();
  if (!normalizedQuery) {
    return;
  }
  const unitIndex = (doc.units || []).findIndex(
    (unit) => archiveSearchUnitHitCount(unit, normalizedQuery) > 0,
  );
  if (unitIndex < 0) {
    return;
  }
  const target = el.workflowPreviewBody?.querySelector(
    `.archive-correction-row[data-unit-index="${unitIndex}"]`,
  );
  if (!target) {
    return;
  }
  target.classList.add("search-match");
  window.requestAnimationFrame(() => {
    target.scrollIntoView({ block: "center", behavior: "auto" });
  });
}

function renderCorpusMapTileGrid(docs) {
  const wrap = document.createElement("div");
  wrap.className = "workflow-tile-grid corpus-map-grid";
  for (const doc of docs) {
    const tile = document.createElement("button");
    tile.type = "button";
    const correctionCount = Number(doc.finalized_correction_count || 0);
    const correctionSentenceCount = Number(doc.finalized_correction_sentence_count || 0);
    const manualCorrectionCount = Number(doc.manual_correction_required_count || 0);
    const localCorrection = archiveCorrectionRecordForDoc(doc);
    tile.className = "workflow-doc-tile resolved corpus-map-tile";
    tile.classList.toggle("has-finalized-corrections", correctionCount > 0);
    tile.classList.toggle("has-local-correction", localCorrection?.status === "draft");
    tile.classList.toggle("has-submitted-correction", localCorrection?.status === "submitted");
    tile.classList.toggle("has-manual-corrections", manualCorrectionCount > 0);
    tile.innerHTML = `
      <span>${escapeHtml(workflowStatusGlyph("resolved"))}</span>
      <strong>${escapeHtml(doc.track_doc_seq)}</strong>
      ${correctionCount ? `<em class="correction-count-badge">${escapeHtml(correctionCount)}</em>` : ""}
      ${manualCorrectionCount ? `<em class="manual-correction-count-badge">${escapeHtml(manualCorrectionCount)}</em>` : ""}
      ${localCorrection ? `<em class="local-correction-badge ${escapeHtml(localCorrection.status)}">${localCorrection.status === "submitted" ? "提出済" : "編集中"}</em>` : ""}
    `;
    tile.title = `${doc.doc_id || ""}\n${doc.text_preview || ""}${
      correctionCount ? `\n${formatArchiveCorrectionSummary(correctionCount, correctionSentenceCount)}` : ""
    }${manualCorrectionCount ? `\n要手動修正: ${manualCorrectionCount}件` : ""}${localCorrection ? `\n${localCorrection.status === "submitted" ? "サーバー処理待ちの提出済み修正" : "ローカル修正案"}` : ""}`;
    tile.addEventListener("click", () => {
      openArchiveDocumentSummary(doc, {
        scrollToManualCorrection: manualCorrectionCount > 0,
      }).catch((error) => {
        showStatus(`確定済み文書を開けませんでした: ${error.message}`, true);
      });
    });
    wrap.append(tile);
  }
  return wrap;
}

async function openArchiveDocumentSummary(summary, options = {}) {
  const doc = await loadArchiveDocument(summary);
  if (!doc) {
    throw new Error("アーカイブ内に文書が見つかりません。");
  }
  openArchiveCorrectionEditor(doc, options);
}

function formatArchiveCorrectionSummary(count, sentenceCount) {
  const corrections = Number(count || 0);
  const sentences = Number(sentenceCount || 0);
  return `修正 ${corrections}回 · 変更文 ${sentences}件`;
}

function archiveCorrectionDocKey(doc) {
  return [
    state.archiveCurrentTrack || "dev",
    String(stableDocumentSeq(doc) || ""),
    String(doc.doc_id || ""),
  ].join("::");
}

function loadArchiveCorrectionStore() {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(archiveCorrectionStorageKey) || "{}");
    const records = parsed && typeof parsed.records === "object" && parsed.records ? { ...parsed.records } : {};
    let migrated = Number(parsed?.schema_version || 1) !== archiveCorrectionStoreSchemaVersion;
    for (const [key, record] of Object.entries(records)) {
      if (record?.status === "submitted" && !record?.submission_id) {
        delete records[key];
        migrated = true;
      }
    }
    const store = {
      schema_version: archiveCorrectionStoreSchemaVersion,
      records,
    };
    if (migrated) {
      saveArchiveCorrectionStore(store);
    }
    return store;
  } catch {
    window.localStorage.removeItem(archiveCorrectionStorageKey);
    return { schema_version: archiveCorrectionStoreSchemaVersion, records: {} };
  }
}

function saveArchiveCorrectionStore(store) {
  if (Object.keys(store.records || {}).length) {
    window.localStorage.setItem(archiveCorrectionStorageKey, JSON.stringify(store));
  } else {
    window.localStorage.removeItem(archiveCorrectionStorageKey);
  }
}

function archiveCorrectionRecordForDoc(doc) {
  const store = loadArchiveCorrectionStore();
  const key = archiveCorrectionDocKey(doc);
  const record = store.records[key];
  if (!record) {
    return null;
  }
  if (record.status === "draft" && !record.submission_id) {
    store.records[key] = {
      ...record,
      schema_version: archiveCorrectionStoreSchemaVersion,
      submission_id: newArchiveCorrectionSubmissionId(doc),
      base_archive_revision: record.base_archive_revision || doc.archive_revision || "",
    };
    saveArchiveCorrectionStore(store);
  }
  const currentRecord = store.records[key];
  const appliedSubmissionIds = new Set(
    doc.applied_finalized_correction_submission_ids || [],
  );
  if (
    currentRecord.status === "submitted" &&
    currentRecord.submission_id &&
    appliedSubmissionIds.has(currentRecord.submission_id)
  ) {
    delete store.records[key];
    saveArchiveCorrectionStore(store);
    return null;
  }
  if (!Array.isArray(doc.units)) {
    return currentRecord;
  }
  const currentUnits = new Map((doc.units || []).map((unit) => [String(unit.unit_id || ""), unit]));
  if (
    currentRecord.status === "submitted" &&
    currentRecord.submission_id &&
    (currentRecord.units || []).length > 0 &&
    (currentRecord.units || []).every((saved) => {
      const current = currentUnits.get(String(saved.unit_id || ""));
      return (current?.applied_finalized_correction_submission_ids || []).includes(currentRecord.submission_id);
    })
  ) {
    delete store.records[key];
    saveArchiveCorrectionStore(store);
    return null;
  }
  if (currentRecord.status === "submitted" && currentRecord.submission_id) {
    return currentRecord;
  }
  const remaining = (currentRecord.units || []).filter((saved) => {
    const current = currentUnits.get(String(saved.unit_id || ""));
    if (saved.acknowledgement_only === true) {
      return !current || Boolean(current.manual_correction_required);
    }
    const disposition = saved.disposition || (saved.skip === false ? "Keep" : saved.skip ? "Skip" : "");
    if (disposition === "Keep" && current?.skipped) {
      return true;
    }
    if (disposition === "Skip" && !current?.skipped) {
      return true;
    }
    if (disposition === "Exclude" && !current?.excluded) {
      return true;
    }
    if (disposition === "Exclude" && current?.excluded) {
      return false;
    }
    return !current || !yomiTokenPairsEqual(
      archiveUnitYomiTokenPairs(current),
      correctionRecordTokenPairs(saved, "proposed"),
    );
  });
  if (remaining.length === 0) {
    delete store.records[key];
    saveArchiveCorrectionStore(store);
    return null;
  }
  if (remaining.length !== (currentRecord.units || []).length) {
    store.records[key] = { ...currentRecord, units: remaining };
    saveArchiveCorrectionStore(store);
  }
  return store.records[key];
}

function persistArchiveCorrectionDraft(doc, parsedChanges = null) {
  const parsed = parsedChanges || collectArchiveCorrectionChanges(doc);
  const store = loadArchiveCorrectionStore();
  const key = archiveCorrectionDocKey(doc);
  if (!parsed.ok) {
    delete store.records[key];
    saveArchiveCorrectionStore(store);
    return null;
  }
  const now = Math.floor(Date.now() / 1000);
  const previous = store.records[key] || {};
  const submissionId =
    previous.status === "draft" && previous.submission_id
      ? previous.submission_id
      : newArchiveCorrectionSubmissionId(doc);
  store.records[key] = {
    schema_version: archiveCorrectionStoreSchemaVersion,
    submission_id: submissionId,
    track_name: state.archiveCurrentTrack || "dev",
    doc_id: doc.doc_id || "",
    track_doc_seq: stableDocumentSeq(doc) || null,
    batch_name: doc.batch_name || "",
    archive_shard: doc.archive_shard || doc.shard_path || state.archiveCurrentShardPath || "",
    base_archive_revision: doc.archive_revision || "",
    status: "draft",
    units: parsed.changedUnits,
    created_at_epoch: previous.created_at_epoch || now,
    updated_at_epoch: now,
  };
  delete store.records[key].submitted_at_epoch;
  saveArchiveCorrectionStore(store);
  return store.records[key];
}

function newArchiveCorrectionSubmissionId(doc) {
  const randomPart = globalThis.crypto?.randomUUID?.().replaceAll("-", "") ||
    `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}`;
  const track = String(state.archiveCurrentTrack || "dev").replace(/[^A-Za-z0-9_-]+/g, "_");
  const seq = stableDocumentSeq(doc) || "unknown";
  return `finalized_correction__client__${track}_${seq}__${randomPart}`;
}

function markArchiveCorrectionSubmitted(key) {
  const store = loadArchiveCorrectionStore();
  const record = store.records[key];
  if (!record) {
    showStatus("ローカルの修正案はすでに削除されています。", true);
    return;
  }
  store.records[key] = {
    ...record,
    status: "submitted",
    submitted_at_epoch: Math.floor(Date.now() / 1000),
  };
  saveArchiveCorrectionStore(store);
  closeWorkflowDocumentPreview();
  render();
  showStatus("修正をローカルで提出済みにしました。サーバーに反映されるまで表示されます。");
}

function archiveCorrectionIsEditing() {
  if (el.workflowPreviewModal?.classList.contains("hidden")) {
    return false;
  }
  const active = document.activeElement;
  if (active?.closest?.(".archive-correction-editor")) {
    return true;
  }
  return Boolean(el.workflowPreviewBody?.querySelector?.(".archive-correction-editor:not(.hidden)"));
}

function archiveCorrectionHasUnsavedEdits() {
  if (el.workflowPreviewModal?.classList.contains("hidden")) {
    return false;
  }
  const editors = el.workflowPreviewBody?.querySelectorAll?.(".archive-correction-editor:not(.hidden)") || [];
  return [...editors].some((editor) => {
    const row = editor.closest(".archive-correction-row");
    const textarea = editor.querySelector(".archive-correction-unit-textarea");
    if (!row || !textarea) {
      return false;
    }
    const baseline = row.dataset.proposedYomi || editor.dataset.originalYomi || "";
    return String(textarea.value || "").trim() !== String(baseline).trim();
  });
}

function openArchiveCorrectionEditor(doc, { scrollToManualCorrection = false } = {}) {
  const units = doc.units || [];
  const localCorrection = archiveCorrectionRecordForDoc(doc);
  el.workflowPreviewTitle.textContent = `文書 ${doc.track_doc_seq} を修正`;
  const correctionCount = Number(doc.finalized_correction_count || 0);
  const correctionSentenceCount = Number(doc.finalized_correction_sentence_count || 0);
  el.workflowPreviewMeta.textContent = `${doc.doc_id || ""} · ${doc.batch_name || ""} · 確定済みデータの修正${
    correctionCount ? ` · ${formatArchiveCorrectionSummary(correctionCount, correctionSentenceCount)}` : ""
  }${doc.manual_correction_required_count ? ` · 要手動修正: ${doc.manual_correction_required_count}件` : ""}`;
  el.workflowPreviewBody.innerHTML = "";

  const intro = document.createElement("p");
  intro.className = "muted archive-correction-help";
  intro.textContent =
    "修正する確定済みの文を選んでください。この画面では既存の文区切りを維持したまま読みを修正します。文区切りの変更にはまだ対応していません。";
  el.workflowPreviewBody.append(intro);

  if (localCorrection) {
    const localState = document.createElement("p");
    localState.className = `archive-correction-local-state ${localCorrection.status}`;
    localState.textContent = localCorrection.status === "submitted"
      ? "ローカルでは提出済みです。サーバーによるIssueの取り込みを待っています。再編集・再提出もできます。"
      : "このブラウザに保存された修正案を復元しました。";
    el.workflowPreviewBody.append(localState);
  }

  const list = document.createElement("div");
  list.className = "archive-correction-list";
  for (const [index, unit] of units.entries()) {
    list.append(renderArchiveCorrectionRow(unit, index, doc, localCorrection));
  }
  el.workflowPreviewBody.append(list);

  const validation = document.createElement("p");
  validation.className = "archive-correction-validation muted";
  validation.dataset.archiveCorrectionSummary = "true";
  validation.textContent = doc.manual_correction_required_count
    ? "変更がなくても、要手動修正の確認結果をIssueで提出できます。"
    : "一つ以上の文を編集・保存してから、JSONをコピーしてIssueを開いてください。";
  el.workflowPreviewBody.append(validation);

  el.workflowPreviewActions.innerHTML = "";
  const copyOnlyButton = document.createElement("button");
  copyOnlyButton.type = "button";
  copyOnlyButton.className = "secondary-button";
  copyOnlyButton.textContent = "JSONをコピー";
  copyOnlyButton.dataset.archiveCorrectionExport = "true";
  copyOnlyButton.disabled = true;
  copyOnlyButton.addEventListener("click", async () => {
    await copyArchiveCorrectionPayload(doc, { openIssue: false });
  });

  const openIssueButton = document.createElement("button");
  openIssueButton.type = "button";
  openIssueButton.textContent = "JSONをコピーしてIssueを開く";
  openIssueButton.dataset.archiveCorrectionExport = "true";
  openIssueButton.disabled = true;
  openIssueButton.addEventListener("click", async () => {
    await copyArchiveCorrectionPayload(doc, { openIssue: true });
  });

  const note = document.createElement("p");
  note.className = "muted";
  note.textContent = "修正依頼はJSONとしてコピーし、GitHub Issueから提出します。";
  el.workflowPreviewActions.append(copyOnlyButton, openIssueButton, note);
  updateArchiveCorrectionSummary();
  el.workflowPreviewBody.scrollTop = 0;
  el.workflowPreviewBody.scrollLeft = 0;
  el.workflowPreviewModal.classList.remove("hidden");
  window.requestAnimationFrame(() => {
    if (scrollToManualCorrection) {
      const flaggedRow = el.workflowPreviewBody.querySelector(
        ".archive-correction-row.manual-correction-required",
      );
      if (flaggedRow) {
        flaggedRow.scrollIntoView({ block: "center", behavior: "auto" });
        return;
      }
    }
    el.workflowPreviewBody.scrollTo({ top: 0, left: 0, behavior: "auto" });
  });
  updateRuntimePollingForInteraction();
}

function renderArchiveCorrectionRow(unit, index, doc, localCorrection = null) {
  const row = document.createElement("article");
  row.className = "archive-correction-row";
  row.dataset.unitIndex = String(index);
  row.dataset.originalDisposition = unit.skipped ? "Skip" : unit.excluded ? "Exclude" : "Keep";
  row.classList.toggle("manual-correction-required", Boolean(unit.manual_correction_required));
  row.classList.toggle("skipped-tombstone", Boolean(unit.skipped));
  row.classList.toggle("excluded-tombstone", Boolean(unit.excluded));

  const summary = document.createElement("div");
  summary.className = "archive-correction-row-summary";
  const rubyLine = document.createElement("div");
  rubyLine.className = "ruby-line resolved-ruby-line";
  const originalTokenPairs = archiveUnitYomiTokenPairs(unit);
  const originalEditableYomi = serializeEditableYomiTokens(originalTokenPairs);
  const footnotes = normalizedStrongRepairFootnotes(unit.strong_repair_evidence || []);
  if (originalTokenPairs.length) {
    rubyLine.append(
      ...renderReadonlyRubyFromTokensWithFootnotes(
        yomiTokenPairObjects(originalTokenPairs),
        unit.ruby_tokens || [],
        unit.strong_repair_evidence || [],
      ),
    );
  } else if (unit.excluded) {
    rubyLine.textContent = localizedTombstoneLabel(unit.tombstone_label);
  } else {
    rubyLine.textContent = unit.text || "";
  }
  const editButton = document.createElement("button");
  editButton.type = "button";
  editButton.className = "secondary-button compact-button";
  editButton.dataset.archiveYomiEdit = "true";
  editButton.textContent = unit.skipped ? "復帰して編集" : "編集";
  editButton.disabled = Boolean(unit.excluded);
  if (unit.skipped) {
    editButton.title = "スキップされた文をコーパスに戻し、保存済みの読みを編集します";
  }
  editButton.addEventListener("click", () => openArchiveCorrectionRowEditor(row, unit));
  const actions = document.createElement("div");
  actions.className = "archive-correction-row-actions";
  if (unit.excluded) {
    const excluded = document.createElement("span");
    excluded.className = "excluded-tombstone-label";
    excluded.textContent = localizedTombstoneLabel(unit.tombstone_label);
    actions.append(excluded);
  }
  if (unit.manual_correction_required) {
    const flag = document.createElement("span");
    flag.className = "manual-correction-row-flag";
    flag.textContent = "⚑";
    flag.title = "後で手動修正が必要です";
    flag.setAttribute("aria-label", "後で手動修正が必要です");
    actions.append(flag);
  }
  if (!unit.excluded) {
    const dispositionControls = document.createElement("div");
    dispositionControls.className = "archive-disposition-controls";
    dispositionControls.setAttribute("role", "group");
    dispositionControls.setAttribute("aria-label", "コーパスでの扱い");
    const options = [
      {
        value: unit.skipped ? "Keep" : "Skip",
        label: unit.skipped ? "復帰" : "スキップ",
        title: unit.skipped ? "この文をコーパスに戻します" : "後から復帰可能な状態でスキップします",
      },
      {
        value: "Exclude",
        label: "除外",
        title: "機密性の高い内容を提出後に恒久的に除外します",
      },
    ];
    for (const option of options) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `secondary-button compact-button archive-disposition-button disposition-${option.value.toLowerCase()}`;
      button.dataset.disposition = option.value;
      button.textContent = option.label;
      button.title = option.title;
      button.addEventListener("click", () => {
        const original = row.dataset.originalDisposition || "Keep";
        const current = row.dataset.proposedDisposition || original;
        const next = current === option.value ? original : option.value;
        setArchiveCorrectionDisposition(row, unit, doc, next);
      });
      dispositionControls.append(button);
    }
    actions.append(dispositionControls);
  }
  actions.append(editButton);
  summary.append(rubyLine, actions);

  const saved = document.createElement("div");
  saved.className = "archive-correction-saved hidden";
  saved.innerHTML = `
    <strong>編集後の読み</strong>
    <code></code>
  `;

  const editor = document.createElement("div");
  editor.className = "archive-correction-editor hidden";
  editor.dataset.originalYomi = originalEditableYomi;
  editor.innerHTML = `
    <label>
      <span>読みデータ</span>
      <textarea class="archive-correction-unit-textarea" rows="3">${escapeHtml(originalEditableYomi)}</textarea>
    </label>
    <p class="archive-correction-row-validation muted">まだ変更されていません。</p>
    <div class="archive-correction-editor-actions">
      <button type="button" class="secondary-button compact-button" data-archive-correction-save>保存</button>
      <button type="button" class="secondary-button compact-button" data-archive-correction-cancel>キャンセル</button>
    </div>
  `;
  const textarea = editor.querySelector(".archive-correction-unit-textarea");
  textarea.addEventListener("input", () => updateArchiveCorrectionRowState(row, unit));
  textarea.addEventListener("keydown", (event) => {
    handleArchiveCorrectionEditorKeydown(event, row, unit, doc);
  });
  editor.querySelector("[data-archive-correction-save]")?.addEventListener("click", () => saveArchiveCorrectionRow(row, unit, doc));
  editor.querySelector("[data-archive-correction-cancel]")?.addEventListener("click", () => cancelArchiveCorrectionRowEdit(row));

  row.append(summary);
  appendStrongRepairFootnoteList(row, footnotes);
  row.append(saved, editor);
  const restored = (localCorrection?.units || []).find(
    (savedUnit) =>
      String(savedUnit.unit_id || "") === String(unit.unit_id || "") &&
      String(savedUnit.text || "") === String(unit.text || "") &&
      yomiTokenPairsEqual(correctionRecordTokenPairs(savedUnit, "original"), originalTokenPairs),
  );
  const restoredProposed = restored ? serializeEditableYomiTokens(correctionRecordTokenPairs(restored, "proposed")) : "";
  const restoredDisposition = restored?.disposition || (restored?.skip === false ? "Keep" : "");
  if (restoredProposed || restoredDisposition) {
    if (restoredProposed && restoredProposed !== originalEditableYomi) {
      row.dataset.proposedYomi = restoredProposed;
    }
    if (restoredDisposition) {
      row.dataset.proposedDisposition = restoredDisposition;
    }
    updateArchiveCorrectionChangedState(row);
    row.classList.toggle("submitted", localCorrection.status === "submitted");
    textarea.value = restoredProposed || originalEditableYomi;
    renderArchiveCorrectionSavedYomi(row);
  }
  updateArchiveDispositionControls(row);
  return row;
}

function handleArchiveCorrectionEditorKeydown(event, row, unit, doc) {
  if (
    event.key !== "Enter" ||
    event.shiftKey ||
    event.isComposing ||
    event.keyCode === 229
  ) {
    return;
  }
  event.preventDefault();
  saveArchiveCorrectionRow(row, unit, doc);
}

function openArchiveCorrectionRowEditor(row, unit) {
  const editor = row.querySelector(".archive-correction-editor");
  const button = row.querySelector("[data-archive-yomi-edit]");
  if (!editor || !button) {
    return;
  }
  const textarea = editor.querySelector(".archive-correction-unit-textarea");
  if (textarea) {
    textarea.value = row.dataset.proposedYomi || editor.dataset.originalYomi || "";
  }
  editor.classList.remove("hidden");
  button.disabled = true;
  updateArchiveCorrectionRowState(row, unit);
  textarea?.focus();
}

function updateArchiveCorrectionRowState(row, unit) {
  const editor = row.querySelector(".archive-correction-editor");
  const textarea = editor?.querySelector(".archive-correction-unit-textarea");
  const validationNode = editor?.querySelector(".archive-correction-row-validation");
  if (!editor || !textarea || !validationNode) {
    return;
  }
  const original = editor.dataset.originalYomi || "";
  const proposed = String(textarea.value || "").trim();
  const saved = row.dataset.proposedYomi || "";
  const baseline = saved || original;
  const changed = proposed !== original;
  const dirty = proposed !== baseline;
  row.classList.remove("invalid");
  validationNode.classList.remove("error");
  validationNode.textContent = !dirty
    ? row.classList.contains("changed")
      ? "保存済みです。"
      : "まだ変更されていません。"
    : changed
      ? "未保存の変更があります。保存すると検証され、Issue用JSONに含まれます。"
      : "保存すると、保存済みの修正が取り消されます。";
  updateArchiveCorrectionSummary();
}

function collectArchiveCorrectionChanges(doc, { includeFlagAcknowledgements = false } = {}) {
  const units = doc.units || [];
  const rows = [...(el.workflowPreviewBody?.querySelectorAll?.(".archive-correction-row") || [])];
  const changedUnits = [];
  for (const row of rows) {
    const index = Number(row.dataset.unitIndex);
    const unit = units[index];
    const editor = row.querySelector(".archive-correction-editor");
    const original = String(editor.dataset.originalYomi || "").trim();
    const proposed = String(row.dataset.proposedYomi || original).trim();
    const originalDisposition = row.dataset.originalDisposition || "Keep";
    const proposedDisposition = row.dataset.proposedDisposition || originalDisposition;
    const dispositionChanged = proposedDisposition !== originalDisposition;
    if (!unit || (proposed === original && !dispositionChanged)) {
      continue;
    }
    const validation = validateRenderedYomiCorrection(unit, proposed);
    if (!validation.ok) {
      return { ok: false, error: `文 ${unit.unit_id || index + 1}: ${validation.error}` };
    }
    changedUnits.push({
      unit_id: String(unit.unit_id || ""),
      unit_seq: Number(unit.unit_seq || index + 1),
      text: unit.text || "",
      original_yomi_tokens: archiveUnitYomiTokenPairs(unit),
      proposed_yomi_tokens: validation.tokens,
      ...(dispositionChanged
        ? {
            disposition: proposedDisposition,
            skip: proposedDisposition !== "Keep",
          }
        : {}),
    });
  }
  if (!changedUnits.length && includeFlagAcknowledgements) {
    for (const [index, unit] of units.entries()) {
      if (!unit.manual_correction_required || unit.excluded) {
        continue;
      }
      const originalTokens = archiveUnitYomiTokenPairs(unit);
      const original = serializeEditableYomiTokens(originalTokens);
      const validation = validateRenderedYomiCorrection(unit, original);
      if (!validation.ok) {
        return { ok: false, error: `文 ${unit.unit_id || index + 1}: ${validation.error}` };
      }
      changedUnits.push({
        unit_id: String(unit.unit_id || ""),
        unit_seq: Number(unit.unit_seq || index + 1),
        text: unit.text || "",
        original_yomi_tokens: originalTokens,
        proposed_yomi_tokens: validation.tokens,
        acknowledgement_only: true,
      });
    }
  }
  if (!changedUnits.length) {
    return { ok: false, error: "読みの変更がありません。" };
  }
  return {
    ok: true,
    changedUnits,
    acknowledgementOnly: changedUnits.every((unit) => unit.acknowledgement_only === true),
  };
}

function saveArchiveCorrectionRow(row, unit, doc) {
  const editor = row.querySelector(".archive-correction-editor");
  const textarea = editor?.querySelector(".archive-correction-unit-textarea");
  const validationNode = editor?.querySelector(".archive-correction-row-validation");
  if (!editor || !textarea || !validationNode) {
    return;
  }
  const original = String(editor.dataset.originalYomi || "").trim();
  const proposed = normalizeRenderedYomiCorrectionReadings(String(textarea.value || "").trim());
  textarea.value = proposed;
  if (proposed === original && !unit.skipped) {
    clearArchiveCorrectionRow(row, doc);
    return;
  }
  const validation = validateRenderedYomiCorrection(unit, proposed);
  if (!validation.ok) {
    row.classList.add("invalid");
    validationNode.textContent = validation.error;
    validationNode.classList.add("error");
    updateArchiveCorrectionSummary();
    return;
  }
  row.dataset.proposedYomi = proposed;
  if (unit.skipped) {
    row.dataset.proposedDisposition = "Keep";
  }
  updateArchiveCorrectionChangedState(row);
  updateArchiveDispositionControls(row);
  row.classList.remove("invalid", "submitted");
  validationNode.textContent = "保存済みです。";
  validationNode.classList.remove("error");
  renderArchiveCorrectionSavedYomi(row);
  closeArchiveCorrectionRowEditor(row);
  persistArchiveCorrectionDraft(doc);
  updateArchiveCorrectionSummary();
}

function clearArchiveCorrectionRow(row, doc = null) {
  delete row.dataset.proposedYomi;
  row.classList.remove("invalid");
  const editor = row.querySelector(".archive-correction-editor");
  const textarea = editor?.querySelector(".archive-correction-unit-textarea");
  const validationNode = editor?.querySelector(".archive-correction-row-validation");
  if (textarea && editor) {
    textarea.value = editor.dataset.originalYomi || "";
  }
  if (validationNode) {
    validationNode.textContent = "まだ変更されていません。";
    validationNode.classList.remove("error");
  }
  renderArchiveCorrectionSavedYomi(row);
  updateArchiveCorrectionChangedState(row);
  closeArchiveCorrectionRowEditor(row);
  if (doc) {
    persistArchiveCorrectionDraft(doc);
  }
  updateArchiveCorrectionSummary();
}

function cancelArchiveCorrectionRowEdit(row) {
  const editor = row.querySelector(".archive-correction-editor");
  const textarea = editor?.querySelector(".archive-correction-unit-textarea");
  if (editor && textarea) {
    const baseline = row.dataset.proposedYomi || editor.dataset.originalYomi || "";
    const proposed = String(textarea.value || "").trim();
    if (proposed !== baseline && !window.confirm("未保存の読み編集を破棄しますか？")) {
      return;
    }
    textarea.value = baseline;
    const validationNode = editor.querySelector(".archive-correction-row-validation");
    if (validationNode) {
      validationNode.textContent = row.classList.contains("changed") ? "保存済みです。" : "まだ変更されていません。";
      validationNode.classList.remove("error");
    }
    row.classList.remove("invalid");
  }
  closeArchiveCorrectionRowEditor(row);
  updateArchiveCorrectionSummary();
}

function closeArchiveCorrectionRowEditor(row) {
  row.querySelector(".archive-correction-editor")?.classList.add("hidden");
  const button = row.querySelector("[data-archive-yomi-edit]");
  if (button) {
    button.disabled = false;
  }
}

function renderArchiveCorrectionSavedYomi(row) {
  const saved = row.querySelector(".archive-correction-saved");
  const code = saved?.querySelector("code");
  if (!saved || !code) {
    return;
  }
  const proposed = row.dataset.proposedYomi || "";
  saved.classList.toggle("hidden", !proposed);
  code.textContent = proposed;
}

function setArchiveCorrectionDisposition(row, unit, doc, disposition) {
  const original = row.dataset.originalDisposition || "Keep";
  if (disposition === original) {
    delete row.dataset.proposedDisposition;
  } else {
    row.dataset.proposedDisposition = disposition;
  }
  updateArchiveDispositionControls(row);
  updateArchiveCorrectionChangedState(row);
  row.classList.remove("submitted");
  persistArchiveCorrectionDraft(doc);
  updateArchiveCorrectionSummary();
}

function updateArchiveDispositionControls(row) {
  const original = row.dataset.originalDisposition || "Keep";
  const current = row.dataset.proposedDisposition || original;
  row.classList.toggle("draft-skip", current === "Skip");
  row.classList.toggle("draft-exclude", current === "Exclude");
  for (const button of row.querySelectorAll(".archive-disposition-button")) {
    button.setAttribute("aria-pressed", String(button.dataset.disposition === current));
  }
}

function updateArchiveCorrectionChangedState(row) {
  const editor = row.querySelector(".archive-correction-editor");
  const originalYomi = String(editor?.dataset.originalYomi || "").trim();
  const proposedYomi = String(row.dataset.proposedYomi || originalYomi).trim();
  const originalDisposition = row.dataset.originalDisposition || "Keep";
  const proposedDisposition = row.dataset.proposedDisposition || originalDisposition;
  row.classList.toggle(
    "changed",
    proposedYomi !== originalYomi || proposedDisposition !== originalDisposition,
  );
}

function updateArchiveCorrectionSummary() {
  const summary = el.workflowPreviewBody?.querySelector?.("[data-archive-correction-summary='true']");
  if (!summary) {
    return;
  }
  const changed = el.workflowPreviewBody.querySelectorAll(".archive-correction-row.changed").length;
  const invalid = el.workflowPreviewBody.querySelectorAll(".archive-correction-row.invalid").length;
  const openEditors = el.workflowPreviewBody.querySelectorAll(".archive-correction-editor:not(.hidden)").length;
  const flagged = el.workflowPreviewBody.querySelectorAll(
    ".archive-correction-row.manual-correction-required",
  ).length;
  const exportButtons = el.workflowPreviewActions?.querySelectorAll?.("[data-archive-correction-export='true']") || [];
  const canExport = (changed > 0 || flagged > 0) && invalid === 0 && openEditors === 0;
  for (const button of exportButtons) {
    button.disabled = !canExport;
  }
  if (invalid) {
    summary.textContent = `${invalid}件の文にエラーがあります。提出前に修正してください。`;
    summary.classList.add("error");
    return;
  }
  if (openEditors) {
    summary.textContent = "開いている編集欄を保存またはキャンセルしてから提出してください。";
    summary.classList.add("error");
    return;
  }
  summary.textContent = changed
    ? `${changed}件の変更をIssueで提出できます。`
    : flagged
      ? `要手動修正 ${flagged}件を、変更なしの確認結果としてIssueで提出できます。`
      : "一つ以上の文を編集・保存してから、JSONをコピーしてIssueを開いてください。";
  summary.classList.remove("error");
}

function validateRenderedYomiCorrection(unit, proposed) {
  if (!proposed) {
    return { ok: false, error: "読みデータが空です。" };
  }
  const tokens = parseRenderedYomiCorrectionTokens(proposed);
  if (!tokens.length) {
    return { ok: false, error: "読みデータにトークンがありません。" };
  }
  const surfaceText = [];
  const baselinePairCounts = new Map();
  for (const [surface, reading] of archiveUnitYomiTokenPairs(unit)) {
    const key = JSON.stringify([surface, reading]);
    baselinePairCounts.set(key, (baselinePairCounts.get(key) || 0) + 1);
  }
  for (const token of tokens) {
    if (!token.ok) {
      return { ok: false, error: token.error };
    }
    const baselineKey = JSON.stringify([token.surface, token.reading]);
    const baselineCount = baselinePairCounts.get(baselineKey) || 0;
    if (baselineCount) {
      baselinePairCounts.set(baselineKey, baselineCount - 1);
    } else {
      const readingValidation = validateRenderedYomiReading(token.surface, token.reading);
      if (!readingValidation.ok) {
        return { ok: false, error: `トークン ${token.raw}: ${readingValidation.error}` };
      }
    }
    surfaceText.push(token.surface);
  }
  const originalSurfaceText = archiveUnitYomiTokenPairs(unit).map(([surface]) => surface).join("");
  const expectedText = normalizeCorrectionSourceText(originalSurfaceText || unit.text || "");
  const proposedText = normalizeCorrectionSourceText(surfaceText.join(""));
  if (expectedText && proposedText !== expectedText) {
    return {
      ok: false,
      error: `原文が変わっています: 入力 ${proposedText} / 期待値 ${expectedText}。`,
    };
  }
  return { ok: true, tokens: tokens.map((token) => [token.surface, token.reading]) };
}

function parseRenderedYomiCorrectionTokens(rendered) {
  return String(rendered || "")
    .trim()
    .split(/[ \t\r\n]+/)
    .filter(Boolean)
    .map((raw) => {
      if (raw === "/") {
        return { ok: true, raw, surface: " ", reading: "" };
      }
      const parsed = splitEditableYomiToken(raw);
      return parsed.ok ? { ...parsed, raw } : { ...parsed, raw, error: `トークン ${raw}: ${parsed.error}` };
    });
}

function normalizeRenderedYomiCorrectionReadings(rendered) {
  const tokens = parseRenderedYomiCorrectionTokens(rendered);
  if (!tokens.length || tokens.some((token) => !token.ok)) {
    return String(rendered || "").trim();
  }
  return serializeEditableYomiTokens(tokens.map((token) => [token.surface, hiraganaToKatakana(token.reading)]));
}

function normalizeCorrectionSourceText(value) {
  return String(value || "").replace(/[ \t\r\n\u00a0]+/g, "");
}

function hiraganaToKatakana(value) {
  return String(value || "").replace(/[ぁ-ゖ]/gu, (char) =>
    String.fromCharCode(char.charCodeAt(0) + 0x60),
  );
}

function validateRenderedYomiReading(surface, reading) {
  if (/^[ \u00a0\u3000]+$/u.test(surface)) {
      return reading && !/^[ \u00a0\u3000]+$/u.test(reading)
      ? { ok: false, error: "空白トークンの読みは空または空白である必要があります。" }
      : { ok: true };
  }
  if (isNumericOnlySurface(surface)) {
    if (allowsOptionalJapaneseNumeralReading(surface)) {
      return !reading || /^[ァ-ヺー]+$/u.test(reading)
        ? { ok: true }
        : { ok: false, error: "漢数字列の読みは空またはカタカナにしてください。" };
    }
    return reading ? { ok: false, error: "数字のみの表記には読みを付けないでください。" } : { ok: true };
  }
  if (numericCompoundReadings(surface)) {
    return reading && /^[ァ-ヺー]+$/u.test(reading)
      ? { ok: true }
      : { ok: false, error: "数字複合語の読みは空でないカタカナにしてください。" };
  }
  if (reading === "カオモジ") {
    return isSymbolicKaomojiCorrectionSurface(surface)
      ? { ok: true }
      : { ok: false, error: "「カオモジ」は記号的な顔文字だけに使用できます。" };
  }
  if (isStandaloneLaughterW(surface) && !reading) {
    return { ok: true };
  }
  if (/[\p{Script=Han}々〆〻A-Za-zＡ-Ｚａ-ｚ]/u.test(surface)) {
    if (!reading) {
      return { ok: false, error: "漢字または英字を含む表記には仮名の読みが必要です。" };
    }
    return /^[ァ-ヺー]+$/u.test(reading)
      ? { ok: true }
      : { ok: false, error: "漢字または英字を含む表記の読みはカタカナにしてください。" };
  }
  const expected = surface.replace(/[ぁ-ゖ]/gu, (char) => hiraganaToKatakana(char));
  if (reading === expected) {
    return { ok: true };
  }
  return { ok: false, error: `読みは ${expected || "（空）"} にしてください。` };
}

function isSymbolicKaomojiCorrectionSurface(surface) {
  return (
    [...String(surface || "")].length >= 3 &&
    !/^(?:\([ぁ-ゖァ-ヺ\p{Script=Han}々〆〻]+\)|（[ぁ-ゖァ-ヺ\p{Script=Han}々〆〻]+）)$/u.test(surface) &&
    /[^\p{L}\p{N}\s]/u.test(surface)
  );
}

function numericCompoundReadings(surface) {
  const normalized = String(surface || "").replace(/[０-９]/gu, (char) =>
    String.fromCharCode(char.charCodeAt(0) - 0xfee0),
  );
  return {
    "1日": ["イチニチ", "ツイタチ"],
    "2日": ["フツカ"],
    "3日": ["ミッカ"],
    "4日": ["ヨッカ"],
    "5日": ["イツカ"],
    "6日": ["ムイカ"],
    "7日": ["ナノカ"],
    "8日": ["ヨウカ"],
    "9日": ["ココノカ"],
    "10日": ["トオカ"],
    "14日": ["ジュウヨッカ"],
    "20日": ["ハツカ"],
    "24日": ["ニジュウヨッカ"],
    "1人": ["ヒトリ"],
    "2人": ["フタリ"],
    "1つ": ["ヒトツ"],
    "2つ": ["フタツ"],
    "3つ": ["ミッツ"],
    "4つ": ["ヨッツ"],
    "5つ": ["イツツ"],
    "6つ": ["ムッツ"],
    "7つ": ["ナナツ"],
    "8つ": ["ヤッツ"],
    "9つ": ["ココノツ"],
  }[normalized] || null;
}

function isNumericOnlySurface(surface) {
  // ASCII Roman-looking strings such as "I" and "III" stay alphabetic because
  // they are ambiguous. Single Japanese numeral kanji stay lexical, while
  // multi-character digit runs and circle zero belong to the numeric layer.
  const value = String(surface || "");
  if (!/^[0-9０-９ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫⅬⅭⅮⅯⅰⅱⅲⅳⅴⅵⅶⅷⅸⅹⅺⅻⅼⅽⅾⅿ〇○零一二三四五六七八九]+$/u.test(value)) {
    return false;
  }
  if (!/^[〇○零一二三四五六七八九]+$/u.test(value)) {
    return true;
  }
  return [...value].length >= 2 || value === "〇" || value === "○";
}

function allowsOptionalJapaneseNumeralReading(surface) {
  const value = String(surface || "");
  return [...value].length >= 2 && /^[〇○零一二三四五六七八九]+$/u.test(value);
}

function isStandaloneLaughterW(surface) {
  return /^[wｗ]+$/iu.test(String(surface || ""));
}

function archiveCorrectionIssueTitle(doc) {
  const seq = stableDocumentSeq(doc) || doc.doc_id || "unknown";
  return `[Finalized Correction] ${seq}`;
}

function buildArchiveCorrectionPayload(doc, parsed) {
  const localRecord = archiveCorrectionRecordForDoc(doc);
  return {
    submission_type: "finalized_correction_patch",
    schema_version: 2,
    submission_id: localRecord?.submission_id || newArchiveCorrectionSubmissionId(doc),
    track_name: state.archiveCurrentTrack || "dev",
    review_stage: "finalized_correction",
    doc_id: doc.doc_id || "",
    track_doc_seq: stableDocumentSeq(doc) || null,
    batch_name: doc.batch_name || "",
    generated_at_epoch: Math.floor(Date.now() / 1000),
    base_archive_revision: localRecord?.base_archive_revision || doc.archive_revision || "",
    source: {
      archive_index_path: state.manifest?.archive?.index_path || "",
      archive_shard: doc.archive_shard || doc.shard_path || state.archiveCurrentShardPath || "",
      page_url: window.location.href,
    },
    units: parsed.changedUnits,
  };
}

async function copyArchiveCorrectionPayload(doc, { openIssue = false } = {}) {
  const parsed = collectArchiveCorrectionChanges(doc, { includeFlagAcknowledgements: true });
  if (!parsed.ok) {
    showStatus(parsed.error, true);
    return false;
  }
  if (
    parsed.acknowledgementOnly &&
    !window.confirm(
      `${parsed.changedUnits.length}件の要手動修正フラグを、読みの変更なしで確認済みにします。続けますか？`,
    )
  ) {
    return false;
  }
  const exclusionCount = parsed.changedUnits.filter(
    (unit) => unit.disposition === "Exclude",
  ).length;
  if (
    exclusionCount &&
    !window.confirm(
      `${exclusionCount}文が、サーバーによるIssue適用後に恒久的に除外されます。続けますか？`,
    )
  ) {
    return false;
  }
  persistArchiveCorrectionDraft(doc, parsed);
  const payload = buildArchiveCorrectionPayload(doc, parsed);
  const copied = await copyTextToClipboard(formatSubmissionJson(payload));
  if (openIssue) {
    openUrlInNewTab(buildGithubIssueUrl(archiveCorrectionIssueTitle(doc)));
    state.pendingArchiveCorrectionKey = archiveCorrectionDocKey(doc);
    state.pendingIssueTaskId = null;
  }
  showStatus(
    copied
      ? "修正用JSONをコピーしました。GitHub Issueの本文に貼り付けてください。"
      : "クリップボードへのコピーに失敗しました。編集画面から修正用JSONを手動でコピーしてください。",
    !copied,
  );
  return copied;
}

function renderWorkflowTaskDashboard(allDocs, actionableDocs, task) {
  const dashboard = document.createElement("div");
  dashboard.className = "workflow-dashboard";
  dashboard.append(renderWorkflowPackMap(allDocs));

  const body = document.createElement("div");
  body.className = "workflow-body";
  const queues = document.createElement("div");
  queues.className = "workflow-queues";
  queues.append(
    renderWorkflowQueue({
      docs: allDocs,
      task,
      queueStage: "yomi_final_review",
      title: "一括レビュー",
      actionLabel: "一括レビューを開始",
      takeNextCount: 5,
      takeNextOptions: workflowTakeNextOptions,
    }),
    renderWorkflowQueue({
      docs: allDocs,
      task,
      queueStage: "yomi_strong_repair_review",
      title: "詳細修正",
      actionLabel: "詳細修正を開始",
      takeNextCount: 5,
      takeNextOptions: workflowTakeNextOptions,
    }),
  );
  body.append(queues);
  dashboard.append(body);
  el.taskDocList.append(dashboard);
}

function renderWorkflowPackMap(docs) {
  const section = document.createElement("section");
  section.className = "workflow-pack-map";
  const rows = workflowDocumentStates(docs);
  const manualCorrectionCount = archiveManualCorrectionCount();
  section.innerHTML = `
    <div class="workflow-heading">
      <div>
        <h3>作業中の文書</h3>
        <p class="muted">現在レビュー対象になっている文書と、その処理状況です。</p>
      </div>
      <div class="workflow-heading-actions">
        <div class="workflow-legend-inline">
          <span><span class="workflow-dot strong"></span>詳細修正</span>
          <span><span class="workflow-dot final"></span>一括レビュー待ち</span>
        </div>
        ${hasReviewArchive() ? `<button class="secondary-button compact-button corpus-map-link" type="button" title="コーパスマップを開く">確定済みコーパス${manualCorrectionCount ? `<em class="corpus-map-manual-correction-badge" title="要手動修正 ${manualCorrectionCount}件">! ${manualCorrectionCount}</em>` : ""}</button>` : ''}
      </div>
    </div>
  `;
  section.querySelector(".corpus-map-link")?.addEventListener("click", () => {
    openArchiveBrowser().catch((error) => {
      showStatus(`コーパスマップを開けませんでした: ${error.message}`, true);
    });
  });
  const tileGrid = document.createElement("div");
  tileGrid.className = "workflow-tile-grid";
  for (const row of rows) {
    tileGrid.append(renderWorkflowTile(row, { compact: false }));
  }
  section.append(tileGrid);
  return section;
}

function archiveManualCorrectionCount() {
  const track = state.archiveIndex?.tracks?.dev || state.manifest?.archive?.tracks?.dev;
  return Number(track?.manual_correction_required_count || 0);
}

function renderWorkflowQueue({
  docs,
  task,
  queueStage,
  title,
  actionLabel,
  takeNextCount,
  takeNextOptions = null,
}) {
  const section = document.createElement("section");
  section.className = `workflow-queue ${queueStage === "yomi_final_review" ? "final" : "strong"}`;
  const queueDocs = dedupeWorkflowQueueDocs(
    docs.filter((doc) => workflowDocBelongsInQueue(doc, queueStage)),
    queueStage,
  );
  const actionableDocs = queueDocs.filter((doc) => docIsActionable(doc));
  const selectedDocs = actionableDocs.filter((doc) => task.doc_ids.includes(taskDocKey(doc)));
  const selectedItems = itemsForTask(task).filter((item) => itemReviewStage(item) === queueStage);
  const localSubmittedCount = queueDocs.filter((doc) => docIsSubmittedLocally(doc) && !docIsProcessingOnServer(doc)).length;
  const processingCount = queueDocs.filter((doc) => docIsProcessingOnServer(doc)).length;
  const heading = document.createElement("div");
  heading.className = "workflow-heading";
  heading.innerHTML = `
    <div>
      <h3>${escapeHtml(title)}</h3>
      <p class="muted">作業可能: ${actionableDocs.length}文書${localSubmittedCount ? ` · ローカル提出済み: ${localSubmittedCount}` : ""}${processingCount ? ` · サーバー処理中: ${processingCount}` : ""}</p>
    </div>
    <strong class="workflow-selected-count">${selectedDocs.length
      ? `選択中: ${selectedDocs.length}文書、${selectedItems.length}項目`
      : "未選択"}</strong>
  `;
  section.append(heading);

  const tiles = document.createElement("div");
  tiles.className = "workflow-tile-grid queue";
  for (const doc of queueDocs) {
    const row = workflowDocumentStateForQueueDoc(doc);
    const tile = renderWorkflowTile(row, { compact: true });
    const docKey = taskDocKey(doc);
    const isSelected = docIsActionable(doc) && task.doc_ids.includes(docKey);
    tile.classList.toggle("selected", isSelected);
    if (docIsActionable(doc)) {
      tile.addEventListener("click", () => toggleDocumentTask(docKey, !isSelected));
    }
    tiles.append(tile);
  }
  if (!actionableDocs.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "このキューには作業可能な文書がありません。";
    tiles.append(empty);
  }
  section.append(tiles);

  const actions = document.createElement("div");
  actions.className = "button-row workflow-actions";
  const startButton = document.createElement("button");
  startButton.type = "button";
  startButton.textContent = actionLabel;
  startButton.disabled = selectedDocs.length === 0;
  startButton.addEventListener("click", () => startReviewTask());
  const takeNextButton = document.createElement("button");
  takeNextButton.type = "button";
  takeNextButton.className = "secondary-button";
  takeNextButton.textContent = takeNextOptions ? "次を選択" : `次の${takeNextCount}件を選択`;
  takeNextButton.disabled = actionableDocs.length === 0;
  let selectedTakeNextCount = takeNextCount;
  let takeNextControl = takeNextButton;
  if (takeNextOptions) {
    selectedTakeNextCount = loadWorkflowTakeNextCount(
      queueStage,
      takeNextOptions,
      takeNextCount,
    );
    const takeNextSelect = document.createElement("select");
    takeNextSelect.className = "workflow-take-next-count";
    takeNextSelect.setAttribute("aria-label", `${title}の文書数`);
    for (const optionCount of takeNextOptions) {
      const option = document.createElement("option");
      option.value = String(optionCount);
      option.textContent = String(optionCount);
      option.selected = optionCount === selectedTakeNextCount;
      takeNextSelect.append(option);
    }
    takeNextSelect.disabled = actionableDocs.length === 0;
    takeNextSelect.addEventListener("change", () => {
      selectedTakeNextCount = Number(takeNextSelect.value);
      saveWorkflowTakeNextCount(queueStage, selectedTakeNextCount);
    });
    const takeNextGroup = document.createElement("span");
    takeNextGroup.className = "workflow-take-next-control";
    takeNextGroup.append(takeNextButton, takeNextSelect);
    takeNextControl = takeNextGroup;
  }
  takeNextButton.addEventListener("click", () => {
    takeNextQueueDocuments(queueStage, selectedTakeNextCount);
  });
  const selectAllButton = document.createElement("button");
  selectAllButton.type = "button";
  selectAllButton.className = "secondary-button";
  selectAllButton.textContent = "すべて選択";
  selectAllButton.disabled = actionableDocs.length === 0 || selectedDocs.length === actionableDocs.length;
  selectAllButton.addEventListener("click", () => selectAllDocumentTasks(queueStage));
  const clearButton = document.createElement("button");
  clearButton.type = "button";
  clearButton.className = "secondary-button";
  clearButton.textContent = "選択解除";
  clearButton.disabled = selectedDocs.length === 0;
  clearButton.addEventListener("click", () => clearQueueTaskSelection(queueStage));
  actions.append(startButton, takeNextControl, selectAllButton, clearButton);
  section.append(actions);
  return section;
}

function renderWorkflowTile(row, { compact }) {
  const tile = document.createElement("button");
  tile.type = "button";
  tile.className = `workflow-doc-tile ${row.status}`;
  tile.classList.toggle("submitted", Boolean(row.submitted));
  tile.classList.toggle("local-submitted", Boolean(row.local_submitted && !row.processing));
  tile.classList.toggle("processing", Boolean(row.processing));
  tile.classList.toggle("apply-failed", Boolean(row.apply_failed));
  tile.disabled = compact && (row.status === "not-started" || row.submitted);
  tile.innerHTML = `
    <strong>${escapeHtml(String(row.display_seq))}</strong>
    <span>${escapeHtml(workflowStatusGlyph(row.status))}</span>
    ${row.processing ? '<em class="workflow-processing-badge">サーバー処理中</em>' : ""}
    ${row.local_submitted && !row.processing ? '<em class="workflow-local-submitted-badge">ローカル提出済み</em>' : ""}
    ${row.apply_failed ? '<em class="workflow-apply-failed-badge">適用失敗</em>' : ""}
  `;
  if (!compact && row.preview) {
    tile.title = row.preview;
  } else if (row.apply_failed) {
    tile.title = "提出された修正を適用できませんでした。GitHub Issueは開いたままです。";
  } else if (row.processing) {
    tile.title = "提出内容をサーバーで処理しています。";
  } else if (row.local_submitted) {
    tile.title = row.completed_via || "提出済みです。再編集する場合はローカル提出済みタスクから開いてください。";
  }
  if (!compact) {
    tile.addEventListener("click", () => {
      openWorkflowDocumentPreview(row.display_seq).catch((error) => {
        showStatus(`文書プレビューを開けませんでした: ${error.message}`, true);
      });
    });
  }
  return tile;
}

async function openWorkflowDocumentPreview(displaySeq) {
  if (!el.workflowPreviewModal || !state.currentPack) {
    return;
  }
  const docs = buildDocumentTasks(state.currentPack);
  const row = workflowDocumentStates(docs).find((candidate) => Number(candidate.display_seq) === Number(displaySeq));
  if (!row) {
    return;
  }
  const previewItems = workflowPreviewItemsForDocument(row);
  const archivedDocument = row.status === "resolved"
    ? await loadArchivedWorkflowDocument(row)
    : null;
  const actionDoc = workflowPreviewActionDocument(docs, row);
  const previewDraft = workflowPreviewDraftForRow(row);
  el.workflowPreviewTitle.textContent = `文書 ${row.display_seq}`;
  el.workflowPreviewMeta.textContent = workflowPreviewMetaText(
    row,
    archivedDocument?.units || previewItems,
    actionDoc,
  );
  el.workflowPreviewBody.innerHTML = "";
  if (archivedDocument) {
    renderArchivedWorkflowDocumentPreview(archivedDocument, el.workflowPreviewBody);
  } else if (previewItems.length === 0) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = row.preview || "この文書にはレビュー項目がありません。";
    el.workflowPreviewBody.append(empty);
  } else {
    let lastDocId = null;
    withTemporaryPreviewDraft(previewDraft, () => {
      for (const item of previewItems) {
        if (item.doc_id && item.doc_id !== lastDocId) {
          el.workflowPreviewBody.append(renderDocumentSeparator(item));
          lastDocId = item.doc_id;
        }
        el.workflowPreviewBody.append(renderPreviewItem(item));
      }
    });
  }

  el.workflowPreviewActions.innerHTML = "";
  if (actionDoc) {
    const startButton = document.createElement("button");
    startButton.type = "button";
    startButton.textContent =
      actionDoc.queue_stage === "yomi_strong_repair_review"
        ? "詳細修正を開始"
        : "一括レビューを開始";
    startButton.addEventListener("click", () => {
      closeWorkflowDocumentPreview();
      startSingleDocumentTask(actionDoc);
    });
    el.workflowPreviewActions.append(startButton);
  } else {
    const note = document.createElement("span");
    note.className = "muted";
    note.textContent = "閲覧専用です。この文書には現在作業できるキューがありません。";
    el.workflowPreviewActions.append(note);
  }

  el.workflowPreviewModal.classList.remove("hidden");
  updateRuntimePollingForInteraction();
}

async function loadArchivedWorkflowDocument(row) {
  if (!hasReviewArchive()) {
    return null;
  }
  if (!state.archiveIndex) {
    state.archiveIndex = await fetchJson(state.manifest.archive.index_path);
  }
  return loadArchiveDocument({
    track_name: state.currentPack?.track_name || state.currentPackMeta?.track_name || "dev",
    track_doc_seq: Number(row.display_seq || 0),
    doc_id: row.doc_id || "",
  });
}

async function loadArchiveDocument(summary) {
  if (!state.archiveIndex) {
    state.archiveIndex = await fetchJson(state.manifest.archive.index_path);
  }
  const trackName = summary.track_name || state.archiveCurrentTrack || "dev";
  const track = state.archiveIndex?.tracks?.[trackName];
  const displaySeq = Number(summary.track_doc_seq || summary.display_seq || 0);
  const shardPath = String(summary.shard_path || "");
  const shard = shardPath
    ? { path: shardPath }
    : (track?.shards || []).find(
        (candidate) =>
          displaySeq >= Number(candidate.start_track_doc_seq || 0) &&
          displaySeq <= Number(candidate.end_track_doc_seq || 0),
      );
  if (!shard?.path) {
    return null;
  }
  let payload = state.archiveShardCache.get(shard.path);
  if (!payload) {
    payload = await fetchJson(shard.path);
    state.archiveShardCache.set(shard.path, payload);
  }
  const doc = (payload.documents || []).find(
    (doc) =>
      Number(doc.track_doc_seq || 0) === displaySeq &&
      (!summary.doc_id || String(doc.doc_id || "") === String(summary.doc_id)),
  ) || null;
  return doc ? { ...doc, archive_shard: shard.path } : null;
}

function renderArchivedWorkflowDocumentPreview(doc, container) {
  for (const unit of doc.units || []) {
    const node = document.createElement("article");
    node.className = "workflow-preview-item resolved-yomi-preview";
    node.classList.toggle("skipped-tombstone", Boolean(unit.skipped));
    node.classList.toggle("excluded-tombstone", Boolean(unit.excluded));
    const rubyLine = document.createElement("p");
    rubyLine.className = "ruby-line resolved-ruby-line";
    const tokenPairs = archiveUnitYomiTokenPairs(unit);
    if (tokenPairs.length) {
      rubyLine.append(
        ...renderReadonlyRubyFromTokensWithFootnotes(
          yomiTokenPairObjects(tokenPairs),
          unit.ruby_tokens || [],
          unit.strong_repair_evidence || [],
        ),
      );
    } else if (unit.excluded) {
      rubyLine.textContent = localizedTombstoneLabel(unit.tombstone_label);
    } else {
      rubyLine.textContent = unit.text || "";
    }
    node.append(rubyLine);
    appendStrongRepairFootnoteList(node, normalizedStrongRepairFootnotes(unit.strong_repair_evidence || []));
    if (unit.skipped) {
      const label = document.createElement("span");
      label.className = "skipped-tombstone-label";
      label.textContent = "スキップ済み";
      node.append(label);
    }
    if (unit.excluded) {
      const label = document.createElement("span");
      label.className = "excluded-tombstone-label";
      label.textContent = localizedTombstoneLabel(unit.tombstone_label);
      node.append(label);
    }
    container.append(node);
  }
}

function withTemporaryPreviewDraft(previewDraft, callback) {
  if (!previewDraft) {
    callback();
    return;
  }
  const originalDraft = state.currentDraft;
  state.currentDraft = previewDraft;
  try {
    callback();
  } finally {
    state.currentDraft = originalDraft;
  }
}

function workflowPreviewDraftForRow(row) {
  if (row.status === "resolved") {
    return null;
  }
  const docKeys = workflowDocKeysForSeq(row.display_seq);
  if (!docKeys.length) {
    return null;
  }
  const activeTask = normalizeTask(state.currentDraft.task, state.currentPack);
  if (
    activeTask.mode === "documents" &&
    activeTask.doc_ids.some((docId) => docKeys.includes(docId))
  ) {
    return state.currentDraft;
  }
  const saved = listSavedTaskDrafts().find(
    (record) =>
      Array.isArray(record.task?.doc_ids) &&
      record.task.doc_ids.some((docId) => docKeys.includes(docId)),
  );
  if (!saved) {
    return null;
  }
  return {
    ...state.currentDraft,
    task: saved.task,
    overrides: cloneJson(saved.overrides || {}),
  };
}

function workflowDocKeysForSeq(displaySeq) {
  return buildDocumentTasks(state.currentPack)
    .filter((doc) => documentDisplaySeq(doc) === Number(displaySeq))
    .map((doc) => taskDocKey(doc));
}

function closeWorkflowDocumentPreview() {
  el.workflowPreviewModal?.classList.add("hidden");
  if (state.currentStageId === "archive_browser") {
    render();
  }
  updateRuntimePollingForInteraction();
}

function requestCloseWorkflowDocumentPreview() {
  if (
    archiveCorrectionHasUnsavedEdits() &&
    !window.confirm("未保存の読み編集を破棄して閉じますか？ 保存済みのローカル修正案は残ります。")
  ) {
    return false;
  }
  closeWorkflowDocumentPreview();
  return true;
}

function workflowPreviewItemsForDocument(row) {
  const allItems = (state.currentPack?.items || []).filter(
    (item) => itemDisplayDocSeq(item) === Number(row.display_seq),
  );
  const finalItems = allItems.filter((item) => itemReviewStage(item) === "yomi_final_review");
  const strongItems = allItems.filter((item) => itemReviewStage(item) === "yomi_strong_repair_review");
  if (row.status === "final") {
    return finalItems;
  }
  if (row.status === "strong") {
    return strongItems;
  }
  if (row.status === "resolved" && finalItems.length > 0) {
    return mergeResolvedStrongRepairPreviewItems(finalItems, strongItems);
  }
  if (strongItems.length > 0) {
    return strongItems;
  }
  if (finalItems.length > 0) {
    return finalItems;
  }
  return allItems;
}

function mergeResolvedStrongRepairPreviewItems(finalItems, strongItems) {
  const strongByUnit = new Map();
  for (const item of strongItems) {
    const unitId = String(item.unit_id || "");
    if (unitId && item.rendered_yomi_after) {
      strongByUnit.set(unitId, item);
    }
  }
  return finalItems.map((item) => {
    const strongItem = strongByUnit.get(String(item.unit_id || ""));
    const baseResolvedItem = {
      ...item,
      resolved_preview_rendered_yomi: item.rendered_yomi || "",
      resolved_preview_ruby_tokens: item.rendered_yomi_ruby_tokens || [],
    };
    if (!strongItem) {
      return baseResolvedItem;
    }
    return {
      ...baseResolvedItem,
      resolved_preview_rendered_yomi: strongItem.rendered_yomi_after,
      resolved_preview_ruby_tokens: strongItem.rendered_yomi_after_ruby_tokens || [],
      resolved_preview_source_item_id: strongItem.item_id,
    };
  });
}

function workflowPreviewActionDocument(docs, row) {
  if (!["final", "strong"].includes(row.status)) {
    return null;
  }
  const queueStage = row.status === "strong" ? "yomi_strong_repair_review" : "yomi_final_review";
  return docs.find(
    (doc) =>
      documentDisplaySeq(doc) === Number(row.display_seq) &&
      doc.queue_stage === queueStage &&
      docIsActionable(doc),
  ) || null;
}

function workflowPreviewMetaText(row, items, actionDoc) {
  if (row.apply_failed) {
    return `適用失敗 · ${items.length}項目 · サーバー側の問題解決後に再度開いてください`;
  }
  const statusLabel = row.status === "strong"
    ? "詳細修正"
    : row.status === "final"
      ? "一括レビュー"
      : row.status === "resolved"
        ? "確定済み"
        : "作業なし";
  const itemText = `${items.length}項目`;
  if (actionDoc) {
    return `${statusLabel} · ${itemText} · 開始ボタンからこの文書を作業できます`;
  }
  if (row.submitted) {
    return `${statusLabel} · ${itemText} · ${row.completed_via || "提出済み"}`;
  }
  return `${statusLabel} · ${itemText}`;
}

function renderPreviewItem(item) {
  const node = el.itemTemplate.content.firstElementChild.cloneNode(true);
  node.classList.add("workflow-preview-item");
  const override = state.currentDraft?.overrides?.[item.item_id] || null;
  if (item.resolved_preview_rendered_yomi) {
    renderResolvedYomiPreviewItem({ node, item });
    return node;
  }
  if (itemReviewStage(item) === "yomi_final_review") {
    renderYomiItem({ node, item, override, editable: false });
    return node;
  }
  if (itemReviewStage(item) === "yomi_strong_repair_review") {
    renderStrongRepairItem({ node, item, override, editable: false });
    return node;
  }
  node.querySelector(".item-seq").textContent = `#${item.seq}`;
  node.querySelector(".item-title").textContent = item.text || item.entity_key || item.item_id;
  node.querySelectorAll(".editable-only").forEach((element) => element.classList.add("hidden"));
  node.querySelector(".readonly-only")?.classList.remove("hidden");
  return node;
}

function renderResolvedYomiPreviewItem({ node, item }) {
  node.innerHTML = "";
  node.classList.add("resolved-yomi-preview");
  const rubyLine = document.createElement("p");
  rubyLine.className = "ruby-line resolved-ruby-line";
  const tokens = parseRenderedYomiTokens(item.resolved_preview_rendered_yomi || "");
  rubyLine.append(...renderReadonlyRubyFromTokensWithNodes(tokens, item.resolved_preview_ruby_tokens || []));
  node.append(rubyLine);
}

function renderReadonlyRubyFromTokensWithNodes(tokens, rubyTokens) {
  const previewItem = { rendered_yomi_after_ruby_tokens: rubyTokens || [] };
  const nodes = [];
  for (const [index, token] of tokens.entries()) {
    nodes.push(...renderReadonlyRubyFromToken(previewItem, token, index));
  }
  if (!nodes.length) {
    nodes.push(document.createTextNode(""));
  }
  return nodes;
}

function renderReadonlyRubyFromTokensWithFootnotes(tokens, rubyTokens, evidence) {
  const notes = normalizedStrongRepairFootnotes(evidence);
  if (!notes.length) {
    return renderReadonlyRubyFromTokensWithNodes(tokens, rubyTokens);
  }
  const markerNumbersByEnd = new Map();
  const usedMatches = new Set();
  const matchByRegion = new Map();
  for (const note of notes) {
    for (const target of note.targets) {
      const regionKey = target.region_id || `${target.surface}:${target.surface_occurrence_index ?? ""}`;
      let match = matchByRegion.get(regionKey);
      if (!match) {
        const candidates = findRenderedTokenSpans(tokens, target.surface);
        match = Number.isInteger(target.surface_occurrence_index)
          ? candidates[target.surface_occurrence_index]
          : candidates.find((candidate) => !usedMatches.has(strongRepairMatchKey(candidate)));
        if (match) {
          matchByRegion.set(regionKey, match);
          usedMatches.add(strongRepairMatchKey(match));
        }
      }
      if (!match) {
        continue;
      }
      const numbers = markerNumbersByEnd.get(match.end - 1) || [];
      if (!numbers.includes(note.number)) {
        numbers.push(note.number);
      }
      markerNumbersByEnd.set(match.end - 1, numbers);
    }
  }
  const previewItem = { rendered_yomi_after_ruby_tokens: rubyTokens || [] };
  const nodes = [];
  for (const [index, token] of tokens.entries()) {
    nodes.push(...renderReadonlyRubyFromToken(previewItem, token, index));
    nodes.push(...strongRepairFootnoteMarkerNodes(markerNumbersByEnd.get(index) || []));
  }
  return nodes;
}

function startSingleDocumentTask(doc) {
  state.currentDraft.task = {
    mode: "documents",
    doc_ids: [taskDocKey(doc)],
    started: false,
  };
  startReviewTask();
}

function workflowDocumentStates(docs) {
  const bySeq = new Map();
  for (const doc of docs) {
    const seq = documentDisplaySeq(doc);
    if (!bySeq.has(seq)) {
      bySeq.set(seq, {
        doc_id: doc.doc_id || "",
        doc_seq: Number(doc.doc_seq || seq),
        track_doc_seq: Number(doc.track_doc_seq || seq),
        display_seq: seq,
        status: "not-started",
        preview: doc.preview || "",
        completed_via: "",
        submitted: false,
        local_submitted: false,
        processing: false,
        apply_failed: false,
      });
    }
    const row = bySeq.get(seq);
    row.preview = row.preview || doc.preview || "";
    row.apply_failed = row.apply_failed || String(doc.state || "") === "strong_apply_failed";
    if (documentIsResolved(doc)) {
      if (!["final", "strong"].includes(row.status)) {
        row.status = "resolved";
        row.completed_via =
          String(doc.state || "") === "strong_reviewed" || Number(doc.strong_repair_item_count || 0) > 0
            ? "詳細修正後に確定"
            : "一括レビューのみで確定";
      }
      continue;
    }
    const bucketStatus = workflowDocumentBucketStatus(doc);
    if (bucketStatus === "final") {
      row.status = "final";
      row.submitted = row.submitted || docIsSubmitted(doc);
      row.local_submitted = row.local_submitted || docIsSubmittedLocally(doc);
      row.processing = row.processing || docIsProcessingOnServer(doc);
      row.completed_via = row.submitted ? submittedWorkflowLabel(doc) : "";
    } else if (row.status !== "final" && bucketStatus === "strong") {
      row.status = "strong";
      row.submitted = row.submitted || docIsSubmitted(doc);
      row.local_submitted = row.local_submitted || docIsSubmittedLocally(doc);
      row.processing = row.processing || docIsProcessingOnServer(doc);
      row.completed_via = row.submitted ? submittedWorkflowLabel(doc) : "";
    } else if (
      !["final", "strong"].includes(row.status) &&
      Number(doc.item_count || 0) > 0
    ) {
      row.status = "resolved";
      row.completed_via =
        doc.queue_stage === "yomi_strong_repair_review"
          ? "詳細修正後に確定"
          : "一括レビューのみで確定";
    }
  }
  return [...bySeq.values()].sort((left, right) => left.display_seq - right.display_seq);
}

function withSubmittedProcessingPlaceholders(docs) {
  const result = [...docs];
  const existing = new Set(docs.map((doc) => taskDocKey(doc)));
  for (const record of listSavedTaskDrafts()) {
    if (taskRecordStatus(record) !== "submitted") {
      continue;
    }
    for (const ref of record.document_refs || []) {
      const taskKey = String(ref.task_doc_id || "");
      if (!taskKey || existing.has(taskKey) || finalizedArchiveContainsDocumentRef(ref)) {
        continue;
      }
      const queueStage = ref.queue_stage || record.queue_stage || stageFromTaskDocId(taskKey);
      result.push({
        doc_id: ref.doc_id || baseDocIdFromTaskDocId(taskKey),
        task_doc_id: taskKey,
        queue_stage: queueStage,
        doc_seq: Number(ref.doc_seq || ref.track_doc_seq || 0),
        track_doc_seq: stableDocumentSeq(ref),
        item_count: Number(ref.item_count || 0),
        unresolved_count: Number(ref.unresolved_count || 0),
        state: queueStage === "yomi_strong_repair_review" ? "strong_reviewed" : "final_reviewed",
        workflow_state: queueStage === "yomi_strong_repair_review"
          ? "escalated_submitted"
          : "bulk_submitted",
        queue_member: true,
        selectable: false,
        preview: ref.preview || "",
      });
      existing.add(taskKey);
    }
  }
  return result;
}

function documentIsResolved(doc) {
  if (isUnifiedReviewPack(state.currentPack) || doc?.awaiting_finalization) {
    return false;
  }
  const stateName = String(doc?.state || "");
  return stateName === "complete" || stateName === "skipped";
}

function documentHasPendingCanonicalState(doc) {
  const stateName = String(doc?.state || "");
  return (
    stateName === "final_pending" ||
    stateName === "final_in_review" ||
    stateName === "final_reviewed" ||
    stateName === "strong_pending" ||
    stateName === "strong_in_review" ||
    stateName === "strong_apply_failed"
  );
}

function submittedWorkflowLabel(doc) {
  const stateName = String(doc?.state || "");
  if (stateName === "complete" || stateName === "skipped") {
    return "提出済み・確定処理待ち";
  }
  if (stateName.startsWith("strong_")) {
    return "サーバーで詳細修正を処理中";
  }
  if (stateName === "final_reviewed") {
    return "サーバーで一括レビューを処理中";
  }
  return "提出済み・取り込み待ち";
}

function pendingSourceQueueStatus(doc) {
  const canonical = queueStatusFromDocumentState(doc);
  if (canonical) {
    return canonical;
  }
  if (doc.queue_stage === "yomi_strong_repair_review" && docIsActionable(doc)) {
    return "strong";
  }
  return "final";
}

function submittedSourceQueueStatus(doc) {
  const taskKey = taskDocKey(doc);
  const submittedTask = listSavedTaskDrafts().find(
    (record) =>
      taskRecordStatus(record) === "submitted" &&
      Array.isArray(record.task?.doc_ids) &&
      record.task.doc_ids.includes(taskKey),
  );
  if (submittedTask?.queue_stage === "yomi_strong_repair_review") {
    return "strong";
  }
  if (submittedTask?.queue_stage === "yomi_final_review") {
    return "final";
  }
  return pendingSourceQueueStatus(doc);
}

function workflowDocumentStateForQueueDoc(doc) {
  const submitted = docIsSubmitted(doc);
  const localSubmitted = docIsSubmittedLocally(doc);
  const processing = docIsProcessingOnServer(doc);
  return {
    doc_seq: doc.doc_seq,
    track_doc_seq: stableDocumentSeq(doc),
    display_seq: documentDisplaySeq(doc),
    status: queueStatusFromDocumentState(doc) || (doc.queue_stage === "yomi_strong_repair_review" ? "strong" : "final"),
    preview: doc.preview || "",
    submitted,
    local_submitted: localSubmitted,
    processing,
    completed_via: submitted ? submittedWorkflowLabel(doc) : "",
    apply_failed: String(doc.state || "") === "strong_apply_failed",
  };
}

function workflowStatusGlyph(status) {
  if (status === "resolved") {
    return "✓";
  }
  if (status === "strong") {
    return "!";
  }
  if (status === "final") {
    return "B";
  }
  return "–";
}

function workflowDocBelongsInQueue(doc, queueStage) {
  return workflowDocumentBucketStatus(doc) === queueStatusForStage(queueStage);
}

function workflowDocumentBucketStatus(doc) {
  if (documentIsResolved(doc)) {
    return null;
  }
  const canonical = queueStatusFromDocumentState(doc);
  if (canonical) {
    return canonical;
  }
  if (doc.queue_stage === "yomi_final_review" && docIsActionable(doc)) {
    return "final";
  }
  if (doc.queue_stage === "yomi_strong_repair_review" && docIsActionable(doc)) {
    return "strong";
  }
  if (documentHasPendingCanonicalState(doc)) {
    return pendingSourceQueueStatus(doc);
  }
  if (doc?.awaiting_finalization) {
    return queueStatusForStage(doc.queue_stage);
  }
  if (isUnifiedReviewPack(state.currentPack)) {
    return queueStatusForStage(doc.queue_stage);
  }
  if (docIsSubmittedLocally(doc)) {
    return submittedSourceQueueStatus(doc);
  }
  return null;
}

function queueStatusFromDocumentState(doc) {
  const stateName = String(doc?.state || "");
  if (documentIsResolved(doc)) {
    return null;
  }
  if (stateName.startsWith("final_")) {
    return "final";
  }
  if (
    stateName === "strong_pending" ||
    stateName === "strong_in_review" ||
    stateName === "strong_apply_failed" ||
    stateName === "strong_reviewed"
  ) {
    return "strong";
  }
  return null;
}

function queueStatusForStage(queueStage) {
  if (queueStage === "yomi_final_review") {
    return "final";
  }
  if (queueStage === "yomi_strong_repair_review") {
    return "strong";
  }
  return null;
}

function dedupeWorkflowQueueDocs(docs, queueStage) {
  const bySeq = new Map();
  for (const doc of docs) {
    const seq = documentDisplaySeq(doc);
    const current = bySeq.get(seq);
    if (!current || workflowQueueDocRank(doc, queueStage) < workflowQueueDocRank(current, queueStage)) {
      bySeq.set(seq, doc);
    }
  }
  return [...bySeq.values()].sort((left, right) => documentDisplaySeq(left) - documentDisplaySeq(right));
}

function workflowQueueDocRank(doc, queueStage) {
  if (doc.queue_stage === queueStage && docIsActionable(doc)) {
    return 0;
  }
  if (doc.queue_stage === queueStage && Number(doc.item_count || 0) > 0) {
    return 1;
  }
  if (Number(doc.item_count || 0) > 0) {
    return 2;
  }
  return 3;
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

  renderSavedTaskGroup(
    "保留中のローカルタスク",
    savedTasks.filter((record) => taskRecordStatus(record) === "deferred"),
    docs,
  );
  renderSavedTaskGroup(
    "提出済みのローカルタスク",
    savedTasks.filter((record) => taskRecordStatus(record) === "submitted"),
    docs,
  );
}

function renderSavedTaskGroup(titleText, records, docs) {
  if (!records.length) {
    return;
  }
  const heading = document.createElement("div");
  heading.className = "task-draft-heading muted";
  heading.textContent = titleText;
  el.taskDraftList.append(heading);

  for (const record of records) {
    const row = document.createElement("article");
    row.className = "task-draft-row";
    row.classList.toggle("submitted-task-draft", taskRecordStatus(record) === "submitted");

    const body = document.createElement("div");
    body.className = "task-draft-body";
    const title = document.createElement("strong");
    title.textContent = localizedTaskLabel(record.task_label || record.task_id || "ローカルタスク");
    const meta = document.createElement("div");
    meta.className = "task-draft-meta";
    meta.textContent = formatTaskDraftMeta(record, docs);
    body.append(title, meta);

    const button = document.createElement("button");
    button.type = "button";
    const submitted = taskRecordStatus(record) === "submitted";
    const currentDocs = buildDocumentTasks(state.currentPack);
    const canReopenSubmitted = !submitted || (record.task?.doc_ids || []).some((docId) => {
      const doc = currentDocs.find((candidate) => taskDocKey(candidate) === String(docId));
      return doc && !docIsProcessingOnServer(doc);
    });
    button.textContent =
      submitted
        ? canReopenSubmitted
          ? `${localizedTaskLabel(record.task_label || "タスク")}を再度開く`
          : "サーバー処理中"
        : `${localizedTaskLabel(record.task_label || "タスク")}に戻る`;
    button.disabled = !canReopenSubmitted;
    button.addEventListener("click", () => {
      resumeTaskDraft(record.task_id);
    });

    row.append(body, button);
    el.taskDraftList.append(row);
  }
}

function taskRecordStatus(record) {
  return record?.status === "submitted" ? "submitted" : "deferred";
}

function submittedTaskDocIds() {
  const ids = new Set();
  for (const record of listSavedTaskDrafts()) {
    if (taskRecordStatus(record) !== "submitted") {
      continue;
    }
    for (const docId of record.task?.doc_ids || []) {
      ids.add(String(docId));
    }
  }
  return ids;
}

function docIsSubmittedLocally(doc) {
  return submittedTaskDocIds().has(taskDocKey(doc));
}

function docIsProcessingOnServer(doc) {
  const stateName = String(doc?.state || "");
  const workflowState = String(doc?.workflow_state || "");
  return (
    stateName === "final_reviewed" ||
    stateName === "strong_reviewed" ||
    (Boolean(doc?.awaiting_finalization) && (stateName === "complete" || stateName === "skipped")) ||
    workflowState === "bulk_submitted" ||
    workflowState === "escalated_submitted"
  );
}

function docIsSubmitted(doc) {
  return docIsSubmittedLocally(doc) || docIsProcessingOnServer(doc);
}

function docIsActionable(doc) {
  return doc?.selectable !== false && !docIsSubmittedLocally(doc) && !docIsProcessingOnServer(doc);
}

function renderTaskDocumentRow(doc, task) {
  const row = document.createElement("article");
  row.className = "task-doc-row";
  const docKey = taskDocKey(doc);
  const localSubmitted = docIsSubmittedLocally(doc);
  const processing = docIsProcessingOnServer(doc);
  const submitted = localSubmitted || processing;
  row.classList.toggle("selected", !submitted && task.doc_ids.includes(docKey));
  row.classList.toggle("empty-task-doc", Number(doc.item_count || 0) === 0);
  row.classList.toggle("unselectable-task-doc", doc.selectable === false || submitted);
  row.classList.toggle("submitted-task-doc", submitted);

  const label = document.createElement("label");
  label.className = "task-doc-check";
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.disabled = doc.selectable === false || submitted;
  checkbox.checked = !submitted && task.doc_ids.includes(docKey);
  checkbox.addEventListener("change", () => {
    toggleDocumentTask(docKey, checkbox.checked);
  });
  const title = document.createElement("span");
  title.className = "task-doc-title";
  title.textContent = `文書 ${documentDisplaySeq(doc)}`;
  label.append(checkbox, title);

  const meta = document.createElement("div");
  meta.className = "task-doc-meta";
  const itemSeq = doc.item_count > 0 ? `項目 ${doc.from_seq}-${doc.to_seq}` : "レビュー項目なし";
  const stateText = doc.state ? `${localizedDocumentState(doc.state)} · ` : "";
  const submittedText = processing
    ? "サーバー処理中 · "
    : localSubmitted
      ? "ローカル提出済み · "
      : "";
  const sourceText = isUnifiedReviewPack(state.currentPack)
    ? `一括 ${doc.final_item_count || 0} / 詳細 ${doc.strong_repair_item_count || 0} · `
    : "";
  meta.textContent =
    `${submittedText}${stateText}${sourceText}${doc.item_count}項目 · 要確認 ${doc.unresolved_count}か所 · ${doc.unit_count || 0}文 · ${itemSeq}`;

  const preview = document.createElement("div");
  preview.className = "task-doc-preview";
  preview.textContent = doc.preview;

  const actions = document.createElement("div");
  actions.className = "task-doc-actions";
  for (const [labelText, handler] of [
    ["これだけ", () => selectOnlyDocumentTask(docKey)],
  ]) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "secondary-button";
    button.disabled = doc.selectable === false || submitted;
    button.textContent = labelText;
    button.addEventListener("click", handler);
    actions.append(button);
  }

  row.append(label, meta, preview, actions);
  return row;
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
  const editable = isEditable();
  const visibleItems = getVisibleItems();
  el.itemsSummary.textContent = `${pack.items.length}項目中 ${visibleItems.length}項目を表示`;
  el.itemsContainer.innerHTML = "";

  let lastDocId = null;
  for (const item of visibleItems) {
    if (item.doc_id && item.doc_id !== lastDocId) {
      el.itemsContainer.append(renderDocumentSeparator(item));
      lastDocId = item.doc_id;
    }
    const node = el.itemTemplate.content.firstElementChild.cloneNode(true);
    const override = state.currentDraft.overrides[item.item_id] || null;

    node.classList.toggle("has-override", Boolean(override));

    node.querySelector(".item-seq").textContent = `#${item.seq}`;
    if (itemReviewStage(item) === "yomi_final_review") {
      renderYomiItem({ node, item, override, editable });
      el.itemsContainer.append(node);
      continue;
    }
    if (itemReviewStage(item) === "yomi_strong_repair_review") {
      renderStrongRepairItem({ node, item, override, editable });
      el.itemsContainer.append(node);
      continue;
    }
    node.querySelector(".item-title").textContent = item.entity_key;

    const proposedBadge = node.querySelector(".proposed-badge");
    proposedBadge.textContent = localizedDecisionLabel(item.proposed_action);
    proposedBadge.classList.add(item.proposed_action);

    const overrideBadge = node.querySelector(".override-badge");
    if (override) {
      overrideBadge.textContent = localizedDecisionLabel(override.decision);
      overrideBadge.classList.remove("hidden");
    } else {
      overrideBadge.classList.add("hidden");
    }

    const meta = node.querySelector(".item-meta");
    meta.innerHTML = [
      ["表記", (item.surface_forms || []).join(" | ") || "なし"],
      ["支持", `${item.evidence.supporting_observations}件 / ${item.evidence.supporting_batch_count}バッチ`],
      ["反対", `${item.evidence.opposing_observations}件 / ${item.evidence.opposing_batch_count}バッチ`],
      ["確信度", formatConfidenceCounts(item.evidence.confidence_counts)],
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
      li.textContent = "注記の例はありません。";
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
  left.textContent = `文書 ${itemDisplayDocSeq(item) || ""}`;
  const right = document.createElement("span");
  right.textContent = item.doc_id || "";
  separator.append(left, right);
  return separator;
}

function renderStrongRepairItem({ node, item, override, editable }) {
  node.innerHTML = "";
  node.classList.add("strong-repair-card");
  node.classList.toggle("has-override", Boolean(override));

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
    ? `${item.region_count}か所`
    : item.repair_status || "処理待ち";
  badges.append(statusBadge);
  if (item.used_web_search) {
    const webBadge = document.createElement("span");
    webBadge.className = "badge";
    webBadge.textContent = "検索";
    badges.append(webBadge);
  }
  if (override?.decision) {
    const overrideBadge = document.createElement("span");
    overrideBadge.className = "badge override-badge";
    overrideBadge.textContent = "編集済み";
    badges.append(overrideBadge);
  }
  titleWrap.append(titleRow, badges);
  const manualCorrectionControl = createManualCorrectionFlag({
    checked: override?.manual_correction_required ?? item.manual_correction_required ?? false,
    editable,
    onChange: (checked) => {
      const current = ensureStrongRepairOverride(item.item_id);
      current.manual_correction_required = checked;
      cleanupStrongRepairOverride(item.item_id);
      touchDraft();
      renderSubmissionPreview();
    },
  });
  header.append(titleWrap, manualCorrectionControl);
  node.append(header);

  const afterLine = document.createElement("p");
  afterLine.className = "ruby-line strong-repair-after";
  const footnotes = strongRepairItemFootnotes(item);
  afterLine.append(...renderStrongRepairAfterLine(item, override, editable, footnotes));
  node.append(afterLine);
  appendStrongRepairFootnoteList(node, footnotes.notes);

  const details = document.createElement("details");
  details.className = "strong-repair-debug";
  const summary = document.createElement("summary");
  summary.textContent = "詳細";
  details.append(summary);
  const grid = document.createElement("dl");
  grid.className = "strong-repair-grid";
  for (const [label, value] of [
    ["原文", item.text || ""],
    ["却下した読み", strongRepairRegions(item).map(formatRejectedReadings).join(" | ")],
    ["修正案", strongRepairRegions(item).map((region) => formatRepairProposal(region.llm_parsed || [])).join(" | ")],
    ["修正前", item.rendered_yomi_before || ""],
    ["修正後", item.rendered_yomi_after || ""],
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
    noteTitle.textContent = "注記";
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

function renderStrongRepairAfterLine(item, override, editable, footnotes = strongRepairItemFootnotes(item)) {
  const tokens = item.rendered_yomi_after_tokens?.length
    ? yomiTokenPairObjects(item.rendered_yomi_after_tokens)
    : parseRenderedYomiTokens(item.rendered_yomi_after || "");
  const matches = [];
  const usedMatches = new Set();
  const mappingErrors = [];
  for (const region of strongRepairRegions(item)) {
    const span = region.rejected_span || "";
    const candidates = span ? findRenderedTokenSpans(tokens, span) : [];
    const preferred = region.display_mapping;
    const match = candidates.find(
      (candidate) =>
        !usedMatches.has(strongRepairMatchKey(candidate)) &&
        (!preferred || strongRepairMappingsEqual(candidate, preferred)),
    );
    if (match) {
      const editorMatch = strongRepairEditorMatch(match, region, tokens);
      usedMatches.add(strongRepairMatchKey(match));
      matches.push({ ...editorMatch, region });
    } else {
      mappingErrors.push(
        region.mapping_error || `Cannot map rejected span: ${span || "(empty)"}`,
      );
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
    const nodes = tokens.flatMap((token, index) =>
      renderReadonlyRubyFromToken(item, token, index),
    );
    nodes.push(...renderStrongRepairMappingErrors(mappingErrors));
    return nodes;
  }
  const nodes = [];
  const byStart = new Map(usableMatches.map((match) => [match.start, match]));
  for (let index = 0; index < tokens.length; index += 1) {
    const match = byStart.get(index);
    if (match) {
      if (match.prefix) {
        nodes.push(document.createTextNode(match.prefix));
      }
      nodes.push(renderStrongRepairSpanEditor(item, match.region, override, editable));
      nodes.push(...strongRepairFootnoteMarkerNodes(footnotes.byRegion.get(match.region) || []));
      if (match.suffix) {
        nodes.push(document.createTextNode(match.suffix));
      }
      index = match.end - 1;
      continue;
    }
    nodes.push(...renderReadonlyRubyFromToken(item, tokens[index], index));
  }
  nodes.push(...renderStrongRepairMappingErrors(mappingErrors));
  return nodes;
}

function strongRepairItemFootnotes(item) {
  const notes = [];
  const noteNumberByComment = new Map();
  const byRegion = new Map();
  for (const region of strongRepairRegions(item)) {
    const numbers = [];
    for (const rawComment of region.llm_comments || []) {
      const comment = String(rawComment || "").trim();
      if (!comment) {
        continue;
      }
      let number = noteNumberByComment.get(comment);
      if (!number) {
        number = notes.length + 1;
        noteNumberByComment.set(comment, number);
        notes.push({ number, comment, used_web_search: Boolean(region.used_web_search) });
      }
      if (!numbers.includes(number)) {
        numbers.push(number);
      }
    }
    byRegion.set(region, numbers);
  }
  return { notes, byRegion };
}

function normalizedStrongRepairFootnotes(evidence) {
  const notes = [];
  const noteByComment = new Map();
  for (const item of evidence || []) {
    const comment = String(item?.comment || "").trim();
    if (!comment) {
      continue;
    }
    let note = noteByComment.get(comment);
    if (!note) {
      note = {
        number: notes.length + 1,
        comment,
        used_web_search: Boolean(item?.used_web_search),
        targets: [],
      };
      noteByComment.set(comment, note);
      notes.push(note);
    }
    note.used_web_search = note.used_web_search || Boolean(item?.used_web_search);
    note.targets.push({
      surface: String(item?.surface || ""),
      region_id: String(item?.region_id || ""),
      surface_occurrence_index: Number.isInteger(item?.surface_occurrence_index)
        ? item.surface_occurrence_index
        : null,
    });
  }
  return notes;
}

function strongRepairFootnoteMarkerNodes(numbers) {
  return numbers.map((number) => {
    const marker = document.createElement("span");
    marker.className = "strong-repair-footnote-marker";
    marker.textContent = `*${number}`;
    return marker;
  });
}

function appendStrongRepairFootnoteList(node, notes) {
  if (!notes.length) {
    return;
  }
  const block = document.createElement("div");
  block.className = "strong-repair-footnotes";
  for (const note of notes) {
    const line = document.createElement("p");
    line.append(document.createTextNode(`*${note.number} `));
    appendMarkdownLinks(line, note.comment);
    block.append(line);
  }
  node.append(block);
}

function appendMarkdownLinks(node, text) {
  const source = String(text || "");
  const linkPattern = /\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)/giu;
  let cursor = 0;
  for (const match of source.matchAll(linkPattern)) {
    node.append(document.createTextNode(source.slice(cursor, match.index)));
    const link = document.createElement("a");
    link.href = match[2];
    link.textContent = match[1];
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    node.append(link);
    cursor = Number(match.index) + match[0].length;
  }
  node.append(document.createTextNode(source.slice(cursor)));
}

function strongRepairEditorMatch(match, region, tokens) {
  const editorSurface = defaultStrongRepairSegments(region)
    .map((segment) => segment.surface || "")
    .join("");
  const mappedSurface = tokens
    .slice(match.start, match.end)
    .map((token) => token.surface || "")
    .join("");
  if (editorSurface && editorSurface === mappedSurface) {
    return { ...match, prefix: "", suffix: "" };
  }
  return match;
}

function strongRepairMappingsEqual(left, right) {
  return (
    Number(left.start) === Number(right.start) &&
    Number(left.end) === Number(right.end) &&
    Number(left.start_offset || 0) === Number(right.start_offset || 0) &&
    Number(left.end_offset || 0) === Number(right.end_offset || 0)
  );
}

function renderStrongRepairMappingErrors(errors) {
  return errors.map((message) => {
    const node = document.createElement("span");
    node.className = "strong-repair-mapping-error";
    node.textContent = "対応付けエラー";
    node.title = message;
    return node;
  });
}

function strongRepairMatchKey(match) {
  return `${match.start}:${match.end}:${match.prefix || ""}:${match.suffix || ""}`;
}

function strongRepairRegions(item) {
  return item.regions?.length ? item.regions : [item];
}

function findRenderedTokenSpans(tokens, surfaceSpan) {
  if (!surfaceSpan) {
    return [];
  }
  const surfaces = tokens.map((token) => token.surface || "");
  const starts = [];
  let combined = "";
  for (const surface of surfaces) {
    starts.push(combined.length);
    combined += surface;
  }
  const matches = [];
  let searchFrom = 0;
  while (searchFrom <= combined.length - surfaceSpan.length) {
    const charStart = combined.indexOf(surfaceSpan, searchFrom);
    if (charStart < 0) {
      break;
    }
    const charEnd = charStart + surfaceSpan.length;
    const start = starts.findIndex(
      (tokenStart, index) =>
        tokenStart <= charStart && charStart < tokenStart + surfaces[index].length,
    );
    const endIndex = starts.findIndex(
      (tokenStart, index) =>
        tokenStart < charEnd && charEnd <= tokenStart + surfaces[index].length,
    );
    if (start >= 0 && endIndex >= 0) {
      const startOffset = charStart - starts[start];
      const endOffset = charEnd - starts[endIndex];
      const prefix = surfaces[start].slice(0, startOffset);
      const suffix = surfaces[endIndex].slice(endOffset);
      if (/^[ぁ-ヺー]*$/u.test(prefix + suffix)) {
        matches.push({
          start,
          end: endIndex + 1,
          start_offset: startOffset,
          end_offset: endOffset,
          prefix,
          suffix,
        });
      }
    }
    searchFrom = charStart + 1;
  }
  return matches;
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
    input.placeholder = "読み";
    input.addEventListener("input", () => {
      const current = ensureStrongRepairRegionOverride(item.item_id, region.region_id || region.item_id);
      const currentSegments = current.manual_segments?.length
        ? current.manual_segments
        : defaultStrongRepairSegments(region);
      currentSegments[index].reading = input.value;
      currentSegments[index].edited = true;
      setStrongRepairManualSegments(item, region, currentSegments);
      touchDraft();
      const nextRegion = strongRepairRegionOverride(state.currentDraft.overrides[item.item_id], region);
      const previewSegments = nextRegion?.manual_segments?.length
        ? nextRegion.manual_segments
        : currentSegments;
      wrapper.classList.toggle("changed", Boolean(nextRegion?.manual_segments?.length));
      preview.replaceChildren(
        ...renderStrongRepairSegmentRuby(item, region, previewSegments, editable),
      );
      renderSubmissionPreview();
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
    button.title = editable ? "読み候補を切り替える" : "";
    if (segment.reading) {
      const numericNodes = numericKanaSuffixRubyNodes(segment.surface, segment.reading);
      if (numericNodes) {
        button.append(...renderRubyDisplayNodes(numericNodes));
      } else {
        const ruby = document.createElement("ruby");
        ruby.append(document.createTextNode(segment.surface || ""));
        const rt = document.createElement("rt");
        rt.textContent = segment.reading;
        ruby.append(rt);
        button.append(ruby);
      }
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

function isVariationSelector(char) {
  const codePoint = String(char || "").codePointAt(0);
  return Number.isInteger(codePoint) && (
    (codePoint >= 0xfe00 && codePoint <= 0xfe0f) ||
    (codePoint >= 0xe0100 && codePoint <= 0xe01ef)
  );
}

function reviewSurfaceGraphemes(surface) {
  const graphemes = [];
  for (const char of Array.from(surface || "")) {
    if (isVariationSelector(char) && graphemes.length) {
      graphemes[graphemes.length - 1] += char;
    } else {
      graphemes.push(char);
    }
  }
  return graphemes;
}

function renderStrongRepairSplitControls(item, region, segments) {
  const wrap = document.createElement("span");
  wrap.className = "split-controls";
  const chars = reviewSurfaceGraphemes(region.rejected_span);
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
      button.title = "区切りを切り替える";
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
    cursor += reviewSurfaceGraphemes(segment.surface).length;
    indexes.add(cursor);
  }
  indexes.delete(0);
  indexes.delete(
    (segments || []).reduce(
      (total, segment) => total + reviewSurfaceGraphemes(segment.surface).length,
      0,
    )
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
    !window.confirm("区切りを変更すると、この範囲の読み入力欄が作り直されます。続けますか？")
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
  const chars = reviewSurfaceGraphemes(region.rejected_span);
  let start = 0;
  const surfaces = [];
  for (const end of [...ordered, chars.length]) {
    surfaces.push(chars.slice(start, end).join(""));
    start = end;
  }
  const readings = defaultStrongRepairReadingsForSegments(
    region,
    surfaces,
    previousSegments,
  );
  const nextSegments = surfaces.map((surface, index) => ({
    surface,
    reading: readings[index] || "",
    edited: false,
  }));
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
    ...(typeof current.manual_correction_required === "boolean"
      ? { manual_correction_required: current.manual_correction_required }
      : {}),
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
  if (typeof current.manual_correction_required === "boolean") {
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

function defaultStrongRepairReadingsForSegments(region, surfaces, previousSegments) {
  const knownWholeReadings = strongRepairKnownWholeReadings(region);
  const candidates = surfaces.map((surface) => {
    const values = [];
    const previous = (previousSegments || []).find(
      (segment) => segment.surface === surface && segment.reading,
    );
    if (previous?.reading) {
      values.push(previous.reading);
    }
    for (const reading of strongRepairReadingCycleCandidates(region, surface)) {
      if (reading && !values.includes(reading)) {
        values.push(reading);
      }
    }
    return values;
  });
  for (const wholeReading of knownWholeReadings) {
    const matched = matchStrongRepairSegmentReadings(candidates, wholeReading);
    if (matched) {
      return matched;
    }
  }
  return surfaces.map((surface) =>
    defaultStrongRepairReadingForSegment(region, surface, previousSegments),
  );
}

function strongRepairKnownWholeReadings(region) {
  const span = region.rejected_span || "";
  const values = [];
  const add = (reading) => {
    const normalized = katakanaToHiragana(String(reading || ""));
    if (normalized && !values.includes(normalized)) {
      values.push(normalized);
    }
  };
  const addRows = (rows) => {
    if ((rows || []).map((row) => row?.surface || "").join("") === span) {
      add((rows || []).map((row) => row?.reading || "").join(""));
    }
  };
  addRows(region.llm_parsed || []);
  addRows(region.repair_log?.replacement || []);
  addRows(region.rejected_readings || []);
  addRows(
    (region.target_escalations || []).map((target) => ({
      surface: target.surface,
      reading: target.current_reading_hiragana,
    })),
  );
  for (const reading of region.reading_candidates?.[span] || []) {
    add(reading);
  }
  add(region.reading_hints?.[span]);
  return values;
}

function matchStrongRepairSegmentReadings(candidates, wholeReading) {
  const match = [];
  const visit = (index, prefix) => {
    if (index === candidates.length) {
      return prefix === wholeReading;
    }
    for (const reading of candidates[index]) {
      const next = prefix + reading;
      if (!wholeReading.startsWith(next)) {
        continue;
      }
      match[index] = reading;
      if (visit(index + 1, next)) {
        return true;
      }
    }
    return false;
  };
  return visit(0, "") ? [...match] : null;
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
  const numericNodes = numericKanaSuffixRubyNodes(token.surface, token.reading);
  if (numericNodes) {
    return renderRubyDisplayNodes(numericNodes);
  }
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

function numericKanaSuffixRubyNodes(surface, reading) {
  if (!numericCompoundReadings(surface) || !reading) {
    return null;
  }
  const match = String(surface).match(/^([0-9０-９]+)([ぁ-ゖァ-ヺー]+)$/u);
  if (!match) {
    return null;
  }
  const readingHiragana = katakanaToHiragana(reading);
  const suffixHiragana = katakanaToHiragana(match[2]);
  if (!readingHiragana.endsWith(suffixHiragana) || readingHiragana.length <= suffixHiragana.length) {
    return null;
  }
  return [
    {
      type: "ruby",
      text: match[1],
      reading: readingHiragana.slice(0, -suffixHiragana.length),
    },
    { type: "text", text: match[2] },
  ];
}

function parseRenderedYomiTokens(rendered) {
  return parseRenderedYomiCorrectionTokens(rendered)
    .filter((token) => token.ok)
    .map(({ surface, reading }) => ({ surface, reading }));
}

function normalizeYomiTokenPairs(value) {
  if (!Array.isArray(value)) {
    return [];
  }
  const normalized = [];
  for (const token of value) {
    if (!Array.isArray(token) || token.length !== 2 || typeof token[0] !== "string" || typeof token[1] !== "string") {
      return [];
    }
    normalized.push([token[0], token[1]]);
  }
  return normalized;
}

function yomiTokenPairObjects(tokenPairs) {
  return normalizeYomiTokenPairs(tokenPairs).map(([surface, reading]) => ({ surface, reading }));
}

function archiveUnitYomiTokenPairs(unit) {
  const canonical = normalizeYomiTokenPairs(unit?.yomi_tokens);
  if (canonical.length) {
    return canonical;
  }
  return parseRenderedYomiTokens(unit?.rendered_yomi || "").map(({ surface, reading }) => [surface, reading]);
}

function correctionRecordTokenPairs(record, prefix) {
  const canonical = normalizeYomiTokenPairs(record?.[`${prefix}_yomi_tokens`]);
  if (canonical.length) {
    return canonical;
  }
  return parseRenderedYomiTokens(record?.[`${prefix}_rendered_yomi`] || "")
    .map(({ surface, reading }) => [surface, reading]);
}

function yomiTokenPairsEqual(left, right) {
  return JSON.stringify(normalizeYomiTokenPairs(left)) === JSON.stringify(normalizeYomiTokenPairs(right));
}

function serializeEditableYomiTokens(tokenPairs) {
  return normalizeYomiTokenPairs(tokenPairs)
    .map(([surface, reading]) => `${escapeEditableYomiComponent(surface)}/${escapeEditableYomiComponent(reading)}`)
    .join(" ");
}

function escapeEditableYomiComponent(value) {
  return String(value || "")
    .replaceAll("\\", "\\\\")
    .replaceAll("/", "\\/")
    .replaceAll(" ", "\\s")
    .replaceAll("\t", "\\t")
    .replaceAll("\r", "\\r")
    .replaceAll("\n", "\\n");
}

function splitEditableYomiToken(token) {
  const parts = [[], []];
  let partIndex = 0;
  let escaped = false;
  const escapeValues = { s: " ", t: "\t", r: "\r", n: "\n" };
  for (const char of String(token || "")) {
    if (escaped) {
      parts[partIndex].push(escapeValues[char] ?? char);
      escaped = false;
      continue;
    }
    if (char === "\\") {
      escaped = true;
      continue;
    }
    if (char === "/" && partIndex === 0) {
      partIndex = 1;
      continue;
    }
    parts[partIndex].push(char);
  }
  if (escaped) {
    return { ok: false, error: "末尾のエスケープが不完全です。" };
  }
  if (partIndex === 0) {
    return { ok: false, error: "表記/読み の形式にしてください。" };
  }
  const surface = parts[0].join("");
  if (!surface) {
    return { ok: false, error: "スラッシュの前に表記がありません。" };
  }
  return { ok: true, surface, reading: parts[1].join("") };
}

function shouldDisplayRuby(surface, reading) {
  if (!surface || !reading || surface === reading) {
    return false;
  }
  return /[\p{Script=Han}々〆〻ヵヶA-Za-zＡ-Ｚａ-ｚ]/u.test(surface);
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

function renderYomiItem({ node, item, override, editable }) {
  if (override) {
    override.targets ||= {};
    override.bridge_atoms ||= {};
    override.span_overrides ||= {};
    recomputeRepairAtomSpans(item, override);
  }
  node.innerHTML = "";
  node.classList.add("yomi-card");
  node.classList.toggle("all-safe", item.unresolved_target_count === 0);
  node.classList.toggle("has-unresolved", item.unresolved_target_count > 0);
  const directEditTokens = normalizeYomiTokenPairs(override?.direct_yomi_tokens);
  const hasDirectEdit = override?.resolution === "direct_edit" && directEditTokens.length > 0;
  node.classList.toggle("direct-yomi-edit", hasDirectEdit);

  const controls = document.createElement("div");
  controls.className = "yomi-controls";

  const defaultDisposition = yomiItemDefaultDisposition(item);
  const currentDisposition =
    override?.disposition ||
    (typeof override?.skip === "boolean" ? (override.skip ? "Skip" : "Keep") : defaultDisposition);
  setYomiDispositionClasses(node, currentDisposition);
  const scopeSelector = document.createElement("div");
  scopeSelector.className = "yomi-scope-selector";
  scopeSelector.setAttribute("role", "group");
  scopeSelector.setAttribute("aria-label", "コーパスでの扱い");
  const scopeOptions = [
    {
      value: "Skip",
      glyph: "▣",
      title: "後から復帰可能な状態でスキップします",
    },
    { value: "Exclude", glyph: "⛨", title: "機密性の高い内容を恒久的に除外します" },
  ];
  for (const option of scopeOptions) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `yomi-scope-option scope-${option.value.toLowerCase()}`;
    button.title = option.title;
    button.disabled = !editable;
    button.dataset.disposition = option.value;
    button.setAttribute("aria-label", option.title);
    button.setAttribute("aria-pressed", String(currentDisposition === option.value));
    button.textContent = option.glyph;
    if (editable) {
      button.addEventListener("click", () => {
        const nextDisposition = button.getAttribute("aria-pressed") === "true"
          ? "Keep"
          : option.value;
        const draft = ensureYomiOverride(item.item_id);
        draft.disposition = nextDisposition;
        draft.skip = nextDisposition !== "Keep";
        cleanupYomiOverride(item.item_id);
        touchDraft();
        for (const sibling of scopeSelector.querySelectorAll(".yomi-scope-option")) {
          sibling.setAttribute(
            "aria-pressed",
            String(sibling.dataset.disposition === nextDisposition),
          );
        }
        setYomiDispositionClasses(node, nextDisposition);
        renderSubmissionPreview();
      });
    }
    scopeSelector.append(button);
  }
  controls.append(scopeSelector);

  const manualCorrectionControl = createManualCorrectionFlag({
    checked: override?.manual_correction_required ?? item.manual_correction_required ?? false,
    editable,
    onChange: (checked) => {
      const draft = ensureYomiOverride(item.item_id);
      draft.manual_correction_required = checked;
      cleanupYomiOverride(item.item_id);
      touchDraft();
      renderSubmissionPreview();
    },
  });
  controls.append(manualCorrectionControl);

  node.append(controls);

  const rubyLine = document.createElement("p");
  rubyLine.className = "ruby-line";
  rubyLine.append(...renderRubySegments(item, hasDirectEdit ? null : override, editable && !hasDirectEdit));
  if (editable) {
    const directEditButton = document.createElement("button");
    directEditButton.type = "button";
    directEditButton.className = "yomi-direct-edit-button";
    directEditButton.textContent = "🔧";
    directEditButton.title = hasDirectEdit ? "この文の読みデータを再編集します" : "この文の読みデータを直接編集します";
    directEditButton.setAttribute("aria-label", directEditButton.title);
    directEditButton.addEventListener("click", () => {
      openYomiDirectEditor(node, item);
    });
    rubyLine.append(directEditButton);
  }
  node.append(rubyLine);

  if (hasDirectEdit) {
    node.append(renderSavedYomiDirectEdit(directEditTokens));
  }

  if (editable) {
    node.append(renderYomiDirectEditor(node, item, directEditTokens));
  }

  if (!editable) {
    return;
  }
}

function yomiDirectEditBaselineTokens(item) {
  return archiveUnitYomiTokenPairs(item);
}

function renderSavedYomiDirectEdit(tokens) {
  const saved = document.createElement("div");
  saved.className = "yomi-direct-edit-saved";
  const label = document.createElement("strong");
  label.textContent = "編集後";
  const rubyLine = document.createElement("p");
  rubyLine.className = "ruby-line resolved-ruby-line";
  rubyLine.append(...renderReadonlyRubyFromTokens(yomiTokenPairObjects(tokens)));
  saved.append(label, rubyLine);
  return saved;
}

function renderYomiDirectEditor(node, item, savedTokens) {
  const baselineTokens = yomiDirectEditBaselineTokens(item);
  const initialTokens = savedTokens.length ? savedTokens : baselineTokens;
  const editor = document.createElement("div");
  editor.className = "yomi-direct-edit-editor hidden";
  editor.dataset.originalYomi = serializeEditableYomiTokens(baselineTokens);
  editor.innerHTML = `
    <label>
      <span>読みデータ</span>
      <textarea class="yomi-direct-edit-textarea" rows="3">${escapeHtml(serializeEditableYomiTokens(initialTokens))}</textarea>
    </label>
    <p class="yomi-direct-edit-validation muted">表記/読み 形式で、この文全体の読みを編集します。</p>
    <div class="yomi-direct-edit-actions">
      <button type="button" class="secondary-button compact-button" data-yomi-direct-save>保存</button>
      <button type="button" class="secondary-button compact-button" data-yomi-direct-cancel>キャンセル</button>
      <button type="button" class="secondary-button compact-button" data-yomi-direct-revert>元に戻す</button>
    </div>
  `;
  const textarea = editor.querySelector(".yomi-direct-edit-textarea");
  textarea.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing || event.keyCode === 229) {
      return;
    }
    event.preventDefault();
    saveYomiDirectEdit(node, item);
  });
  editor.querySelector("[data-yomi-direct-save]")?.addEventListener("click", () => {
    saveYomiDirectEdit(node, item);
  });
  editor.querySelector("[data-yomi-direct-cancel]")?.addEventListener("click", () => {
    cancelYomiDirectEdit(node, item);
  });
  editor.querySelector("[data-yomi-direct-revert]")?.addEventListener("click", () => {
    revertYomiDirectEdit(item);
  });
  return editor;
}

function openYomiDirectEditor(node, item) {
  const editor = node.querySelector(".yomi-direct-edit-editor");
  const textarea = editor?.querySelector(".yomi-direct-edit-textarea");
  if (!editor || !textarea) {
    return;
  }
  const override = state.currentDraft.overrides[item.item_id] || {};
  const saved = normalizeYomiTokenPairs(override.direct_yomi_tokens);
  textarea.value = serializeEditableYomiTokens(saved.length ? saved : yomiDirectEditBaselineTokens(item));
  node.classList.add("direct-yomi-editing");
  for (const token of node.querySelectorAll(".ruby-line > .ruby-token")) {
    if (token instanceof HTMLButtonElement) {
      token.disabled = true;
    }
  }
  editor.classList.remove("hidden");
  textarea.focus();
}

function saveYomiDirectEdit(node, item) {
  const editor = node.querySelector(".yomi-direct-edit-editor");
  const textarea = editor?.querySelector(".yomi-direct-edit-textarea");
  const validationNode = editor?.querySelector(".yomi-direct-edit-validation");
  if (!editor || !textarea || !validationNode) {
    return;
  }
  const proposed = normalizeRenderedYomiCorrectionReadings(String(textarea.value || "").trim());
  textarea.value = proposed;
  const validation = validateRenderedYomiCorrection(item, proposed);
  if (!validation.ok) {
    node.classList.add("direct-yomi-invalid");
    validationNode.classList.add("error");
    validationNode.textContent = validation.error;
    return;
  }
  const baselineTokens = yomiDirectEditBaselineTokens(item);
  if (yomiTokenPairsEqual(validation.tokens, baselineTokens)) {
    revertYomiDirectEdit(item);
    return;
  }
  const draft = ensureYomiOverride(item.item_id);
  draft.resolution = "direct_edit";
  draft.original_yomi_tokens = baselineTokens;
  draft.direct_yomi_tokens = validation.tokens;
  draft.targets = {};
  draft.span_overrides = {};
  draft.bridge_atoms = {};
  node.classList.remove("direct-yomi-invalid");
  touchDraft();
  render();
}

function cancelYomiDirectEdit(node, item) {
  const editor = node.querySelector(".yomi-direct-edit-editor");
  const textarea = editor?.querySelector(".yomi-direct-edit-textarea");
  if (!editor || !textarea) {
    return;
  }
  const override = state.currentDraft.overrides[item.item_id] || {};
  const saved = normalizeYomiTokenPairs(override.direct_yomi_tokens);
  const baseline = serializeEditableYomiTokens(saved.length ? saved : yomiDirectEditBaselineTokens(item));
  if (String(textarea.value || "").trim() !== baseline && !window.confirm("未保存の読み編集を破棄しますか？")) {
    return;
  }
  textarea.value = baseline;
  node.classList.remove("direct-yomi-invalid");
  node.classList.remove("direct-yomi-editing");
  for (const token of node.querySelectorAll(".ruby-line > .ruby-token")) {
    if (token instanceof HTMLButtonElement) {
      token.disabled = false;
    }
  }
  editor.classList.add("hidden");
}

function revertYomiDirectEdit(item) {
  const draft = state.currentDraft.overrides[item.item_id];
  if (draft) {
    delete draft.resolution;
    delete draft.original_yomi_tokens;
    delete draft.direct_yomi_tokens;
    cleanupYomiOverride(item.item_id);
  }
  touchDraft();
  render();
}

function yomiItemDefaultDisposition(item) {
  return ["Keep", "Skip", "Exclude"].includes(item?.initial_disposition)
    ? item.initial_disposition
    : "Keep";
}

function setYomiDispositionClasses(node, disposition) {
  node.classList.toggle("machine-skip", disposition === "Skip");
  node.classList.toggle("machine-exclude", disposition === "Exclude");
}

function createManualCorrectionFlag({ checked, editable, onChange }) {
  const label = document.createElement("label");
  label.className = "yomi-control yomi-flag yomi-manual-correction-flag";
  label.title = "後で手動修正が必要です";
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.disabled = !editable;
  checkbox.checked = Boolean(checked);
  checkbox.setAttribute("aria-label", "後で手動修正が必要です");
  const glyph = document.createElement("span");
  glyph.className = "control-glyph";
  glyph.setAttribute("aria-hidden", "true");
  glyph.textContent = "⚑";
  label.append(checkbox, glyph);
  if (editable && onChange) {
    checkbox.addEventListener("change", () => onChange(checkbox.checked));
  }
  return label;
}

function renderRubySegments(item, override, editable) {
  const nodes = [];
  const targetsById = Object.fromEntries(reviewActionTargets(item).map((target) => [target.item_id, target]));
  const segments = item.ruby_segments || [{ type: "text", text: item.text || "" }];
  if (
    !segments.some((segment) => segment.type === "ruby") &&
    item.rendered_yomi &&
    Array.isArray(item.rendered_yomi_ruby_tokens)
  ) {
    const tokens = parseRenderedYomiTokens(item.rendered_yomi);
    if (tokens.length) {
      return renderReadonlyRubyFromTokensWithNodes(
        tokens,
        item.rendered_yomi_ruby_tokens,
      );
    }
  }
  for (let index = 0; index < segments.length; index += 1) {
    const segment = segments[index];
    if (segment.type !== "ruby") {
      nodes.push(...renderYomiTextSegmentWithNumericMerge(item, segment, segments[index - 1], segments[index + 1], override, editable, targetsById));
      continue;
    }
    const target = targetsById[segment.target_item_id];
    if (!target) {
      if (segment.display_only && segment.reading) {
        const numericNodes = numericKanaSuffixRubyNodes(segment.text, segment.reading);
        if (numericNodes) {
          nodes.push(...renderRubyDisplayNodes(numericNodes));
        } else {
          const ruby = document.createElement("ruby");
          ruby.append(document.createTextNode(segment.text || ""));
          const rt = document.createElement("rt");
          rt.textContent = segment.reading;
          ruby.append(rt);
          nodes.push(ruby);
        }
      } else {
        nodes.push(document.createTextNode(segment.text || ""));
      }
      continue;
    }
    nodes.push(renderRubySpan(item, target, override, editable));
  }
  return nodes;
}

function reviewActionTargets(item) {
  if (Array.isArray(item.interaction_spans) && item.interaction_spans.length > 0) {
    return item.interaction_spans;
  }
  return item.targets || [];
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
  let leading = previousNoRuby ? numericMergeRun(text, "leading") : "";
  let trailing = nextNoRuby ? numericMergeRun(text, "trailing") : "";
  let leadingKind = leading ? "numeric" : "";
  let trailingKind = trailing ? "numeric" : "";
  if (!leading && previousNoRuby && isKanaMergeEligibleTarget(previousTarget)) {
    leading = adjacentKanaToken(item, previousTarget, text, "after");
    leadingKind = leading ? "kana" : "";
  }
  if (!trailing && nextNoRuby && isKanaMergeEligibleTarget(nextTarget)) {
    trailing = adjacentKanaToken(item, nextTarget, text, "before");
    trailingKind = trailing ? "kana" : "";
  }
  const textChars = [...text];
  const opportunities = [];
  if (leading && previousTarget) {
    opportunities.push({
      start: 0,
      end: [...leading].length,
      absoluteStart: Number(previousTarget.target_end),
      absoluteEnd: Number(previousTarget.target_end) + [...leading].length,
      surface: leading,
      kind: leadingKind,
    });
  }
  if (trailing && nextTarget) {
    const length = [...trailing].length;
    opportunities.push({
      start: textChars.length - length,
      end: textChars.length,
      absoluteStart: Number(nextTarget.target_start) - length,
      absoluteEnd: Number(nextTarget.target_start),
      surface: trailing,
      kind: trailingKind,
    });
  }
  const unique = [];
  for (const opportunity of opportunities.sort((left, right) => left.start - right.start)) {
    const existing = unique.find(
      (candidate) => candidate.start === opportunity.start && candidate.end === opportunity.end
    );
    if (!existing) {
      unique.push(opportunity);
    }
  }
  let cursor = 0;
  for (const opportunity of unique) {
    if (opportunity.start < cursor) {
      continue;
    }
    if (cursor < opportunity.start) {
      nodes.push(document.createTextNode(textChars.slice(cursor, opportunity.start).join("")));
    }
    nodes.push(renderRepairBridgeButton(item, opportunity, override, editable));
    cursor = opportunity.end;
  }
  if (cursor < textChars.length) {
    nodes.push(document.createTextNode(textChars.slice(cursor).join("")));
  }
  return nodes;
}

function numericMergeRun(text, side) {
  // Keep adjacent numeral systems separate: in GⅠ９勝, Ⅰ belongs with G while ９ may
  // independently belong with 勝.
  const roman = "ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩⅪⅫⅬⅭⅮⅯⅰⅱⅲⅳⅴⅵⅶⅷⅸⅹⅺⅻⅼⅽⅾⅿ";
  const pattern = side === "trailing"
    ? new RegExp(`([0-9０-９]+|[${roman}]+)$`, "u")
    : new RegExp(`^([0-9０-９]+|[${roman}]+)`, "u");
  return text.match(pattern)?.[1] || "";
}

function targetForRubySegment(segment, targetsById) {
  if (!segment || segment.type !== "ruby") {
    return null;
  }
  return targetsById[segment.target_item_id] || null;
}

function isNoRubyTarget(target, override) {
  const targetDraft = override?.targets?.[target.item_id] || null;
  return isUnresolvedNoRubyCandidate(selectedCandidate(target, targetDraft));
}

function noRubyState(candidate) {
  if (candidate?.source !== "none") {
    return "";
  }
  return candidate.no_ruby_state || (candidate.accepted ? "intentional" : "unresolved");
}

function isUnresolvedNoRubyCandidate(candidate) {
  return noRubyState(candidate) === "unresolved";
}

function isIntentionalNoRubyCandidate(candidate) {
  return noRubyState(candidate) === "intentional";
}

function isKanaMergeEligibleTarget(target) {
  return Boolean(target) && /[\p{Script=Han}々〆ヵヶ]/u.test(target.surface || "");
}

function adjacentKanaToken(item, target, textSegment, side) {
  const tokens = renderedYomiTokenSpans(item);
  const boundary = Number(side === "before" ? target.target_start : target.target_end);
  if (!Number.isInteger(boundary)) {
    return "";
  }
  const token = tokens.find((candidate) =>
    side === "before" ? candidate.end === boundary : candidate.start === boundary
  );
  if (!token || !/^[ぁ-ゖゝゞァ-ヺヽヾー]+$/u.test(token.surface)) {
    return "";
  }
  if (side === "before" && !textSegment.endsWith(token.surface)) {
    return "";
  }
  if (side === "after" && !textSegment.startsWith(token.surface)) {
    return "";
  }
  return token.surface;
}

function renderedYomiTokenSpans(item) {
  let cursor = 0;
  return parseRenderedYomiTokens(item.rendered_yomi || "").map((token) => {
    const surface = String(token.surface || "").replaceAll("\u00a0", " ");
    const start = cursor;
    cursor += [...surface].length;
    return { surface, reading: token.reading || "", start, end: cursor };
  });
}

function hasNoRubyCandidate(target) {
  return (target?.candidates || []).some(isUnresolvedNoRubyCandidate);
}

function renderRepairBridgeButton(item, opportunity, override, editable) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = `ruby-token adjacent-merge-token ${opportunity.kind}-merge-token`;
  const atom = repairBridgeAtom(opportunity);
  const active = Boolean(override?.bridge_atoms?.[atom.id]);
  button.classList.toggle("changed", active);
  button.disabled = !editable;
  button.title = active
    ? `${opportunity.kind === "kana" ? "カナ" : "数字"}を修正範囲に含めています。タップすると解除します。`
    : `この${opportunity.kind === "kana" ? "カナ" : "数字"}を隣接する読みキャンセル範囲に含めます。`;
  button.textContent = opportunity.surface;
  if (editable) {
    button.addEventListener("click", () => {
      toggleRepairBridgeAtom(item, atom, elementAnchorRect(button));
    });
  }
  return button;
}

function repairBridgeAtom(opportunity) {
  return {
    id: `bridge:${opportunity.absoluteStart}-${opportunity.absoluteEnd}:${opportunity.kind}`,
    start: opportunity.absoluteStart,
    end: opportunity.absoluteEnd,
    surface: opportunity.surface,
    kind: opportunity.kind,
  };
}

function toggleRepairBridgeAtom(item, atom, anchorRect = null) {
  const previousOverride = cloneDraftValue(state.currentDraft.overrides[item.item_id] || null);
  const draft = ensureYomiOverride(item.item_id);
  const enabling = !draft.bridge_atoms[atom.id];
  if (!enabling) {
    delete draft.bridge_atoms[atom.id];
  } else {
    draft.bridge_atoms[atom.id] = atom;
  }
  recomputeRepairAtomSpans(item, draft);
  cleanupYomiOverride(item.item_id);
  touchDraft();
  render();
  if (enabling) {
    const target = cancelledTargetsTouchingAtom(item, draft, atom)[0];
    if (target) {
      registerRepeatedCancellation(
        item,
        target,
        previousOverride,
        anchorRect,
      );
    }
  }
}

function cancelledTargetsTouchingAtom(item, draft, atom) {
  return reviewActionTargets(item).filter((target) =>
    isNoRubyTarget(target, draft) &&
    (Number(target.target_end) === Number(atom.start) || Number(target.target_start) === Number(atom.end))
  );
}

function isDerivedRepairAtomSpan(span) {
  return [
    "numeric_merge_no_reading",
    "kana_merge_no_reading",
    "repair_atom_merge_no_reading",
  ].includes(span?.repair_reason);
}

function recomputeRepairAtomSpans(item, draft) {
  migrateLegacyMergeSpans(item, draft);
  for (const [spanId, span] of Object.entries(draft.span_overrides || {})) {
    if (isDerivedRepairAtomSpan(span)) {
      delete draft.span_overrides[spanId];
    }
  }
  const targetAtoms = reviewActionTargets(item)
    .filter((target) => isNoRubyTarget(target, draft))
    .map((target) => ({
      type: "target",
      start: Number(target.target_start),
      end: Number(target.target_end),
      surface: target.surface || "",
      target,
    }))
    .filter((atom) => Number.isInteger(atom.start) && Number.isInteger(atom.end));
  const bridgeAtoms = Object.values(draft.bridge_atoms || {})
    .map((atom) => ({
      type: "bridge",
      start: Number(atom.start),
      end: Number(atom.end),
      surface: String(atom.surface || ""),
      kind: atom.kind === "numeric" ? "numeric" : "kana",
      id: atom.id,
    }))
    .filter((atom) => Number.isInteger(atom.start) && Number.isInteger(atom.end));
  const connectedBridgeIds = new Set();
  const components = connectedRepairAtomComponents([...targetAtoms, ...bridgeAtoms]);
  const text = [...String(item.text || "")];
  for (const component of components) {
    const targets = component.filter((atom) => atom.type === "target");
    const bridges = component.filter((atom) => atom.type === "bridge");
    if (!targets.length || !bridges.length) {
      continue;
    }
    bridges.forEach((atom) => connectedBridgeIds.add(atom.id));
    const start = Math.min(...component.map((atom) => atom.start));
    const end = Math.max(...component.map((atom) => atom.end));
    const originalSurface = text.slice(start, end).join("");
    const kinds = new Set(bridges.map((atom) => atom.kind));
    const repairReason = kinds.size === 1
      ? `${[...kinds][0]}_merge_no_reading`
      : "repair_atom_merge_no_reading";
    const span = {
      id: `repair-atoms:${item.item_id}:${start}-${end}`,
      decision: "segmentation",
      target_item_ids: targets.map((atom) => atom.target.item_id),
      original_surface: originalSurface,
      segments: [{ surface: originalSurface, reading: "" }],
      repair_required: true,
      repair_reason: repairReason,
    };
    draft.span_overrides[span.id] = span;
  }
  for (const atomId of Object.keys(draft.bridge_atoms || {})) {
    if (!connectedBridgeIds.has(atomId)) {
      delete draft.bridge_atoms[atomId];
    }
  }
}

function connectedRepairAtomComponents(atoms) {
  const ordered = atoms.slice().sort((left, right) => left.start - right.start || left.end - right.end);
  const components = [];
  for (const atom of ordered) {
    const current = components.at(-1);
    const currentEnd = current ? Math.max(...current.map((entry) => entry.end)) : null;
    if (!current || atom.start > currentEnd) {
      components.push([atom]);
    } else {
      current.push(atom);
    }
  }
  return components;
}

function migrateLegacyMergeSpans(item, draft) {
  if (draft.repair_atoms_migrated) {
    return;
  }
  draft.repair_atoms_migrated = true;
  const text = [...String(item.text || "")];
  const targetsById = new Map(reviewActionTargets(item).map((target) => [target.item_id, target]));
  for (const span of Object.values(draft.span_overrides || {})) {
    if (!isDerivedRepairAtomSpan(span) || span.repair_reason === "repair_atom_merge_no_reading") {
      continue;
    }
    const targets = (span.target_item_ids || []).map((id) => targetsById.get(id)).filter(Boolean);
    const originalSurface = String(span.original_surface || "");
    if (!targets.length || !originalSurface) {
      continue;
    }
    const targetStart = Math.min(...targets.map((target) => Number(target.target_start)));
    const targetEnd = Math.max(...targets.map((target) => Number(target.target_end)));
    const length = [...originalSurface].length;
    for (let start = Math.max(0, targetEnd - length); start <= targetStart; start += 1) {
      const end = start + length;
      if (end < targetEnd || text.slice(start, end).join("") !== originalSurface) {
        continue;
      }
      addMigratedBridgeAtom(draft, text, start, targetStart, span.repair_reason);
      addMigratedBridgeAtom(draft, text, targetEnd, end, span.repair_reason);
      break;
    }
  }
}

function addMigratedBridgeAtom(draft, text, start, end, repairReason) {
  if (end <= start) {
    return;
  }
  const kind = repairReason === "numeric_merge_no_reading" ? "numeric" : "kana";
  const atom = {
    id: `bridge:${start}-${end}:${kind}`,
    start,
    end,
    surface: text.slice(start, end).join(""),
    kind,
  };
  draft.bridge_atoms[atom.id] = atom;
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
  button.classList.toggle("no-ruby-unresolved", isUnresolvedNoRubyCandidate(candidate));
  button.classList.toggle("no-ruby-intentional", isIntentionalNoRubyCandidate(candidate));
  button.disabled = !editable;
  button.title = rubyTitle(target, candidate);

  const numericNodes = numericKanaSuffixRubyNodes(target.surface, candidate?.reading);
  if (numericNodes) {
    button.append(...renderRubyDisplayNodes(numericNodes));
  } else if (candidate?.source === "none") {
    const ruby = document.createElement("ruby");
    ruby.append(document.createTextNode(target.surface));
    const rt = document.createElement("rt");
    rt.textContent = isIntentionalNoRubyCandidate(candidate) ? "−" : "?";
    ruby.append(rt);
    button.append(ruby);
  } else if (candidate?.ruby_nodes?.length) {
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
    let longPressTimer = null;
    let suppressNextClick = false;
    const clearLongPressTimer = () => {
      if (longPressTimer !== null) {
        window.clearTimeout(longPressTimer);
        longPressTimer = null;
      }
    };
    button.addEventListener("pointerdown", () => {
      clearLongPressTimer();
      suppressNextClick = false;
      longPressTimer = window.setTimeout(() => {
        longPressTimer = null;
        suppressNextClick = true;
        toggleYomiNoRubyDefault(item, target, candidate, elementAnchorRect(button));
      }, yomiLongPressMs);
    });
    button.addEventListener("pointerup", clearLongPressTimer);
    button.addEventListener("pointercancel", clearLongPressTimer);
    button.addEventListener("pointerleave", clearLongPressTimer);
    button.addEventListener("selectstart", (event) => event.preventDefault());
    button.addEventListener("contextmenu", (event) => event.preventDefault());
    button.addEventListener("click", () => {
      if (suppressNextClick) {
        suppressNextClick = false;
        return;
      }
      cycleYomiTarget(item, target, candidate, elementAnchorRect(button));
    });
  }
  return button;
}

function selectedCandidate(target, targetDraft) {
  if (targetDraft?.choice_id) {
    return candidateForId(target, targetDraft.choice_id);
  }
  if (targetDraft?.choice_source) {
    return candidateForSource(target, targetDraft.choice_source);
  }
  return defaultCandidate(target);
}

function candidateKey(candidate) {
  if (!candidate) {
    return "";
  }
  return candidate.id || candidate.source || "";
}

function candidateForId(target, id) {
  const candidates = target.candidates || [];
  return candidates.find((candidate) => candidateKey(candidate) === id) || null;
}

function candidateForSource(target, source) {
  const candidates = target.candidates || [];
  return candidates.find((candidate) => candidate.source === source) || candidates[0] || null;
}

function defaultCandidate(target) {
  const candidates = target.candidates || [];
  const defaultId = target.default_candidate_id;
  if (defaultId) {
    const candidate = candidateForId(target, defaultId);
    if (candidate) {
      return candidate;
    }
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
  let reading = candidate?.reading ? ` / ${candidate.reading}` : " / ルビなし";
  if (isIntentionalNoRubyCandidate(candidate)) {
    reading = " / ルビなし（確定）";
  } else if (isUnresolvedNoRubyCandidate(candidate)) {
    reading = " / 未解決・要修正";
  }
  return `${target.surface}${reading}`;
}

function cycleYomiTarget(item, target, currentCandidate, anchorRect = null) {
  const candidates = yomiCycleCandidates(target);
  if (candidates.length === 0) {
    return;
  }
  const currentIndex = candidates.findIndex(
    (candidate) => candidateKey(candidate) === candidateKey(currentCandidate)
  );
  const next = candidates[(currentIndex + 1 + candidates.length) % candidates.length];
  applyYomiCandidateWithRepeatedCancellation(item, target, next, anchorRect);
}

function yomiCycleCandidates(target) {
  const candidates = target.candidates || [];
  const readingCandidates = candidates.filter((candidate) => candidate.source !== "none");
  const noRubyCandidates = candidates.filter((candidate) => candidate.source === "none");
  const defaultKey = candidateKey(defaultCandidate(target));
  const defaultIndex = readingCandidates.findIndex(
    (candidate) => candidateKey(candidate) === defaultKey
  );
  if (defaultIndex > 0) {
    const [defaultReading] = readingCandidates.splice(defaultIndex, 1);
    readingCandidates.unshift(defaultReading);
  }
  return [...readingCandidates, ...noRubyCandidates];
}

function toggleYomiNoRubyDefault(item, target, currentCandidate, anchorRect = null) {
  const noneCandidate = candidateForId(target, "none") || candidateForSource(target, "none");
  const defaultChoice = defaultCandidate(target);
  const next = isUnresolvedNoRubyCandidate(currentCandidate) ? defaultChoice : noneCandidate;
  if (!next) {
    return;
  }
  applyYomiCandidateWithRepeatedCancellation(item, target, next, anchorRect);
}

function applyYomiCandidateWithRepeatedCancellation(item, target, next, anchorRect = null) {
  const previousOverride = cloneDraftValue(state.currentDraft.overrides[item.item_id] || null);
  applyYomiCandidate(item, target, next);
  if (isUnresolvedNoRubyCandidate(next)) {
    registerRepeatedCancellation(item, target, previousOverride, anchorRect);
  } else if (state.repeatCancellation?.targetIds?.has(target.item_id)) {
    dismissRepeatedCancellation();
  }
}

function cloneDraftValue(value) {
  return value === null || value === undefined
    ? null
    : JSON.parse(JSON.stringify(value));
}

function registerRepeatedCancellation(
  item,
  target,
  previousOverride,
  anchorRect = null,
) {
  if (!item?.doc_id || !target?.item_id) {
    return;
  }
  const componentTargets = connectedCancelledTargets(item, target);
  const componentIds = new Set(componentTargets.map((candidate) => candidate.item_id));
  let action = state.repeatCancellation;
  const canExtend = action && action.itemId === item.item_id &&
    [...componentIds].some((targetId) => action.targetIds.has(targetId));
  if (!canExtend) {
    dismissRepeatedCancellation();
    action = {
      itemId: item.item_id,
      docId: item.doc_id,
      reviewStage: itemReviewStage(item),
      targetIds: new Set(),
      itemSnapshots: new Map(),
      delayTimer: null,
      expiryTimer: null,
      matches: [],
      anchorRect: null,
    };
    state.repeatCancellation = action;
  }
  if (!action.itemSnapshots.has(item.item_id)) {
    action.itemSnapshots.set(item.item_id, previousOverride);
  }
  action.targetIds = componentIds;
  action.anchorRect = anchorRect || action.anchorRect;
  scheduleRepeatedCancellation(action);
}

function connectedCancelledTargets(item, anchorTarget) {
  const draft = state.currentDraft.overrides[item.item_id];
  const atoms = [
    ...reviewActionTargets(item)
      .filter((candidate) => isNoRubyTarget(candidate, draft))
      .map((candidate) => ({
        type: "target",
        start: Number(candidate.target_start),
        end: Number(candidate.target_end),
        target: candidate,
      })),
    ...Object.values(draft?.bridge_atoms || {}).map((atom) => ({
      type: "bridge",
      start: Number(atom.start),
      end: Number(atom.end),
    })),
  ].filter((atom) => Number.isInteger(atom.start) && Number.isInteger(atom.end));
  const component = connectedRepairAtomComponents(atoms).find((candidate) =>
    candidate.some(
      (atom) => atom.type === "target" && atom.target.item_id === anchorTarget.item_id,
    )
  );
  return (component || [])
    .filter((atom) => atom.type === "target")
    .map((atom) => atom.target)
    .sort((left, right) => Number(left.target_start) - Number(right.target_start));
}

function scheduleRepeatedCancellation(action) {
  window.clearTimeout(action.delayTimer);
  window.clearTimeout(action.expiryTimer);
  el.repeatCancellationBar?.classList.add("hidden");
  el.repeatCancellationApply?.classList.remove("hidden");
  action.delayTimer = window.setTimeout(() => showRepeatedCancellation(action), repeatCancellationDelayMs);
}

function showRepeatedCancellation(action) {
  if (state.repeatCancellation !== action) {
    return;
  }
  const sourceItem = state.currentPack?.items?.find((item) => item.item_id === action.itemId);
  if (!sourceItem) {
    dismissRepeatedCancellation();
    return;
  }
  const pattern = repeatedCancellationPattern(sourceItem, action);
  action.pattern = pattern;
  action.matches = pattern ? findRepeatedCancellationMatches(sourceItem, pattern) : [];
  if (!pattern || action.matches.length === 0) {
    dismissRepeatedCancellation();
    return;
  }
  el.repeatCancellationMessage.textContent =
    `「${pattern.surface}」と同じ箇所がこの文書に残り${action.matches.length}件あります。`;
  el.repeatCancellationApply.textContent = `残り${action.matches.length}件にも適用`;
  el.repeatCancellationBar.classList.remove("hidden");
  positionRepeatedCancellationBar(action.anchorRect);
  action.expiryTimer = window.setTimeout(dismissRepeatedCancellation, repeatCancellationLifetimeMs);
}

function elementAnchorRect(element) {
  const rect = element?.getBoundingClientRect?.();
  if (!rect) {
    return null;
  }
  return {
    left: rect.left,
    top: rect.top,
    right: rect.right,
    bottom: rect.bottom,
  };
}

function positionRepeatedCancellationBar(anchorRect) {
  const bar = el.repeatCancellationBar;
  if (!bar || !anchorRect) {
    return;
  }
  const viewport = window.visualViewport;
  const viewportLeft = viewport?.offsetLeft || 0;
  const viewportTop = viewport?.offsetTop || 0;
  const viewportWidth = viewport?.width || window.innerWidth;
  const viewportHeight = viewport?.height || window.innerHeight;
  const margin = 10;
  // Leave enough separation that the popover does not visually cover the
  // cancelled ruby target, especially on a zoomed touch viewport.
  const gap = 16;
  bar.style.width = `${Math.max(140, Math.min(680, viewportWidth - margin * 2))}px`;
  bar.style.left = `${viewportLeft + margin}px`;
  bar.style.top = `${viewportTop + margin}px`;
  bar.style.bottom = "auto";
  bar.style.transform = "none";
  const bounds = bar.getBoundingClientRect();
  const preferredLeft = (anchorRect.left + anchorRect.right - bounds.width) / 2;
  const maxLeft = viewportLeft + viewportWidth - bounds.width - margin;
  const left = Math.max(viewportLeft + margin, Math.min(preferredLeft, maxLeft));
  const below = anchorRect.bottom + gap;
  const above = anchorRect.top - bounds.height - gap;
  const maxTop = viewportTop + viewportHeight - bounds.height - margin;
  const top = below <= maxTop ? below : Math.max(viewportTop + margin, above);
  bar.style.left = `${left}px`;
  bar.style.top = `${top}px`;
}

function repeatedCancellationPattern(item, action) {
  const targets = reviewActionTargets(item)
    .filter((target) => action.targetIds.has(target.item_id))
    .sort((left, right) => Number(left.target_start) - Number(right.target_start));
  if (!targets.length) {
    return null;
  }
  const mergeOps = repeatedCancellationMergeOps(item, targets);
  if (!targetsConnectedByMergeOps(targets, mergeOps)) {
    return null;
  }
  const origin = Number(targets[0].target_start);
  const targetSpecs = targets.map((target) => ({
    surface: target.surface || "",
    offsetStart: Number(target.target_start) - origin,
    offsetEnd: Number(target.target_end) - origin,
  }));
  const componentStart = Math.min(0, ...mergeOps.map((operation) => operation.offsetStart));
  const componentEnd = Math.max(
    ...targetSpecs.map((spec) => spec.offsetEnd),
    ...mergeOps.map((operation) => operation.offsetEnd),
  );
  const text = [...String(item.text || "")];
  return {
    surface: text.slice(origin + componentStart, origin + componentEnd).join(""),
    targetSpecs,
    mergeOps,
  };
}

function repeatedCancellationMergeOps(item, targets) {
  const draft = state.currentDraft.overrides[item.item_id];
  const origin = Number(targets[0].target_start);
  const minTargetStart = Math.min(...targets.map((target) => Number(target.target_start)));
  const maxTargetEnd = Math.max(...targets.map((target) => Number(target.target_end)));
  return Object.values(draft?.bridge_atoms || {})
    .filter((atom) => Number(atom.end) >= minTargetStart && Number(atom.start) <= maxTargetEnd)
    .map((atom) => ({
      offsetStart: Number(atom.start) - origin,
      offsetEnd: Number(atom.end) - origin,
      surface: String(atom.surface || ""),
      kind: atom.kind === "numeric" ? "numeric" : "kana",
    }));
}

function targetsConnectedByMergeOps(targets, mergeOps) {
  const origin = Number(targets[0].target_start);
  const atoms = [
    ...targets.map((target) => ({
      start: Number(target.target_start) - origin,
      end: Number(target.target_end) - origin,
    })),
    ...mergeOps.map((operation) => ({ start: operation.offsetStart, end: operation.offsetEnd })),
  ];
  return connectedRepairAtomComponents(atoms).length === 1;
}

function findRepeatedCancellationMatches(sourceItem, pattern) {
  const matches = [];
  const matchedTargetSets = new Set();
  for (const item of state.currentPack?.items || []) {
    if (
      String(item.doc_id || "") !== String(sourceItem.doc_id || "") ||
      itemReviewStage(item) !== itemReviewStage(sourceItem)
    ) {
      continue;
    }
    const targets = reviewActionTargets(item).slice().sort(
      (left, right) => Number(left.target_start) - Number(right.target_start)
    );
    for (let start = 0; start <= targets.length - pattern.targetSpecs.length; start += 1) {
      const windowTargets = targets.slice(start, start + pattern.targetSpecs.length);
      if (
        item.item_id === sourceItem.item_id &&
        windowTargets.every((target) => state.repeatCancellation.targetIds.has(target.item_id))
      ) {
        continue;
      }
      if (!windowTargets.every((target, index) => targetMatchesCancellationSpec(item, target, pattern.targetSpecs[index]))) {
        continue;
      }
      const matchOrigin = Number(windowTargets[0].target_start);
      if (!windowTargets.every((target, index) =>
        Number(target.target_start) - matchOrigin === pattern.targetSpecs[index].offsetStart &&
        Number(target.target_end) - matchOrigin === pattern.targetSpecs[index].offsetEnd
      )) {
        continue;
      }
      if (!pattern.mergeOps.every((operation) => mergeOperationFitsItem(item, matchOrigin, operation))) {
        continue;
      }
      matches.push({ item, targets: windowTargets });
      matchedTargetSets.add(repeatedCancellationMatchKey(item, windowTargets));
    }
    // Interaction spans are UI units, not lexical identity. The same text can
    // therefore be one target in one sentence and several targets in another.
    // For plain adjacent cancellations, match the textual span as well.
    if (pattern.mergeOps.length === 0) {
      for (const windowTargets of cancellationTargetsForText(item, pattern.surface, targets)) {
        const key = repeatedCancellationMatchKey(item, windowTargets);
        if (matchedTargetSets.has(key)) {
          continue;
        }
        if (
          item.item_id === sourceItem.item_id &&
          windowTargets.every((target) => state.repeatCancellation.targetIds.has(target.item_id))
        ) {
          continue;
        }
        matches.push({ item, targets: windowTargets });
        matchedTargetSets.add(key);
      }
    }
  }
  return matches;
}

function repeatedCancellationMatchKey(item, targets) {
  return `${item.item_id}:${targets.map((target) => target.item_id).join(",")}`;
}

function cancellationTargetsForText(item, surface, targets) {
  const text = [...String(item.text || "")];
  const pattern = [...String(surface || "")];
  if (!pattern.length) {
    return [];
  }
  const matches = [];
  for (let start = 0; start <= text.length - pattern.length; start += 1) {
    if (text.slice(start, start + pattern.length).join("") !== surface) {
      continue;
    }
    const end = start + pattern.length;
    const windowTargets = targets.filter((target) =>
      Number(target.target_start) >= start && Number(target.target_end) <= end
    );
    if (!windowTargets.length || Number(windowTargets[0].target_start) !== start ||
        Number(windowTargets.at(-1).target_end) !== end) {
      continue;
    }
    if (windowTargets.some((target, index) =>
      index > 0 && Number(target.target_start) !== Number(windowTargets[index - 1].target_end)
    )) {
      continue;
    }
    if (!windowTargets.every((target) => targetMatchesCancellationSpec(item, target, {
      surface: target.surface || "",
    }))) {
      continue;
    }
    matches.push(windowTargets);
  }
  return matches;
}

function targetMatchesCancellationSpec(item, target, spec) {
  if ((target.surface || "") !== spec.surface || !candidateForId(target, "none")) {
    return false;
  }
  const draft = state.currentDraft.overrides[item.item_id];
  const selected = selectedCandidate(target, draft?.targets?.[target.item_id] || null);
  return !isUnresolvedNoRubyCandidate(selected);
}

function mergeOperationFitsItem(item, origin, operation) {
  const text = [...String(item.text || "")];
  const start = origin + operation.offsetStart;
  const end = origin + operation.offsetEnd;
  if (!Number.isInteger(start) || !Number.isInteger(end)) {
    return false;
  }
  return text.slice(start, end).join("") === operation.surface;
}

function applyRepeatedCancellation() {
  const action = state.repeatCancellation;
  if (!action?.matches?.length) {
    return;
  }
  for (const match of action.matches) {
    if (!action.itemSnapshots.has(match.item.item_id)) {
      action.itemSnapshots.set(
        match.item.item_id,
        cloneDraftValue(state.currentDraft.overrides[match.item.item_id] || null),
      );
    }
    const draft = ensureYomiOverride(match.item.item_id);
    match.targets.forEach((target) => {
      const none = candidateForId(target, "none") || candidateForSource(target, "none");
      draft.targets[target.item_id] = {
        choice_id: candidateKey(none),
        choice_source: "none",
        selected_reading: null,
        custom_reading: null,
      };
    });
    for (const operation of action.pattern?.mergeOps || []) {
      const origin = Number(match.targets[0].target_start);
      const atom = repairBridgeAtom({
        absoluteStart: origin + operation.offsetStart,
        absoluteEnd: origin + operation.offsetEnd,
        surface: operation.surface,
        kind: operation.kind,
      });
      draft.bridge_atoms[atom.id] = atom;
    }
    recomputeRepairAtomSpans(match.item, draft);
  }
  touchDraft();
  render();
  el.repeatCancellationMessage.textContent =
    `「${action.pattern.surface}」を同じ${action.matches.length}件にも適用しました。`;
  el.repeatCancellationApply.classList.add("hidden");
  window.clearTimeout(action.expiryTimer);
  action.expiryTimer = window.setTimeout(dismissRepeatedCancellation, repeatCancellationLifetimeMs);
}

function undoRepeatedCancellation() {
  const action = state.repeatCancellation;
  if (!action) {
    return;
  }
  for (const [itemId, snapshot] of action.itemSnapshots.entries()) {
    if (snapshot === null) {
      delete state.currentDraft.overrides[itemId];
    } else {
      state.currentDraft.overrides[itemId] = cloneDraftValue(snapshot);
    }
  }
  touchDraft();
  dismissRepeatedCancellation();
  render();
}

function dismissRepeatedCancellation() {
  const action = state.repeatCancellation;
  if (action) {
    window.clearTimeout(action.delayTimer);
    window.clearTimeout(action.expiryTimer);
  }
  state.repeatCancellation = null;
  el.repeatCancellationBar?.classList.add("hidden");
  el.repeatCancellationApply?.classList.remove("hidden");
  if (el.repeatCancellationBar) {
    el.repeatCancellationBar.style.removeProperty("width");
    el.repeatCancellationBar.style.removeProperty("left");
    el.repeatCancellationBar.style.removeProperty("top");
    el.repeatCancellationBar.style.removeProperty("bottom");
    el.repeatCancellationBar.style.removeProperty("transform");
  }
}

function applyYomiCandidate(item, target, next) {
  const draft = ensureYomiOverride(item.item_id);
  if (candidateKey(next) === candidateKey(defaultCandidate(target))) {
    delete draft.targets[target.item_id];
    cleanupYomiOverride(item.item_id);
  } else {
    draft.targets[target.item_id] = {
      choice_id: candidateKey(next),
      choice_source: next.source,
      selected_reading: next.reading ?? null,
      custom_reading: null,
    };
  }
  recomputeRepairAtomSpans(item, draft);
  cleanupYomiOverride(item.item_id);
  touchDraft();
  render();
}

function ensureYomiOverride(itemId) {
  if (!state.currentDraft.overrides[itemId]) {
    state.currentDraft.overrides[itemId] = {
      targets: {},
      bridge_atoms: {},
      span_overrides: {},
      note: "",
    };
  }
  if (!state.currentDraft.overrides[itemId].targets) {
    state.currentDraft.overrides[itemId].targets = {};
  }
  if (!state.currentDraft.overrides[itemId].span_overrides) {
    state.currentDraft.overrides[itemId].span_overrides = {};
  }
  if (!state.currentDraft.overrides[itemId].bridge_atoms) {
    state.currentDraft.overrides[itemId].bridge_atoms = {};
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
  const hasBridgeAtoms = Object.keys(draft.bridge_atoms || {}).length > 0;
  const hasDirectEdit =
    draft.resolution === "direct_edit" && normalizeYomiTokenPairs(draft.direct_yomi_tokens).length > 0;
  const item = state.currentPack?.items?.find((row) => row.item_id === itemId);
  const defaultDisposition = yomiItemDefaultDisposition(item);
  const hasDispositionChange =
    typeof draft.disposition === "string" && draft.disposition !== defaultDisposition;
  if (
    !hasTargets &&
    !hasSpanOverrides &&
    !hasBridgeAtoms &&
    !hasDirectEdit &&
    !draft.skip &&
    !hasDispositionChange &&
    !draft.note &&
    typeof draft.manual_correction_required !== "boolean"
  ) {
    delete state.currentDraft.overrides[itemId];
  }
}

function renderSubmissionPreview() {
  if (!isEditable()) {
    el.submissionPreview.value =
      "過去のレビュー内容は閲覧専用のため、レビュー結果を提出できません。";
    renderIssueUrlSummary(null);
    return;
  }
  const payload = buildSubmissionPayload();
  el.submissionPreview.value = formatSubmissionJson(payload);
  renderIssueUrlSummary(buildIssueUrls(payload), payload);
}

function renderControlState() {
  const editable = isEditable();
  const started = isTaskStarted();
  el.backToTaskPicker.disabled = !editable || !started;
  el.completeTask.disabled = !editable || !started;
  el.resetDraft.disabled = !editable || !started;
  el.openIssueTitle.disabled = !editable;
  el.openIssueBottom.disabled = !editable;
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

async function openIssueForCurrentTask() {
  if (!isEditable() || !isTaskStarted()) {
    const urls = buildIssueUrls();
    openUrlInNewTab(urls.issue.url);
    return;
  }
  const submission = buildSubmissionPayload();
  if (!confirmTerminalExclusions(submission)) {
    return;
  }
  const record = currentTaskDraftRecord();
  state.currentDraft.saved_tasks[record.task_id] = {
    ...record,
    status: "deferred",
    awaiting_issue_confirmation: true,
  };
  touchDraft();
  const copied = await copyTextToClipboard(formatSubmissionJson(submission));
  const urls = buildIssueUrls();
  openUrlInNewTab(urls.issue.url);
  state.pendingIssueTaskId = record.task_id;
  showStatus(
    copied
      ? "提出用JSONをコピーしました。GitHub Issueの本文に貼り付けてIssueを作成し、この画面に戻ってください。"
      : "クリップボードへのコピーに失敗しました。選択されたJSONを手動でコピーしてGitHub Issueの本文に貼り付け、Issueを作成してからこの画面に戻ってください。",
    !copied,
  );
}

function restorePendingIssueConfirmation() {
  if (!state.currentDraft) {
    state.pendingIssueTaskId = null;
    hideIssueReturnModal();
    return;
  }
  const pending = Object.values(state.currentDraft.saved_tasks || {}).find(
    (record) => record?.awaiting_issue_confirmation,
  );
  if (!pending?.task_id) {
    state.pendingIssueTaskId = null;
    hideIssueReturnModal();
    return;
  }
  state.pendingIssueTaskId = pending.task_id;
  showIssueReturnModal();
}

function clearPendingIssueConfirmation(taskId) {
  if (!state.currentDraft || !taskId) {
    return;
  }
  const record = state.currentDraft.saved_tasks?.[taskId];
  if (!record?.awaiting_issue_confirmation) {
    return;
  }
  delete record.awaiting_issue_confirmation;
  touchDraft();
}

async function copySubmissionJsonToClipboard() {
  const submission = buildSubmissionPayload();
  if (!confirmTerminalExclusions(submission)) {
    return null;
  }
  const payload = formatSubmissionJson(submission);
  return copyTextToClipboard(payload);
}

function confirmTerminalExclusions(payload) {
  const overrides = [
    ...(payload?.overrides || []),
    ...(payload?.submissions || []).flatMap((submission) => submission?.overrides || []),
  ];
  const explicitIds = overrides
    .filter((row) => row?.disposition === "Exclude")
    .map((row) => String(row.item_id || ""))
    .filter(Boolean);
  const itemIds = [...new Set(explicitIds)];
  if (!itemIds.length) {
    return true;
  }
  if (!window.confirm(
    `${itemIds.length}文を恒久的に除外します。原文と読みは公開データから削除され、コーパスマップから復帰できなくなります。続けますか？`,
  )) {
    return false;
  }
  const submissions = payload.submissions || [payload];
  for (const submission of submissions) {
    if (submission.review_stage !== "yomi_final_review") {
      continue;
    }
    const sourceItemIds = new Set(
      (state.currentPack?.items || [])
        .filter(
          (item) =>
            isItemIncludedInSubmission(item) &&
            itemReviewStage(item) === submission.review_stage &&
            (!submission.pack_id || item.source_pack_id === submission.pack_id),
        )
        .map((item) => originalItemId(item)),
    );
    const confirmedIds = itemIds.filter((itemId) => sourceItemIds.has(itemId));
    if (confirmedIds.length) {
      submission.terminal_exclusion_confirmation = {
        confirmed: true,
        item_ids: confirmedIds,
      };
    }
  }
  return true;
}

function formatSubmissionJson(payload) {
  return formatSubmissionJsonValue(payload, 0, "");
}

function formatSubmissionJsonValue(value, depth, key) {
  if (value === null || typeof value !== "object") {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) {
    if (isSentenceYomiTokenArray(key, value)) {
      return JSON.stringify(value);
    }
    if (!value.length) {
      return "[]";
    }
    const indent = "  ".repeat(depth);
    const childIndent = "  ".repeat(depth + 1);
    return `[\n${value
      .map((item) => `${childIndent}${formatSubmissionJsonValue(item, depth + 1, "")}`)
      .join(",\n")}\n${indent}]`;
  }
  const entries = Object.entries(value).filter(([, item]) => item !== undefined);
  if (!entries.length) {
    return "{}";
  }
  const indent = "  ".repeat(depth);
  const childIndent = "  ".repeat(depth + 1);
  return `{\n${entries
    .map(
      ([childKey, item]) =>
        `${childIndent}${JSON.stringify(childKey)}: ${formatSubmissionJsonValue(
          item,
          depth + 1,
          childKey,
        )}`,
    )
    .join(",\n")}\n${indent}}`;
}

function isSentenceYomiTokenArray(key, value) {
  return (
    key.endsWith("_yomi_tokens") &&
    value.every(
      (pair) =>
        Array.isArray(pair) &&
        pair.length === 2 &&
        pair.every((part) => typeof part === "string"),
    )
  );
}

async function copyTextToClipboard(payload) {
  try {
    await navigator.clipboard.writeText(payload);
    return true;
  } catch {
    if (el.submissionPreview) {
      el.submissionPreview.value = payload;
      el.submissionPreview.focus();
      el.submissionPreview.select();
    }
    return false;
  }
}

function showIssueReturnModal() {
  const archiveCorrection = Boolean(state.pendingArchiveCorrectionKey);
  if (el.issueReturnTitle) {
    el.issueReturnTitle.textContent = archiveCorrection
      ? "修正用Issueを作成しましたか？"
      : "GitHub Issueを作成しましたか？";
  }
  if (el.issueReturnDescription) {
    el.issueReturnDescription.textContent = archiveCorrection
      ? "修正用JSONを貼り付けてIssueを作成した場合は、提出済みにしてください。まだの場合はローカルの修正案を残します。"
      : "コピーしたJSONを貼り付けてIssueを作成した場合は、このタスクを提出済みにしてください。まだの場合はローカル作業を続けられます。";
  }
  el.issueReturnModal?.classList.remove("hidden");
  updateRuntimePollingForInteraction();
}

function hideIssueReturnModal() {
  el.issueReturnModal?.classList.add("hidden");
  updateRuntimePollingForInteraction();
}

function buildIssueTitle(payload) {
  const stages = new Set(payload.task?.queue_stages || [payload.review_stage]);
  let stageLabel = "Yomi Review";
  if (stages.has("yomi_final_review") && stages.has("yomi_strong_repair_review")) {
    stageLabel = "Bulk + Escalated";
  } else if (stages.has("yomi_strong_repair_review")) {
    stageLabel = "Escalated Repair";
  } else if (stages.has("yomi_final_review")) {
    stageLabel = "Bulk Review";
  }
  const docs = formatDocSeqs(payload.task?.track_doc_seqs || []);
  return `[${stageLabel}] ${docs || payload.pack_id || "review"}`;
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
    el.issueUrlSummary.textContent = "過去のレビュー内容は閲覧専用のため、Issueを作成できません。";
    return;
  }
  const jsonLength = payload ? formatSubmissionJson(payload).length : 0;
  el.issueUrlSummary.textContent =
    `Issue URL: ${urls.issue.length}文字。メインボタンで${jsonLength}文字のJSONをコピーしてGitHubを開きます。`;
}

function buildSubmissionPayload() {
  const pack = state.currentPack;
  if (isUnifiedReviewPack(pack)) {
    return buildUnifiedSubmissionPayload();
  }
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

function buildUnifiedSubmissionPayload() {
  const pack = state.currentPack;
  const now = Date.now();
  const reviewer = el.reviewerName.value.trim();
  const generatedAt = Math.floor(now / 1000);
  const iso = new Date(now).toISOString();
  const submissions = [];
  for (const source of pack.source_packs || []) {
    const items = getIncludedItems().filter(
      (item) => item.source_review_stage === source.review_stage && item.source_pack_id === source.pack_id,
    );
    if (!items.length) {
      continue;
    }
    const overrides = source.review_stage === "yomi_final_review"
      ? getActiveYomiOverrides(source.review_stage, source.pack_id)
      : source.review_stage === "yomi_strong_repair_review"
        ? getActiveStrongRepairOverrides(source.review_stage, source.pack_id)
        : [];
    submissions.push({
      schema_version: submissionSchemaVersion,
      submission_type: "review_patch",
      review_stage: source.review_stage,
      pack_id: source.pack_id,
      submission_id: `${source.pack_id}__${iso}`,
      reviewer,
      generated_at_epoch: generatedAt,
      task: buildSubmissionTaskMetadata(source.review_stage, source.pack_id),
      reviewed_ranges: buildReviewedRangesForItems(items),
      overrides,
    });
  }
  return {
    schema_version: submissionSchemaVersion,
    submission_type: "review_bundle",
    review_stage: "unified_yomi_review",
    pack_id: pack.pack_id,
    submission_id: `${pack.pack_id}__${iso}`,
    reviewer,
    generated_at_epoch: generatedAt,
    task: buildSubmissionTaskMetadata(),
    submissions,
  };
}

function buildSubmissionTaskMetadata(reviewStage = null, packId = null) {
  const task = normalizeTask(state.currentDraft.task, state.currentPack);
  if (task.mode !== "documents") {
    return { mode: "full_pack" };
  }
  const docs = buildDocumentTasks(state.currentPack).filter((row) => {
    if (!task.doc_ids.includes(taskDocKey(row))) {
      return false;
    }
    if (reviewStage && row.queue_stage && row.queue_stage !== reviewStage) {
      return false;
    }
    if (packId && row.source_pack_id && row.source_pack_id !== packId) {
      return false;
    }
    return true;
  });
  return {
    mode: "documents",
    task_id: state.currentDraft.active_task_id || null,
    task_label: state.currentDraft.active_task_label || null,
    doc_keys: docs.map((doc) => taskDocKey(doc)),
    doc_ids: docs.map((doc) => doc.doc_id),
    track_doc_seqs: docs.map((doc) => documentDisplaySeq(doc)),
    queue_stages: [...new Set(docs.map((doc) => doc.queue_stage).filter(Boolean))],
    track_doc_ranges: buildReviewedDocumentRanges(docs),
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
  if (isUnifiedReviewPack(pack)) {
    return [
      ...getActiveYomiOverrides("yomi_final_review"),
      ...getActiveStrongRepairOverrides("yomi_strong_repair_review"),
    ];
  }
  return getActiveOverrides().map((item) => ({
    item_id: item.item_id,
    decision: item.decision,
    ...(item.note ? { note: item.note } : {}),
  }));
}

function getActiveYomiOverrides(reviewStage = "yomi_final_review", packId = null) {
  return Object.entries(state.currentDraft.overrides)
    .map(([itemId, override]) => {
      const item = state.currentPack.items.find((row) => row.item_id === itemId);
      if (
        !item ||
        !isItemIncludedInSubmission(item) ||
        itemReviewStage(item) !== reviewStage ||
        (packId && item.source_pack_id !== packId)
      ) {
        return null;
      }
      return {
        item_id: originalItemId(item),
        ...(override.resolution === "direct_edit"
          ? {
              resolution: "direct_edit",
              original_yomi_tokens: normalizeYomiTokenPairs(override.original_yomi_tokens),
              direct_yomi_tokens: normalizeYomiTokenPairs(override.direct_yomi_tokens),
            }
          : {}),
        ...(typeof override.disposition === "string"
          ? { disposition: override.disposition }
          : {}),
        ...(typeof override.skip === "boolean" ? { skip: override.skip } : {}),
        ...(typeof override.manual_correction_required === "boolean"
          ? { manual_correction_required: override.manual_correction_required }
          : {}),
        targets: Object.entries(override.targets || {}).map(([targetItemId, target]) => ({
          item_id: targetItemId,
          choice_id: target.choice_id || null,
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
      (row) =>
        row.targets.length > 0 ||
        row.span_overrides.length > 0 ||
        row.resolution === "direct_edit" ||
        "disposition" in row ||
        "skip" in row ||
        "manual_correction_required" in row ||
        row.note
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

function getActiveStrongRepairOverrides(reviewStage = "yomi_strong_repair_review", packId = null) {
  return Object.entries(state.currentDraft.overrides)
    .map(([itemId, override]) => {
      const item = state.currentPack.items.find((row) => row.item_id === itemId);
      if (
        !item ||
        !isItemIncludedInSubmission(item) ||
        itemReviewStage(item) !== reviewStage ||
        (packId && item.source_pack_id !== packId)
      ) {
        return null;
      }
      const row = {
        item_id: originalItemId(item),
        decision: override.decision || "accept",
        ...(typeof override.manual_correction_required === "boolean"
          ? { manual_correction_required: override.manual_correction_required }
          : {}),
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
        "manual_correction_required" in row ||
        row.note ||
        (row.regions && row.regions.length > 0)
    );
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
  const docKeys = new Set(task.doc_ids);
  if (isUnifiedReviewPack(state.currentPack)) {
    return items.filter((item) => docKeys.has(queueDocKey(itemReviewStage(item), item.doc_id)));
  }
  return items.filter((item) => docKeys.has(item.doc_id));
}

function isUnifiedReviewPack(pack) {
  return pack?.review_stage === "unified_yomi_review";
}

function itemReviewStage(item) {
  return item.source_review_stage || state.currentPack?.review_stage || "";
}

function itemReviewStageForPack(item, pack) {
  return item.source_review_stage || pack?.review_stage || "";
}

function originalItemId(item) {
  return item.original_item_id || item.item_id;
}

function isItemIncludedInSubmission(item) {
  return getVisibleItems().some((row) => row.item_id === item.item_id);
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

function buildReviewedRangesForItems(sourceItems) {
  const seqs = sourceItems
    .map((item) => Number(item.original_seq || item.seq))
    .filter((seq) => Number.isInteger(seq))
    .sort((a, b) => a - b);
  if (seqs.length === 0) {
    return [];
  }
  const ranges = [];
  let fromSeq = seqs[0];
  let toSeq = seqs[0];
  for (const seq of seqs.slice(1)) {
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
    normalizeTask(state.currentDraft.task, state.currentPack).doc_ids.includes(taskDocKey(doc))
  );
  const seqs = sourceDocs
    .map((doc) => documentDisplaySeq(doc))
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
    ranges.push({ from_track_doc_seq: fromDocSeq, to_track_doc_seq: toDocSeq });
    fromDocSeq = seq;
    toDocSeq = seq;
  }
  ranges.push({ from_track_doc_seq: fromDocSeq, to_track_doc_seq: toDocSeq });
  return ranges;
}

function getIncludedItems() {
  return getVisibleItems();
}

function buildDocumentTasks(pack) {
  if (pack?.documents?.length) {
    const itemStats = new Map();
    for (const item of pack.items || []) {
      const docId = item.doc_id || "";
      if (!docId) {
        continue;
      }
      const key = isUnifiedReviewPack(pack) ? queueDocKey(itemReviewStageForPack(item, pack), docId) : docId;
      if (!itemStats.has(key)) {
        itemStats.set(key, {
          from_seq: item.seq,
          to_seq: item.seq,
          item_count: 0,
          unresolved_count: 0,
          final_item_count: 0,
          strong_repair_item_count: 0,
        });
      }
      const stats = itemStats.get(key);
      stats.from_seq = Math.min(stats.from_seq, item.seq);
      stats.to_seq = Math.max(stats.to_seq, item.seq);
      stats.item_count += 1;
      stats.unresolved_count += Number(item.unresolved_target_count ?? item.region_count ?? 0);
      if (itemReviewStageForPack(item, pack) === "yomi_final_review") {
        stats.final_item_count += 1;
      }
      if (itemReviewStageForPack(item, pack) === "yomi_strong_repair_review") {
        stats.strong_repair_item_count += 1;
      }
    }
    return pack.documents
      .map((doc) => {
        const stats = itemStats.get(taskDocKey(doc)) || {};
        return {
          doc_id: doc.doc_id || "",
          task_doc_id: taskDocKey(doc),
          queue_stage: doc.queue_stage || pack.review_stage || "",
          source_pack_id: doc.source_pack_id || "",
          doc_seq: doc.doc_seq || 0,
          track_doc_seq: stableDocumentSeq(doc),
          from_seq: stats.from_seq ?? 0,
          to_seq: stats.to_seq ?? 0,
          item_count: stats.item_count ?? Number(doc.item_count || 0),
          unresolved_count: stats.unresolved_count ?? Number(doc.region_count || 0),
          final_item_count: stats.final_item_count ?? Number(doc.final_item_count || 0),
          strong_repair_item_count:
            stats.strong_repair_item_count ?? Number(doc.strong_repair_item_count || 0),
          unit_count: Number(doc.unit_count || 0),
          state: doc.state || "",
          workflow_state: doc.workflow_state || "",
          workflow_queue_stage: doc.workflow_queue_stage || "",
          awaiting_finalization: Boolean(doc.awaiting_finalization),
          queue_member: documentBelongsToQueue(doc.queue_stage || pack.review_stage || "", doc),
          selectable: "selectable" in doc ? Boolean(doc.selectable) : Number(doc.item_count || 0) > 0,
          preview: doc.preview || "",
        };
      })
      .sort(
        (left, right) =>
          documentDisplaySeq(left) - documentDisplaySeq(right) ||
          queueStageSort(left.queue_stage) - queueStageSort(right.queue_stage),
      );
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
        track_doc_seq: stableDocumentSeq(item),
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

function buildActionableDocumentTasks(pack) {
  if (isUnifiedReviewPack(pack) && Array.isArray(pack.actionable_documents)) {
    return buildDocumentTasks({ ...pack, documents: pack.actionable_documents });
  }
  return buildDocumentTasks(pack).filter((doc) => doc.selectable !== false);
}

function normalizeTask(task, pack) {
  const docs = buildActionableDocumentTasks(pack);
  const validDocIds = new Set(docs.map((doc) => taskDocKey(doc)));
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
  };
}

function itemIdsForTaskDocIds(pack, docIds) {
  const docIdSet = new Set((docIds || []).map(String));
  if (docIdSet.size === 0) {
    return new Set();
  }
  const itemIds = new Set();
  for (const item of pack?.items || []) {
    const docKey = isUnifiedReviewPack(pack)
      ? queueDocKey(itemReviewStageForPack(item, pack), item.doc_id)
      : item.doc_id;
    if (docIdSet.has(docKey)) {
      itemIds.add(item.item_id);
    }
  }
  return itemIds;
}

function filterOverridesForTask(pack, task, overrides) {
  if (!overrides || task.mode !== "documents" || task.doc_ids.length === 0) {
    return {};
  }
  const itemIds = itemIdsForTaskDocIds(pack, task.doc_ids);
  const itemsById = new Map((pack.items || []).map((item) => [item.item_id, item]));
  const filtered = {};
  for (const [itemId, override] of Object.entries(overrides || {})) {
    if (itemIds.has(itemId)) {
      const normalized = normalizeStoredOverrideForItem(pack, itemsById.get(itemId), override);
      if (normalized) {
        filtered[itemId] = normalized;
      }
    }
  }
  return filtered;
}

function normalizeStoredOverrideForItem(pack, item, override) {
  if (!item || itemReviewStageForPack(item, pack) !== "yomi_strong_repair_review") {
    return override;
  }
  const regionsById = new Map(
    strongRepairRegions(item).map((region) => [region.region_id || region.item_id, region]),
  );
  const regions = {};
  for (const [regionId, storedRegion] of Object.entries(override?.regions || {})) {
    const currentRegion = regionsById.get(regionId);
    const segments = normalizeStrongRepairSegments(storedRegion?.manual_segments || []);
    if (
      currentRegion &&
      segments.length > 0 &&
      segments.map((segment) => segment.surface).join("") === currentRegion.rejected_span
    ) {
      regions[regionId] = { ...storedRegion, manual_segments: segments };
    }
  }
  const note = String(override?.note || "").trim();
  const hasManualCorrectionOverride = typeof override?.manual_correction_required === "boolean";
  if (Object.keys(regions).length === 0 && !note && !hasManualCorrectionOverride) {
    return null;
  }
  return { ...override, note, regions };
}

function taskQueueStage(task) {
  if (!isUnifiedReviewPack(state.currentPack) || !task?.doc_ids?.length) {
    return null;
  }
  const docs = buildDocumentTasks(state.currentPack);
  const selected = docs.find((doc) => task.doc_ids.includes(taskDocKey(doc)));
  return selected?.queue_stage || null;
}

function selectableDocsForCurrentTask(docs, task, queueStage = null) {
  const selectableDocs = docs.filter((doc) => docIsActionable(doc));
  if (!isUnifiedReviewPack(state.currentPack)) {
    return selectableDocs;
  }
  const targetQueueStage =
    queueStage ||
    taskQueueStage(task) ||
    (selectableDocs.some((doc) => doc.queue_stage === "yomi_final_review")
      ? "yomi_final_review"
      : selectableDocs[0]?.queue_stage);
  return selectableDocs.filter((doc) => doc.queue_stage === targetQueueStage);
}

function formatReviewStageLabel(stage) {
  if (stage === "yomi_final_review") {
    return "一括レビュー";
  }
  if (stage === "yomi_strong_repair_review") {
    return "詳細修正";
  }
  return stage || "レビュー";
}

function syncLocalTaskRecordsForCurrentPack() {
  if (!state.currentPack || !state.currentDraft) {
    return;
  }
  const currentKey = currentDraftStorageKey();
  const currentRecords = {};
  const migratedActiveRecords = [];
  const changedKeys = new Set();
  const draftKeys = localDraftStorageKeys();

  for (const key of draftKeys) {
    let parsed;
    if (key === currentKey) {
      parsed = state.currentDraft;
    } else {
      const raw = window.localStorage.getItem(key);
      if (!raw) {
        continue;
      }
      try {
        parsed = JSON.parse(raw);
      } catch {
        window.localStorage.removeItem(key);
        continue;
      }
    }
    const sourceStage = reviewStageFromDraftStorageKey(key) || parsed?.review_stage || "";
    const nextSavedTasks = {};
    let sourceChanged = false;
    for (const [taskId, rawRecord] of Object.entries(parsed?.saved_tasks || {})) {
      const normalized = normalizeLocalTaskRecordForCurrentPack(rawRecord, sourceStage);
      if (!normalized) {
        sourceChanged = true;
        continue;
      }
      const currentTaskId = uniqueTaskIdForRecords(normalized.task_id || taskId, currentRecords);
      currentRecords[currentTaskId] = { ...normalized, task_id: currentTaskId };
      if (key === currentKey) {
        nextSavedTasks[currentTaskId] = { ...normalized, task_id: currentTaskId };
      } else {
        sourceChanged = true;
      }
    }
    if (key !== currentKey) {
      const rawActiveRecord = localTaskRecordFromActiveDraft(parsed);
      const normalizedActive = normalizeLocalTaskRecordForCurrentPack(rawActiveRecord, sourceStage);
      if (normalizedActive) {
        const activeTaskId = uniqueTaskIdForRecords(
          normalizedActive.task_id || "migrated_active_task",
          currentRecords,
        );
        const migrated = { ...normalizedActive, task_id: activeTaskId };
        currentRecords[activeTaskId] = migrated;
        migratedActiveRecords.push(migrated);
      }
      if (rawActiveRecord) {
        parsed.active_task_id = null;
        parsed.active_task_label = null;
        parsed.task = { mode: "documents", doc_ids: [], started: false };
        parsed.overrides = {};
        sourceChanged = true;
      }
    }
    if (key === currentKey) {
      continue;
    }
    if (sourceChanged) {
      parsed.saved_tasks = nextSavedTasks;
      if (draftHasLocalWork(parsed)) {
        window.localStorage.setItem(key, JSON.stringify(parsed));
      } else {
        window.localStorage.removeItem(key);
      }
      changedKeys.add(key);
    }
  }

  const before = JSON.stringify(state.currentDraft.saved_tasks || {});
  if (!isTaskStarted() && migratedActiveRecords.length > 0) {
    const active = [...migratedActiveRecords].sort(
      (left, right) => Number(right.updated_at_epoch || 0) - Number(left.updated_at_epoch || 0),
    )[0];
    state.currentDraft.active_task_id = active.task_id;
    state.currentDraft.active_task_label = active.task_label || active.task_id;
    state.currentDraft.task = {
      ...normalizeTask(active.task, state.currentPack),
      started: true,
    };
    state.currentDraft.overrides = cloneJson(active.overrides || {});
    delete currentRecords[active.task_id];
  }
  state.currentDraft.saved_tasks = currentRecords;
  const after = JSON.stringify(state.currentDraft.saved_tasks || {});
  if (before !== after || changedKeys.size > 0) {
    saveDraft();
  }
}

function localTaskRecordFromActiveDraft(draft) {
  if (!draft?.task?.started || !taskDocIdsForStorageTask(draft.task).length) {
    return null;
  }
  const taskId = draft.active_task_id || "migrated_active_task";
  return {
    task_id: taskId,
    task_label: draft.active_task_label || taskId,
    task_number: taskNumberFromId(taskId),
    status: "deferred",
    task: { ...draft.task, started: false },
    overrides: cloneJson(draft.overrides || {}),
    updated_at_epoch: draft.updated_at_epoch || null,
  };
}

function normalizeLocalTaskRecordForCurrentPack(rawRecord, sourceStage = "") {
  if (!rawRecord?.task_id && !rawRecord?.task) {
    return null;
  }
  const taskStage = localTaskRecordStage(rawRecord, sourceStage);
  if (!taskStage) {
    return null;
  }
  const submitted = taskRecordStatus(rawRecord) === "submitted";
  const docIds = taskDocIdsForStorageTask(rawRecord.task);
  const storedRefs = new Map(
    (rawRecord.document_refs || []).map((ref) => [String(ref.task_doc_id || ""), ref]),
  );
  const currentDocs = buildDocumentTasks(state.currentPack);
  const retainedDocIds = [];
  const documentRefs = [];
  for (const docId of docIds) {
    const currentDoc = currentQueueDocForTaskDocId(docId, taskStage, { submitted });
    if (currentDoc) {
      retainedDocIds.push(taskDocKey(currentDoc));
      documentRefs.push(localTaskDocumentRef(currentDoc));
      continue;
    }
    if (!submitted) {
      continue;
    }
    const baseDocId = baseDocIdFromTaskDocId(docId);
    const sameStageDoc = currentDocs.find(
      (doc) =>
        String(doc.doc_id || "") === baseDocId &&
        String(doc.queue_stage || "") === taskStage,
    );
    const ref = storedRefs.get(String(docId)) ||
      (sameStageDoc ? localTaskDocumentRef(sameStageDoc) : minimalLocalTaskDocumentRef(docId, taskStage));
    if (finalizedArchiveContainsDocumentRef(ref)) {
      continue;
    }
    if (documentHasAdvancedBeyondTaskStage(baseDocId, taskStage, currentDocs)) {
      continue;
    }
    retainedDocIds.push(String(docId));
    documentRefs.push(ref);
  }
  if (!retainedDocIds.length) {
    return null;
  }
  const task = {
    ...rawRecord.task,
    mode: "documents",
    doc_id: undefined,
    doc_ids: retainedDocIds,
    started: false,
  };
  const normalized = {
    ...rawRecord,
    queue_stage: taskStage,
    status: taskRecordStatus(rawRecord),
    task,
    document_refs: documentRefs,
    overrides: submitted
      ? cloneJson(rawRecord.overrides || {})
      : filterOverridesForTask(state.currentPack, task, rawRecord.overrides || {}),
  };
  delete normalized.from_seq;
  delete normalized.to_seq;
  return normalized;
}

function localTaskDocumentRef(doc) {
  return {
    task_doc_id: taskDocKey(doc),
    doc_id: String(doc.doc_id || ""),
    queue_stage: String(doc.queue_stage || ""),
    doc_seq: Number(doc.doc_seq || 0),
    track_doc_seq: stableDocumentSeq(doc),
    item_count: Number(doc.item_count || 0),
    unresolved_count: Number(doc.unresolved_count || 0),
    preview: String(doc.preview || ""),
  };
}

function minimalLocalTaskDocumentRef(taskDocId, queueStage) {
  return {
    task_doc_id: String(taskDocId || ""),
    doc_id: baseDocIdFromTaskDocId(taskDocId),
    queue_stage: String(queueStage || stageFromTaskDocId(taskDocId) || ""),
    doc_seq: 0,
    track_doc_seq: 0,
    item_count: 0,
    unresolved_count: 0,
    preview: "",
  };
}

function documentHasAdvancedBeyondTaskStage(docId, taskStage, docs) {
  const taskOrder = queueStageSort(taskStage);
  if (taskOrder >= 99) {
    return false;
  }
  return docs.some((doc) => {
    if (String(doc.doc_id || "") !== String(docId || "")) {
      return false;
    }
    const currentOrder = queueStageSort(doc.queue_stage);
    return currentOrder < 99 && currentOrder > taskOrder;
  });
}

function finalizedArchiveContainsDocumentRef(ref) {
  const seq = stableDocumentSeq(ref);
  if (!Number.isInteger(seq) || seq <= 0) {
    return false;
  }
  const ranges = state.manifest?.archive?.tracks?.dev?.finalized_track_doc_seq_ranges || [];
  return ranges.some(
    (range) => Array.isArray(range) && seq >= Number(range[0]) && seq <= Number(range[1]),
  );
}

function currentQueueDocForTaskDocId(taskDocId, taskStage, { submitted = false } = {}) {
  const baseDocId = baseDocIdFromTaskDocId(taskDocId);
  return buildDocumentTasks(state.currentPack).find(
    (doc) =>
      String(doc.doc_id || "") === baseDocId &&
      doc.queue_stage === taskStage &&
      docServerStageIsTaskValid(doc, { submitted }),
  ) || null;
}

function docServerStageIsTaskValid(doc, { submitted = false } = {}) {
  if (!doc || documentIsResolved(doc)) {
    return false;
  }
  return doc.selectable !== false || (submitted && docIsProcessingOnServer(doc));
}

function localTaskRecordStage(record, sourceStage = "") {
  if (record?.queue_stage) {
    return record.queue_stage;
  }
  const fromDocIds = taskDocIdsForStorageTask(record?.task)
    .map(stageFromTaskDocId)
    .find(Boolean);
  if (fromDocIds) {
    return fromDocIds;
  }
  if (sourceStage === "yomi_final_review" || sourceStage === "yomi_strong_repair_review") {
    return sourceStage;
  }
  return "";
}

function taskDocIdsForStorageTask(task) {
  if (task?.mode === "document" && task.doc_id) {
    return [String(task.doc_id)];
  }
  if (task?.mode === "documents" && Array.isArray(task.doc_ids)) {
    return [...new Set(task.doc_ids.map(String))];
  }
  return [];
}

function stageFromTaskDocId(taskDocId) {
  const value = String(taskDocId || "");
  const separator = value.indexOf("::");
  return separator >= 0 ? value.slice(0, separator) : "";
}

function baseDocIdFromTaskDocId(taskDocId) {
  const value = String(taskDocId || "");
  const separator = value.indexOf("::");
  return separator >= 0 ? value.slice(separator + 2) : value;
}

function currentDraftStorageKey() {
  return draftStorageKey(state.currentPack.review_stage, state.currentPack.pack_id);
}

function localDraftStorageKeys() {
  const keys = [];
  for (let index = 0; index < window.localStorage.length; index += 1) {
    const key = window.localStorage.key(index);
    if (key?.startsWith("yomi-corpus:draft:")) {
      keys.push(key);
    }
  }
  if (!keys.includes(currentDraftStorageKey())) {
    keys.push(currentDraftStorageKey());
  }
  return keys;
}

function reviewStageFromDraftStorageKey(key) {
  const parts = String(key || "").split(":");
  return parts.length >= 4 ? parts[2] : "";
}

function uniqueTaskIdForRecords(taskId, records) {
  const base = taskId || "task";
  if (!records[base]) {
    return base;
  }
  let suffix = 2;
  while (records[`${base}_${suffix}`]) {
    suffix += 1;
  }
  return `${base}_${suffix}`;
}

function draftHasLocalWork(draft) {
  return Boolean(
    draft?.active_task_id ||
      Object.keys(draft?.saved_tasks || {}).length > 0 ||
      Object.keys(draft?.overrides || {}).length > 0 ||
      draft?.task?.doc_ids?.length > 0,
  );
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

function findSavedTaskDraftOverlap(docIds) {
  const selected = new Set((docIds || []).map(String));
  if (selected.size === 0) {
    return null;
  }
  for (const record of listSavedTaskDrafts()) {
    const recordDocIds = (record.task?.doc_ids || []).map(String);
    const overlap = recordDocIds.filter((docId) => selected.has(docId));
    if (overlap.length > 0) {
      return { record, overlap };
    }
  }
  return null;
}

function formatTaskOverlapMessage(overlap) {
  const docs = buildDocumentTasks(state.currentPack);
  const docSeqs = (overlap?.overlap || [])
    .map((docId) => documentDisplaySeq(docs.find((doc) => taskDocKey(doc) === docId)))
    .filter((seq) => Number.isInteger(seq));
  const label = localizedTaskLabel(
    overlap?.record?.task_label || overlap?.record?.task_id || "別のローカルタスク",
  );
  const docsText = docSeqs.length ? `文書 ${formatDocSeqs(docSeqs)}` : "選択した文書";
  return `${docsText}はすでに${label}に含まれています。先にそのタスクを再開してください。`;
}

function canonicalDocIdKey(docIds) {
  const order = new Map(buildActionableDocumentTasks(state.currentPack).map((doc, index) => [taskDocKey(doc), index]));
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
    task_label: `タスク ${number}`,
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
  const normalizedTask = normalizeTask(state.currentDraft.task, state.currentPack);
  const docsByKey = new Map(
    buildDocumentTasks(state.currentPack).map((doc) => [taskDocKey(doc), doc]),
  );
  return {
    ...identity,
    status: "deferred",
    queue_stage: taskQueueStage(state.currentDraft.task),
    task: {
      ...normalizedTask,
      started: false,
    },
    document_refs: normalizedTask.doc_ids
      .map((docId) => docsByKey.get(docId))
      .filter(Boolean)
      .map(localTaskDocumentRef),
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
  const selectedDocs = docs.filter((doc) => docIds.has(taskDocKey(doc)));
  const selectedKeys = new Set(selectedDocs.map((doc) => taskDocKey(doc)));
  const missingRefs = (record.document_refs || []).filter(
    (ref) => docIds.has(String(ref.task_doc_id || "")) && !selectedKeys.has(String(ref.task_doc_id || "")),
  );
  const docSeqs = [
    ...selectedDocs.map((doc) => documentDisplaySeq(doc)),
    ...missingRefs.map((ref) => stableDocumentSeq(ref)),
  ].filter((seq) => Number.isInteger(seq) && seq > 0);
  const itemCount = selectedDocs.reduce((sum, doc) => sum + Number(doc.item_count || 0), 0) +
    missingRefs.reduce((sum, ref) => sum + Number(ref.item_count || 0), 0);
  const parts = [];
  const queueStages = [...new Set(selectedDocs.map((doc) => doc.queue_stage).filter(Boolean))];
  if (queueStages.length) {
    parts.push(queueStages.map(formatReviewStageLabel).join(" + "));
  }
  if (docSeqs.length) {
    parts.push(`文書 ${formatDocSeqs(docSeqs)}`);
  }
  parts.push(`${itemCount}項目`);
  if (record.updated_at_epoch) {
    parts.push(`保存 ${formatDate(record.updated_at_epoch)}`);
  }
  if (record.submitted_at_epoch) {
    parts.push(`提出 ${formatDate(record.submitted_at_epoch)}`);
  }
  return parts.join(" · ");
}

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value));
}

function toggleDocumentTask(docId, selected) {
  const docs = buildActionableDocumentTasks(state.currentPack);
  const doc = docs.find((row) => taskDocKey(row) === docId);
  if (!docIsActionable(doc)) {
    return;
  }
  const task = normalizeTask(state.currentDraft.task, state.currentPack);
  let nextDocIds = task.doc_ids;
  if (selected && isUnifiedReviewPack(state.currentPack)) {
    nextDocIds = nextDocIds.filter((existingId) => {
      const existingDoc = docs.find((row) => taskDocKey(row) === existingId);
      return existingDoc?.queue_stage === doc.queue_stage;
    });
  }
  const docIds = new Set(nextDocIds);
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
  touchDraft();
  render();
}

function selectOnlyDocumentTask(docId) {
  const doc = buildActionableDocumentTasks(state.currentPack).find((row) => taskDocKey(row) === docId);
  if (!docIsActionable(doc)) {
    return;
  }
  state.currentDraft.task = {
    mode: "documents",
    doc_ids: [docId],
    started: false,
  };
  touchDraft();
  render();
}

function selectAllDocumentTasks(queueStage = null) {
  const task = normalizeTask(state.currentDraft.task, state.currentPack);
  const docs = selectableDocsForCurrentTask(buildActionableDocumentTasks(state.currentPack), task, queueStage);
  state.currentDraft.task = {
    mode: "documents",
    doc_ids: docs.map((doc) => taskDocKey(doc)),
    started: false,
  };
  touchDraft();
  render();
}

function clearQueueTaskSelection(queueStage) {
  const task = normalizeTask(state.currentDraft.task, state.currentPack);
  const docs = buildActionableDocumentTasks(state.currentPack);
  const docIds = task.doc_ids.filter((docId) => {
    const doc = docs.find((row) => taskDocKey(row) === docId);
    return doc?.queue_stage !== queueStage;
  });
  state.currentDraft.task = {
    ...task,
    mode: docIds.length > 0 ? "documents" : "all",
    doc_ids: docIds,
    started: false,
  };
  touchDraft();
  render();
}

function takeNextQueueDocuments(queueStage, count) {
  const docs = buildActionableDocumentTasks(state.currentPack)
    .filter((doc) => doc.queue_stage === queueStage && docIsActionable(doc))
    .sort((left, right) => documentDisplaySeq(left) - documentDisplaySeq(right))
    .slice(0, count);
  if (!docs.length) {
    return;
  }
  state.currentDraft.task = {
    mode: "documents",
    doc_ids: docs.map((doc) => taskDocKey(doc)),
    started: false,
  };
  touchDraft();
  render();
}

function loadWorkflowTakeNextCount(queueStage, options, fallback) {
  try {
    const stored = Number(
      window.localStorage.getItem(`${workflowTakeNextCountStorageKey}:${queueStage}`),
    );
    return options.includes(stored) ? stored : fallback;
  } catch {
    return fallback;
  }
}

function saveWorkflowTakeNextCount(queueStage, count) {
  try {
    window.localStorage.setItem(
      `${workflowTakeNextCountStorageKey}:${queueStage}`,
      String(count),
    );
  } catch {
    // The selection remains usable for this page even if storage is unavailable.
  }
}

function startReviewTask() {
  const task = normalizeTask(state.currentDraft.task, state.currentPack);
  if (task.doc_ids.length === 0) {
    showStatus("タスクを開始する前に一つ以上の文書を選択してください。", true);
    return;
  }
  const matchingDraft = findSavedTaskDraftByDocIds(task.doc_ids);
  if (matchingDraft) {
    resumeTaskDraft(matchingDraft.task_id);
    showStatus(`${localizedTaskLabel(matchingDraft.task_label || "保留中のタスク")}に戻りました。`);
    return;
  }
  const overlap = findSavedTaskDraftOverlap(task.doc_ids);
  if (overlap) {
    showStatus(formatTaskOverlapMessage(overlap), true);
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
  touchDraft();
  render({ scrollToTop: true });
}

function isTaskStarted() {
  return Boolean(normalizeTask(state.currentDraft?.task, state.currentPack).started);
}

function clearTaskSelection() {
  state.currentDraft.task = { mode: "documents", doc_ids: [], started: false };
  state.currentDraft.active_task_id = null;
  state.currentDraft.active_task_label = null;
  touchDraft();
  render();
}

function deferCurrentTask() {
  if (!isTaskStarted()) {
    clearTaskSelection();
    return;
  }
  const record = currentTaskDraftRecord();
  state.currentDraft.saved_tasks[record.task_id] = { ...record, status: "deferred" };
  clearActiveTaskState();
  touchDraft();
  showStatus(`${localizedTaskLabel(record.task_label || "タスク")}をローカルで保留しました。`);
  render({ scrollToTop: true });
}

function completeCurrentTask() {
  if (!isTaskStarted()) {
    clearTaskSelection();
    return;
  }
  if (
    !window.confirm(
      "このローカルタスクを提出済みにしますか？ GitHub Issueをすでに作成している場合を除き、先に「JSONをコピーしてIssueを開く」を実行してください。",
    )
  ) {
    return;
  }
  const record = currentTaskDraftRecord();
  state.currentDraft.saved_tasks[record.task_id] = {
    ...record,
    status: "submitted",
    submitted_at_epoch: Math.floor(Date.now() / 1000),
  };
  clearActiveTaskState();
  touchDraft();
  showStatus(`${localizedTaskLabel(record.task_label || "タスク")}をローカルで提出済みにしました。サーバーによるIssueの取り込みを待っています。`);
  render({ scrollToTop: true });
}

function resumeTaskDraft(taskId) {
  const record = normalizeLocalTaskRecordForCurrentPack(state.currentDraft.saved_tasks?.[taskId], "");
  if (!record) {
    delete state.currentDraft.saved_tasks?.[taskId];
    touchDraft();
    showStatus("そのローカルタスクは現在のレビューステージには適用できません。", true);
    render();
    return;
  }
  state.currentDraft.active_task_id = record.task_id;
  state.currentDraft.active_task_label = record.task_label || record.task_id;
  state.currentDraft.task = {
    ...normalizeTask(record.task, state.currentPack),
    started: true,
  };
  state.currentDraft.overrides = cloneJson(record.overrides || {});
  delete state.currentDraft.saved_tasks[taskId];
  touchDraft();
  render({ scrollToTop: true });
}

function markSavedTaskSubmitted(taskId) {
  const record = state.currentDraft.saved_tasks?.[taskId] || currentTaskDraftRecord();
  const submittedRecord = {
    ...record,
    status: "submitted",
    submitted_at_epoch: Math.floor(Date.now() / 1000),
  };
  delete submittedRecord.awaiting_issue_confirmation;
  state.currentDraft.saved_tasks[record.task_id] = submittedRecord;
  if (state.currentDraft.active_task_id === record.task_id) {
    clearActiveTaskState();
  }
  touchDraft();
  showStatus(`${localizedTaskLabel(record.task_label || "タスク")}をローカルで提出済みにしました。サーバーによるIssueの取り込みを待っています。`);
  render();
}

function clearActiveTaskState() {
  state.currentDraft.active_task_id = null;
  state.currentDraft.active_task_label = null;
  state.currentDraft.task = { mode: "documents", doc_ids: [], started: false };
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
    const draft = normalizeReviewDraft(parsed, pack);
    window.localStorage.setItem(key, JSON.stringify(draft));
    return draft;
  } catch {
    return createEmptyDraft(pack);
  }
}

function normalizeReviewDraft(parsed, pack) {
  const base = createEmptyDraft(pack);
  const activeTask = normalizeTask(parsed?.task, pack);
  const activeTaskIsValid = activeTask.mode === "documents" && activeTask.doc_ids.length > 0;
  const draft = {
    ...base,
    ...parsed,
    schema_version: 2,
    task: activeTaskIsValid ? activeTask : base.task,
    overrides: activeTaskIsValid ? filterOverridesForTask(pack, activeTask, parsed?.overrides || {}) : {},
    saved_tasks: {},
    next_task_number: Math.max(1, Number(parsed?.next_task_number || 1)),
  };
  delete draft.from_seq;
  delete draft.to_seq;

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
      task_label: rawRecord?.task_label || (taskNumber ? `タスク ${taskNumber}` : taskId),
      task_number: taskNumber || null,
      status: rawRecord?.status === "submitted" ? "submitted" : "deferred",
      task: { ...task, started: false },
      overrides: filterOverridesForTask(pack, task, rawRecord?.overrides || {}),
      updated_at_epoch: rawRecord?.updated_at_epoch || null,
      submitted_at_epoch: rawRecord?.submitted_at_epoch || null,
      awaiting_issue_confirmation: Boolean(rawRecord?.awaiting_issue_confirmation),
    };
  }

  draft.active_task_id = parsed?.active_task_id || null;
  draft.active_task_label = parsed?.active_task_label || draft.active_task_id || null;
  if (!draft.task.started || draft.task.mode !== "documents" || draft.task.doc_ids.length === 0) {
    draft.active_task_id = null;
    draft.active_task_label = null;
    draft.overrides = {};
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
  return `yomi-corpus:draft:${reviewStage}:${packId}:v2`;
}

function loadSettings() {
  try {
    const raw = window.localStorage.getItem(settingsKey);
    if (raw) {
      const parsed = JSON.parse(raw);
      el.reviewerName.value = parsed.reviewer_name || "";
    }
  } catch {
    // ignore
  }
  state.uiMode = "workflow";
  if (el.uiModeSelect) {
    el.uiModeSelect.value = state.uiMode;
  }
}

function saveSettings() {
  window.localStorage.setItem(
    settingsKey,
    JSON.stringify({
      reviewer_name: el.reviewerName.value.trim(),
      ui_mode: state.uiMode,
    })
  );
}

function updateLocation(stageId, packId) {
  const params = new URLSearchParams(window.location.search);
  params.set("stage", stageId);
  params.set("pack", packId);
  params.delete("ui");
  window.history.replaceState({}, "", `${window.location.pathname}?${params.toString()}`);
}

function normalizeUiMode(mode) {
  return "workflow";
}

function formatConfidenceCounts(counts) {
  const entries = Object.entries(counts || {});
  if (entries.length === 0) {
    return "なし";
  }
  return entries.map(([key, value]) => `${key}:${value}`).join(", ");
}

function localizedDecisionLabel(value) {
  return {
    accept: "採用",
    reject: "却下",
    defer: "保留",
    keep: "維持",
    skip: "スキップ",
    exclude: "除外",
  }[String(value || "").toLowerCase()] || String(value || "");
}

function localizedTombstoneLabel(value) {
  return !value || value === "Removed" ? "除外済み" : String(value);
}

function localizedTaskLabel(value) {
  return String(value || "").replace(/^Task (\d+)$/u, "タスク $1");
}

function localizedDocumentState(value) {
  return {
    final_pending: "一括レビュー待ち",
    final_in_review: "一括レビュー中",
    final_reviewed: "一括レビューをサーバーで処理中",
    strong_pending: "詳細修正待ち",
    strong_in_review: "詳細修正中",
    strong_apply_failed: "詳細修正の適用失敗",
    strong_reviewed: "詳細修正をサーバーで処理中",
    complete: "確定済み",
    skipped: "スキップ済み",
  }[String(value || "")] || String(value || "");
}

function formatDate(epochSeconds) {
  if (!epochSeconds) {
    return "不明";
  }
  return new Date(epochSeconds * 1000).toLocaleString("ja-JP");
}

function showStatus(message, isError = false) {
  el.statusBanner.textContent = message;
  el.statusBanner.classList.remove("hidden");
  el.statusBanner.style.color = isError ? "var(--danger)" : "var(--warning)";
}

async function fetchJson(url) {
  const cacheBuster = new URLSearchParams(window.location.search).get("v");
  const requestUrl = new URL(url, window.location.href);
  if (cacheBuster) {
    requestUrl.searchParams.set("v", cacheBuster);
  }
  const response = await fetch(requestUrl, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status} for ${url}`);
  }
  return response.json();
}

function startRuntimeStatusPolling() {
  if (!state.manifest?.runtime_status?.path) {
    return;
  }
  state.runtimePollingStarted = true;
  pollRuntimeStatus();
}

async function pollRuntimeStatus() {
  clearRuntimePollTimer();
  const path = state.manifest?.runtime_status?.path;
  if (!path || automaticRuntimeRefreshIsPaused()) {
    return;
  }
  const generation = state.runtimePollGeneration;
  try {
    const separator = path.includes("?") ? "&" : "?";
    const bucket = Math.floor(Date.now() / 30000);
    const runtimeStatus = await fetchJson(`${path}${separator}v=${bucket}`);
    if (generation !== state.runtimePollGeneration || automaticRuntimeRefreshIsPaused()) {
      return;
    }
    const previousRevision = Number(state.runtimeStatus?.state_revision || 0);
    const nextRevision = Number(runtimeStatus.state_revision || 0);
    state.runtimeStatus = runtimeStatus;
    state.runtimePollFailures = 0;
    renderRuntimeStatus();
    if (previousRevision > 0 && nextRevision > previousRevision) {
      if (isTaskStarted()) {
        el.serverUpdateMessage.textContent = "作業中にサーバー側の状態が更新されました。ローカル作業は保存されています。";
        el.serverUpdateBanner.classList.remove("hidden");
      } else {
        window.location.reload();
        return;
      }
    }
  } catch (error) {
    if (generation !== state.runtimePollGeneration || automaticRuntimeRefreshIsPaused()) {
      return;
    }
    state.runtimePollFailures += 1;
    console.warn("Runtime status poll failed", error);
  }
  scheduleRuntimeStatusPoll();
}

function automaticRuntimeRefreshIsPaused() {
  const searchActive =
    state.currentStageId === "archive_browser" && Boolean(state.archiveSearchQuery.trim());
  const previewOpen = Boolean(
    el.workflowPreviewModal && !el.workflowPreviewModal.classList.contains("hidden"),
  );
  const issueDialogOpen = Boolean(
    el.issueReturnModal && !el.issueReturnModal.classList.contains("hidden"),
  );
  return searchActive || previewOpen || issueDialogOpen;
}

function updateRuntimePollingForInteraction() {
  state.runtimePollGeneration += 1;
  clearRuntimePollTimer();
  if (
    state.runtimePollingStarted &&
    !automaticRuntimeRefreshIsPaused() &&
    state.manifest?.runtime_status?.path
  ) {
    pollRuntimeStatus();
  }
}

function clearRuntimePollTimer() {
  if (state.runtimePollTimer !== null) {
    window.clearTimeout(state.runtimePollTimer);
    state.runtimePollTimer = null;
  }
}

function scheduleRuntimeStatusPoll() {
  if (automaticRuntimeRefreshIsPaused()) {
    return;
  }
  const status = state.runtimeStatus || {};
  const polling = status.client_polling || {};
  let seconds = document.hidden
    ? Number(polling.hidden_seconds || 300)
    : runtimeScheduleIsNear(status)
      ? Number(polling.near_seconds || 15)
      : Number(polling.normal_seconds || 60);
  if (state.runtimePollFailures > 0) {
    seconds = Math.min(300, seconds * 2 ** Math.min(state.runtimePollFailures, 5));
  }
  state.runtimePollTimer = window.setTimeout(pollRuntimeStatus, Math.max(1, seconds) * 1000);
}

function runtimeScheduleIsNear(status) {
  const schedule = status.schedule || {};
  const anchor = Number(schedule.anchor_epoch || 0);
  const interval = Number(schedule.interval_seconds || 0);
  const grace = Number(schedule.grace_seconds || 0);
  if (!anchor || !interval || !grace) {
    return false;
  }
  const now = Date.now() / 1000;
  const elapsed = Math.max(0, now - anchor);
  const remainder = elapsed % interval;
  return Math.min(remainder, interval - remainder) <= grace;
}

function nextExpectedRuntimeEpoch(status) {
  const schedule = status.schedule || {};
  const anchor = Number(schedule.anchor_epoch || 0);
  const interval = Number(schedule.interval_seconds || 0);
  if (!anchor || !interval) {
    return 0;
  }
  const now = Date.now() / 1000;
  return anchor + (Math.floor(Math.max(0, now - anchor) / interval) + 1) * interval;
}

function renderRuntimeStatus() {
  const status = state.runtimeStatus;
  if (!status) {
    el.runtimeStatusLine.classList.add("hidden");
    return;
  }
  const labels = {
    idle: "待機中",
    waiting_for_review: "レビュー待ち",
    running: "同期中",
    error: "同期エラー",
  };
  const parts = [`サーバー: ${labels[status.status] || status.status || "不明"}`];
  if (status.last_successful_sync_epoch) {
    parts.push(`最終同期 ${formatDate(status.last_successful_sync_epoch)}`);
  }
  const nextExpected = nextExpectedRuntimeEpoch(status);
  if (nextExpected) {
    parts.push(`次回確認予定 ${formatDate(nextExpected)}`);
  }
  el.runtimeStatusLine.textContent = parts.join(" · ");
  el.runtimeStatusLine.classList.remove("hidden");
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
