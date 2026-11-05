# Module contracts

`circuit_bench.host.Tree.resolve(tree, ref)` selects the product. `Host(Tree)` provides call(op, **args), agent(method, **args), events().
`circuit_bench.grading.Grader(Tree).grade(task_directory, artifact_path)` returns an independent functional report. `grade(...)` is the one-shot equivalent. Native reference circuits are verifier regressions, never visible to the agent.
Task directories: instruction.md (entire user request), task.json (id/artifact/circuit/timeout_minutes), environment/input/ (starting project), tests/oracle.json (independent specification), tests/reference.circ (regression answer).
`circuit_bench.episode.execute` invokes the actual product agent unchanged. Formal runs expose only instruction and input; evaluator code/references remain outside the agent filesystem.
Results: manifest.json, run.json (outcome/elapsedMs/ledger/meter), requests.jsonl, events.jsonl, result/ containing saved project. Functional grade.json and visual evidence are generated independently afterwards.
New tasks are authored here from mathematical specifications. No private course corpus, old repo history, user logs, credentials, or personal paths belong in this repository.
