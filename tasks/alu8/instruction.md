Build and save an 8-bit combinational ALU in the empty `main` circuit of `design.circ`.

The top-level pins must be exactly these names, directions, and widths:

| Pins | Direction | Width |
|---|---|---|
| A, B | input | 8 each |
| OP | input | 3 |
| Y | output | 8 |
| C, Z | output | 1 each |

Inputs are unsigned bytes. Compute the selected operation below; discard bits above bit 7 in Y. `Z` is 1 exactly when Y is zero, for every operation.

| OP | Y | C |
|---|---|---|
| 0 | A + B modulo 256 | carry out (A + B > 255) |
| 1 | A − B modulo 256 | borrow (A < B) |
| 2 | bitwise A AND B | 0 |
| 3 | bitwise A OR B | 0 |
| 4 | bitwise A XOR B | 0 |
| 5 | bitwise NOT A | 0 |
| 6 | A shifted left by one bit, zero filled | original A bit 7 |
| 7 | A shifted right by one bit, zero filled | original A bit 0 |

B has no effect for OP 5–7. Outputs must settle to these values for every input combination and remain stable while inputs are held; there is no clock or retained state.

You may use standard components, subcircuits, direct file editing, or scripts. No particular gate count, internal layout, or implementation is required. Keep the drawing readable, verify the circuit, and save `design.circ` with `main` as the entry point.
