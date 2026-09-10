"""Build a small, provenance-preserving evaluation from human boundary edits."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data/experiments/repair_segmentation_20260908"


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    baseline = OUTPUT / "baseline_prompt.txt"
    if not baseline.exists():
        baseline.write_text((ROOT / "config/prompts/yomi_repair.txt").read_text())
    rows = []
    name_checks = []
    seen = set()
    for batch in ("1050", "1052", "1054"):
        queue = {r["item_id"]: r for r in map(json.loads, (ROOT / f"data/units/dev_batch_{batch}/yomi_strong_repair_queue.jsonl").read_text().splitlines())}
        for path in sorted((ROOT / "data/review_submissions/yomi_strong_repair").glob(f"*dev_batch_{batch}_v1__*.json")):
            submission = json.loads(path.read_text())
            for override in submission.get("overrides", []):
                for region in override.get("regions", []):
                    segments = region.get("manual_segments", [])
                    row = queue.get(region["region_id"])
                    if row and row.get("rejected_span") == "西尾美恋" and len(name_checks) < 3:
                        name_checks.append({**row, "expected_segments": segments, "human_submission": str(path.relative_to(ROOT))})
                    if row is None or not row.get("rejected_span") or len(segments) < 2 or row["rejected_span"] in seen:
                        continue
                    seen.add(row["rejected_span"])
                    rows.append({**row, "expected_segments": segments, "human_submission": str(path.relative_to(ROOT))})
    (OUTPUT / "eval.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
    (OUTPUT / "name_contexts.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in name_checks[1:]))
    controls = []
    old_inputs = ROOT / "data/experiments/yomi_repair_regression_segmentation_20260630/input.jsonl"
    expected = [
        [("池尻", "いけじり"), ("中学校", "ちゅうがっこう")],
        [("視来", "みき")], [("視来", "みき")],
        [("一発", "いっぱつ")], [("真光元", "しんこうげん")],
        [("靏見", "つるみ")],
    ]
    for row, segments in zip(map(json.loads, old_inputs.read_text().splitlines()), expected, strict=True):
        row["expected_segments"] = [{"surface": s, "reading": r} for s, r in segments]
        controls.append(row)
    (OUTPUT / "controls.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in controls))
    print([(row["rejected_span"], row["expected_segments"]) for row in rows])


if __name__ == "__main__":
    main()
