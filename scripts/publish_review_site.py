from __future__ import annotations

import argparse
import json

from yomi_corpus.paths import repo_root
from yomi_corpus.review_site import publish_review_site


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish review UI assets and review packs into docs/ for GitHub Pages."
    )
    parser.add_argument(
        "--web-review-dir",
        default=str(repo_root() / "web" / "review"),
        help="Source directory for the review UI.",
    )
    parser.add_argument(
        "--docs-dir",
        default=str(repo_root() / "docs"),
        help="Destination docs/ directory used by GitHub Pages.",
    )
    parser.add_argument(
        "--review-pack-root",
        default=str(repo_root() / "data" / "review_packs"),
        help="Root directory containing source review pack JSON files.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = publish_review_site(
        web_review_dir=args.web_review_dir,
        docs_dir=args.docs_dir,
        review_pack_root=args.review_pack_root,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
