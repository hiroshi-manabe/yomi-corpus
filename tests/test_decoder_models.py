from __future__ import annotations

import json
from pathlib import Path

from yomi_corpus.decoder_models import refresh_decoder_model
from yomi_corpus.pipeline import PipelineWorkspace
from yomi_corpus.yomi.corpus_frequency import SurfaceReadingStats
from yomi_corpus.yomi.ngram_reading_transitions import NgramReadingTransitionStats


def test_refresh_decoder_model_exports_track_corpora_and_updates_track(tmp_path: Path) -> None:
    root = tmp_path
    batch_name = "dev_batch_0001"
    batch_dir = root / "data" / "units" / batch_name
    batch_dir.mkdir(parents=True)
    (batch_dir / "units.yomi.final.jsonl").write_text(
        json.dumps(
                {
                    "unit_id": "u1",
                    "text": "学校です。",
                    "analysis": {
                    "mechanical": {
                        "yomi": {
                            "rendered": "学校/ガッコウ です/デス 。/。",
                        }
                    }
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    workspace = PipelineWorkspace(root)
    workspace.ensure_dirs()
    workspace.batch_state_path(batch_name).write_text(
        json.dumps(
            {
                "batch_name": batch_name,
                "track_name": "dev",
                "batch_kind": "dev",
                "pipeline_profile": "dev",
                "dataset_name": "demo",
                "dataset_config_path": "config/datasets/demo.toml",
                "dataset_source_path": "/tmp/source.jsonl.gz",
                "target_documents": 1,
                "docs_written": 1,
                "units_written": 1,
                "current_stage": "yomi_finalized",
                "yomi_policy": {
                    "unit_mode": "sentence",
                    "auto_accept_profile": "stable_two_kanji",
                },
                "llm_policy": {
                    "yomi_reading": "standard",
                    "yomi_repair": "economy",
                    "yomi_rescue": "standard",
                },
                "llm_execution_policy": {
                    "yomi_reading": "background",
                    "yomi_repair": "background",
                    "yomi_rescue": "background",
                },
                "blocking_reason": None,
                "skipped_review_gates": [],
                "artifacts": {},
                "updated_at": "2026-06-26T00:00:00Z",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    base_corpus = root / "base.txt"
    base_corpus.write_text("元\t*\t元\t元\tモト\nEOS\n", encoding="utf-8")
    build_script = root / "fake_build_model.py"
    build_script.write_text(
        "\n".join(
            [
                "import argparse, json",
                "from pathlib import Path",
                "p=argparse.ArgumentParser()",
                "p.add_argument('--base-corpus')",
                "p.add_argument('--extra-corpus', action='append', default=[])",
                "p.add_argument('--output-dir')",
                "p.add_argument('--skip-kenlm', action='store_true')",
                "a=p.parse_args()",
                "out=Path(a.output_dir); out.mkdir(parents=True, exist_ok=True)",
                "print('fake stdout')",
                "print('fake stderr', file=__import__('sys').stderr)",
                "(out/'build_args.json').write_text(json.dumps(vars(a), ensure_ascii=False), encoding='utf-8')",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    summary = refresh_decoder_model(
        root=root,
        track_name="dev",
        model_id="m1",
        decoder_build_script=build_script,
        base_corpus=base_corpus,
        skip_kenlm=True,
        capture_build_output=True,
    )

    corpus_path = root / "data" / "decoder_corpora" / "dev" / f"{batch_name}.txt"
    model_dir = root / "data" / "decoder_models" / "dev" / "m1"
    assert summary.finalized_batches == [batch_name]
    assert corpus_path.exists()
    assert "学校\t*\t学校\t学校\tガッコウ" in corpus_path.read_text(encoding="utf-8")
    build_args = json.loads((model_dir / "build_args.json").read_text(encoding="utf-8"))
    assert build_args["extra_corpus"] == [str(corpus_path)]
    assert workspace.load_track_state("dev").decoder_model_dir == str(model_dir)
    assert (model_dir / "yomi_corpus_refresh.json").exists()
    stats_path = model_dir / "surface_reading_stats.tsv"
    stats_manifest_path = model_dir / "surface_reading_stats.manifest.json"
    assert summary.corpus_frequency_stats_artifact == str(stats_path)
    assert summary.corpus_frequency_manifest == str(stats_manifest_path)
    stats = SurfaceReadingStats.load_tsv(stats_path)
    assert stats.rows_by_surface["元"][0].count == 1
    assert stats.rows_by_surface["学校"][0].count == 1
    stats_manifest = json.loads(stats_manifest_path.read_text(encoding="utf-8"))
    assert stats_manifest["source_corpus_paths"] == [str(base_corpus), str(corpus_path)]
    stable_path = model_dir / "stable_surface_readings.tsv"
    stable_manifest_path = model_dir / "stable_surface_readings.manifest.json"
    assert summary.stable_surface_lexicon_artifact == str(stable_path)
    assert summary.stable_surface_lexicon_manifest == str(stable_manifest_path)
    assert stable_path.exists()
    stable_manifest = json.loads(stable_manifest_path.read_text(encoding="utf-8"))
    assert stable_manifest["source_corpus_paths"] == [str(base_corpus), str(corpus_path)]
    assert stable_manifest["parameters"]["max_span_tokens"] == 4
    transitions_path = model_dir / "ngram_reading_transitions.tsv"
    transitions_manifest_path = model_dir / "ngram_reading_transitions.manifest.json"
    assert summary.ngram_reading_transitions_artifact == str(transitions_path)
    assert summary.ngram_reading_transitions_manifest == str(
        transitions_manifest_path
    )
    transition_stats = NgramReadingTransitionStats.load_tsv(transitions_path)
    assert transition_stats.judge("元", "モト", "学校", "ガッコウ").reason == (
        "missing_surface_transition"
    )
    transition_manifest = json.loads(
        transitions_manifest_path.read_text(encoding="utf-8")
    )
    assert transition_manifest["source_corpus_paths"] == [
        str(base_corpus),
        str(corpus_path),
    ]
    assert (model_dir / "build.stdout.log").read_text(encoding="utf-8").strip() == "fake stdout"
    assert (model_dir / "build.stderr.log").read_text(encoding="utf-8").strip() == "fake stderr"
