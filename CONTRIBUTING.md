# Contributing

Start with a small, complete task or a coherent harness experiment. A useful
change explains the observed problem, the changed mechanism and what evidence
would distinguish improvement from regression. Model runs use your own environment
variables and quota; ordinary tests never make paid calls.

For tasks, write the public behavioral contract first. Keep grader logic
independent of the reference circuit and test both valid alternative solutions
and realistic mistakes. Use synthetic, independently authored projects. Do not
commit user conversations, credentials, local paths or downloaded circuit corpora.

For visual metrics, show examples where the metric agrees and disagrees with
actual reading. Freeze a family-level holdout before fitting thresholds. Report
coverage and uncertainty. An uncalibrated metric belongs in diagnostics rather
than the headline score.

Keep raw runs local. Review generated reports before sharing them. Retain failed
attempts, missing usage and evaluator errors. A new task version or grader version
must remain distinguishable from the previous one.
