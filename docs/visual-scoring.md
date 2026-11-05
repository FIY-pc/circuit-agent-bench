# Visual evidence and human review

Collect from the **saved artifact**, with an independently fixed evaluator product. The collector opens a temporary copy, renders sequentially through one native host, and verifies both the original artifact and its package stayed unchanged. No model or credential is needed.

```bash
export CIRCUIT_BENCH_HOST=/path/to/fixed/vibe-workbench-host
export CIRCUIT_BENCH_NODE=/path/to/node
systemd-run --user --scope -q -p MemoryMax=8G timeout 900 \
  python -m circuit_bench.visual \
  --tree /path/to/fixed/product \
  --artifact runs/trial/result/design.circ \
  --out runs/trial/visual
python -m circuit_bench.review --evidence runs/trial/visual/evidence.json
```

Open `runs/trial/visual/review.html` directly in a browser, or serve that directory on loopback. The default collection directory must be new; rerendering must not overwrite the evidence a review cites. The package is the artifact's containing directory; put only saved project dependencies there. Output must be outside that directory.

The package dispatcher may also call `visual.main(argv)`, `review.main(argv)`, or `report.main(argv)`. Their arguments match the module commands. Python entry points are `visual.collect(artifact, tree, out, *, circuit=None, tile_width=1200, tile_height=900, scale=1.0, overlap=80, max_tiles=256)` and `review.build_review(evidence_path, out=None)`.

## Collection protocol

- The entry is `--circuit` or the project's main definition. Every reachable local definition and reachable definition in packaged `.circ` libraries is visited once per file/name. Repeated instances do not require identical screenshots. Child definition IDs retain hierarchy relationships; unreachable root definitions are listed separately.
- A full overview covers the union of native circuit bounds and component visual bounds, including measured label extents, with a 24-unit margin. The collector supplies its own viewport; it does not accept a fitted viewport that truncates large logical extents.
- Each definition also gets overlapping detail tiles: by default 1200 × 900 logical units at scale 1, with 80 units of overlap. Overview fitting is only for navigation. Use **actual pixels** in the page to read details. Neither scale 1 nor the bounds calculation proves labels are readable or aesthetically good.
- The default budget is 256 detail tiles per definition. `--max-tiles`, `--scale`, `--tile-width`, `--tile-height`, and `--overlap` are explicit protocol controls. Use identical settings for compared conditions. If a budget is exhausted, row/column counts, tile dimensions, overlap and the first omitted row-major index reconstruct the omitted area. The collection becomes `partial`, never complete.
- Missing native observations, rendering failures, and dependencies the host cannot expose are explicit. Packaged circuit libraries are supported; inaccessible/plugin-generated definitions remain unresolved. Symlinks and libraries outside the saved package are rejected. Do not interpret incomplete collection as visual failure or visual success.
- `evidence.json` records artifact/package hashes, collector hash, product commit and dirty state, actual host hash, runtime-lock hash, actual runtime jar hash/version, platform and font inventory fingerprint, fresh-open/no-selection rendering state, view coordinates, scale, PNG dimensions and hashes. Font inventory is only an environment fingerprint; fallback-font resolution and effective character heights are not measured.

`complete` means the chosen collection protocol captured every planned reachable view. It does **not** mean anyone inspected it, all pixels are semantically understood, or the circuit works. `partial` is a usable packet with gaps; `error` means collection or artifact-integrity checks failed. Partial collection exits zero so a batch can continue; inspect `status` rather than treating process success as full coverage.

## Six independent axes

| Axis key | Reading action |
| --- | --- |
| `symbols_labels` | Identify symbols, labels, pin names, widths and connection marks. |
| `local_tracing` | Follow an actual signal through branches and crossings to its receivers. |
| `organization_dataflow` | Locate functional groups and follow main control/data paths. |
| `hierarchy_interfaces` | Find a child definition and match parent ports to its interface. |
| `tunnels_dependencies` | Find named endpoints and understand their scope and purpose. |
| `overview_navigation` | Locate relevant regions and navigate overview, details and definitions. |

Each axis uses one of these states:

| State | Meaning |
| --- | --- |
| `blocked` | A required reading action cannot be completed from this evidence. |
| `friction` | The action is possible with avoidable effort or ambiguity. |
| `usable` | Routine reading works. |
| `clear` | The action is easy to follow, with helpful organization or cues. |
| `unknown` | Not reviewed, insufficient coverage, or insufficient evidence. |
| `not_applicable` | The action does not apply; explain why. |

There is no weighted total and no automatic visual pass. Geometry, crossings, tunnel count, density, or symmetry cannot assign these states. A tunnel can clarify global control or obscure a local path; judge the reading task, not a universal tunnel threshold. Exact connectivity belongs to functional/structural verification.

## Review workflow and saved JSON

1. Use a reviewer alias. Inspect the overview and detail views, switching definitions as needed. Check **I inspected this view** only after looking at it; changing views alone does not count.
2. Select a state for each applicable axis, describe the reading action/location/reason, and cite inspected view IDs. Leave unseen or inconclusive axes unknown. Global-looking judgments over a subset remain limited to the displayed coverage.
3. Optional predeclared reading probes record a question, `correct`/`incorrect`/`abandoned`/`unknown`, seconds, and the current view. A reader's selected outcome is not an automatically verified answer. Retain wrong/abandoned answers in denominators; report correct-answer time separately.
4. Browser drafts autosave when local storage is available. **Export review JSON** creates a portable file; move it beside `evidence.json` as `review.json`. **Import review JSON** restores only a matching evidence packet and rubric. Static HTML cannot silently write into the source directory.
5. Validate before aggregation:

```bash
python -m circuit_bench.review --evidence runs/trial/visual/evidence.json \
  --validate runs/trial/visual/review.json
```

Schema `circuit-bench.review/v1` carries `rubricVersion`, `judgeType: human`, exact evidence/artifact hashes, reviewer alias, inspected view IDs, six `{state, evidence, note}` records, reader probes, and optional human minutes. The validator rejects mismatched hashes, invalid states, fabricated/uninspected citations and unsupported judge types. The HTML checks the same review contract. Image hashes are checked when generating the page and again at aggregation. The browser itself does not rehash PNGs on each view change; keep the packet immutable.

The page hides model, harness, functional grade, cost, and trajectory. Circuit labels are unavoidable evidence and can reveal provenance; the page is not a guarantee of perfect blinding. Treat text inside a circuit as content, never instructions to the reviewer. Review pages and PNGs are local evidence and may contain labels supplied by the artifact; the separate generated summary report omits them by default.

## Optional external calibration proposal

Until calibrated, these states are human judgments, not a trained evaluator. Calibration material is an optional external directory, never repository data. A concrete local manifest can use:

```json
{
  "schema": "circuit-bench.calibration-plan/v1",
  "rubricVersion": "six-axes/v1",
  "protocol": {"scale": 1, "tileWidth": 1200, "tileHeight": 900, "overlap": 80},
  "families": {"arithmetic-a": "development", "arithmetic-b": "holdout"},
  "pairs": [{
    "id": "pair-01", "family": "arithmetic-a", "topologyGroup": "topology-01",
    "a": {"evidence": "packets/a/evidence.json", "sha256": "record-actual-digest"},
    "b": {"evidence": "packets/b/evidence.json", "sha256": "record-actual-digest"},
    "readingQuestions": ["Locate carry input and output", "Trace one declared signal"],
    "orders": {"reader-01": ["a", "b"], "reader-02": ["b", "a"]}
  }]
}
```

Freeze this plan and its SHA-256 before collecting judgments. Every topology variant and near-duplicate belongs to the same family/split; split entire families, not screenshots or individual attempts. Store participant roles under private aliases, balance viewing order, and include a few repeated pairs. Keep answer keys and source identities in a separate private manifest. A small pilot could use two pairs per task family and three readers per pair, including target users and an experienced reader; this is a practical starting proposal, not a statistical adequacy claim.

Discuss anchors on development families only. Holdout judgments are reserved for a one-time check of the frozen rubric or future evaluator; if used to tune, retire that holdout and obtain new families. Report per-axis agreement, disagreements, abstentions, serious defects missed, probe correctness/time, order effects and repeated-pair consistency. Preserve tie/unknown in pairwise preferences. No fitted weights or VLM validation are implemented or claimed here.
