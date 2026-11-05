# Module contracts

The shared layer contains `identity`, `experiment`, `package`, `meter`, task
oracles, reports and human review. `adapters/vibe_logisim/` contains source
selection/building, product HTTP/SSE, provider setup, turn execution and native
transcript retention. `grading` and `visual` currently use that adapter's native
host as their fixed Logisim backend; they never invoke its agent. A second
simulator is not implemented. See [adapter contracts](adapters.md).

`circuit_bench.adapters.vibe_logisim.host.Tree.resolve(tree, ref)` selects the product. `Host(Tree)` provides call(op, **args), agent(method, **args), events().
`circuit_bench.grading.Grader(Tree).grade(task_directory, artifact_path)` returns an independent functional report. `grade(...)` is the one-shot equivalent. Native reference circuits are verifier regressions, never visible to the agent.
Task directories: instruction.md (entire user request), task.json (id/artifact/circuit/timeout_minutes), environment/input/ (starting project), tests/oracle.json (independent specification), tests/reference.circ (regression answer).
`circuit_bench.adapters.vibe_logisim.episode.execute` invokes the actual product agent unchanged. Formal runs expose only instruction and input; evaluator code/references remain outside the agent filesystem.
Results: manifest.json, run.json (outcome/elapsedMs/ledger/meter), requests.jsonl, events.jsonl, result/ containing saved project. Functional grade.json and visual evidence are generated independently afterwards.

Episode retention: the live local project is `result/`. Harbor sets
`CIRCUIT_BENCH_PROJECT_DIR=/logs/artifacts/project`, which is collected even if the
episode is killed; normal cleanup also copies it into the episode's `result/`.
`run.json` starts with `outcome=running` and `finalized=false`. SIGINT/SIGTERM
finalize it as interrupted. Harbor requests termination and recovery before
collection; a killed process is recorded as interrupted with `recovered=true`,
and missing tool accounting stays unknown. The request/event journals remain
available even when the process cannot produce final usage totals.

`trajectory/conversations.json` retains the native transcript, full tool inputs
and outputs, compaction records and inline images. `trajectory/media.json` maps
decoded images to content-addressed files under `trajectory/media/`.
Snapshots follow the product's own persisted conversation saves every 250 ms,
with a final export before private state removal. `trajectory/status.json`
records the last retained snapshot or an explicit export failure. A hard kill
can only preserve the last product save, not messages still held in memory.
The product keeps the raw transcript when compacting context: checkpoints
project a shorter provider request, while native saves retain the original
messages. The latest snapshot therefore includes observations preceding
compaction; no duplicate snapshot archive is needed for compaction.
Export reads only `conversations.json`, strips account/service selection fields,
and never follows arbitrary file references or copies profile/auth/session
directories. Native transcript export failure leaves the private temporary state
in place and is recorded in `run.json.retentionErrors`.

Cancellation and trajectory regressions make no model calls:
`python -m unittest discover -s tests -p 'test_episode_retention.py' -v`.
New tasks are authored here from mathematical specifications. No private course corpus, old repo history, user logs, credentials, or personal paths belong in this repository.
