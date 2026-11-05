"""Built-in integrations; external runners use Harbor's own agent interface."""
import importlib

BUILTINS = {'pi': 'circuit_bench.adapters.pi', 'vibe-logisim': 'circuit_bench.adapters.vibe_logisim'}


def integration(name):
    try:
        return importlib.import_module(BUILTINS[name])
    except KeyError:
        raise ValueError(f'Unknown adapter {name!r}; choose {", ".join(BUILTINS)} or supply a Harbor agent') from None


def agent_config(candidate, model):
    """Select an integration explicitly, or pass a Harbor agent through unchanged."""
    adapter, agent = candidate.get('adapter'), candidate.get('agent')
    if bool(adapter) == bool(agent):
        raise ValueError('Each candidate needs exactly one adapter or Harbor agent')
    if adapter:
        agent = integration(adapter).agent_config(model)
    else:
        if not isinstance(agent, dict) or bool(agent.get('name')) == bool(agent.get('import_path')):
            raise ValueError('Harbor agent needs exactly one name or import_path')
        if set(agent) - {'name', 'import_path', 'kwargs'}:
            raise ValueError('Use the shared model configuration and environment credentials, not agent overrides')
    return {**agent, 'model_name': model['id'], 'skip_model_parsing': True}


def required_environment(candidate):
    if candidate.get('adapter'):
        return integration(candidate['adapter']).REQUIRED_ENV
    return candidate.get('required_env', [])
