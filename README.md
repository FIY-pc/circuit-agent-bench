# Circuit Agent Bench

[中文快速开始](docs/quick-start-zh.md)

Evaluate a real model and its agent harness by having them **build working digital
circuits**. Tasks range from a full adder through modules and processors. Results
separate functional correctness, visual readability and execution cost.

Tasks, scoring and experiment records are independent of the candidate harness.
To study models, keep the harness fixed and vary the model. To study a harness,
keep the model fixed and vary the implementation. State both in every result.

Harbor manages isolated trials and a separate verifier. Included adapters run
the stock Pi coding-agent CLI or Vibe Logisim; other agents use Harbor's existing agent
interface. The current task set delivers Logisim `.circ` files, and the independent
verifier uses a fixed native Logisim backend. That artifact/runtime requirement
does not require the candidate to use Vibe Logisim. Reference circuits and grading
code are absent from the agent image. See [integrations](docs/adapters.md).

## Prepare the environment

The verified host platform is Linux with Docker. Windows users can use a Linux
WSL2 environment with Docker integration; that route has not yet been exercised.
You need Python 3.12+, Node 24+, npm and Docker. Building the Vibe candidate or the
native verifier also needs the product's Rust toolchain and JDK 17+. A prepared
product checkout can be used directly, or a tag/commit can be fetched and prepared
in the cache without changing that checkout.

Install the pinned Harbor environment from this repository root:

```sh
uv venv --python 3.12
uv pip install --python .venv/bin/python -r requirements.lock
source .venv/bin/activate
```

## Choose a candidate and build the verifier

For a generic coding-agent baseline, build the pinned **Pi 1.5.0 CLI**. It uses
its own prompt, file/shell tools, retries and compaction; this candidate contains
no Vibe product or circuit-editing API. A pinned stock Logisim CLI is available
through its shell. See [Pi baseline](docs/pi-baseline.md).

```sh
python -m circuit_bench image --adapter pi --tag circuit-agent:pi \
  --manifest .cache/pi-image.json
```

For a Vibe Logisim candidate, use `--adapter vibe-logisim`. `--tree`
measures current tracked file contents, including staged/unstaged edits. Stage
new source files first. `--ref` fetches a tag or commit from the product repository.
Images are local; these commands do not push them.

```sh
python -m circuit_bench image --adapter vibe-logisim --tree /path/to/vibe-logisim \
  --tag circuit-agent:baseline --manifest .cache/candidate.json
```

Both candidates use an independently pinned verifier, which is kept offline
and receives the submitted artifact only after the agent finishes:

```sh
python -m circuit_bench image --adapter vibe-logisim --ref v26.11.2 --role verifier \
  --tag circuit-verifier:baseline --manifest .cache/verifier.json
```

Builds may take several minutes and use several GiB. On a memory-constrained
Linux workstation, prefix build/test commands with
`systemd-run --user --scope -p MemoryMax=8G timeout 900`.
The default trial concurrency is one and each container has a 6 GiB limit.

Set your own service credentials in your shell. They are passed in memory;
do not put them in an experiment file or commit an environment file.

```sh
export CIRCUIT_BENCH_BASE_URL='https://your-service.example/v1'
export CIRCUIT_BENCH_MODEL='your-exact-model-id'
read -rsp 'API key: ' CIRCUIT_BENCH_API_KEY; echo
export CIRCUIT_BENCH_API_KEY
```

Copy `experiments/pi-baseline.json` (Pi) or `experiments/development.json` (Vibe)
to a file under `.cache/`. Set the model's
**actual** `context_window`, reasoning effort, API format, tasks and image names.
The example's 200000 is illustrative, not an automatic limit. The benchmark adds
no arbitrary output-token cap and does not replace the product's context management.
Pi requires explicit `max_output` and `vision` for every API; it does not use
upstream's default model capacities. For Vibe with `anthropic-messages`, also set
`model.max_output` to the service's supported output capacity. No value is guessed.
The Messages base URL is
the SDK endpoint root (for example `https://your-service.example`); a trailing
`/v1` is also accepted. Chat/Responses normally use a base ending in `/v1`.

```sh
python -m circuit_bench experiment prepare .cache/my-experiment.json \
  --output runs/experiment-001
python -m circuit_bench experiment run runs/experiment-001
```

The experiment freezes task hashes, model configuration and image IDs. A changed
image cannot silently replace a prepared candidate. Re-running the command keeps
started attempts and continues only unstarted jobs; request another repeat to
retry a failed trial. All attempts remain visible. Raw runs are ignored by Git.

## Read the results

Each Harbor trial has `agent/episode/` with saved artifacts, requests, tool calls,
trajectory and model usage, plus `verifier/grade.json` with independent functional
results. Harbor exceptions and model failures remain part of the record.

- [Tasks and functional coverage](docs/tasks.md)
- [Evaluation protocol and research sources](docs/protocol.md)
- [Visual evidence and review](docs/visual-scoring.md)
- [All-attempt reports and paired comparisons](docs/reporting.md)
- [Task provenance and local calibration data](docs/data-policy.md)
- [Implementation and validation status](docs/validation.md)

Generate a compact HTML/JSON report, including failures and missing attempts:

```sh
python -m circuit_bench report runs/experiment-001 --out reports/experiment-001
```

For visual review, collect the saved circuit with the fixed verifier product:

```sh
python -m circuit_bench visual --tree /path/to/verifier-product \
  --artifact /path/to/agent/episode/result/design.circ --out reports/circuit-001
python -m circuit_bench review --evidence reports/circuit-001/evidence.json
```

Open the generated `index.html` or `review.html` in a browser. Export a completed
review as `review.json` beside its evidence packet before aggregating it. Collect
under the episode's `visual/` directory to have reports discover it automatically.

Use `python -m circuit_bench --help` for commands, or append `--help` to a command.
The visual/report commands can operate on saved artifacts without another model
call. Functional success alone does not mean a readable diagram. Unreviewed visual
quality and unreported token usage are shown as unknown, never as zero or success.

## Compare models or harnesses

For a harness comparison, add a Pi candidate
`{ "id": "pi", "adapter": "pi", "image": "circuit-agent:pi" }`, or another
`{ "id": "candidate", "adapter": "vibe-logisim", "image": "circuit-agent:candidate" }` to the
experiment and keep the model and verifier fixed. Describe the coherent change in
a short note: problem, mechanism, expected benefit and possible regression. The
scheduler interleaves candidates within each task/repeat. Start with a small
exploration set, then repeat uncertain or important cases. Report each task and
all attempts; there is no weighted score that lets cheap wrong circuits win.

For model comparisons, keep the candidate image, agent configuration, tasks,
verifier and trial budgets fixed. Prepare a separate experiment for each model
with its exact ID and supported capacities, then pass all experiment directories
to `report`. The tables separate models; automated matched-pair analysis currently
covers fixed-model harness comparisons only. A result measures model performance
under the stated harness, not a harness-free intrinsic ability.

## Contribute a task

A task contains `instruction.md`, public `task.json`, `environment/input/`, and
verifier-only `tests/oracle.json`. State the actual user-visible behavior and
interfaces, not a preferred tool sequence or a hidden reference topology.
Add an independent mathematical/state-machine oracle, a valid native reference,
and realistic wrong artifacts that the grader rejects. Record known coverage
limits. See [task documentation](docs/tasks.md) for generator and native test commands.

```sh
python -m unittest discover -s tests -v
```

Pure tests need no model or product process. Native functional tests are opt-in.
Infrastructure smoke tests and hand-built references validate the evaluator;
they are never counted as model capability results.

Code and synthetic task assets: [GPL-3.0](LICENSE). Product/runtime dependencies
are obtained separately. This repository contains no historical student corpus.
