from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from yomi_corpus.review_site import build_review_manifest, publish_review_archive, publish_review_site


class ReviewSiteTests(unittest.TestCase):
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
        self.assertEqual(stage["label"], "Yomi Final Review")
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
        self.assertEqual(stage["label"], "Yomi Strong Repair Review")
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

            archive = publish_review_archive(project_root=root, review_output_dir=output_root, shard_size=100)

            index = json.loads((output_root / "archive" / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(archive["tracks"]["dev"]["document_count"], 3)
            self.assertEqual(index["tracks"]["dev"]["search_path"], "./archive/dev/search.json")
            shard_path = output_root / index["tracks"]["dev"]["shards"][0]["path"].removeprefix("./")
            shard = json.loads(shard_path.read_text(encoding="utf-8"))
            search = json.loads((output_root / "archive" / "dev" / "search.json").read_text(encoding="utf-8"))
            self.assertEqual(search["document_count"], 3)
            self.assertEqual(search["documents"][0]["track_doc_seq"], 1)
            self.assertEqual(search["documents"][0]["text"], "学校です。\n今日です。")
            self.assertEqual(search["documents"][0]["shard_path"], index["tracks"]["dev"]["shards"][0]["path"])
            self.assertEqual(shard["documents"][0]["track_doc_seq"], 1)
            self.assertEqual(shard["documents"][0]["finalized_correction_count"], 2)
            self.assertEqual(shard["documents"][0]["finalized_correction_sentence_count"], 2)
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
            self.assertEqual(shard["documents"][0]["units"][0]["finalized_correction_count"], 2)
            self.assertEqual(
                shard["documents"][0]["units"][0]["applied_finalized_correction_submission_ids"],
                ["correction_1", "correction_2"],
            )
            self.assertEqual(shard["documents"][1]["units"][0]["rendered_yomi"], "Ⅱ/")
            self.assertEqual(shard["documents"][2]["units"][0]["rendered_yomi"], "聖飢魔Ⅱ/セイキマツ")


if __name__ == "__main__":
    unittest.main()
