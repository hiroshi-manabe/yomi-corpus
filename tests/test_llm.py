from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from yomi_corpus.llm.backend import (
    build_response_create_kwargs,
    extract_usage_from_batch_item,
    write_batch_requests,
)
from yomi_corpus.llm.config import apply_llm_profile, load_llm_profile, load_llm_task_config
from yomi_corpus.llm.parsers import parse_output
from yomi_corpus.llm.runner import (
    count_result_parse_errors,
    load_background_records,
    load_result_item_count,
    load_result_item_ids,
    run_background_task,
    run_llm_task,
    run_sync_task,
    write_background_records,
)
from yomi_corpus.llm.schemas import LLMResult
from yomi_corpus.llm.prompts import render_prompt
from yomi_corpus.llm.rendering import compact_rendered_for_llm
from yomi_corpus.llm.tasks import build_prompt_items
from yomi_corpus.llm.usage import normalize_usage


class LLMScaffoldingTests(unittest.TestCase):
    def test_load_llm_task_config(self) -> None:
        config = load_llm_task_config("config/llm/alphabetic_entity_judge.toml")
        self.assertEqual(config.task_name, "alphabetic_entity_judge")
        self.assertEqual(config.model, "gpt-5.5")
        self.assertEqual(config.parser, "json_object")
        self.assertEqual(config.rendered_yomi_display, "full")
        self.assertTrue(config.include_source_text)
        self.assertEqual(config.batch_max_requests_per_batch, 50000)

    def test_load_yomi_triage_uses_furigana_display(self) -> None:
        config = load_llm_task_config("config/llm/yomi_triage.toml")
        self.assertEqual(config.rendered_yomi_display, "furigana_no_space")

    def test_load_yomi_triage_can_omit_source_text(self) -> None:
        config = load_llm_task_config("config/llm/yomi_triage_furigana_no_text.toml")
        self.assertEqual(config.rendered_yomi_display, "furigana_no_space")
        self.assertFalse(config.include_source_text)

    def test_load_yomi_repair_uses_web_search_profile(self) -> None:
        config = load_llm_task_config("config/llm/yomi_repair.toml")
        self.assertEqual(config.reasoning_effort, "medium")
        self.assertEqual(config.verbosity, "medium")
        self.assertEqual(config.text_format, "text")
        self.assertTrue(config.enable_web_search)

    def test_scope_triage_prompt_mentions_sensitive_private_person_skip(self) -> None:
        config = load_llm_task_config("config/llm/scope_triage.toml")
        prompt = Path(config.prompt_template).read_text(encoding="utf-8")

        self.assertIn("private person", prompt)
        self.assertIn("criminal suspicion", prompt)
        self.assertIn("When unsure, Skip", prompt)

    def test_apply_llm_profile_overrides_model(self) -> None:
        config = load_llm_task_config("config/llm/yomi_triage.toml")
        profile = load_llm_profile("smoke")
        self.assertEqual(profile["model"], "gpt-5.4-nano")

        updated = apply_llm_profile(config, "smoke")
        self.assertEqual(updated.model, "gpt-5.4-nano")
        self.assertEqual(updated.reasoning_effort, config.reasoning_effort)

    def test_render_prompt_requires_variables(self) -> None:
        prompt = render_prompt("Hello {name}", {"name": "world"})
        self.assertEqual(prompt, "Hello world")

    def test_build_prompt_items_for_alphabetic_entity(self) -> None:
        config = load_llm_task_config("config/llm/alphabetic_entity_judge.toml")
        rows = [
            {
                "entity_key": "led zeppelin",
                "surface_forms": ["Led Zeppelin"],
                "occurrence_count": 1,
                "unit_count": 1,
                "example_texts": ["Led Zeppelinが好きです。"],
            }
        ]
        items = build_prompt_items(config, rows)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].item_id, "led zeppelin")
        self.assertIn("Led Zeppelin", items[0].prompt)

    def test_build_prompt_items_for_yomi_triage(self) -> None:
        config = load_llm_task_config("config/llm/yomi_triage.toml")
        items = build_prompt_items(
            config,
            [
                {
                    "unit_id": "u1",
                    "text": "大学です。",
                    "rendered": "大学/ダイガク です/デス 。/。",
                }
            ],
        )

        self.assertEqual(items[0].item_id, "u1")
        self.assertIn("Return exactly one token and nothing else", items[0].prompt)
        self.assertIn("大学（だいがく）です。", items[0].prompt)
        self.assertIn("Text: 大学です。", items[0].prompt)
        self.assertNotIn("です/デス", items[0].prompt)
        self.assertEqual(items[0].metadata["rendered_full"], "大学/ダイガク です/デス 。/。")
        self.assertEqual(items[0].metadata["rendered_prompt"], "大学（だいがく）です。")

    def test_compact_rendered_for_llm_keeps_kanji_and_latin_readings(self) -> None:
        rendered = (
            "こんな/コンナ 感じ/カンジ で/デ ラミン/ラミン トン/トン "
            "OK/オーケー ＯＫ/オーケー ＄/＄ 2/ ./. 価格/カカク"
        )
        self.assertEqual(
            compact_rendered_for_llm(rendered),
            "こんな 感じ/カンジ で ラミン トン OK/オーケー ＯＫ/オーケー ＄ 2 . 価格/カカク",
        )

    def test_build_prompt_items_for_non_target_judge(self) -> None:
        config = load_llm_task_config("config/llm/non_target_judge.toml")
        items = build_prompt_items(
            config,
            [{"unit_id": "u1", "text": "大学です。"}],
        )

        self.assertEqual(config.task_name, "non_target_judge")
        self.assertEqual(items[0].item_id, "u1")
        self.assertIn("non-target", items[0].prompt)

    def test_build_prompt_items_for_yomi_repair_target_group(self) -> None:
        config = load_llm_task_config("config/llm/yomi_repair.toml")
        items = build_prompt_items(
            config,
            [
                {
                    "item_id": "u1::target_group:1",
                    "unit_id": "u1",
                    "text": "それを、旧池尻中学校を改装した世田谷ものづくり学校で行われます。",
                    "rendered_yomi": (
                        "それ/ソレ を/ヲ 、/、 旧/キュウ 池尻中/イケジリナカ 学校/ガッコウ "
                        "を/ヲ 改装/カイソウ し/シ た/タ 世田谷/セタガヤ ものづくり/モノヅクリ "
                        "学校/ガッコウ で/デ 行わ/オコナワ れ/レ ます/マス 。/。"
                    ),
                    "repair_scope": "target_group",
                    "target_escalations": [
                        {
                            "surface": "池尻中",
                            "rejected_readings": [
                                {"surface": "池尻中", "reading": "いけじりなか"}
                            ],
                        },
                        {
                            "surface": "学校",
                            "rejected_readings": [
                                {"surface": "学校", "reading": "がっこう"}
                            ],
                        },
                    ],
                }
            ],
        )

        self.assertEqual(config.parser, "json_array")
        self.assertEqual(items[0].item_id, "u1::target_group:1")
        self.assertIn("Rejected span: 池尻中学校", items[0].prompt)
        self.assertIn("Rejected readings: 池尻中=いけじりなか; 学校=がっこう", items[0].prompt)
        self.assertIn("Prefer word-level segmentation", items[0].prompt)
        self.assertIn('"used_web_search":true/false', items[0].prompt)
        self.assertNotIn("e.g.", items[0].prompt)

    def test_parse_json_output(self) -> None:
        parsed = parse_output('{"status":"in_scope","confidence":"high","note":"ok"}', "json_object")
        self.assertEqual(parsed["status"], "in_scope")

    def test_parse_json_array_output(self) -> None:
        parsed = parse_output(
            '[{"surface":"池尻","reading":"いけじり","used_web_search":false}]',
            "json_array",
        )
        self.assertEqual(parsed[0]["surface"], "池尻")

    def test_parse_yomi_reading_completion_output(self) -> None:
        self.assertEqual(
            parse_output('{"年":"ねん"}', "yomi_reading_completion_json", metadata={"surface": "年"}),
            {"年": "ねん"},
        )
        self.assertEqual(
            parse_output('"ねん"}', "yomi_reading_completion_json", metadata={"surface": "年"}),
            {"年": "ねん"},
        )
        self.assertEqual(
            parse_output("オーケー", "yomi_reading_completion_json", metadata={"surface": "OK"}),
            {"OK": "オーケー"},
        )
        self.assertEqual(
            parse_output('"し"', "yomi_reading_completion_json", metadata={"surface": "占"}),
            {"占": "し"},
        )
        self.assertEqual(
            parse_output(
                'と**思**い、2017年7月に立ち上げた。->{"思":"おも"}',
                "yomi_reading_completion_json",
                metadata={"surface": "思"},
            ),
            {"思": "おも"},
        )
        self.assertEqual(
            parse_output(
                '{"FX":"エフエックス","CFD":"シーエフディー"}',
                "yomi_reading_completion_json",
                metadata={"surface": "FX"},
            ),
            {"FX": "エフエックス", "CFD": "シーエフディー"},
        )

    def test_parse_yomi_triage_label_output(self) -> None:
        parsed = parse_output("Review", "yomi_triage_label")
        self.assertEqual(parsed, {"status": "Review"})

    def test_parse_yomi_triage_reasoned_label_output(self) -> None:
        parsed = parse_output(
            "Reason: No correction needed.\nAnswer: OK",
            "yomi_triage_reasoned_label",
        )
        self.assertEqual(parsed, {"status": "OK", "reason": "No correction needed."})

    def test_parse_scope_triage_label_output(self) -> None:
        parsed = parse_output("Skip", "scope_triage_label")
        self.assertEqual(parsed, {"status": "Skip"})

    def test_build_response_kwargs_for_gpt5(self) -> None:
        config = load_llm_task_config("config/llm/alphabetic_entity_judge.toml")
        kwargs = build_response_create_kwargs(config, "prompt")
        self.assertEqual(kwargs["model"], "gpt-5.5")
        self.assertIn("text", kwargs)
        self.assertIn("reasoning", kwargs)
        self.assertNotIn("tools", kwargs)

    def test_build_response_kwargs_for_yomi_repair_enables_web_search(self) -> None:
        config = load_llm_task_config("config/llm/yomi_repair.toml")
        kwargs = build_response_create_kwargs(config, "prompt")
        self.assertEqual(kwargs["model"], "gpt-5.5")
        self.assertEqual(kwargs["text"], {"verbosity": "medium", "format": {"type": "text"}})
        self.assertEqual(kwargs["reasoning"], {"effort": "medium"})
        self.assertEqual(kwargs["tools"], [{"type": "web_search"}])

    def test_build_response_kwargs_supports_background(self) -> None:
        config = load_llm_task_config("config/llm/yomi_reading.toml")
        kwargs = build_response_create_kwargs(config, "prompt", background=True)
        self.assertEqual(kwargs["background"], True)
        self.assertEqual(kwargs["input"][0]["content"], "prompt")

    def test_write_batch_requests_jsonl(self) -> None:
        config = load_llm_task_config("config/llm/alphabetic_entity_judge.toml")
        rows = [
            {
                "entity_key": "run boys",
                "surface_forms": ["Run Boys"],
                "occurrence_count": 2,
                "unit_count": 2,
                "example_texts": ["Run Boysが出店しました。"],
            }
        ]
        items = build_prompt_items(config, rows)
        output_path = PROJECT_ROOT / "tests" / "tmp_batch_requests.jsonl"
        if output_path.exists():
            output_path.unlink()
        write_batch_requests(config, items, output_path)
        lines = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["custom_id"], "run boys")
        self.assertEqual(lines[0]["url"], "/v1/responses")
        output_path.unlink()

    def test_normalize_usage_supports_responses_shape(self) -> None:
        usage = normalize_usage(
            {
                "input_tokens": 1200,
                "input_tokens_details": {"cached_tokens": 1024},
                "output_tokens": 8,
                "output_tokens_details": {"reasoning_tokens": 2},
                "total_tokens": 1208,
            }
        )
        self.assertEqual(usage["input_tokens"], 1200)
        self.assertEqual(usage["cached_input_tokens"], 1024)
        self.assertEqual(usage["output_tokens"], 8)
        self.assertEqual(usage["reasoning_tokens"], 2)
        self.assertEqual(usage["total_tokens"], 1208)

    def test_extract_usage_from_batch_item(self) -> None:
        usage = extract_usage_from_batch_item(
            {
                "custom_id": "run boys",
                "response": {
                    "body": {
                        "usage": {
                            "input_tokens": 600,
                            "input_tokens_details": {"cached_tokens": 512},
                            "output_tokens": 5,
                            "output_tokens_details": {"reasoning_tokens": 1},
                            "total_tokens": 605,
                        }
                    }
                },
            }
        )
        self.assertEqual(usage["cached_input_tokens"], 512)

    def test_run_sync_task_resumes_from_existing_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.jsonl"
            output_path = root / "results.jsonl"
            job_dir = root / "job"
            input_path.write_text(
                "\n".join(
                    [
                        json.dumps(
                            {
                                "unit_id": "u1",
                                "text": "大学です。",
                                "rendered": "大学/ダイガク です/デス 。/。",
                            },
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            {
                                "unit_id": "u2",
                                "text": "方です。",
                                "rendered": "方/ホウ です/デス 。/。",
                            },
                            ensure_ascii=False,
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            output_path.write_text(
                json.dumps(
                    {
                        "item_id": "u1",
                        "raw_text": "OK",
                        "parsed": {"status": "OK"},
                        "parse_error": None,
                        "usage": {},
                        "metadata": {},
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            class FakeBackend:
                seen: list[str] = []

                def __init__(self, **kwargs: object) -> None:
                    pass

                def run_item(self, task_config: object, item: object) -> LLMResult:
                    self.seen.append(item.item_id)
                    return LLMResult(
                        item_id=item.item_id,
                        raw_text="Review",
                        parsed={"status": "Review"},
                        parse_error=None,
                        usage={"input_tokens": 1},
                        metadata={},
                    )

            with patch("yomi_corpus.llm.runner.OpenAIResponsesBackend", FakeBackend):
                summary = run_sync_task(
                    "config/llm/yomi_triage.toml",
                    str(input_path),
                    str(output_path),
                    job_dir=str(job_dir),
                )

            self.assertEqual(summary.total_items, 2)
            self.assertEqual(summary.completed_items, 2)
            self.assertEqual(summary.skipped_items, 1)
            self.assertEqual(FakeBackend.seen, ["u2"])
            rows = [
                json.loads(line)
                for line in output_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual([row["item_id"] for row in rows], ["u1", "u2"])
            self.assertTrue((job_dir / "manifest.json").exists())

    def test_run_llm_task_batch_reports_remote_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.jsonl"
            output_path = root / "results.jsonl"
            job_dir = root / "job"
            input_path.write_text(
                "\n".join(
                    [
                        json.dumps({"unit_id": "u1", "text": "大学です。", "rendered": "大学/ダイガク"}),
                        json.dumps({"unit_id": "u2", "text": "方です。", "rendered": "方/ホウ"}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            def fake_prepare(*args: object, **kwargs: object) -> None:
                job_dir.mkdir(parents=True)
                (job_dir / "status.json").write_text(
                    json.dumps({"state": "prepared"}),
                    encoding="utf-8",
                )

            def fake_submit(*args: object, **kwargs: object) -> dict[str, object]:
                return {"state": "running", "remote_status": "in_progress", "batch_id": "batch_1"}

            def fake_poll(*args: object, **kwargs: object) -> dict[str, object]:
                return {
                    "state": "running",
                    "remote_status": "in_progress",
                    "batch_id": "batch_1",
                    "remote_snapshot": {
                        "request_counts": {"total": 2, "completed": 1, "failed": 0}
                    },
                }

            with (
                patch("yomi_corpus.llm.runner.prepare_batch_job", side_effect=fake_prepare),
                patch("yomi_corpus.llm.runner.submit_batch_job", side_effect=fake_submit),
                patch("yomi_corpus.llm.runner.poll_batch_job", side_effect=fake_poll),
            ):
                summary = run_llm_task(
                    "config/llm/yomi_triage.toml",
                    str(input_path),
                    str(output_path),
                    execution_mode="batch",
                    job_dir=str(job_dir),
                    batch_wait=False,
                )

            self.assertEqual(summary.mode, "batch")
            self.assertEqual(summary.status, "running")
            self.assertEqual(summary.total_items, 2)
            self.assertEqual(summary.completed_items, 1)
            self.assertEqual(summary.remote_status, "in_progress")
            self.assertFalse(output_path.exists())

    def test_run_background_task_submits_and_parses_completed_responses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.jsonl"
            output_path = root / "results.jsonl"
            job_dir = root / "job"
            input_path.write_text(
                json.dumps(
                    {
                        "unit_id": "u1",
                        "text": "大学です。",
                        "rendered": "大学/ダイガク です/デス 。/。",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            class FakeBackend:
                submitted: list[str] = []
                retrieved: list[str] = []

                def __init__(self, **kwargs: object) -> None:
                    pass

                def submit_background_item(self, task_config: object, item: object) -> dict[str, object]:
                    self.submitted.append(item.item_id)
                    return {"response_id": f"resp_{item.item_id}", "status": "queued"}

                def retrieve_response(self, response_id: str) -> dict[str, object]:
                    self.retrieved.append(response_id)
                    return {
                        "response_id": response_id,
                        "status": "completed",
                        "raw_text": "OK",
                        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                    }

            with patch("yomi_corpus.llm.runner.OpenAIResponsesBackend", FakeBackend):
                summary = run_background_task(
                    "config/llm/yomi_triage.toml",
                    str(input_path),
                    str(output_path),
                    job_dir=str(job_dir),
                )

            self.assertEqual(summary.mode, "background")
            self.assertEqual(summary.status, "completed")
            self.assertEqual(summary.completed_items, 1)
            self.assertEqual(FakeBackend.submitted, ["u1"])
            self.assertEqual(FakeBackend.retrieved, ["resp_u1"])
            rows = [
                json.loads(line)
                for line in output_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(rows[0]["item_id"], "u1")
            self.assertEqual(rows[0]["parsed"], {"status": "OK"})
            self.assertTrue((job_dir / "responses.jsonl").exists())
            self.assertTrue((job_dir / "manifest.json").exists())

    def test_run_background_task_resumes_without_resubmitting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.jsonl"
            output_path = root / "results.jsonl"
            job_dir = root / "job"
            input_path.write_text(
                json.dumps(
                    {
                        "unit_id": "u1",
                        "text": "大学です。",
                        "rendered": "大学/ダイガク です/デス 。/。",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            job_dir.mkdir()
            (job_dir / "responses.jsonl").write_text(
                json.dumps(
                    {
                        "item_id": "u1",
                        "response_id": "resp_existing",
                        "status": "in_progress",
                        "metadata": {},
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            class FakeBackend:
                submitted: list[str] = []

                def __init__(self, **kwargs: object) -> None:
                    pass

                def submit_background_item(self, task_config: object, item: object) -> dict[str, object]:
                    self.submitted.append(item.item_id)
                    return {"response_id": f"resp_{item.item_id}", "status": "queued"}

                def retrieve_response(self, response_id: str) -> dict[str, object]:
                    return {
                        "response_id": response_id,
                        "status": "completed",
                        "raw_text": "Review",
                        "usage": None,
                    }

            with patch("yomi_corpus.llm.runner.OpenAIResponsesBackend", FakeBackend):
                summary = run_background_task(
                    "config/llm/yomi_triage.toml",
                    str(input_path),
                    str(output_path),
                    job_dir=str(job_dir),
                )

            self.assertEqual(FakeBackend.submitted, [])
            self.assertEqual(summary.status, "completed")
            rows = [
                json.loads(line)
                for line in output_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(rows[0]["parsed"], {"status": "Review"})

    def test_run_background_task_waits_and_polls_until_completed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.jsonl"
            output_path = root / "results.jsonl"
            job_dir = root / "job"
            input_path.write_text(
                json.dumps(
                    {
                        "unit_id": "u1",
                        "text": "大学です。",
                        "rendered": "大学/ダイガク です/デス 。/。",
                    },
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )

            class FakeBackend:
                polls = 0

                def __init__(self, **kwargs: object) -> None:
                    pass

                def submit_background_item(self, task_config: object, item: object) -> dict[str, object]:
                    return {"response_id": "resp_u1", "status": "queued"}

                def retrieve_response(self, response_id: str) -> dict[str, object]:
                    self.polls += 1
                    if self.polls == 1:
                        return {"response_id": response_id, "status": "in_progress"}
                    return {
                        "response_id": response_id,
                        "status": "completed",
                        "raw_text": "OK",
                        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
                    }

            with patch("yomi_corpus.llm.runner.OpenAIResponsesBackend", FakeBackend):
                summary = run_background_task(
                    "config/llm/yomi_triage.toml",
                    str(input_path),
                    str(output_path),
                    job_dir=str(job_dir),
                    poll_interval_seconds=0,
                )

            self.assertEqual(summary.status, "completed")
            self.assertEqual(summary.completed_items, 1)
            rows = [
                json.loads(line)
                for line in output_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(rows[0]["parsed"], {"status": "OK"})

    def test_result_loaders_ignore_truncated_tail_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "results.jsonl"
            path.write_text(
                json.dumps({"item_id": "u1", "parse_error": None})
                + "\n"
                + json.dumps({"item_id": "u2", "parse_error": "bad"})
                + "\n"
                + '{"item_id": "u3"',
                encoding="utf-8",
            )

            self.assertEqual(load_result_item_ids(path), {"u1", "u2"})
            self.assertEqual(load_result_item_count(path), 2)
            self.assertEqual(count_result_parse_errors(path), 1)

    def test_background_records_ignore_truncated_tail_and_write_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "responses.jsonl"
            path.write_text(
                json.dumps({"item_id": "u1", "response_id": "resp_1"})
                + "\n"
                + '{"item_id": "u2"',
                encoding="utf-8",
            )

            self.assertEqual(load_background_records(path), {"u1": {"item_id": "u1", "response_id": "resp_1"}})

            class Item:
                def __init__(self, item_id: str) -> None:
                    self.item_id = item_id

            write_background_records(
                path,
                {
                    "u1": {"item_id": "u1", "response_id": "resp_1"},
                    "u2": {"item_id": "u2", "response_id": "resp_2"},
                },
                [Item("u1"), Item("u2")],
            )

            self.assertFalse(list(root.glob(".*.tmp")))
            self.assertEqual(set(load_background_records(path)), {"u1", "u2"})

    def test_run_llm_task_batch_fetches_completed_results(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "input.jsonl"
            output_path = root / "results.jsonl"
            job_dir = root / "job"
            input_path.write_text(
                json.dumps({"unit_id": "u1", "text": "大学です。", "rendered": "大学/ダイガク"})
                + "\n",
                encoding="utf-8",
            )

            def fake_prepare(*args: object, **kwargs: object) -> None:
                job_dir.mkdir(parents=True)
                (job_dir / "status.json").write_text(
                    json.dumps({"state": "prepared"}),
                    encoding="utf-8",
                )

            def fake_submit(*args: object, **kwargs: object) -> dict[str, object]:
                return {"state": "completed", "remote_status": "completed", "batch_id": "batch_1"}

            def fake_fetch(*args: object, **kwargs: object) -> dict[str, object]:
                (job_dir / "results.parsed.jsonl").write_text(
                    json.dumps(
                        {
                            "item_id": "u1",
                            "raw_text": "OK",
                            "parsed": {"status": "OK"},
                            "parse_error": None,
                            "usage": {},
                            "metadata": {},
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )
                return {"state": "fetched", "remote_status": "completed", "batch_id": "batch_1"}

            with (
                patch("yomi_corpus.llm.runner.prepare_batch_job", side_effect=fake_prepare),
                patch("yomi_corpus.llm.runner.submit_batch_job", side_effect=fake_submit),
                patch("yomi_corpus.llm.runner.fetch_batch_job", side_effect=fake_fetch),
            ):
                summary = run_llm_task(
                    "config/llm/yomi_triage.toml",
                    str(input_path),
                    str(output_path),
                    execution_mode="batch",
                    job_dir=str(job_dir),
                )

            self.assertEqual(summary.status, "completed")
            self.assertEqual(summary.completed_items, 1)
            self.assertTrue(output_path.exists())


if __name__ == "__main__":
    unittest.main()
