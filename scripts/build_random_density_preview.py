"""Compare vocabulary density gates on a reproducible random source pool."""
import argparse
import hashlib
import json
from pathlib import Path
import random
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from yomi_corpus.selection_experiments import build_selection_experiment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--pool-size", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=20260907)
    args = parser.parse_args()
    rng = random.Random(args.seed)
    with sqlite3.connect(args.index) as conn:
        conn.row_factory = sqlite3.Row
        pool = []
        for i, row in enumerate(conn.execute(
            "SELECT * FROM documents WHERE source_line_no > 50000 ORDER BY source_line_no"
        )):
            if i < args.pool_size:
                pool.append(dict(row))
            else:
                j = rng.randrange(i + 1)
                if j < args.pool_size:
                    pool[j] = dict(row)
        for row in pool:
            row["targets"] = [r[0] for r in conn.execute(
                "SELECT t.lemma FROM hits h JOIN targets t USING(target_id) "
                "WHERE h.source_line_no=? ORDER BY t.target_id", (row["source_line_no"],)
            )]
    pool.sort(key=lambda r: r["source_line_no"])
    identity = hashlib.sha256(json.dumps(pool, sort_keys=True).encode()).hexdigest()[:16]
    plan_path = args.output.with_suffix(".pool.json")
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps({"plan_id": identity, "selection": pool}, ensure_ascii=False))
    artifact = build_selection_experiment(
        plan_path=plan_path, index_path=args.index, output_path=args.output,
        density_thresholds=(0, 1, 2, 5, 10), random_seed=args.seed,
    )
    for strategy in artifact["strategies"]:
        print(strategy["label"], next(r for r in strategy["runs"] if r["character_budget"] == 100000))


if __name__ == "__main__":
    main()
