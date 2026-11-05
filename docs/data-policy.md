# Task and calibration data

Repository tasks are authored from explicit mathematical and state-machine
specifications. Their starting projects and reference circuits are synthetic;
source descriptions live beside each task. No downloaded student archive,
assignment PDF, user conversation, model credential or existing course solution
belongs in a task package or in repository history.

External real circuits may calibrate the visual reviewer locally. Store them
outside this repository. Record a local source manifest, permission to use,
circuit family, anonymized case ID, review outcome and scoring version. Split by
circuit family/source lineage before fitting; random crops of the same CPU cannot
appear in both fit and evaluation sets. Holdout scores measure generalization to
those families, not all possible circuit diagrams.

Only reviewed, aggregate calibration results and justified parameters may be
proposed for inclusion. Raw paths, names, identifiers and circuit files are not
needed to publish the scorer. Calibration is optional: until a proposed scorer
has demonstrated agreement and acceptable severe-error recall, reports retain
human rubric results and unknowns instead of fabricating a visual score.

Run directories are private by default. They contain exact provider model IDs,
full product events, generated circuit projects and process output. Share the
sanitized report after inspection; sharing a repository does not share old runs.
The credential proxy keeps the actual key in memory. Product profiles contain
only a loopback endpoint and placeholder key. Repository examples never include
service-specific endpoints or keys.

The repository is independently initialized. It does not carry the history or
data of earlier experiments. Code and synthetic task assets use GPL-3.0, matching
the product integration's license. The product, Java runtimes and dependencies
are built/downloaded separately with their own licenses; none are vendored here.
