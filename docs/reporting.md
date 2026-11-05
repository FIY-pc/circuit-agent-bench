# Reports across all attempts

```bash
python -m circuit_bench.report runs/experiment-01 --out reports/experiment-01 --tasks tasks
# Multiple experiments, standalone episodes, or explicitly empty attempts:
python -m circuit_bench.report --runs runs/experiment-01 runs/experiment-02 runs/incomplete \
  --out reports/comparison --tasks tasks
```

Open `reports/experiment-01/index.html`. `report.json` contains the same counts plus all per-metric paired deltas. The generated directory is the shareable summary; raw trials, image packets and human-review prose remain separate. The module uses only the standard library and never invokes the model or renderer.

Python entry points: `report.main(argv=None)`, `discover_attempts(roots)`, `aggregate(roots, tasks=None)`, and `write_report(roots, out, tasks=None)`. The optional `tasks` mapping is `{task_id: task_metadata}` and supplies `family`; absent family stays unknown. The parent CLI may dispatch `python -m circuit_bench report` to `main()`.

## Input layout and denominators

An experiment root contains `experiment.json`, the frozen model/task/image/candidate manifest, and `jobs` entries with `name`, `task`, `candidate`, `repeat`, and `config`. The report uses **every scheduled job**, including jobs whose directory was never created. It links each row to the experiment alias, job, candidate, repeat and trial.

```text
experiment.json
launches.jsonl                           # optional launcher exit codes
packages/<job>/task.toml                 # resource/time conditions for pairing
jobs/<job>/<trial>/
  result.json                           # Harbor status / exception_info
  exception.txt                         # optional Harbor exception
  agent/episode/
    manifest.json                       # exact model / harness identities (private)
    run.json                            # outcome, elapsedMs, ledger, meter
    tool-calls.jsonl                     # only tool names/counts are summarized
    result/                             # saved project
    visual/evidence.json                # separately collected
    visual/review.json                   # human export
  verifier/grade.json                    # independent functional grade
```

`verifier/visual/evidence.json` is also recognized. Standalone episodes use `grade.json` and `visual/` beside `run.json`. Recursive discovery also finds orphan episodes and standalone Harbor trials with verifier or exception evidence. Additional unplanned job directories are retained and labeled. Multiple trial/retry directories each become rows; the report never chooses the best one. Duplicate input paths are deduplicated, not duplicate attempts.

A missing trial becomes `missing_trial`; launcher failure and Harbor exceptions become explicit infrastructure states. An absent/unreadable episode manifest, run, grade, tool ledger, or review is recorded as a gap. No full exception text enters the summary. Entirely unrecorded attempts outside the supplied roots cannot be recovered; a supplied empty directory becomes one unknown row. An empty experiment manifest is also explicit.

Raw `byTask` / `byFamily` groups retain each candidate/model/harness and execution condition and count passed, failed, invalid artifact, evaluator error, and unknown separately. The displayed fraction is **observed functional passes / all supplied or scheduled attempts**, including missing trials. This does not assert that unknown grades failed their functional tests. A prepared real-model experiment groups missing infrastructure rows into its planned-real-model condition, while each row still shows actual execution as unknown until recorded. Synthetic/scripted and unclassified infrastructure executions stay in raw tables and cost totals; they never enter capability groups. A completed agent turn is not a functional pass, and Harbor reward is not substituted for `grade.json`.

## Usage, visual coverage and privacy

Construction usage reports raw input/output, cached input/reasoning output subcategories, request/usage coverage, HTTP errors, streaming transport errors, tool calls/failures, and elapsed time. Each aggregate includes the sum of known values and the count of unknown attempts. Missing cost is never silently zero; cached and reasoning tokens are not added again to their parent categories. Monetary conversion requires a separate explicit price schedule and is not guessed. Rendering time and human review minutes are separate from construction. `observedTokens` is a separate lower bound of actually observed usage, never a replacement for unknown totals. `requestsWithoutUsage` counts absent records; `requestsWithPartialUsage` separates streams with only provisional counters from `requestsWithCompleteUsage`. An interrupted stream keeps full totals unknown even if it already reported input or provisional output usage. For older runs without `observedTokens`, the reporter normalizes individual `requests.jsonl` records through the meter and labels the lower-bound source as `requests`; incomplete full totals remain unknown.

Visual rows retain the six states, collection status, inspected/captured/omitted counts, and optional reading-probe outcomes. Missing reviews remain unknown. Invalid image hashes, mismatched evidence/artifact identities, or malformed judgments cannot become visual success. A report does not convert a human state into an objective aesthetic grade.

The summary uses an allowlist of numbers, statuses, benchmark task/family/candidate IDs and hashed identity aliases. It excludes full prompts, response text, commands, provider/model IDs, local paths, exception messages, reviewer prose, and images. Exact IDs remain in the original private manifests; hashes are pseudonymous identifiers, not encryption. Benchmark identifiers should themselves be neutral public-safe labels. Do not publish raw experiment or episode directories just because the summary is shareable.

## Infrastructure assessments

An `assessment.json` at an experiment root or enclosing supplied results directory applies to every enclosed trial, including scheduled jobs with no trial. For example:

```json
{
  "status": "infrastructure_error",
  "exclude_from_capability_comparison": true,
  "retain_usage": true
}
```

The reporter preserves raw outcome, functional status, every usage observation and unknown total. It annotates each affected attempt with assessment identity/status, `excludedFromCapabilityComparison`, and `assessment_infrastructure_error`. `capabilityByTask`, `capabilityByFamily`, and paired comparisons exclude these attempts; raw `byTask`, `byFamily`, every-attempt rows and cost totals retain them. The HTML uses the capability groups and explicitly states the excluded count. Direct episode inputs still inherit an enclosing experiment assessment. Nested exclusions are cumulative; a child cannot silently re-enable an excluded parent.

Assessment cause/intervention prose stays in the private file. The public summary keeps only status, exclusion and a hashed assessment ID. Usage is always retained, even if an assessment would request hiding it. This separates operator-stopped infrastructure failures from claims about model capability without erasing their cost.

## Fixed-model paired harness comparisons

For prepared experiments, the pairing key is the experiment, task and planned repeat. Candidate image IDs identify harness conditions. The frozen task-package digest fixes instructions/input/contract; packaged `task.toml` fixes resource/time limits. The fixed verifier image and actual grade provenance are also compared. Observed model settings must agree with the planned model, and reported model IDs must match across the pair. Raw manifests and grading provenance must supply the required identities; source/harness stability must be verified. Missing identity evidence is reported as ineligible, not manufactured.

Standalone episode pairing is opt-in: include `pairId` for the predeclared repeat and `limits` in each manifest. Required matching fields are model requested/reported IDs, API, effort, context window, task, input/instruction/task-contract digests, isolation, limits, real-model execution, and grader/product/oracle provenance. A harness needs source and binary hashes and `harnessUnchanged: true`. The collector does not infer matching repeats from filenames or successful outcomes.

Duplicate attempts for a harness in the same pair are ambiguous and excluded from paired estimates while staying in all-attempt tables. Unmatched/ineligible attempts and unknown functional pairs are counted. Pair results show both-pass, A-only, B-only, neither, and unknown; raw metric deltas are B minus A over pairs with both values known. Infrastructure-only rows generally lack observed model/evaluator identities and remain in all-attempt denominators even when ineligible for paired analysis.

These are descriptive comparisons of whole harnesses under matched observed conditions. They do not isolate a tool's causal effect, eliminate model/backend drift or task-selection bias, or establish stable rankings from a small sample. No best-of-k, weighted visual total or unearned causal claim is produced.

## Local validation

```bash
systemd-run --user --scope -q -p MemoryMax=8G timeout 900 \
  python -m unittest discover -s tests -p test_visual_review_report.py -v
# Add a real sequential native-render test with the fixed product:
export VIBE_BENCH_NATIVE_TREE=/path/to/fixed/product
# Set VIBE_BENCH_HOST and VIBE_LOGISIM_NODE as in visual-scoring.md, then repeat.
```

The tests cover reachability, wide canvases, library-file identity, source immutability, omitted/error views, review integrity, failure/unknown accounting, privacy allowlisting, paired-condition mismatches, duplicate pair attempts, recursive Harbor discovery, exceptions and missing scheduled trials. Synthetic/scripted tests verify infrastructure, not real-model construction ability or human calibration.
