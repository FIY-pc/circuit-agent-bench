# Evaluation protocol

A task is a practical circuit delivery request: instruction, starting project,
explicit interface, functional specification and time budget. A trial is one
fresh real-model attempt through a stated harness. A harness experiment fixes
the model configuration; a model experiment fixes the harness, task set, verifier
and execution budget. Model results describe capability under those conditions. A coherent tool or control-flow redesign is a valid experimental variable;
its rationale belongs in one short experiment note, not artificial micro-ablations.

## Boundaries

Harbor schedules and isolates trials, collects artifacts, and launches a separate
verifier. The selected harness owns its prompt, tools, compaction, retries and stopping.
The included Vibe Logisim adapter submits the original instruction, with no hints or intermediate
corrections. Writing XML, scripts or custom helper tools is allowed. The verifier
runs a separately selected, fixed product/runtime and judges saved artifacts.
Reference circuits and test programs are absent from the agent image. Current
tasks use Logisim files and a fixed native verifier backend; candidate agents
need only satisfy that artifact contract. They need not use the verifier product
as their editing environment.

Public task sources can become familiar to models. Filesystem separation prevents
accidental answer exposure, not deliberate internet retrieval or training
contamination. Claims are about this task set and configuration, not unseen-circuit
ability. Keep genuinely new confirmation tasks outside development feedback until
an experiment is frozen; once their failures guide improvements, classify them as
development tasks.

## Results

Report a vector, not a weighted total:

- Delivery and function: reloadability, exact public interfaces, independent
  combinational/state-machine semantics, and per-capability groups. Every mandatory
  group must pass. A few hundred easy checks cannot compensate for a broken reset.
- Visual readability: evidence and six anchored review dimensions. Geometry is
  diagnostic, not a substitute for tracing and understanding a circuit.
- Cost: actual input/output/cache/reasoning usage, requests, tools, failed tools,
  elapsed time, and all unsuccessful attempts. Cache and reasoning counts can be
  subsets; do not double-count them. Unreported usage stays unknown.

A functionally correct artifact at timeout is still functionally correct, but did
not complete within the task budget. Model/provider failures, infrastructure
errors and grader errors remain separate from wrong circuit behavior. An unknown,
oscillating or incomplete signal does not pass a functional assertion.

The metering proxy forwards request bodies unchanged and adds no arbitrary output limit,
retry policy or model remapping. Raw usage and reported model IDs are retained in
private run data. Protocol-required output capacity is explicitly configured from
the chosen model capability, never guessed by the benchmark. Provider aliases may drift; exact snapshots are preferable.
Monetary prices, if supplied by downstream analysis, need a dated price table.
Judge-model costs must be separate from the candidate's costs.

## Comparisons with limited resources

Start with single trials across a small development set. Repeat promising,
uncertain and difficult cases; do not require every exploratory edit to trigger
three expensive full-suite runs. Interleave candidates within each task/repeat.
Report success counts and denominators, all attempts, and task-level changes.
No best-of-k selection is presented as pass@1. Small samples support observations,
not precise rankings or claims of statistical significance.

Changing the grader, runtime or task requires regrading saved artifacts where
possible, or separating result versions. Human correction is a different track:
record intervention content, active time and required expertise. Expert rescue
cannot silently count as autonomous success. This first runner executes only the
unassisted track; assisted protocols must be recorded separately.

## Sources and selected mechanisms

- [Harbor tasks](https://docs.harborframework.com/tasks/overview) and
  [separate verifiers](https://docs.harborframework.com/tasks/separate-verifier):
  established environment lifecycle and artifact separation.
- [Harbor installed agents](https://docs.harborframework.com/agents/custom-agents):
  run the actual product, rather than rebuilding its loop in the benchmark.
- [SWE-bench evaluation](https://www.swebench.com/SWE-bench/guides/evaluation/):
  independently execute the delivered artifact.
- [Inspect scorers](https://inspect.aisi.org.uk/scorers.html): distinct scoring
  dimensions and offline reevaluation. Its inference bridge is unnecessary here.
- [Agent evaluation practice](https://www.anthropic.com/research/demystifying-evals-for-ai-agents):
  distinguish task, trial, trace and outcome; combine executable and human judges.
- [tau2-bench](https://github.com/sierra-research/tau2-bench): interaction is part
  of the evaluated environment; an automatic simulated user needs its own validation.

These sources motivate boundaries, not a claim that this small benchmark has
inherited their validation or statistical power.
