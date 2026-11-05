Build and save a single-cycle Compact-8I CPU with three interrupt sources in the empty `main` circuit of `design.circ`. Implement the complete instruction set and architectural state below. This is a small synthetic ISA with no dependency on an existing course processor. Implement the base ISA below plus the interrupt extension in the second half of this request.

The instruction memory is external: the environment reads PC and presents the corresponding 16-bit instruction on INST before each rising clock edge. You build the processor and its internal data memory. No program ROM, fixed demonstration program, or extra top-level control pins are required.

Use exactly these top-level pins:

| Pins | Direction | Width | Meaning |
|---|---|---|---|
| CLK | input | 1 | rising-edge clock |
| RST | input | 1 | synchronous, active-high reset |
| INST | input | 16 | instruction at current PC |
| MA | input | 4 | asynchronous data-memory inspection address |
| IRQ | input | 3 | interrupt request levels; bit 0 is source 1, bit 2 is source 3 |
| PC | output | 8 | instruction address, in words |
| R0, R1, R2, R3 | output | 8 each | current architectural register values |
| HALTED | output | 1 | sticky halt state |
| MD | output | 8 | current data-memory byte at address MA |
| IE | output | 1 | global interrupt enable |
| MASK | output | 3 | per-source enable bits |
| PENDING | output | 3 | latched, unacknowledged requests |
| LEVEL | output | 2 | current priority: 0=main, 1–3=handler |
| DEPTH | output | 2 | number of saved interrupt frames, 0–3 |
| EPC | output | 8 | resume PC in the top saved frame, or 0 when empty |
| ACK | output | 3 | one-hot source accepted at the most recent rising edge, or 0 |

Architectural state comprises PC, four independent writable 8-bit registers, sixteen independent 8-bit data-memory locations, HALTED, and the interrupt state and saved frames defined below. R0 is an ordinary writable register. On a rising edge with RST=1, clear **all** of this state to zero, including data memory and all interrupt state and frames; reset has priority over interrupt sampling, halt, and instruction execution. Initial state before the first reset edge is unspecified. RST pulses without a rising edge have no effect.

On a rising edge with RST=0, first evaluate interrupt acceptance as specified below. If no interrupt is accepted and HALTED=0, execute exactly one instruction. Source operands are the values just before that edge, including when source and destination are the same register. Register and memory writes become visible after the edge. Arithmetic wraps modulo 256. PC normally advances by one modulo 256. When HALTED=1 and RST=0, INST is ignored. IRQ sampling and interrupt acceptance still occur; if no interrupt is accepted, PC, registers, memory, halt state, masks, and saved frames stay unchanged. Falling edges and instruction changes without a rising edge do not execute. MD is an asynchronous view of data memory: changing MA changes MD without a clock and without modifying memory.

Instruction fields (bit 0 is least significant):

- bits 15–12: opcode;
- bits 11–10: register d;
- bits 9–8: register s;
- bits 7–0: unsigned immediate k.

Unused fields are ignored except that the opcode-0 extension below matches exact 16-bit words. There are no illegal opcodes, arithmetic flags, delay slots, or implicit zero register.

| Opcode (hex) | Instruction | Effect at the rising edge |
|---|---|---|
| 0 | NOP / control | exact control encodings below; all other opcode-0 words have no data change |
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


## Interrupt control instructions

These exact 16-bit words reserve encodings that were NOPs in Compact-8. Every other opcode-0 word remains a NOP. Control instructions do not write general registers or data memory.

| Exact word (hex) | Instruction | Effect when executed |
|---|---|---|
| 0001 | EI | set IE=1; advance PC |
| 0002 | DI | set IE=0; advance PC |
| 0003 | IRET | restore the top interrupt frame and pop it; if empty, behave as NOP |
| 0010–0017 | SETMASK k | set MASK to the word's low three bits; preserve IE; advance PC |

MASK bit i enables source i+1 when it is 1. Reset sets MASK=0 and IE=0. EI does not change MASK. DI does not discard pending requests.

## Sampling, priority, and precise entry

At each rising edge, use the following order. Inputs and old state mean values just before the edge.

1. If RST=1, reset the complete CPU, including interrupt state and all saved frames, to zero. Do not sample IRQ or execute INST on that edge.
2. Otherwise form the available requests as `PENDING OR IRQ`. This latches even masked requests and requests arriving while IE=0 or HALTED=1. Request bits are levels sampled only on rising edges; a pulse entirely between edges has no effect. Multiple requests for one source coalesce into one bit, not an event counter.
3. A source is eligible when its available bit is 1, its MASK bit is 1, IE=1, and its priority is **strictly greater** than LEVEL. Source 3 has highest priority, then source 2, then source 1. Choose the highest eligible source. Eligibility uses the old IE, MASK, and LEVEL: EI, DI, SETMASK, or IRET in INST has not yet executed.
4. If a source is accepted, perform interrupt entry instead of executing INST. Clear only that source's available bit to obtain new PENDING; retain every other available bit. Set ACK to the accepted source's one-hot bit. Push a frame containing the resume PC and the old IE, MASK, and LEVEL. For a running CPU, the resume PC is the current PC: the interrupted instruction has not executed. For a halted CPU, it is `(PC + 1) mod 256`: resume after HALT. Set LEVEL to the accepted source's priority, IE=0, HALTED=0, and PC to the corresponding vector. MASK and all general registers and data memory are unchanged by entry.
5. If no source is accepted, store all available bits into PENDING, set ACK=0, and execute INST if the CPU is running. If it is halted, preserve the remaining state as described above.

| Source | IRQ / MASK / PENDING / ACK bit | Priority / LEVEL | Vector PC (hex) |
|---|---|---|---|
| 1 | 0 | 1 | 80 |
| 2 | 1 | 2 | A0 |
| 3 | 2 | 3 | C0 |

ACK is registered: it reports the most recent rising edge and holds between edges. Pending requests are cleared only by reset or acceptance of that source. A source held high is acknowledged once on an accepting edge, then becomes pending again on the next edge if still high. The current source cannot interrupt itself, even after EI, because equal priority is ineligible. Lower-priority sources wait until the active priority falls sufficiently.

Entry is precise at the single-cycle architectural boundary. The interrupted store, register write, branch, jump, HALT, or control instruction must have **no effect** on the entry edge. The instruction at the vector may execute on the next edge. There is no pipeline or partially executed instruction to drain or flush.

## Nesting and return

Entry clears IE. A handler may execute EI to permit higher-priority nesting. Strictly increasing priorities bound nesting to three frames. DEPTH counts those frames; LEVEL is the currently executing priority, not the frame count. EPC displays the resume PC of the top frame, or zero when DEPTH=0. The internal stack implementation is your choice.

IRET with a nonempty stack restores PC, IE, MASK, and LEVEL from the top frame and removes it. It does not increment the restored PC, clear pending requests, acknowledge an interrupt by itself, restore general registers or memory, or restore HALTED. The CPU remains running. Handler register and memory changes persist; software can save and restore them explicitly. IRET with an empty stack changes only PC to PC+1 modulo 256.

An eligible request at the same edge as IRET takes priority over IRET, so that return has not yet occurred: entry pushes the PC of the IRET instruction and the current context. Likewise, an eligible request wins over DI; a request captured while IE=0 cannot preempt EI at that edge, but may be accepted at the following edge. Restored masks and priority take effect for acceptance on subsequent edges.

A useful public exercise is to enable all sources with SETMASK 7 and EI, pulse source 1, let its handler execute EI, then pulse source 3. The saved frames must unwind from level 3 to level 1 to main, resuming each interrupted instruction. Also verify simultaneous requests, masked pulses, memory side effects, HALT wake-up, and reset from inside a nested handler. The evaluator uses additional programs and request schedules.
