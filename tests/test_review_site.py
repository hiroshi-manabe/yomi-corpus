from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from yomi_corpus.review_site import build_review_manifest, publish_review_site


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

    def test_build_review_manifest_exposes_current_working_and_dev_tracks(self) -> None:
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
                        "created_at_epoch": 123,
                        "item_count": 1,
                        "items": [],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            manifest = publish_review_site(
                web_review_dir=web_review_dir,
                docs_dir=docs_dir,
                review_pack_root=root / "data" / "review_packs",
            )

            self.assertTrue((docs_dir / "index.html").exists())
            self.assertTrue((docs_dir / "review" / "index.html").exists())
            self.assertTrue((docs_dir / "review" / "app.js").exists())
            self.assertTrue(
                (docs_dir / "review" / "packs" / "alphabetic_candidates_batch_0001_v1.json").exists()
            )

            saved_manifest = json.loads((docs_dir / "review" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(saved_manifest["default_stage"], "alphabetic_candidate_review")
            self.assertEqual(manifest["stages"]["alphabetic_candidate_review"]["latest_pack_id"], "alphabetic_candidates_batch_0001_v1")


if __name__ == "__main__":
    unittest.main()
