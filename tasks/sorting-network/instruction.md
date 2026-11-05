Build and save a combinational sorter for eight unsigned 8-bit numbers in the empty `main` circuit of `design.circ`.

The interface must contain exactly eight input pins `X0` through `X7` and eight output pins `Y0` through `Y7`, all 8 bits wide.

For every input tuple, outputs must be the same multiset in ascending order:

- Y0 ≤ Y1 ≤ … ≤ Y7 under unsigned comparison (0 through 255).
- Every input value must occur equally often among the outputs. Duplicates are retained.
- Output values must settle without clocking and remain stable while inputs are held. Changing any input must produce the new sorted tuple; there is no retained state.

For example, inputs `255, 0, 128, 1, 128, 7, 3, 0` produce `0, 0, 1, 3, 7, 128, 128, 255`.

A comparator network is one suitable implementation. Any other combinational implementation with the same behavior is also accepted: no particular network, number of stages, gate count, placement, or subcircuit structure is required. Make the signal flow readable, verify the circuit, and save `design.circ` with `main` as the entry point.
