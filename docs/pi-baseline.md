# Stock coding-agent baseline

The `pi` adapter runs the published **Pi 1.5.0 CLI** with its default `read`,
`bash`, `edit` and `write` tools. Pi owns the system prompt, agent loop, retries,
tool execution and context compaction. It receives the task instruction verbatim
in a fresh project copied from the task input. There are no circuit tools, custom
extensions, solution hints or benchmark-imposed turn/output-token caps. The task
environment includes a pinned stock Logisim CLI, callable through `bash`.

The candidate contains Python 3 (as `python` and `python3`), Node, Git, curl,
ripgrep, `file`, JDK 21 and Logisim ITA 2.16.2.2. It does not contain
the Vibe application, circuit-editing APIs, the grader or reference solutions. It can use
its shell and network to obtain other tools; downloads and scripts remain part of
the attempt, and external network state is a reproducibility limit. The provided
runtime avoids requiring every model to obtain its own simulator before working.
This baseline measures construction with a generic coding agent and a standard
domain runtime. It does not
claim to isolate model ability from all tooling.

## Run

Install the pinned Harbor environment as described in the [README](../README.md).
Building the Pi candidate needs Linux, Docker, Python 3.12+, Node 24+ and npm;
it does not require a product checkout or host Rust/Java toolchains; Java is
installed inside the candidate image.

```sh
python -m circuit_bench image --adapter pi --tag circuit-agent:pi \
  --manifest .cache/pi-image.json
```

Pi and its transitive packages are installed with `npm ci` from a committed lock,
including integrity hashes. The image records the installed file hashes, Node
hash, CLI version and lock hash. Runtime files are read-only to the agent.
Updating Pi requires updating that lock and building a new candidate image.
The builder downloads the pinned upstream Logisim release and verifies its SHA256.
Pass `--logisim-jar /path/to/Logisim-ITA.jar` to reuse a checksum-matched local copy.
The candidate exposes `logisim` on PATH, equivalent to headless `java -jar`, and
the jar at `/opt/logisim/Logisim-ITA.jar`. `logisim -help` describes its CLI;
upstream returns exit code 255 for `-help` and `-version`. The exact runtime
version and hash are recorded alongside Pi. No task-specific driver, test program
or evaluation oracle is installed in the candidate.

Use the same separately pinned verifier image as other candidates:

```sh
python -m circuit_bench image --adapter vibe-logisim --ref v26.11.2 --role verifier \
  --tag circuit-verifier:baseline --manifest .cache/verifier.json
```

That build needs the verifier product's Rust/Java prerequisites. The verifier
reopens the submitted `.circ` in an offline container; it does not help Pi during
construction. The current grading backend remains Logisim-specific.

Set `CIRCUIT_BENCH_BASE_URL`, `CIRCUIT_BENCH_API_KEY` and `CIRCUIT_BENCH_MODEL`
in your shell. Copy `experiments/pi-baseline.json` to `.cache/my-pi.json` and fill
the model configuration before preparing an experiment:

- `context_window` and `max_output`: actual supported positive capacities.
  Null values intentionally fail validation. Pi's custom-model defaults are not used.
- `vision`: whether the chosen model accepts images through its read tool.
- `api`: `openai-completions`, `openai-responses` or `anthropic-messages`.
- `effort`: `off` (also accepts `none`), `minimal`, `low`, `medium`, `high`,
  `xhigh` or `max`, according to the provider's capabilities. For the last two,
  supply Pi's explicit `thinking_level_map` so it cannot silently clamp to high.
- Optional `compat` and `thinking_level_map`: upstream Pi model capability fields,
  passed through unchanged. Record provider-specific settings with the experiment.

```sh
python -m circuit_bench experiment prepare .cache/my-pi.json --output runs/pi-001
python -m circuit_bench experiment run runs/pi-001
python -m circuit_bench report runs/pi-001 --out reports/pi-001
```

Native Pi may reduce a response budget to the remaining context space, compact,
or retry a provider error. Those are the pinned harness's behaviors, not a second
benchmark loop. The benchmark enforces the task's wall-clock budget and retains
the partial result when it expires. Changing harness behavior requires a distinct
candidate, not an unrecorded patch during a trial.

## Compare

For model comparison, keep this candidate image, verifier, task set, Pi options
and time budgets fixed. Prepare a separate experiment for each exact model ID
with its real capacities. Pass all experiment directories to `report`; it displays
the model groups separately. Repeat trials before claiming a stable ranking.

For harness comparison, add a Vibe candidate to the same experiment:

```json
{"id": "vibe", "adapter": "vibe-logisim", "image": "circuit-agent:vibe"}
```

The task, model and grader remain the same. Differences in the tools, system
prompt and control flow are the treatment being measured. Describe them together;
tool-call counts alone are not evidence that one harness is better.

## Evidence and isolation

`agent/episode/` contains `events.jsonl`, `stderr.log`, native JSONL sessions under
`trajectory/`, a normalized tool ledger, request usage, the manifest and the final
project copy. Native sessions retain thinking, tool results, embedded images and
compaction entries in Pi's own format. The live project also resides under
`/logs/artifacts/project` so Harbor can collect it after cancellation.

The transparent meter forwards inference without changing the request. The real
service key stays in the supervisor process; Pi receives a loopback endpoint and
placeholder key. Each trial uses a fresh HOME and Pi config, no discovered local
skills/context files/MCP, and no automatic catalog update or telemetry. This is
reproducible process isolation, not a hostile-agent security boundary: agent and
supervisor run under the same container user.

All reported requests include native retries and compaction. Missing final usage
stays unknown. `completed` means the CLI stopped normally; only the separate
verifier determines functional correctness. Visual quality needs its own review.

The adapter subclasses Harbor's installed-agent interface instead of using its
built-in Pi installer because this benchmark freezes the npm lock/image before
trials, requires actual model capacities, meters all requests, and finalizes live
artifacts on cancellation. It launches the original CLI without importing or
reimplementing its agent loop.

Upstream references: [Pi CLI](https://pi.dev/docs/latest/cli),
[model configuration](https://pi.dev/docs/latest/models-configuration),
[JSON events](https://pi.dev/docs/latest/json),
[session format](https://pi.dev/docs/latest/session-format),
[Harbor agents](https://docs.harborframework.com/agents/pre-integrated-agents).
