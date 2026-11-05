Build and save a single-cycle Compact-8 CPU in the empty `main` circuit of `design.circ`. Implement the complete instruction set and architectural state below. This is a small synthetic ISA with no dependency on an existing course processor.

The instruction memory is external: the environment reads PC and presents the corresponding 16-bit instruction on INST before each rising clock edge. You build the processor and its internal data memory. No program ROM, fixed demonstration program, or extra top-level control pins are required.

Use exactly these top-level pins:

| Pins | Direction | Width | Meaning |
|---|---|---|---|
| CLK | input | 1 | rising-edge clock |
| RST | input | 1 | synchronous, active-high reset |
| INST | input | 16 | instruction at current PC |
| MA | input | 4 | asynchronous data-memory inspection address |
| PC | output | 8 | instruction address, in words |
| R0, R1, R2, R3 | output | 8 each | current architectural register values |
| HALTED | output | 1 | sticky halt state |
| MD | output | 8 | current data-memory byte at address MA |

Architectural state comprises PC, four independent writable 8-bit registers, sixteen independent 8-bit data-memory locations, and HALTED. R0 is an ordinary writable register. On a rising edge with RST=1, clear **all** of this state to zero, including data memory; reset has priority over halt and instruction execution. Initial state before the first reset edge is unspecified. RST pulses without a rising edge have no effect.

On every rising edge with RST=0 and HALTED=0, execute exactly one instruction. Source operands are the values just before that edge, including when source and destination are the same register. Register and memory writes become visible after the edge. Arithmetic wraps modulo 256. PC normally advances by one modulo 256. When HALTED=1 and RST=0, every clock edge preserves all architectural state, regardless of INST. Falling edges and instruction changes without a rising edge do not execute. MD is an asynchronous view of data memory: changing MA changes MD without a clock and without modifying memory.

Instruction fields (bit 0 is least significant):

- bits 15–12: opcode;
- bits 11–10: register d;
- bits 9–8: register s;
- bits 7–0: unsigned immediate k.

Unused fields are ignored. There are no illegal opcodes, flags, delay slots, interrupts, or implicit zero register.

| Opcode (hex) | Instruction | Effect at the rising edge |
|---|---|---|
| 0 | NOP | no data change |
| 1 | LDI d, k | R[d] ← k |
| 2 | MOV d, s | R[d] ← R[s] |
| 3 | ADD d, s | R[d] ← (R[d] + R[s]) mod 256 |
| 4 | SUB d, s | R[d] ← (R[d] − R[s]) mod 256 |
| 5 | XOR d, s | R[d] ← R[d] XOR R[s] |
| 6 | AND d, s | R[d] ← R[d] AND R[s] |
| 7 | OR d, s | R[d] ← R[d] OR R[s] |
| 8 | SHL d | R[d] ← R[d] shifted left by 1, zero filled |
| 9 | SHR d | R[d] ← R[d] shifted right by 1, zero filled |
| A | LD d, k | R[d] ← memory[k mod 16] |
| B | ST d, k | memory[k mod 16] ← R[d] |
| C | BZ d, k | if R[d]=0, PC ← k; otherwise PC ← PC+1 |
| D | BNZ d, k | if R[d]≠0, PC ← k; otherwise PC ← PC+1 |
| E | JMP k | PC ← k |
| F | HALT | HALTED ← 1; PC stays at the HALT address |

PC increments normally for opcodes 0–B. Branch and jump targets are absolute 8-bit word addresses. Branches do not modify registers or memory. HALT does not modify registers or memory. Reset allows execution to restart at PC=0 on subsequent edges.

For a small public example, the instruction words `1007 1405 3100 B003 1800 A803 F000` load R0=7 and R1=5, add R1 into R0, store 12 at memory[3], clear R2, load memory[3] into R2, and halt with PC=6. This example is not the only program the CPU must execute.

Use any internal circuitry or subcircuits that satisfy this single-cycle interface. No particular datapath, control implementation, memory component, or layout is prescribed. Organize the diagram so the processor can be understood and modified. Verify actual multi-instruction execution, memory, branches, reset, and halt, then save `design.circ` with `main` as the entry point.
