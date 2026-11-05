"""Vibe Logisim integration and native Logisim verifier backend."""
REQUIRED_ENV = ('CIRCUIT_BENCH_BASE_URL', 'CIRCUIT_BENCH_API_KEY')


def agent_config(model):
    if model.get('api') == 'anthropic-messages' and model.get('max_output') is None:
        raise ValueError('Messages requires the actual model-supported max_output capacity')
    return {'import_path': __name__ + '.harbor_agent:VibeLogisim',
            'kwargs': {'context_window': int(model['context_window']),
                       'effort': model['effort'], 'api': model.get('api', 'openai-completions'),
                       **({'max_output': model['max_output']} if model.get('max_output') is not None else {})}}
