import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('replay', Path(__file__).resolve().parents[1] / 'scripts/replay_speculative_requests.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_identity_includes_model_and_prompt():
    a = {'model': 'one', 'input': 'text'}
    assert module.fingerprint(a) == module.fingerprint({'input': 'text', 'model': 'one'})
    assert module.fingerprint(a) != module.fingerprint({**a, 'model': 'two'})
    assert module.fingerprint(a) != module.fingerprint({**a, 'input': 'other'})


def test_unique_cost_and_occurrence_reuse():
    def row(key):
        return {'key': key, 'input_tokens_estimate': 100, 'output_tokens_estimate': 10}
    result = module.comparison([row('reuse'), row('reuse'), row('waste')],
                               [row('reuse'), row('reuse'), row('miss')])
    assert result['speculative_unique'] == 2
    assert result['reused_occurrences'] == 2
    assert result['wasted'] == result['missing'] == 1
    assert result['cost_ratio_batch_half_output_weight_4'] == 1
