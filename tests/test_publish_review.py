from __future__ import annotations

import json
import runpy
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PUBLISH_REVIEW = runpy.run_path(str(PROJECT_ROOT / "publish-review"), run_name="publish_review_test")
collect_review_artifact_paths = PUBLISH_REVIEW["collect_review_artifact_paths"]


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
            (review_root / "app.js").write_text("// not a publish artifact\n", encoding="utf-8")
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
                "docs/review/manifest.json",
                "docs/review/packs/archived.json",
                "docs/review/packs/current.json",
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


if __name__ == "__main__":
    unittest.main()
