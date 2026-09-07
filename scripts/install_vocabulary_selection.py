"""Install a saved campaign under the refill lock, with backups and ledger validation."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from yomi_corpus.pipeline import PipelineWorkspace
from yomi_corpus.processing_order import ProcessingOrderStore
from yomi_corpus.review_sync import ReviewSyncLock
from yomi_corpus.vocabulary_campaign import index_metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    args = parser.parse_args()
    raw = args.experiment.read_bytes()
    experiment = json.loads(raw)
    lines = experiment["selections"]["20"]["source_lines"]
    if len(lines) != 2000 or not experiment.get("length_matched") or experiment["threshold"] != 5:
        raise ValueError("Expected the agreed length-matched 2,000-document selection")
    with sqlite3.connect(args.index) as conn:
        metadata = index_metadata(conn)
    with ReviewSyncLock(ROOT / "data/state/refill/dev.lock", label="Campaign installation"):
        store = ProcessingOrderStore(ROOT, "dev")
        ledger = PipelineWorkspace(ROOT)._load_document_ledger("dev")["documents"]
        store.validate_frozen_prefix(ledger)
        before = store.read_slots(1, 2000)
        manifest = store.install_selection(2001, lines,
            expected_source_sha256=metadata["source_content_sha256"], backup_dir=args.backup)
        assert store.read_slots(1, 2000) == before
        assert store.read_slots(2001, 2000) == lines
        store.validate_frozen_prefix(ledger)
        (args.backup / "experiment.json").write_bytes(raw)
        print(json.dumps({"cursor": manifest["cursor"], "generation": manifest["order_generation"],
                          "experiment_sha256": hashlib.sha256(raw).hexdigest(),
                          "installed_slots": [2001, 4000], "backup": str(args.backup)}, indent=2))


if __name__ == "__main__":
    main()
