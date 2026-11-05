# Validation status

Observed on 2026-11-05 with Linux, Python 3.12, Harbor 0.25.0, Docker,
Node 24 and the Vibe Logisim v26.11.2 source tree. Logs and raw model records
remain outside version control. These are implementation checks and initial
pilots, not a capability ranking.

## Runtime and isolation

- Candidate images run the actual product host and sidecar. They contain no
  benchmark graders, reference answers or hidden test programs.
- Independent verifier containers use Docker Compose `network_mode: none`.
  An empty artifact completed that lifecycle with a functional failure and no
  Harbor exception.
- Image-build and per-trial preflight exercise component lookup, observation,
  native PNG rendering and simulation before paid inference starts.
- An early pilot exposed a missing native font library. It was stopped and
  classified as an infrastructure failure; its partial usage remains recorded.
  Images were repaired and a fresh trial was run. The failed attempt was retained.
- The complete nested-interrupt reference also passed in the offline verifier
  image: 22 functional groups and 165,746 output checks.

## Evaluators

The integrated suite passed all 64 tests, including optional native rendering and
the actual product SDK against a local Messages fixture (no paid model). The native task suite exercised 44 artifacts through one
serial host: seven correct references and an alternative sorting topology passed;
29 functional mutants and three missing/unknown/oscillating-signal defects failed;
four malformed/dependency documents returned `invalid_artifact`.

The separate visual suite exercised native rendering, immutable evidence, browser
review/export/import, missing coverage, report privacy, interrupted usage and
matched-condition pairing. The report was also checked in a browser on real pilot
outputs. References are functional fixtures, not attractive layout exemplars.

## Real-model pilots

The same configured model and unmodified product harness completed fresh full-adder,
ALU and Compact-8 CPU tasks. Independent saved-artifact verification passed 402 full-adder checks,
19,632 ALU checks across nine groups, and 44,156 CPU checks across eleven groups. No intermediate hints or corrections were
sent. Native overview/detail packets were generated for all three, with all reachable
views captured; the CPU packet includes four definitions and 33 views. Human visual ratings remain unassigned.

A second full-adder run after retention fixes passed all 402 checks. It retained
all nine assistant responses, all fifteen tool results and its native image;
nine requests had confirmed final usage and no transport failures. Cancellation
checks cover SIGTERM, SIGKILL recovery, startup interruption and Harbor execution
cancellation without paid inference. Compaction preserves the product's original
transcript; the exporter does not replace its context mechanism.

The first full-adder and ALU pilots retained lossy UI events rather than full
native transcripts. That evidence gap remains in those old attempts. The CPU's
live native snapshot was recovered and matched against its final response and
all 177 recorded tool results. Later trials use the durable exporter directly.

These are exploratory pilots, including a repeated infrastructure regression.
They show that the lifecycle works on actual model outputs; they do not establish repeatable completion rates, broad model rankings,
or visual quality. Raw token usage, tool failures and elapsed time are retained
in local attempt reports rather than copied into this repository.

## Remaining evidence limits

- Windows/WSL2 and a fresh collaborator installation have not been exercised.
- The interrupt extension has native evaluator validation; a full real-model sweep and
  repeated trials remain future experiments.
- The synthetic CPUs have external instruction buses and single-cycle timing.
  Pipeline hazards, cache behavior and a complete course-scale processor are not
  covered. No equivalence to the hardest course project is claimed.
- Visual dimensions have review anchors and integrity checks, but no calibrated
  automatic visual grader or established inter-rater reliability.
- Finite programs and seeded vectors are fault detection, not equivalence proofs.
