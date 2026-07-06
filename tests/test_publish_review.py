from __future__ import annotations

import json
import runpy
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PUBLISH_REVIEW = runpy.run_path(str(PROJECT_ROOT / "publish-review"), run_name="publish_review_test")
collect_review_artifact_paths = PUBLISH_REVIEW["collect_review_artifact_paths"]
regenerate_review_artifacts = PUBLISH_REVIEW["regenerate_review_artifacts"]


class PublishReviewTests(unittest.TestCase):
    def test_collect_review_artifacts_uses_manifest_referenced_packs_only(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            review_root = root / "docs" / "review"
            packs_root = review_root / "packs"
            packs_root.mkdir(parents=True)
            (packs_root / "current.json").write_text("{}", encoding="utf-8")
            (packs_root / "archived.json").write_text("{}", encoding="utf-8")
            (packs_root / "unreferenced.json").write_text("{}", encoding="utf-8")
            (review_root / "app.js").write_text("// publish artifact\n", encoding="utf-8")
            (review_root / "style.css").write_text("/* publish artifact */\n", encoding="utf-8")
            (review_root / "index.html").write_text("<!doctype html>\n", encoding="utf-8")
            (review_root / "README.md").write_text("publish artifact\n", encoding="utf-8")
            (review_root / "manifest.json").write_text(
                json.dumps(
                    {
                        "current_tracks": {
                            "dev": {"path": "./packs/current.json"},
                        },
                        "stages": {
                            "yomi_final_review": {
                                "packs": [
                                    {"path": "./packs/archived.json"},
                                    {"path": "./packs/current.json"},
                                ]
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )

            rel_paths = [
                str(path.relative_to(root))
                for path in collect_review_artifact_paths(root)
            ]

        self.assertEqual(
            rel_paths,
            [
                "docs/review/README.md",
                "docs/review/app.js",
                "docs/review/index.html",
                "docs/review/manifest.json",
                "docs/review/packs/archived.json",
                "docs/review/packs/current.json",
                "docs/review/style.css",
            ],
        )

    def test_collect_review_artifacts_rejects_paths_outside_packs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            review_root = root / "docs" / "review"
            packs_root = review_root / "packs"
            packs_root.mkdir(parents=True)
            (packs_root / "current.json").write_text("{}", encoding="utf-8")
            (review_root / "app.json").write_text("{}", encoding="utf-8")
            (review_root / "manifest.json").write_text(
                json.dumps(
                    {
                        "current_tracks": {
                            "dev": {"path": "./packs/current.json"},
                            "bad": {"path": "./app.json"},
                        },
                    }
                ),
                encoding="utf-8",
            )

            rel_paths = [
                str(path.relative_to(root))
                for path in collect_review_artifact_paths(root)
            ]

        self.assertEqual(
            rel_paths,
            [
                "docs/review/manifest.json",
                "docs/review/packs/current.json",
            ],
        )

    def test_regenerate_review_artifacts_copies_web_assets_and_packs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            web_root = root / "web" / "review"
            docs_root = root / "docs"
            pack_root = root / "data" / "review_packs" / "yomi_final"
            web_root.mkdir(parents=True)
            pack_root.mkdir(parents=True)
            (web_root / "index.html").write_text("<!doctype html>\n", encoding="utf-8")
            (web_root / "app.js").write_text("// unified source\n", encoding="utf-8")
            (web_root / "style.css").write_text("/* style */\n", encoding="utf-8")
            (pack_root / "pack_1.json").write_text(
                json.dumps(
                    {
                        "pack_id": "pack_1",
                        "review_stage": "yomi_final_review",
                        "track_name": "dev",
                        "created_at_epoch": 1,
                        "item_count": 1,
                    }
                ),
                encoding="utf-8",
            )

            manifest = regenerate_review_artifacts(root)

            self.assertEqual(manifest["default_stage"], "yomi_final_review")
            self.assertEqual((docs_root / "review" / "app.js").read_text(encoding="utf-8"), "// unified source\n")
            self.assertTrue((docs_root / "review" / "packs" / "pack_1.json").exists())


if __name__ == "__main__":
    unittest.main()
