"""The stock Pi CLI, with its own tools, prompts, retries and compaction."""
REQUIRED_ENV = ('CIRCUIT_BENCH_BASE_URL', 'CIRCUIT_BENCH_API_KEY')


def configuration(model):
    for field in ('context_window', 'max_output'):
        if type(model.get(field)) is not int or model[field] <= 0:
            raise ValueError(f'Pi requires the actual model-supported {field}; no default capacity')
    if model['max_output'] > model['context_window']:
        raise ValueError('max_output cannot exceed context_window')
    effort = model['effort']
    if effort == 'none':
        effort = 'off'
    if effort not in ('off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'):
        raise ValueError('Unsupported Pi thinking level')
    api = model.get('api', 'openai-completions')
    if api not in ('openai-completions', 'openai-responses', 'anthropic-messages'):
        raise ValueError('Unsupported API format')
    if type(model.get('vision')) is not bool:
        raise ValueError('Pi requires model.vision to state whether image input is supported')
    # These are upstream model capability declarations, not benchmark tuning.
    for key in ('compat', 'thinking_level_map'):
        if key in model and not isinstance(model[key], dict):
            raise ValueError(key + ' must be a model capability object')
    if effort in ('xhigh', 'max') and model.get('thinking_level_map', {}).get(effort) is None:
        raise ValueError('Specify thinking_level_map for this level; Pi otherwise clamps it to high')
    return {**model, 'api': api}


def agent_config(model):
    return {'import_path': __name__ + '.harbor_agent:Pi',
            'kwargs': {'configuration': configuration(model)}}
