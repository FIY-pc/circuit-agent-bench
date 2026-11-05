# Harness integrations

Circuit Agent Bench defines tasks, artifact contracts, functional oracles, visual
review and experiment records. The candidate supplies its own agent loop, tools,
context management and completion policy. Harbor provides the agent interface;
there is no second plugin framework or benchmark-specific model loop.

## Included integrations

For a stock generic coding agent, select Pi:

```json
{"id": "pi", "adapter": "pi", "image": "circuit-agent:pi"}
```

Build with `python -m circuit_bench image --adapter pi ...`. It installs an
independently locked upstream CLI, plus a standard Logisim command-line runtime.
It uses no Vibe application or circuit-editing API.
See [Pi setup, model capacities and evidence](pi-baseline.md).

Select Vibe Logisim explicitly:

```json
{"id": "baseline", "adapter": "vibe-logisim", "image": "circuit-agent:baseline"}
```

Build its image with `python -m circuit_bench image --adapter vibe-logisim ...`.
Its implementation lives in `circuit_bench/adapters/vibe_logisim/`. Public
configuration uses `CIRCUIT_BENCH_BASE_URL`, `CIRCUIT_BENCH_API_KEY` and
`CIRCUIT_BENCH_MODEL`. Optional local runtime paths use `CIRCUIT_BENCH_NODE`,
`CIRCUIT_BENCH_HOST`, `CIRCUIT_BENCH_TREE` and `CIRCUIT_BENCH_REF`.
`CIRCUIT_BENCH_REPO`, `CIRCUIT_BENCH_CACHE` and `CIRCUIT_BENCH_RUNTIME_CACHE`
control source/cache selection for this integration. Product-specific environment
names and conversation schemas are translated inside the adapter; they are not
benchmark configuration. Rebuild prepared images when updating adapter code.
The initial `VIBE_BENCH_*` names have been replaced by `CIRCUIT_BENCH_*`;
update shell configuration and prepare new experiments after rebuilding images.
Old saved results remain readable. Previously frozen jobs belong to their original
benchmark revision; do not edit their configs in place.

## Another agent

Supply an existing Harbor agent instead of `adapter`:

```json
{
  "id": "other-harness",
  "image": "my-circuit-agent:fixed",
  "agent": {"import_path": "my_agent:MyAgent", "kwargs": {}},
  "required_env": ["MY_AGENT_API_KEY"]
}
```

A Harbor built-in `name` may replace `import_path`; its integration must satisfy
the same input/output contract. Install custom modules into the Python environment
that launches Harbor, or add their directory to `PYTHONPATH`. The scheduler uses
Harbor's `AgentConfig` without replacing the agent's model loop. `model_name`
comes from the experiment, while `kwargs` pass through unchanged. Translate model
settings into that agent's own options explicitly and keep them fixed when
comparing models. An external agent reads its credentials from its documented
environment; the scheduler checks `required_env` names without storing values.
It does not impose the included adapter's endpoint or key variables.

Each isolated task supplies:

- `/workspace/input/`: the fresh starting project.
- `/workspace/task.json`: public metadata, including artifact basename and budget.
- Harbor's `instruction`: the unmodified task request.
- `/logs/artifacts/project/`: the submitted project directory collected for grading.

The integration initializes the project from the input, sets the agent's working
directory or equivalent artifact destination, and retains all required companion
files. The current artifact is `design.circ`. Supply a compatible candidate image
without grader code, reference circuits or hidden test programs. The verifier
image is selected independently; another agent need not include the Vibe product.

The existing separate verifier reopens the collected artifact. It grades behavior
and interfaces, not tool names, call order or a preferred reference topology.
The format is currently Logisim `.circ`; accepting Verilog or another circuit
format would require a separately versioned task and verifier contract.

## Evidence contract

Harbor retains each attempt and verifier outcome even when an integration does
not emit benchmark episode metadata. Such missing usage and trajectory fields
remain unknown, and the attempt cannot enter a fully matched comparison.

For full reports, write `manifest.json` and `run.json` under the agent log directory's
`episode/` (in installed agents, `/logs/agent/episode/`). The manifest records:
`schema=circuit-agent-bench.trial/v1`, `task`, `execution=real-model`, `isolation`,
`model` (requestedId, reportedIds, effort, api, contextWindow), `harness` (actual
sourceSha256 and binarySha256), inputSha256, instructionSha256,
taskContractSha256, and harnessUnchanged. The hashes describe observed artifacts;
unknown identities must remain absent. Other agent implementations may report
those fields without importing the Vibe adapter.

`run.json` records outcome, elapsedMs, finalized, meter (input, output,
cachedInput, reasoningOutput, requests and coverage) and ledger (toolCalls and
failedCalls). Optional `tool-calls.jsonl` records individual tool identifiers,
status and duration. Preserve native traces/media using the agent's own export;
no Vibe conversation schema is required. Keep failed attempts and partial costs.
A scripted fixture is `execution=synthetic`, never a model capability result.

## Choosing what to compare

For harness research, fix the model and compare complete integrations or revisions.
For model research, fix the integration, its options and image, task set, verifier
and execution budget; prepare one experiment per model and aggregate them together.
The current report displays both dimensions, but automatic matched-pair analysis
only covers fixed-model harness changes. Pi and Vibe are available integrations;
repeated multi-model comparisons still require actual runs before capability claims.

The adapter boundary follows [Harbor custom agents](https://docs.harborframework.com/agents/custom-agents)
and [separate verifiers](https://docs.harborframework.com/tasks/separate-verifier).
