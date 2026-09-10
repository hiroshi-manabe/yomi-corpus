#!/usr/bin/env python3
"""Offline request-reuse experiment. Never constructs an API client."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import replace, asdict
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import tiktoken
from yomi_corpus.llm.backend import build_response_create_kwargs
from yomi_corpus.llm.config import apply_llm_profile, load_llm_task_config
from yomi_corpus.llm.tasks import build_prompt_items
from yomi_corpus.yomi.config import load_yomi_generation_config
from yomi_corpus.yomi.export import export_jsonl_yomi
from yomi_corpus.yomi.acceptance import apply_yomi_auto_acceptance_file
from yomi_corpus.yomi.safety import apply_yomi_safety_pre_llm_file
from yomi_corpus.yomi.llm_readings import build_yomi_llm_reading_queue_file


def read(path):
    return json.loads(Path(path).read_text())


def rows(path):
    with Path(path).open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def write_rows(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in data))


def fingerprint(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def comparison(old, actual):
    speculative = {r['key']: r for r in old}
    needed = {r['key']: r for r in actual}
    reusable = speculative.keys() & needed.keys()
    wasted = speculative.keys() - needed.keys()
    missing = needed.keys() - speculative.keys()
    out = dict(speculative_unique=len(speculative), actual_unique=len(needed),
               reusable=len(reusable), wasted=len(wasted), missing=len(missing),
               speculative_occurrences=len(old), actual_occurrences=len(actual),
               reused_occurrences=sum(r['key'] in reusable for r in actual))
    out['foreground_request_fraction_avoided'] = out['reused_occurrences'] / len(actual) if actual else 0
    for label, source, keys in [('speculative', speculative, speculative.keys()), ('actual', needed, needed.keys()),
                                ('wasted', speculative, wasted), ('missing', needed, missing)]:
        out[label + '_input_tokens_estimate'] = sum(source[k]['input_tokens_estimate'] for k in keys)
        out[label + '_output_tokens_estimate'] = sum(source[k]['output_tokens_estimate'] for k in keys)
    # Explicit sensitivity scenarios, not claims about current API prices.
    for weight in (1, 4, 8):
        cost = lambda label: out[label + '_input_tokens_estimate'] + weight * out[label + '_output_tokens_estimate']
        out[f'cost_ratio_batch_half_output_weight_{weight}'] = (0.5 * cost('speculative') + cost('missing')) / cost('actual') if cost('actual') else None
    out['examples'] = {label: [source[k] for k in sorted(keys)[:8]] for label, source, keys in
                       [('reusable', needed, reusable), ('wasted', speculative, wasted), ('missing', needed, missing)]}
    return out


def summarize(plans, requests, windows):
    summary = []
    for start in windows:
        for lag in (50, 100, 200):
            sides = [[p for p in plans if p['window'] == start and p['lag'] == v] for v in (lag, 0)]
            if all(p['job'] in requests for side in sides for p in side):
                old, actual = [[r for p in side for r in requests[p['job']]] for side in sides]
                summary.append(dict(window=start, lag=lag, **comparison(old, actual)))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--windows', default='1891,2071,2171')
    parser.add_argument('--window-documents', type=int, default=100)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    run.mkdir(parents=True, exist_ok=True)
    manifests = [read(p) for p in sorted((ROOT/'data/units').glob('dev_batch_*/manifest.json'))]
    manifests = [m for m in manifests if m.get('processing_slot_start') and m.get('decoder_model_dir')]
    by_slot = {slot: m for m in manifests for slot in range(m['processing_slot_start'], m['processing_slot_end'] + 1)}
    windows = [int(x) for x in args.windows.split(',')]
    task = apply_llm_profile(load_llm_task_config('config/llm/yomi_reading.toml'), 'standard')
    config = replace(load_yomi_generation_config('config/yomi/default.toml'), learned_exact_rewrites=None)
    encoding = tiktoken.get_encoding('o200k_base')
    plans = []
    models = {}
    jobs = {}
    output_samples = defaultdict(list)
    for start in windows:
        targets = {by_slot[s]['batch_name']: by_slot[s] for s in range(start, start + args.window_documents)}
        if min(m['processing_slot_start'] for m in targets.values()) != start or max(m['processing_slot_end'] for m in targets.values()) != start + args.window_documents - 1:
            raise ValueError('Windows must align with complete batches')
        for target in targets.values():
            name = target['batch_name']
            result_path = ROOT/'data/units'/name/'yomi_reading_results.jsonl'
            if result_path.exists():
                for result in rows(result_path):
                    if result.get('usage', {}).get('output_tokens') and not result.get('parse_error'):
                        output_samples[result.get('metadata', {}).get('surface', '')].append(result['usage']['output_tokens'])
            actual = Path(target['decoder_model_dir'])
            for lag in (0, 50, 100, 200):
                model = actual if lag == 0 else Path(by_slot[target['processing_slot_start'] - lag]['decoder_model_dir'])
                refresh = read(model/'yomi_corpus_refresh.json')
                if name in refresh['finalized_batches']:
                    raise ValueError(f'Training leakage: {name} in {model}')
                if model.name > actual.name:
                    raise ValueError('Speculative model is newer than actual model')
                if str(model) not in models:
                    required = ['manifest.json', 'yomi_corpus_refresh.json', 'model.klm', 'lexicon.jsonl', 'ngram_corpus.txt',
                                'surface_reading_stats.tsv', 'stable_surface_readings.tsv']
                    models[str(model)] = {f: {'bytes': (model/f).stat().st_size,
                                              'sha256': hashlib.sha256((model/f).read_bytes()).hexdigest()} for f in required}
                key = name + '_' + model.name
                jobs[key] = (target, model)
                plans.append(dict(window=start, batch=name, lag=lag, job=key, model=str(model)))
    audit = dict(llm_requests_sent=0, windows=windows, documents_per_window=args.window_documents,
                 task=asdict(task), generation_config=asdict(config), models=models, plans=plans,
                 limitations=['Current code, Sudachi dictionary and prompts held fixed.',
                              'Mutable learned exact rewrites disabled in both paths.',
                              'Model selection uses the model recorded at preparation of the batch N slots earlier.',
                              'Recorded model snapshots reused, not rebuilt from mutable corpus paths.',
                              'No Batch turnaround-time or correctness claim; successful cached responses assumed.',
                              'Token estimates use o200k_base prompt tokens; output medians by surface, then global fallback.',
                              'Cost ratios assume half-price Batch and show output/input price-weight sensitivity.'])
    write(run/'audit.json', audit)
    fallback = statistics.median([n for values in output_samples.values() for n in values])
    requests = {}
    for index, (key, (target, model)) in enumerate(jobs.items(), 1):
        directory = run/'jobs'/key
        request_file = directory/'requests.jsonl'
        print(f'{index}/{len(jobs)} {key}', flush=True)
        if request_file.exists():
            requests[key] = rows(request_file)
            continue
        directory.mkdir(parents=True, exist_ok=True)
        source = rows(ROOT/'data/units'/target['batch_name']/'units.jsonl')
        for row in source:
            row['analysis'] = {'mechanical': {}, 'llm': {}, 'human_review': {}}
        write_rows(directory/'input.jsonl', source)
        started = time.monotonic()
        export_jsonl_yomi(input_jsonl=directory/'input.jsonl', output_jsonl=directory/'mechanical.jsonl',
                         config=replace(config, decoder_model_dir=str(model)), strategy_name='aligned_hybrid_v1')
        apply_yomi_auto_acceptance_file(input_jsonl=directory/'mechanical.jsonl', output_jsonl=directory/'accepted.jsonl',
                                       summary_json=directory/'accepted-summary.json', auto_accept_profile='stable_two_kanji', decoder_model_dir=model)
        apply_yomi_safety_pre_llm_file(input_jsonl=directory/'accepted.jsonl', output_jsonl=directory/'safety.jsonl',
                                      summary_json=directory/'safety-summary.json', decoder_model_dir=model)
        build_yomi_llm_reading_queue_file(input_jsonl=directory/'safety.jsonl', output_jsonl=directory/'queue.jsonl',
                                         summary_json=directory/'queue-summary.json', skip_stable_two_kanji=False)
        queue = rows(directory/'queue.jsonl')
        result = []
        for row, prompt in zip(queue, build_prompt_items(task, queue), strict=True):
            body = build_response_create_kwargs(task, prompt.prompt)
            surface = row['surface']
            result.append(dict(key=fingerprint(body), body=body, surface=surface, unit_id=row['unit_id'],
                               text=row['text'], input_tokens_estimate=len(encoding.encode(prompt.prompt)),
                               output_tokens_estimate=statistics.median(output_samples.get(surface, [fallback]))))
        write_rows(request_file, result)
        write(directory/'timing.json', {'seconds': time.monotonic()-started})
        requests[key] = result
        write(run/'summary.json', summarize(plans, requests, windows))
    write(run/'summary.json', summarize(plans, requests, windows))
    print('Completed offline replay', flush=True)


if __name__ == '__main__':
    main()
