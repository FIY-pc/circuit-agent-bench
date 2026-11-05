# Construction task ladder

Run a saved artifact through the independent grader with an explicitly selected verifier product:

```sh
python3 -m circuit_bench.grading \
  --task tasks/cpu8 --artifact result/design.circ \
  --tree /path/to/verifier-product --out grade.json
```

The seven tasks start from an empty `main` circuit. Agents receive the entire `instruction.md` and `environment/input/`, plus the public task metadata. A task's `tests/` directory, oracle module, reference generator, random seeds, and evaluator programs belong only to the verifier. Do not mount the repository into the agent environment. The package builder copies input and metadata into the candidate environment and keeps the oracle in the separate verifier environment.

| Task | Construction target | Independent functional coverage | Agent time limit |
|---|---|---|---|
| `full-adder` | One-bit full adder | Full truth table; every ordered input transition and holds | 15 min |
| `counter4` | Four-bit synchronous counter | Reset priority and timing, rising/falling edges, holds, wrap | 15 min |
| `alu8` | Eight operations on unsigned bytes | Arithmetic, carry/borrow, bitwise logic, shifts, zero flag, operation changes and holds | 25 min |
| `register-file` | Eight writable bytes, two asynchronous reads | Every read-address pair; write enable; address isolation; read-after-write; synchronous reset; held-high and falling-clock behavior | 35 min |
| `sorting-network` | Eight unsigned bytes sorted combinationally | Every binary input pattern, boundary values, duplicates, permutations, random multisets, changes and holds | 45 min |
| `cpu8` | Single-cycle Compact-8 processor | All 16 instructions; dependent execution; source/destination aliases; memory; loops; taken/untaken branches; jumps and PC wrap; halt; reset and restart | 90 min |
| `cpu8-interrupts` | Compact-8I processor with prioritized nested interrupts | Complete base ISA plus three sources; pending/masks; three saved frames; precise PC and suppressed entry-edge side effects; nested return; halt wake-up; reset during nesting | 180 min |

`task.json` includes a stable reporting `family`: `arithmetic` for full-adder and ALU8, `state` for counter4 and register-file, `dataflow` for sorting-network, and `system` for both CPU tasks. Families group results; they do not change grading or imply equal task difficulty.

These limits are initial task budgets, not measured model completion estimates. Functional correctness, visual quality, episode completion, and cost are separate results. The functional grader leaves `layout.status=not_evaluated`; passing it makes no visual-quality claim.

## Interfaces and semantics

The public instruction files are the task contracts. Each specifies every required port, width, timing rule, and operation. Internal component identities, coordinates, net names, gate counts, reference topology, and XML equality are not grading criteria. Standard components, gates, subcircuits, and other behaviorally equivalent implementations are allowed.

`cpu8` deliberately specifies single-cycle architectural timing. Its 16-bit external instruction bus permits testing arbitrary programs without finding or modifying a particular internal ROM. The environment drives the instruction for the expected PC before each rising edge; the grader compares the actual PC, all four registers, halt state, and the selected memory byte throughout the trace. An incorrect PC fails immediately even if the subsequent supplied instruction happens to be useful. It is not a free-running instruction-memory integration test.

The CPU contains sixteen bytes of data memory, visible through an asynchronous inspection port. Tests exercise every location, overwrite and load-after-store, scan memory after execution, then reset and scan again. Reset wins over writes and halt. The stimulus changes one sequential input per sample, separating data setup, rising edge, held-high, falling edge, and holds. Each trace begins with a real reset edge; power-on state before that edge is intentionally unspecified.

Both CPU tasks are runnable synthetic construction tasks with actual sequenced state transitions. They are not implementations of a course ISA or replacements for the hardest course assignment. Neither has a pipeline, speculative wrong-path state, dynamic branch prediction, or a cache.

`cpu8-interrupts` adds a public interrupt contract to the same base ISA. Three sampled IRQ levels latch pending bits even while masked; global enable and per-source masks control acceptance. Higher priorities may preempt after a handler explicitly enables interrupts. Entry saves PC, enable, mask, and priority; three saved frames permit nesting through all sources. The interpreter uses a Python frame stack, independently of the reference circuit's registers and multiplexers. Return restores the complete frame. HALT can be interrupted and resumes at the following PC. Interrupt entry suppresses the current instruction's register, memory, and control effects; that is the precision boundary for this declared single-cycle architecture.

The advanced task runs every base CPU test plus targeted interrupt programs and seeded schedules. These exercise simultaneous requests, masked pulses, held and repeated requests, strict priority, nested mask restoration, entry at store/branch/DI/IRET boundaries, PC wrap, halt wake-up, and reset at depth three. Its public instruction file fully specifies acceptance before instruction execution, pending-bit coalescing, ACK timing, and every control encoding; none of these are hidden architectural constraints.

## Oracle independence and functional groups

`circuit_bench/task_oracles.py` and `circuit_bench/interrupt_oracle.py` compute outputs directly from integer arithmetic, sorting, a register array, and instruction interpreters. Neither imports the reference generators or reads reference outputs. The CPU instruction encoder, interpreter, and synthetic programs are authored for this repository. The reference generators (`reference_circuits.py` and `interrupt_reference.py`) create gates, multiplexers, registers, and arithmetic components independently; they import no oracle code or test programs.

New-task reports expose named functional `groups`, `passed_groups`, `total_groups`, and individual output `checks`. A task passes only when the exact interface and every group pass. Group counts are diagnostic coverage, not weighted capability scores; a large number of repeated samples does not make a harder task worth more.

The checker rejects missing, short, duplicate, out-of-order, unknown, inconsistent-bit/value, and oscillating traces. A missing required port fails interface checking. An output that exists but is undriven fails functional checking. Known native document refusals are `status=invalid_artifact, passed=false`: `INVALID_DOCUMENT`, exact missing-library diagnostics, diagnosed component loss, diagonal wires, and conflicting memory-content serialization. General dependency errors, compiler/runtime/transport faults, and unclassified Java exceptions remain `status=error, passed=null`; a broad loader catch does not turn infrastructure failures into candidate failures. Provenance includes the product, artifact, task, oracle JSON, grader code, and both oracle-module digests. Grading reopens a temporary copy and checks that the submitted artifact is unchanged.

Combinational cases use a persistent trace with repeated holds, rather than only fresh-state truth-table rows. Sorting is judged against Python's sorted multiset, which requires both ascending order and preservation of duplicate counts. Exhaustive binary inputs are useful comparator-network checks, but do not prove arbitrary eight-byte logic correct; boundary and seeded byte tests add coverage. ALU byte-pair testing and all CPU programs are finite, not exhaustive proofs. The seed lives on the evaluator side and can be changed in its oracle JSON.

## Regression circuits and native tests

Each task's `tests/reference.circ` is a functional regression answer. Native tests generate concrete wrong circuits in temporary directories using the reference generators. Only canonical references are committed; deterministic mutant XML is not stored in the repository:

| Task | Implemented fault variants |
|---|---|
| ALU | subtraction adds; arithmetic right shift; missing borrow |
| Register file | ignored write enable; unwritable register zero; aliased read ports; asynchronous reset; falling-edge writes |
| Sorting | omitted final stage; signed ordering; duplicated output |
| CPU | subtraction adds; dropped stores; inverted branch condition; execution after halt; asynchronous reset; falling-edge execution |
| CPU with interrupts | lowest priority wins; ignored masks; lost masked pulses; nesting disabled; saved PC off by one; one overwritten saved PC; missing mask/enable restoration; entry executes the interrupted instruction; halt never wakes; subtraction adds; dropped stores |

The native test suite also builds an alternative bitonic sorter, plus missing, disconnected, and feedback-loop output defects, and concrete loader-refusal documents. The alternative topology must pass; incorrect artifacts must fail in the actual product host. Pure Python trace-corruption tests separately cover malformed host responses. No native test invokes a model.

Regenerate the five generated canonical references deterministically:

```sh
python3 -m circuit_bench.reference_circuits --output tasks
python3 -m unittest discover -s tests -v
```

Native tests are opt-in, serial, and reuse one host. Set product paths for your machine, then use a bounded memory scope:

```sh
export CIRCUIT_BENCH_TREE=/path/to/verifier-product
export CIRCUIT_BENCH_HOST=/path/to/vibe-workbench-host
export CIRCUIT_BENCH_NODE=/path/to/node
systemd-run --user --scope -q -p MemoryMax=8G timeout 900 \
  env CIRCUIT_BENCH_NATIVE=1 CIRCUIT_BENCH_NATIVE_REPORT=/tmp/task-suite-native.json \
  python3 -m unittest discover -s tests -p 'test_tasks_native.py' -v
```

The optional JSON report contains each artifact's full functional result and provenance. Keep machine-specific reports outside the repository. Reference diagrams are simulator fixtures, not calibrated examples of good visual layout. Native host acceptance does not by itself verify a standalone GUI runtime, Windows packaging, container isolation, real-model completion rates, or visual readability.
