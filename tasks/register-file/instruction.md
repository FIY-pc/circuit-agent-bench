Build and save an 8-register × 8-bit register file in the empty `main` circuit of `design.circ`.

Use exactly these top-level pins:

| Pins | Direction | Width |
|---|---|---|
| CLK, RST, WE | input | 1 each |
| WA, RA, RB | input | 3 each |
| WD | input | 8 |
| QA, QB | output | 8 each |

There are eight independent writable bytes, numbered 0–7. Register 0 is writable like every other register. On each rising edge of CLK:

1. If RST=1, clear all eight registers to zero, regardless of WE.
2. Otherwise, if WE=1, write WD into register WA.
3. Otherwise preserve all registers.

Reset is synchronous: changing RST without a rising clock edge must not change the registers. Falling edges and changes to WA, WD, or WE while CLK remains high or low must not write.

Reads are asynchronous and independent: QA always reads register RA; QB always reads register RB. Both ports may select the same register. After a rising edge that writes their selected address, reads show the newly stored byte. With no rising edge there is no write-through bypass from WD. State before the first reset edge is unspecified.

Any internal implementation and additional subcircuits are allowed. Keep the diagram readable, verify addressing, enable, reset, and edge behavior, and save `design.circ` with `main` as the entry point.
