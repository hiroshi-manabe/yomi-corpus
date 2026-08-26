from __future__ import annotations

import errno
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yomi_corpus.review_site import (
    archive_search_unit,
    clear_directory,
    build_current_review_summary,
    build_review_manifest,
    collect_pending_review_search_records,
    collect_review_pack_entries,
    document_belongs_to_pending_pack,
    normalize_archive_yomi_tokens,
    publish_review_archive,
    publish_review_site,
    publish_issue_acknowledgments,
    issue_acknowledgments_need_publish,
    ReviewSitePublishBusy,
)


class ReviewSiteTests(unittest.TestCase):
    def test_publish_issue_acknowledgments_defers_when_site_is_busy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "ack.json"
            source.write_text("{}", encoding="utf-8")
            with patch(
                "yomi_corpus.review_site.review_site_publish_lock",
                side_effect=ReviewSitePublishBusy("busy"),
            ):
                result = publish_issue_acknowledgments(
                    docs_dir=root / "docs",
                    acknowledgment_path=source,
                )
            self.assertEqual(result["status"], "deferred")

    def test_publish_issue_acknowledgments_updates_only_watcher_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            review_dir = root / "docs" / "review"
            review_dir.mkdir(parents=True)
            manifest_path = review_dir / "manifest.json"
            manifest_path.write_text(
                json.dumps({"schema_version": 1, "stages": {}}),
                encoding="utf-8",
            )
            source = root / "dev.acknowledgments.json"
            source.write_text(
                json.dumps({"schema_version": 1, "records": [{"submission_id": "s1"}]}),
                encoding="utf-8",
            )

            result = publish_issue_acknowledgments(
                docs_dir=root / "docs",
                acknowledgment_path=source,
            )

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "generated")
            self.assertEqual(
                manifest["issue_acknowledgments"]["path"],
                "./issue-acknowledgments.json",
            )
            self.assertEqual(
                json.loads(
                    (review_dir / "issue-acknowledgments.json").read_text(encoding="utf-8")
                )["records"][0]["submission_id"],
                "s1",
            )
            self.assertFalse(
                issue_acknowledgments_need_publish(
                    docs_dir=root / "docs",
                    acknowledgment_path=source,
                )
            )

    def test_archive_search_unit_uses_shared_ruby_placement(self) -> None:
        search_unit = archive_search_unit(
            {
                "unit_seq": 1,
                "text": "脱ぐ。",
                "yomi_tokens": [["脱ぐ", "ヌグ"], ["。", "。"]],
            }
        )

        self.assertEqual(
            search_unit["ruby_tokens"][0]["nodes"],
            [
                {"type": "ruby", "text": "脱", "reading": "ぬ"},
                {"type": "text", "text": "ぐ"},
            ],
        )

    def test_review_ui_has_no_obsolete_range_selection_controls(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        app_source = (repository_root / "web/review/app.js").read_text(encoding="utf-8")
        page_source = (repository_root / "web/review/index.html").read_text(encoding="utf-8")

        for obsolete_text in (
            "range_start_doc_id",
            "range_end_doc_id",
            "setDocumentRangeBoundary",
            "selectDocumentRangeForQueue",
            "範囲指定を解除",
            "範囲を選択解除",
        ):
            self.assertNotIn(obsolete_text, app_source + page_source)

        self.assertIn("すべて選択", page_source)
        self.assertIn("作業を破棄", page_source)
        self.assertNotIn("編集をリセット", page_source)
        self.assertIn("takeNextQueueDocuments", app_source)

    def test_review_ui_never_exposes_batch_local_document_numbers(self) -> None:
        repository_root = Path(__file__).resolve().parents[1]
        app_source = (repository_root / "web/review/app.js").read_text(encoding="utf-8")

        self.assertIn("track_doc_seqs: docs.map((doc) => documentDisplaySeq(doc))", app_source)
        self.assertIn("track_doc_ranges: buildReviewedDocumentRanges(docs)", app_source)
        self.assertNotIn("payload.task.doc_seqs", app_source)
        self.assertNotIn("docs.map((doc) => doc.doc_seq)", app_source)
        self.assertNotIn("Number(doc?.track_doc_seq || doc?.doc_seq || 0)", app_source)
        self.assertIn('stageLabel = "Bulk Review"', app_source)
        self.assertIn('stageLabel = "Escalated Repair"', app_source)
        self.assertIn('return `[Finalized Correction] ${seq}`', app_source)
        self.assertNotIn("[yomi-review]", app_source)
        self.assertNotIn("[yomi-correction]", app_source)

    def test_clear_directory_retries_transient_nonempty_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "review"
            child = root / "packs"
            child.mkdir(parents=True)
            (child / "pack.json").write_text("{}", encoding="utf-8")
            real_rmtree = shutil.rmtree
            attempts = 0

            def transient_rmtree(path: Path) -> None:
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise OSError(errno.ENOTEMPTY, "transient PanFS directory state")
                real_rmtree(path)

            with patch("yomi_corpus.review_site.shutil.rmtree", transient_rmtree):
                clear_directory(root)

            self.assertEqual(attempts, 2)
            self.assertEqual(list(root.iterdir()), [])

    def test_pending_pack_ownership_uses_canonical_stage_then_repair_history(self) -> None:
        submitted = {
            "workflow_queue_stage": "yomi_strong_repair_review",
            "strong_repair_item_count": 1,
        }
        completed_without_repairs = {"strong_repair_item_count": 0}

        self.assertFalse(document_belongs_to_pending_pack(submitted, "yomi_final_review"))
        self.assertTrue(
            document_belongs_to_pending_pack(submitted, "yomi_strong_repair_review")
        )
        self.assertTrue(
            document_belongs_to_pending_pack(completed_without_repairs, "yomi_final_review")
        )
        self.assertFalse(
            document_belongs_to_pending_pack(
                completed_without_repairs,
                "yomi_strong_repair_review",
            )
        )

    def test_review_pack_entries_keep_nonfinalized_documents_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pack_root = Path(tmp)
            pack_path = pack_root / "pack.json"
            pack_path.write_text(
                json.dumps(
                    {
                        "pack_id": "yomi_final_dev_batch_0001_v1",
                        "review_stage": "yomi_final_review",
                        "track_name": "dev",
                        "batch_name": "dev_batch_0001",
                        "documents": [
                            {"doc_id": "doc1", "track_doc_seq": 1},
                            {"doc_id": "doc2", "track_doc_seq": 2},
                        ],
                        "items": [],
                    }
                ),
                encoding="utf-8",
            )

            entries = collect_review_pack_entries(
                pack_root,
                finalized_document_keys={(1, "doc1")},
            )

            self.assertEqual(entries[0]["pending_doc_ids"], ["doc2"])
            self.assertEqual(entries[0]["pending_document_count"], 1)

    def test_manifest_keeps_submitted_only_pack_in_current_work(self) -> None:
        manifest = build_review_manifest(
            [
                {
                    "pack_id": "yomi_strong_repair_dev_batch_0001_v1",
                    "title": "Strong repair submitted",
                    "review_stage": "yomi_strong_repair_review",
                    "track_name": "dev",
                    "batch_name": "dev_batch_0001",
                    "created_at_epoch": 10,
                    "item_count": 1,
                    "document_count": 1,
                    "selectable_document_count": 0,
                    "pending_doc_ids": ["doc1"],
                    "pending_document_count": 1,
                    "queue_id": "strong_repair",
                    "site_filename": "yomi_strong_repair_dev_batch_0001_v1.json",
                }
            ]
        )

        self.assertEqual(len(manifest["current_review_queues"]), 1)
        self.assertEqual(manifest["current_review_queues"][0]["pending_doc_ids"], ["doc1"])
        self.assertEqual(
            manifest["current_review_queues"][0]["selectable_document_count"],
            0,
        )

    def test_current_review_summary_contains_documents_without_items(self) -> None:
        entry = {
            "pack_id": "yomi_final_dev_batch_0001_v1",
            "queue_documents": [
                {
                    "doc_id": "doc1",
                    "track_doc_seq": 1,
                    "preview": "preview",
                    "item_count": 3,
                },
                {"doc_id": "doc2", "track_doc_seq": 2, "item_count": 4},
            ],
        }
        queue = {
            "pack_id": entry["pack_id"],
            "review_stage": "yomi_final_review",
            "queue_id": "final_review",
            "title": "Final",
            "track_name": "dev",
            "item_count": 7,
            "pending_doc_ids": ["doc2"],
        }

        summary = build_current_review_summary([entry], [queue])

        self.assertEqual(summary["packs"][0]["documents"], [entry["queue_documents"][1]])
        self.assertNotIn("items", summary["packs"][0])

    def test_pending_review_search_includes_nonfinalized_document_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pack_path = Path(tmp) / "yomi_final_dev_batch_0037_v1.json"
            pack_path.write_text(
                json.dumps(
                    {
                        "documents": [
                            {
                                "doc_id": "ja_cc_level2:0000000339",
                                "track_doc_seq": 339,
                            }
                        ],
                        "items": [
                            {
                                "doc_id": "ja_cc_level2:0000000339",
                                "track_doc_seq": 339,
                                "unit_seq": 2,
                                "text": "𠮟られたりする。",
                                "rendered_yomi": "𠮟ら/シカラ れ/レ たり/タリ する/スル 。/。",
                            },
                            {
                                "doc_id": "ja_cc_level2:0000000339",
                                "track_doc_seq": 339,
                                "unit_seq": 1,
                                "text": "前の文。",
                                "rendered_yomi": "前/マエ の/ノ 文/ブン 。/。",
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            records = collect_pending_review_search_records(
                [
                    {
                        "track_name": "dev",
                        "review_stage": "yomi_final_review",
                        "batch_name": "dev_batch_0037",
                        "created_at_epoch": 1,
                        "pack_id": "yomi_final_dev_batch_0037_v1",
                        "site_filename": pack_path.name,
                        "source_path": pack_path,
                    }
                ],
                track_name="dev",
                finalized_documents=[],
            )

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["track_doc_seq"], 339)
            self.assertEqual(records[0]["doc_id"], "ja_cc_level2:0000000339")
            self.assertEqual(
                records[0]["pack_path"],
                "./packs/yomi_final_dev_batch_0037_v1.json",
            )
            self.assertEqual(
                records[0]["units"][0]["yomi_tokens"],
                [["前", "マエ"], ["の", "ノ"], ["文", "ブン"], ["。", "。"]],
            )
            self.assertEqual(
                records[0]["units"][1]["yomi_tokens"],
                [
                    ["𠮟ら", "シカラ"], ["れ", "レ"], ["たり", "タリ"],
                    ["する", "スル"], ["。", "。"],
                ],
            )
            self.assertTrue(records[0]["units"][0]["ruby_tokens"])

    def test_archive_preserves_optional_japanese_numeral_reading(self) -> None:
        self.assertEqual(
            normalize_archive_yomi_tokens(
                [["一二三", "ヒフミ"], ["二〇〇二", ""], ["Ⅲ", "サン"]]
            ),
            [["一二三", "ヒフミ"], ["二〇〇二", ""], ["Ⅲ", ""]],
        )

    def test_review_assets_use_japanese_user_interface(self) -> None:
        asset_root = Path(__file__).resolve().parents[1] / "web" / "review"
        html = (asset_root / "index.html").read_text(encoding="utf-8")
        app = (asset_root / "app.js").read_text(encoding="utf-8")
        css = (asset_root / "style.css").read_text(encoding="utf-8")

        self.assertIn('<html lang="ja">', html)
        self.assertIn("<h1>レビュー画面</h1>", html)
        self.assertIn("JSONをコピーしてIssueを開く", html)
        self.assertIn('title: "一括レビュー"', app)
        self.assertIn('title: "詳細修正"', app)
        self.assertIn('title: "確定済みコーパス"', app)
        self.assertIn("<h3>作業中の文書</h3>", app)
        self.assertIn("現在レビュー対象になっている文書と、その処理状況です。", app)
        self.assertIn("corpus-map-manual-correction-badge", app)
        self.assertIn("archiveSearchUnitYomiText", app)
        self.assertIn("renderArchiveSearchRubySnippet", app)
        self.assertIn("renderArchiveSearchHistory", app)
        self.assertIn("rememberArchiveSearchQuery", app)
        self.assertIn("archiveSearchHistoryLimit = 12", app)
        self.assertIn("表記/読みで検索", app)
        self.assertIn("handleArchiveCorrectionEditorKeydown", app)
        self.assertIn("data-archive-correction-revert", app)
        self.assertIn("function revertArchiveCorrectionRow", app)
        self.assertIn('event.keyCode === 229', app)
        self.assertIn("event.shiftKey", app)
        self.assertIn("scrollToManualCorrection", app)
        self.assertIn("function scrollReviewPageToTop()", app)
        self.assertIn("render({ scrollToTop: true });", app)
        render_body = app.split("function render({ scrollToTop = false } = {}) {", 1)[1].split(
            "\n}", 1
        )[0]
        self.assertNotIn("syncLocalTaskRecordsForCurrentPack", render_body)
        self.assertEqual(app.count("syncLocalTaskRecordsForCurrentPack();"), 2)
        self.assertIn("includeFlagAcknowledgements", app)
        self.assertIn("acknowledgement_only", app)
        self.assertIn("function renderYomiDirectEditor", app)
        self.assertIn("renderedYomi.textContent = serializeEditableYomiTokens(tokens);", app)
        self.assertIn('resolution: "direct_edit"', app)
        self.assertIn("data-yomi-direct-revert", app)
        self.assertIn("function yomiOverrideHasInteractiveReadingEdits", app)
        self.assertIn("クリックで選択した読み・区切り・結合の変更は取り消されます", app)
        self.assertIn('.replaceAll("\\u3000", "\\\\u3000")', app)
        self.assertIn('if (char === "u")', app)
        self.assertIn(
            "return !current || Boolean(current.manual_correction_required);",
            app,
        )
        self.assertIn('id="repeat-cancellation-bar"', html)
        self.assertIn("registerRepeatedCancellation", app)
        self.assertIn("findRepeatedCancellationMatches", app)
        self.assertIn("cancellationTargetsForText", app)
        self.assertIn("Interaction spans are UI units, not lexical identity.", app)
        self.assertIn("applyYomiCandidateWithRepeatedCancellation", app)
        self.assertIn(
            "cycleYomiTarget(item, target, candidate, elementAnchorRect(button));",
            app,
        )
        self.assertIn("残り${action.matches.length}件にも適用", app)
        self.assertIn("recomputeRepairAtomSpans", app)
        self.assertIn("connectedRepairAtomComponents", app)
        self.assertIn("connectedCancelledTargets", app)
        self.assertIn("positionRepeatedCancellationBar", app)
        self.assertIn("window.visualViewport", app)
        self.assertIn("return !isUnresolvedNoRubyCandidate(selected);", app)
        self.assertIn('rt.textContent = isIntentionalNoRubyCandidate(candidate) ? "−" : "?";', app)
        self.assertIn("function noRubyState(candidate)", app)
        self.assertIn("numericKanaSuffixRubyNodes", app)
        self.assertIn("decorateReadingContrastBadge", app)
        self.assertIn("function readingContrastBadge", app)
        self.assertIn('return currentHasP ? "P" : "B";', app)
        self.assertIn(".reading-contrast-badge", css)
        self.assertIn(".reading-contrast-p", css)
        self.assertIn(".reading-contrast-b", css)
        self.assertIn("Script=Han", app)
        self.assertIn("function reviewSurfaceGraphemes", app)
        self.assertIn("codePoint >= 0xe0100", app)
        self.assertIn("docIsProcessingOnServer", app)
        self.assertIn("サーバー処理中", app)
        self.assertIn('stateName === "strong_reviewed"', app)
        self.assertIn("documentHasAdvancedBeyondTaskStage", app)
        self.assertIn("minimalLocalTaskDocumentRef", app)
        self.assertIn("withSubmittedProcessingPlaceholders", app)
        self.assertIn("finalizedArchiveContainsDocumentRef", app)
        self.assertIn("finalized_track_doc_seq_ranges", app)
        self.assertIn("normalizeStoredSubmittedTask", app)
        self.assertIn('? normalizeStoredSubmittedTask(rawRecord?.task)', app)
        self.assertIn("document_refs: cloneJson(rawRecord?.document_refs || [])", app)
        self.assertIn("awaiting_finalization: Boolean(doc.awaiting_finalization)", app)
        submitted_handler = app.split(
            'el.markSubmitted?.addEventListener("click", () => {', 1
        )[1].split("\n  });", 1)[0]
        self.assertLess(
            submitted_handler.index("hideIssueReturnModal();"),
            submitted_handler.index("markSavedTaskSubmitted(pendingTaskId);"),
        )
        self.assertIn("delete submittedRecord.awaiting_issue_confirmation;", app)

    def test_build_review_manifest_marks_latest_pack_active(self) -> None:
        manifest = build_review_manifest(
            [
                {
                    "pack_id": "alpha_v1",
                    "title": "Alpha v1",
                    "review_stage": "alphabetic_candidate_review",
                    "created_at_epoch": 10,
                    "item_count": 3,
                    "site_filename": "alpha_v1.json",
                },
                {
                    "pack_id": "alpha_v2",
                    "title": "Alpha v2",
                    "review_stage": "alphabetic_candidate_review",
                    "created_at_epoch": 20,
                    "item_count": 4,
                    "site_filename": "alpha_v2.json",
                },
            ]
        )

        stage = manifest["stages"]["alphabetic_candidate_review"]
        self.assertEqual(manifest["default_stage"], "alphabetic_candidate_review")
        self.assertEqual(stage["latest_pack_id"], "alpha_v2")
        self.assertEqual(stage["packs"][0]["status"], "archived")
        self.assertEqual(stage["packs"][1]["status"], "active-working")

    def test_build_review_manifest_exposes_current_tracks_from_given_entries(self) -> None:
        manifest = build_review_manifest(
            [
                {
                    "pack_id": "alphabetic_candidates_batch_0001_v1",
                    "title": "Working",
                    "review_stage": "alphabetic_candidate_review",
                    "track_name": "working",
                    "created_at_epoch": 10,
                    "item_count": 3,
                    "site_filename": "working.json",
                },
                {
                    "pack_id": "alphabetic_candidates_dev_batch_0001_v1",
                    "title": "Dev",
                    "review_stage": "alphabetic_candidate_review",
                    "track_name": "dev",
                    "created_at_epoch": 20,
                    "item_count": 2,
                    "site_filename": "dev.json",
                },
            ]
        )

        stage = manifest["stages"]["alphabetic_candidate_review"]
        self.assertEqual(manifest["default_stage"], "alphabetic_candidate_review")
        self.assertEqual(manifest["current_tracks"]["working"]["pack_id"], "alphabetic_candidates_batch_0001_v1")
        self.assertEqual(manifest["current_tracks"]["dev"]["pack_id"], "alphabetic_candidates_dev_batch_0001_v1")
        self.assertEqual(stage["latest_pack_id"], "alphabetic_candidates_batch_0001_v1")
        self.assertEqual(stage["latest_pack_ids_by_track"]["working"], "alphabetic_candidates_batch_0001_v1")
        self.assertEqual(stage["latest_pack_ids_by_track"]["dev"], "alphabetic_candidates_dev_batch_0001_v1")
        self.assertEqual(stage["packs"][0]["status"], "active-working")
        self.assertEqual(stage["packs"][1]["status"], "active-dev")

    def test_collect_review_pack_entries_keeps_only_dev_packs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pack_root = root / "data" / "review_packs"
            pack_root.mkdir(parents=True)
            (pack_root / "working.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "review_stage": "alphabetic_candidate_review",
                        "pack_id": "alphabetic_candidates_batch_0001_v1",
                        "track_name": "working",
                        "created_at_epoch": 10,
                        "item_count": 1,
                        "items": [],
                    }
                ),
                encoding="utf-8",
            )
            (pack_root / "dev.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "review_stage": "yomi_final_review",
                        "pack_id": "yomi_final_dev_batch_0001_v1",
                        "track_name": "dev",
                        "created_at_epoch": 20,
                        "item_count": 1,
                        "items": [],
                    }
                ),
                encoding="utf-8",
            )

            from yomi_corpus.review_site import collect_review_pack_entries

            entries = collect_review_pack_entries(pack_root)

        self.assertEqual([entry["pack_id"] for entry in entries], ["yomi_final_dev_batch_0001_v1"])

    def test_build_review_manifest_labels_yomi_final_review(self) -> None:
        manifest = build_review_manifest(
            [
                {
                    "pack_id": "yomi_final_dev_batch_0001_v1",
                    "title": "Yomi final review / dev_batch_0001 / v1",
                    "review_stage": "yomi_final_review",
                    "track_name": "dev",
                    "batch_name": "dev_batch_0001",
                    "created_at_epoch": 10,
                    "item_count": 64,
                    "site_filename": "yomi_final_dev_batch_0001_v1.json",
                },
            ]
        )

        stage = manifest["stages"]["yomi_final_review"]
        self.assertEqual(stage["label"], "一括レビュー")
        self.assertEqual(stage["latest_pack_id"], "yomi_final_dev_batch_0001_v1")
        self.assertEqual(manifest["current_tracks"]["dev"]["review_stage"], "yomi_final_review")

    def test_build_review_manifest_labels_yomi_strong_repair_review(self) -> None:
        manifest = build_review_manifest(
            [
                {
                    "pack_id": "yomi_strong_repair_dev_batch_0001_v1",
                    "title": "Yomi strong repair review / dev_batch_0001 / v1",
                    "review_stage": "yomi_strong_repair_review",
                    "track_name": "dev",
                    "created_at_epoch": 10,
                    "item_count": 6,
                    "site_filename": "yomi_strong_repair_dev_batch_0001_v1.json",
                },
            ]
        )

        stage = manifest["stages"]["yomi_strong_repair_review"]
        self.assertEqual(stage["label"], "詳細修正")
        self.assertEqual(stage["latest_pack_id"], "yomi_strong_repair_dev_batch_0001_v1")
        self.assertEqual(
            manifest["current_tracks"]["dev"]["review_stage"],
            "yomi_strong_repair_review",
        )

    def test_build_review_manifest_exposes_current_dev_review_queues(self) -> None:
        manifest = build_review_manifest(
            [
                {
                    "pack_id": "yomi_final_dev_batch_0001_v1",
                    "title": "Yomi final review / dev_batch_0001 / v1",
                    "review_stage": "yomi_final_review",
                    "track_name": "dev",
                    "batch_name": "dev_batch_0001",
                    "created_at_epoch": 10,
                    "item_count": 64,
                    "document_count": 5,
                    "selectable_document_count": 3,
                    "queue_id": "final_review",
                    "site_filename": "yomi_final_dev_batch_0001_v1.json",
                },
                {
                    "pack_id": "yomi_final_dev_batch_0002_v1",
                    "title": "Yomi final review / dev_batch_0002 / v1",
                    "review_stage": "yomi_final_review",
                    "track_name": "dev",
                    "batch_name": "dev_batch_0002",
                    "created_at_epoch": 15,
                    "item_count": 40,
                    "document_count": 4,
                    "selectable_document_count": 2,
                    "queue_id": "final_review",
                    "site_filename": "yomi_final_dev_batch_0002_v1.json",
                },
                {
                    "pack_id": "yomi_strong_repair_dev_batch_0001_v1",
                    "title": "Yomi strong repair review / dev_batch_0001 / v1",
                    "review_stage": "yomi_strong_repair_review",
                    "track_name": "dev",
                    "batch_name": "dev_batch_0001",
                    "created_at_epoch": 20,
                    "item_count": 6,
                    "document_count": 5,
                    "selectable_document_count": 1,
                    "queue_id": "strong_repair",
                    "site_filename": "yomi_strong_repair_dev_batch_0001_v1.json",
                },
            ]
        )

        queues = manifest["current_review_queues"]
        self.assertEqual(
            [row["review_stage"] for row in queues],
            ["yomi_final_review", "yomi_final_review", "yomi_strong_repair_review"],
        )
        self.assertEqual(
            [row["batch_name"] for row in queues],
            ["dev_batch_0001", "dev_batch_0002", "dev_batch_0001"],
        )
        self.assertEqual(queues[0]["queue_id"], "final_review")
        self.assertEqual(queues[0]["selectable_document_count"], 3)
        self.assertEqual(queues[2]["queue_id"], "strong_repair")

    def test_build_review_manifest_defaults_to_newest_current_pack(self) -> None:
        manifest = build_review_manifest(
            [
                {
                    "pack_id": "alphabetic_candidates_batch_0001_v1",
                    "title": "Old working alphabetic",
                    "review_stage": "alphabetic_candidate_review",
                    "track_name": "working",
                    "created_at_epoch": 10,
                    "item_count": 15,
                    "site_filename": "alphabetic_candidates_batch_0001_v1.json",
                },
                {
                    "pack_id": "yomi_final_dev_batch_0001_v1",
                    "title": "New dev yomi",
                    "review_stage": "yomi_final_review",
                    "track_name": "dev",
                    "created_at_epoch": 20,
                    "item_count": 64,
                    "site_filename": "yomi_final_dev_batch_0001_v1.json",
                },
            ]
        )

        self.assertEqual(manifest["default_stage"], "yomi_final_review")

    def test_publish_review_site_copies_assets_and_packs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            web_review_dir = root / "web" / "review"
            docs_dir = root / "docs"
            review_pack_root = root / "data" / "review_packs" / "alphabetic"

            web_review_dir.mkdir(parents=True)
            review_pack_root.mkdir(parents=True)

            (web_review_dir / "index.html").write_text("<html>review</html>", encoding="utf-8")
            (web_review_dir / "app.js").write_text("console.log('ok');", encoding="utf-8")
            (review_pack_root / "batch_0001_candidates_v1.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "review_stage": "alphabetic_candidate_review",
                        "pack_id": "alphabetic_candidates_batch_0001_v1",
                        "track_name": "dev",
                        "created_at_epoch": 123,
                        "item_count": 1,
                        "items": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            runtime_status = root / "data" / "state" / "review_sync" / "dev.runtime_status.json"
            runtime_status.parent.mkdir(parents=True)
            runtime_status.write_text(
                json.dumps({"schema_version": 1, "state_revision": 3}),
                encoding="utf-8",
            )
            acknowledgments = (
                root / "data" / "state" / "issue_watch" / "dev.acknowledgments.json"
            )
            acknowledgments.parent.mkdir(parents=True)
            acknowledgments.write_text(
                json.dumps({"schema_version": 1, "records": [{"submission_id": "s1"}]}),
                encoding="utf-8",
            )

            manifest = publish_review_site(
                web_review_dir=web_review_dir,
                docs_dir=docs_dir,
                review_pack_root=root / "data" / "review_packs",
                project_root=root,
            )

            self.assertTrue((docs_dir / "index.html").exists())
            self.assertTrue((docs_dir / "review" / "index.html").exists())
            self.assertTrue((docs_dir / "review" / "app.js").exists())
            self.assertTrue(
                (docs_dir / "review" / "packs" / "alphabetic_candidates_batch_0001_v1.json").exists()
            )

            saved_manifest = json.loads((docs_dir / "review" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(saved_manifest["default_stage"], "alphabetic_candidate_review")
            self.assertEqual(saved_manifest["runtime_status"]["path"], "./runtime-status.json")
            self.assertEqual(
                saved_manifest["issue_acknowledgments"]["path"],
                "./issue-acknowledgments.json",
            )
            self.assertTrue((docs_dir / "review" / "issue-acknowledgments.json").exists())
            self.assertTrue((docs_dir / "review" / "current-review-summary.json").exists())
            self.assertEqual(
                saved_manifest["current_review_summary"]["path"],
                "./current-review-summary.json",
            )
            self.assertEqual(
                json.loads((docs_dir / "review" / "runtime-status.json").read_text(encoding="utf-8"))[
                    "state_revision"
                ],
                3,
            )
            self.assertEqual(manifest["stages"]["alphabetic_candidate_review"]["latest_pack_id"], "alphabetic_candidates_batch_0001_v1")

    def test_publish_review_archive_exports_finalized_documents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_root = root / "data" / "pipeline" / "batches"
            unit_root = root / "data" / "units" / "dev_batch_0001"
            output_root = root / "docs" / "review"
            batch_root.mkdir(parents=True)
            unit_root.mkdir(parents=True)
            batch_root.joinpath("dev_batch_0001.json").write_text(
                json.dumps(
                    {
                        "batch_name": "dev_batch_0001",
                        "track_name": "dev",
                        "current_stage": "yomi_finalized",
                    }
                ),
                encoding="utf-8",
            )
            unit_root.joinpath("units.yomi.final.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "doc_id": "ja_cc_level2:0000000001",
                                "unit_id": "ja_cc_level2:0000000001:u0001",
                                "unit_seq": 1,
                                "track_doc_seq": 1,
                                "text": "学校です。",
                                "analysis": {
                                    "mechanical": {
                                        "yomi": {
                                            "rendered": "学校/ガッコウ です/デス 。/。",
                                        }
                                    },
                                    "human_review": {
                                        "yomi_final": {
                                            "submission_id": "review_1",
                                        },
                                        "manual_correction": {
                                            "required": True,
                                            "reason": "segmentation",
                                        },
                                        "finalized_corrections": [
                                            {
                                                "submission_id": "correction_1",
                                                "proposed_rendered_yomi": "学校/ガッコウ です/デス 。/。",
                                            },
                                            {
                                                "submission_id": "correction_2",
                                                "proposed_rendered_yomi": "学校/ガッコウ です/デス 。/。",
                                            }
                                        ]
                                    },
                                    "llm": {
                                        "yomi_strong_repair": {
                                            "repairs": [
                                                {
                                                    "item_id": "repair_1",
                                                    "evidence": [
                                                        {
                                                            "region_id": "repair_1",
                                                            "surface": "学校",
                                                            "comment": "Established compound reading.",
                                                            "used_web_search": False,
                                                            "surface_occurrence_index": 0,
                                                        }
                                                    ],
                                                }
                                            ]
                                        }
                                    },
                                },
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "doc_id": "ja_cc_level2:0000000001",
                                "unit_id": "ja_cc_level2:0000000001:u0002",
                                "unit_seq": 2,
                                "track_doc_seq": 1,
                                "text": "今日です。",
                                "analysis": {
                                    "mechanical": {
                                        "yomi": {
                                            "rendered": "今日/キョウ です/デス 。/。",
                                        }
                                    },
                                    "human_review": {
                                        "finalized_corrections": [
                                            {
                                                "submission_id": "correction_1",
                                                "proposed_rendered_yomi": "今日/キョウ です/デス 。/。",
                                            }
                                        ]
                                    },
                                },
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "doc_id": "ja_cc_level2:0000000002",
                                "unit_id": "ja_cc_level2:0000000002:u0001",
                                "unit_seq": 1,
                                "track_doc_seq": 2,
                                "text": "Ⅱ",
                                "analysis": {
                                    "mechanical": {
                                        "yomi": {
                                            "rendered": "Ⅱ/ニ",
                                        }
                                    }
                                },
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "doc_id": "ja_cc_level2:0000000003",
                                "unit_id": "ja_cc_level2:0000000003:u0001",
                                "unit_seq": 1,
                                "track_doc_seq": 3,
                                "text": "聖飢魔Ⅱ",
                                "analysis": {
                                    "mechanical": {
                                        "yomi": {
                                            "rendered": "聖飢魔Ⅱ/セイキマツ",
                                        }
                                    }
                                },
                            },
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            archive = publish_review_archive(project_root=root, review_output_dir=output_root)

            index = json.loads((output_root / "archive" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(archive["tracks"]["dev"]["document_count"], 3)
            self.assertEqual(
                archive["tracks"]["dev"]["finalized_track_doc_seq_ranges"],
                [[1, 3]],
            )
            self.assertEqual(archive["tracks"]["dev"]["manual_correction_required_count"], 1)
            self.assertEqual(
                index["tracks"]["dev"]["finalized_track_doc_seq_ranges"],
                [[1, 3]],
            )
            self.assertEqual(index["tracks"]["dev"]["manual_correction_required_count"], 1)
            self.assertEqual(index["tracks"]["dev"]["search_path"], "./archive/dev/search.json")
            self.assertEqual(index["tracks"]["dev"]["shard_size"], 1)
            self.assertEqual(len(index["tracks"]["dev"]["shards"]), 3)
            summaries = index["tracks"]["dev"]["documents"]
            self.assertEqual(len(summaries), 3)
            self.assertNotIn("units", summaries[0])
            self.assertEqual(summaries[0]["track_doc_seq"], 1)
            self.assertEqual(summaries[0]["text_preview"], "学校です。")
            self.assertEqual(
                summaries[0]["applied_review_submission_ids"],
                ["review_1"],
            )
            self.assertEqual(
                summaries[0]["applied_finalized_correction_submission_ids"],
                ["correction_1", "correction_2"],
            )
            self.assertEqual(summaries[0]["shard_path"], index["tracks"]["dev"]["shards"][0]["path"])
            shard_path = output_root / index["tracks"]["dev"]["shards"][0]["path"].removeprefix("./")
            shard = json.loads(shard_path.read_text(encoding="utf-8"))
            search = json.loads((output_root / "archive" / "dev" / "search.json").read_text(encoding="utf-8"))
            self.assertEqual(search["document_count"], 3)
            self.assertEqual(search["documents"][0]["track_doc_seq"], 1)
            self.assertEqual(search["schema_version"], 3)
            self.assertEqual(
                search["documents"][0]["units"][0]["yomi_tokens"],
                [["学校", "ガッコウ"], ["です", "デス"], ["。", "。"]],
            )
            self.assertEqual(search["documents"][0]["shard_path"], index["tracks"]["dev"]["shards"][0]["path"])
            self.assertEqual(shard["documents"][0]["track_doc_seq"], 1)
            self.assertEqual(shard["documents"][0]["finalized_correction_count"], 2)
            self.assertEqual(shard["documents"][0]["finalized_correction_sentence_count"], 2)
            self.assertEqual(shard["documents"][0]["manual_correction_required_count"], 1)
            self.assertEqual(
                shard["documents"][0]["applied_review_submission_ids"],
                ["review_1"],
            )
            self.assertEqual(
                shard["documents"][0]["applied_finalized_correction_submission_ids"],
                ["correction_1", "correction_2"],
            )
            self.assertRegex(shard["documents"][0]["archive_revision"], r"^[0-9a-f]{16}$")
            self.assertEqual(shard["documents"][0]["units"][0]["rendered_yomi"], "学校/ガッコウ です/デス 。/。")
            self.assertEqual(
                shard["documents"][0]["units"][0]["yomi_tokens"],
                [["学校", "ガッコウ"], ["です", "デス"], ["。", "。"]],
            )
            self.assertTrue(shard["documents"][0]["units"][0]["ruby_tokens"])
            self.assertEqual(
                shard["documents"][0]["units"][0]["strong_repair_evidence"],
                [
                    {
                        "region_id": "repair_1",
                        "surface": "学校",
                        "comment": "Established compound reading.",
                        "used_web_search": False,
                        "surface_occurrence_index": 0,
                    }
                ],
            )
            self.assertEqual(shard["documents"][0]["units"][0]["finalized_correction_count"], 2)
            self.assertTrue(shard["documents"][0]["units"][0]["manual_correction_required"])
            self.assertEqual(
                shard["documents"][0]["units"][0]["applied_finalized_correction_submission_ids"],
                ["correction_1", "correction_2"],
            )
            second_shard_path = (
                output_root
                / index["tracks"]["dev"]["shards"][1]["path"].removeprefix("./")
            )
            second_shard = json.loads(second_shard_path.read_text(encoding="utf-8"))
            third_shard_path = (
                output_root
                / index["tracks"]["dev"]["shards"][2]["path"].removeprefix("./")
            )
            third_shard = json.loads(third_shard_path.read_text(encoding="utf-8"))
            self.assertEqual(second_shard["documents"][0]["units"][0]["rendered_yomi"], "Ⅱ/")
            self.assertEqual(
                third_shard["documents"][0]["units"][0]["rendered_yomi"],
                "聖飢魔Ⅱ/セイキマツ",
            )

    def test_publish_review_archive_exports_confirmed_skip_with_hybrid_ruby(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_root = root / "data" / "pipeline" / "batches"
            unit_root = root / "data" / "units" / "dev_batch_0001"
            output_root = root / "docs" / "review"
            batch_root.mkdir(parents=True)
            unit_root.mkdir(parents=True)
            batch_root.joinpath("dev_batch_0001.json").write_text(
                json.dumps(
                    {
                        "batch_name": "dev_batch_0001",
                        "track_name": "dev",
                        "current_stage": "yomi_finalized",
                    }
                ),
                encoding="utf-8",
            )
            unit_root.joinpath("units.yomi.final.jsonl").write_text("", encoding="utf-8")
            unit_root.joinpath("units.yomi.skipped.jsonl").write_text(
                json.dumps(
                    {
                        "doc_id": "ja_cc_level2:0000000159",
                        "unit_id": "ja_cc_level2:0000000159:u0016",
                        "unit_seq": 16,
                        "track_doc_seq": 159,
                        "text": "社会実験『MeguruQuruwa』で検証されました。",
                        "analysis": {
                            "mechanical": {
                                "yomi": {
                                    "rendered": "社会/シャカイ 実験/ジッケン 『/『 MeguruQuruwa/メグルクルワ 』/』 で/デ 検証/ケンショウ さ/サ れ/レ まし/マシ た/タ 。/。"
                                },
                                "alphabetic_scope": {
                                    "reasons": [{"entity_key": "meguruquruwa"}]
                                }
                            },
                            "llm": {
                                "scope_triage": {
                                    "status": "Skip",
                                    "source": "provisional_alphabetic_skip",
                                }
                            },
                            "human_review": {
                                "yomi_final": {
                                    "reviewed": True,
                                    "skip": True,
                                    "submission_id": "review-159",
                                }
                            },
                        },
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            publish_review_archive(
                project_root=root,
                review_output_dir=output_root,
                shard_size=100,
            )

            index = json.loads((output_root / "archive" / "index.json").read_text(encoding="utf-8"))
            shard_path = output_root / index["tracks"]["dev"]["shards"][0]["path"].removeprefix("./")
            shard = json.loads(shard_path.read_text(encoding="utf-8"))
            doc = shard["documents"][0]
            unit = doc["units"][0]
            self.assertEqual(
                unit["rendered_yomi"],
                "社会/シャカイ 実験/ジッケン 『/『 MeguruQuruwa/メグルクルワ 』/』 で/デ 検証/ケンショウ さ/サ れ/レ まし/マシ た/タ 。/。",
            )
            self.assertTrue(unit["ruby_tokens"])
            search = json.loads(
                (output_root / "archive" / "dev" / "search.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(doc["skipped_unit_count"], 1)
            self.assertTrue(unit["skipped"])
            self.assertTrue(unit["yomi_tokens"])
            self.assertEqual(unit["skip_provenance"]["submission_id"], "review-159")
            self.assertEqual(search["documents"][0]["units"], [])

    def test_publish_review_archive_exports_content_free_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            batch_root = root / "data" / "pipeline" / "batches"
            unit_root = root / "data" / "units" / "dev_batch_0001"
            output_root = root / "docs" / "review"
            batch_root.mkdir(parents=True)
            unit_root.mkdir(parents=True)
            batch_root.joinpath("dev_batch_0001.json").write_text(
                json.dumps(
                    {
                        "batch_name": "dev_batch_0001",
                        "track_name": "dev",
                        "current_stage": "yomi_finalized",
                    }
                ),
                encoding="utf-8",
            )
            unit_root.joinpath("units.yomi.final.jsonl").write_text(
                "\n".join(
                    json.dumps(
                        {
                            "doc_id": "doc-sensitive",
                            "track_doc_seq": 13,
                            "unit_id": f"doc-sensitive:u{unit_seq:04d}",
                            "unit_seq": unit_seq,
                            "text": text,
                            "analysis": {
                                "mechanical": {
                                    "yomi": {"rendered": rendered_yomi}
                                }
                            },
                        },
                        ensure_ascii=False,
                    )
                    for unit_seq, text, rendered_yomi in (
                        (1, "前です。", "前/マエ です/デス 。/。"),
                        (3, "後です。", "後/アト です/デス 。/。"),
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            unit_root.joinpath("units.yomi.excluded.jsonl").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "excluded": True,
                        "tombstone_label": "Removed",
                        "doc_id": "doc-sensitive",
                        "track_doc_seq": 13,
                        "unit_id": "doc-sensitive:u0002",
                        "unit_seq": 2,
                        "reason_category": "sensitive_content",
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            publish_review_archive(
                project_root=root,
                review_output_dir=output_root,
                shard_size=100,
            )

            index = json.loads((output_root / "archive" / "index.json").read_text(encoding="utf-8"))
            shard_path = output_root / index["tracks"]["dev"]["shards"][0]["path"].removeprefix("./")
            shard = json.loads(shard_path.read_text(encoding="utf-8"))
            search = json.loads(
                (output_root / "archive" / "dev" / "search.json").read_text(encoding="utf-8")
            )
            doc = shard["documents"][0]
            unit = doc["units"][0]
            self.assertEqual(doc["excluded_unit_count"], 1)
            self.assertEqual(
                [row["unit_seq"] for row in doc["units"]],
                [1, 2, 3],
            )
            excluded = doc["units"][1]
            self.assertTrue(excluded["excluded"])
            self.assertEqual(excluded["tombstone_label"], "Removed")
            self.assertEqual(excluded["text"], "")
            self.assertEqual(excluded["yomi_tokens"], [])
            self.assertEqual(
                [unit["yomi_tokens"] for unit in search["documents"][0]["units"]],
                [
                    [["前", "マエ"], ["です", "デス"], ["。", "。"]],
                    [["後", "アト"], ["です", "デス"], ["。", "。"]],
                ],
            )


if __name__ == "__main__":
    unittest.main()
