from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from yomi_corpus.pipeline import PipelineWorkspace
from yomi_corpus.processing_order import ProcessingOrderStore


class ProcessingOrderTests(unittest.TestCase):
    def test_identity_order_swap_and_reservation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.jsonl.gz"
            write_source(source, 5)
            ledger = [
                {"track_doc_seq": 1, "source_line_no": 1},
                {"track_doc_seq": 2, "source_line_no": 2},
            ]
            store = ProcessingOrderStore(root, "dev")

            manifest = store.ensure(
                source_path=source,
                dataset_name="demo",
                ledger_rows=ledger,
            )

            self.assertEqual(manifest["cursor"], 3)
            self.assertEqual(store.read_slots(1, 5), [1, 2, 3, 4, 5])
            with self.assertRaisesRegex(ValueError, "frozen"):
                store.swap_slots(2, 5)

            store.swap_slots(3, 5)
            self.assertEqual(store.read_slots(1, 5), [1, 2, 5, 4, 3])
            reservation = store.reserve(batch_name="dev_batch_0002", count=2)
            self.assertEqual(reservation["source_line_nos"], [5, 4])
            committed = store.commit_reservation("dev_batch_0002")
            self.assertEqual(committed["cursor"], 5)
            self.assertIsNone(committed["reservation"])

    def test_pipeline_uses_processing_slot_as_human_document_number(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.jsonl.gz"
            write_source(source, 4)
            config_dir = root / "config" / "datasets"
            config_dir.mkdir(parents=True)
            (config_dir / "demo.toml").write_text(
                f'name = "demo"\nsource_path = "{source}"\n', encoding="utf-8"
            )
            store = ProcessingOrderStore(root, "dev")
            store.ensure(source_path=source, dataset_name="demo", ledger_rows=[])
            store.swap_slots(1, 4)

            workspace = PipelineWorkspace(root)
            workspace.prepare_next_batch(
                track_name="dev",
                target_documents=2,
                dataset_config_path="config/datasets/demo.toml",
            )

            units = [
                json.loads(line)
                for line in (root / "data" / "units" / "dev_batch_0001" / "units.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            first_by_doc = {}
            for unit in units:
                first_by_doc.setdefault(unit["doc_id"], unit)
            self.assertEqual(
                [(row["doc_id"], row["track_doc_seq"], row["source_line_no"]) for row in first_by_doc.values()],
                [
                    ("demo:0000000004", 1, 4),
                    ("demo:0000000002", 2, 2),
                ],
            )

    def test_cursor_reconciles_contiguous_ledger_assignment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.jsonl.gz"
            write_source(source, 4)
            store = ProcessingOrderStore(root, "dev")
            store.ensure(source_path=source, dataset_name="demo", ledger_rows=[])

            manifest = store.ensure(
                source_path=source,
                dataset_name="demo",
                ledger_rows=[{"track_doc_seq": 1, "source_line_no": 1}],
            )

            self.assertEqual(manifest["cursor"], 2)

    def test_materialized_reservation_is_committed_during_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.jsonl.gz"
            write_source(source, 3)
            store = ProcessingOrderStore(root, "dev")
            store.ensure(source_path=source, dataset_name="demo", ledger_rows=[])
            reservation = store.reserve(batch_name="dev_batch_0001", count=2)
            assignments = ProcessingOrderStore.reservation_assignments(reservation)
            batch_dir = root / "data" / "units" / "dev_batch_0001"
            batch_dir.mkdir(parents=True)
            (batch_dir / "manifest.json").write_text(
                json.dumps({"processing_order_assignments": assignments}),
                encoding="utf-8",
            )
            batch_state = root / "data" / "pipeline" / "batches" / "dev_batch_0001.json"
            batch_state.parent.mkdir(parents=True)
            batch_state.write_text("{}", encoding="utf-8")
            ledger = [
                {"track_doc_seq": 1, "source_line_no": 1},
                {"track_doc_seq": 2, "source_line_no": 2},
            ]

            manifest = store.ensure(
                source_path=source,
                dataset_name="demo",
                ledger_rows=ledger,
            )

            self.assertEqual(manifest["cursor"], 3)
            self.assertIsNone(manifest["reservation"])

    def test_migrate_unprocessed_suffix_preserves_identity_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_source = root / "old.jsonl.gz"
            new_source = root / "new.jsonl.gz"
            write_identified_source(old_source, ["a", "b", "c", "d", "e"])
            write_identified_source(new_source, ["b", "a", "d", "f", "e"])
            ledger = [
                {"track_doc_seq": 1, "source_line_no": 1},
                {"track_doc_seq": 2, "source_line_no": 2},
            ]
            store = ProcessingOrderStore(root, "dev")
            store.ensure(source_path=old_source, dataset_name="demo", ledger_rows=ledger)
            store.swap_slots(3, 5)

            manifest = store.migrate_unprocessed_suffix(
                source_path=new_source,
                dataset_name="demo",
                ledger_rows=ledger,
                frozen_through_slot=2,
            )

            self.assertEqual(store.read_slots(1, 5), [1, 2, 5, 3, 4])
            self.assertEqual(manifest["cursor"], 3)
            self.assertEqual(manifest["document_count"], 5)
            self.assertEqual(manifest["order_generation"], 3)
            self.assertEqual(manifest["source_path"], str(new_source.resolve()))

    def test_rewind_to_frozen_prefix_after_suffix_retirement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.jsonl.gz"
            write_source(source, 5)
            store = ProcessingOrderStore(root, "dev")
            store.ensure(source_path=source, dataset_name="demo", ledger_rows=[])
            store.reserve(batch_name="dev_batch_0001", count=4)
            store.commit_reservation("dev_batch_0001")
            retained = [
                {"track_doc_seq": 1, "source_line_no": 1},
                {"track_doc_seq": 2, "source_line_no": 2},
            ]

            manifest = store.rewind_to_frozen_prefix(
                ledger_rows=retained,
                frozen_through_slot=2,
            )

            self.assertEqual(manifest["cursor"], 3)
            self.assertEqual(store.read_slots(1, 5), [1, 2, 3, 4, 5])


def write_source(path: Path, count: int) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as output:
        for number in range(1, count + 1):
            output.write(
                json.dumps(
                    {"text": f"文書{number}です。", "source_file": path.name},
                    ensure_ascii=False,
                )
                + "\n"
            )


def write_identified_source(path: Path, ids: list[str]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as output:
        for source_id in ids:
            output.write(
                json.dumps(
                    {
                        "text": f"文書{source_id}です。",
                        "meta": {"docId": source_id},
                        "source_file": path.name,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )


if __name__ == "__main__":
    unittest.main()
