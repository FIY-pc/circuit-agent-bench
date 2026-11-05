"""Independent mathematical specifications and evaluator-only stimuli.

This module never imports the reference generator or reads reference circuits.
Seeds are reproducibility inputs for the verifier, not agent-facing examples.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import itertools
import random


def ports(inputs, outputs):
    return [dict(name=n, width=w, direction=d) for d, ps in
            (("input", inputs), ("output", outputs)) for n, w in ps]


INTERFACES = {
    "alu8": ports([("A", 8), ("B", 8), ("OP", 3)], [("Y", 8), ("C", 1), ("Z", 1)]),
    "register_file": ports([("CLK", 1), ("RST", 1), ("WE", 1), ("WA", 3),
                            ("WD", 8), ("RA", 3), ("RB", 3)], [("QA", 8), ("QB", 8)]),
    "sorting_network": ports([(f"X{i}", 8) for i in range(8)], [(f"Y{i}", 8) for i in range(8)]),
    "cpu8": ports([("CLK", 1), ("RST", 1), ("INST", 16), ("MA", 4)],
                  [("PC", 8), *[(f"R{i}", 8) for i in range(4)], ("HALTED", 1), ("MD", 8)]),
}


def alu(a, b, op):
    if op == 0:
        full = a + b
        y, c = full & 255, int(full > 255)
    elif op == 1:
        y, c = (a - b) & 255, int(a < b)
    elif op == 2:
        y, c = a & b, 0
    elif op == 3:
        y, c = a | b, 0
    elif op == 4:
        y, c = a ^ b, 0
    elif op == 5:
        y, c = a ^ 255, 0
    elif op == 6:
        y, c = (a << 1) & 255, a >> 7
    elif op == 7:
        y, c = a >> 1, a & 1
    else:
        raise ValueError("unknown ALU operation")
    return {"Y": y, "C": c, "Z": int(y == 0)}


def combinational_case(name, vectors):
    """Hold each value for one extra sample in a persistent simulation."""
    initial = dict(vectors[0][0])
    previous, events, expected = dict(initial), [], []
    for index, (inputs, outputs) in enumerate(vectors):
        tick = 2 * index
        for signal, value in inputs.items():
            if previous[signal] != value:
                events.append(dict(tick=tick, input=signal, value=value))
        previous = dict(inputs)
        expected.extend([dict(tick=tick, values=outputs), dict(tick=tick + 1, values=outputs)])
    return dict(name=name, ticks=len(vectors) * 2 - 1, inputs=initial, events=events), expected


def alu_cases(seed):
    rng = random.Random(seed)
    boundaries = [0, 1, 2, 15, 16, 127, 128, 254, 255]
    for op, name in enumerate(("add-carry", "subtract-borrow", "and", "or", "xor", "not", "shift-left", "shift-right")):
        pairs = list(itertools.product(boundaries, repeat=2))
        pairs += [(a, (a * 73 + 19) & 255) for a in range(256)]
        pairs += [(rng.randrange(256), rng.randrange(256)) for _ in range(64)]
        yield combinational_case(name, [(dict(A=a, B=b, OP=op), alu(a, b, op)) for a, b in pairs])
    yield combinational_case("operation-transitions", [(dict(A=173, B=91, OP=op), alu(173, 91, op))
                                                       for _ in range(8) for op in rng.sample(range(8), 8)])


def sorting_cases(seed):
    rng = random.Random(seed)
    groups = {
        "all-binary-patterns": list(itertools.product((0, 255), repeat=8)),
        "unsigned-extremes-and-duplicates": [[0]*8, [255]*8, [128]*8,
            [255, 0, 128, 127, 1, 254, 128, 0], list(range(8)), list(reversed(range(8)))],
        "permutations": [rng.sample([0, 1, 2, 63, 127, 128, 254, 255], 8) for _ in range(128)],
        "random-multisets": [[rng.randrange(256) for _ in range(8)] for _ in range(128)],
    }
    for name, values in groups.items():
        vectors = [(dict(zip((f"X{i}" for i in range(8)), xs)),
                    dict(zip((f"Y{i}" for i in range(8)), sorted(xs)))) for xs in values]
        yield combinational_case(name, vectors)


class Stimulus:
    """One changed input per sample for sequential timing checks."""
    def __init__(self, name, inputs):
        self.name, self.inputs, self.levels = name, dict(inputs), dict(inputs)
        self.events, self.tick = [], 0

    def set(self, **changes):
        for name, value in changes.items():
            if self.levels[name] != value:
                self.tick += 1
                self.events.append(dict(tick=self.tick, input=name, value=value))
                self.levels[name] = value
        return self

    def hold(self, ticks=1):
        self.tick += ticks
        return self

    def pulse(self):
        return self.set(CLK=0).hold().set(CLK=1).hold().set(CLK=0).hold()

    def scenario(self):
        return dict(name=self.name, ticks=self.tick, inputs=self.inputs, events=self.events)


def register_expectations(scenario):
    registers, expected = None, []
    levels = dict(scenario["inputs"])
    events = {e["tick"]: e for e in scenario["events"]}
    if len(events) != len(scenario["events"]):
        raise ValueError("ambiguous sequential stimulus")
    for tick in range(scenario["ticks"] + 1):
        before = levels["CLK"]
        if tick in events:
            e = events[tick]
            levels[e["input"]] = e["value"]
        if before == 0 and levels["CLK"] == 1:
            if levels["RST"]:
                registers = [0] * 8
            elif registers is not None and levels["WE"]:
                registers[levels["WA"]] = levels["WD"]
        if registers is not None:
            expected.append(dict(tick=tick, values=dict(QA=registers[levels["RA"]], QB=registers[levels["RB"]])))
    if registers is None:
        raise ValueError("reset edge required")
    return expected


def register_cases(seed):
    rng = random.Random(seed)
    s = Stimulus("write-read-address-isolation", dict(CLK=0, RST=1, WE=0, WA=0, WD=0, RA=0, RB=0))
    s.pulse().set(RST=0)
    # All eight registers are writable, including R0. Verify same-address reads.
    for addr in range(8):
        s.set(WE=1, WA=addr, WD=(addr * 37 + 19) & 255, RA=addr, RB=addr).pulse()
    s.set(WE=0)
    for a, b in itertools.product(range(8), repeat=2):
        s.set(RA=a, RB=b).hold()
    for _ in range(96):
        a = rng.randrange(8)
        s.set(WE=rng.randrange(2), WA=a, WD=rng.randrange(256), RA=a, RB=rng.randrange(8)).pulse()
        # Changing write data/address while CLK is high must not write again.
        s.set(CLK=1).set(WD=rng.randrange(256), WA=rng.randrange(8)).hold(2).set(CLK=0)
    scenario = s.scenario()
    yield scenario, register_expectations(scenario)
    s = Stimulus("synchronous-reset-priority-and-hold", dict(CLK=0, RST=1, WE=0, WA=0, WD=0, RA=0, RB=7))
    s.pulse().set(RST=0)
    for addr in range(8):
        s.set(WE=1, WA=addr, WD=255-addr).pulse()
    # Reset pulses without an edge must preserve state; reset wins over write.
    s.set(RST=1).hold(3).set(RST=0).hold().set(RA=3, RB=6).hold()
    s.set(RST=1, WA=3, WD=91).pulse().set(RST=0, WE=0)
    for addr in range(8):
        s.set(RA=addr, RB=7-addr).pulse()
    scenario = s.scenario()
    yield scenario, register_expectations(scenario)


# Compact-8 ISA: fields [15:12] opcode, [11:10] destination,
# [9:8] source, [7:0] immediate. All four registers are ordinary writable state.
OPCODES = {"NOP": 0, "LDI": 1, "MOV": 2, "ADD": 3, "SUB": 4,
           "XOR": 5, "AND": 6, "OR": 7, "SHL": 8, "SHR": 9,
           "LD": 10, "ST": 11, "BZ": 12, "BNZ": 13, "JMP": 14, "HALT": 15}


def encode(op, d=0, s=0, imm=0):
    if op not in OPCODES or not 0 <= d < 4 or not 0 <= s < 4 or not 0 <= imm < 256:
        raise ValueError("invalid instruction")
    return (OPCODES[op] << 12) | (d << 10) | (s << 8) | imm


@dataclass
class CPU:
    pc: int = 0
    registers: list[int] = field(default_factory=lambda: [0]*4)
    memory: list[int] = field(default_factory=lambda: [0]*16)
    halted: bool = False

    def step(self, word):
        if self.halted:
            return
        op, d, src, immediate = word >> 12, (word >> 10) & 3, (word >> 8) & 3, word & 255
        a, b = self.registers[d], self.registers[src]
        next_pc = (self.pc + 1) & 255
        if op == 1: self.registers[d] = immediate
        elif op == 2: self.registers[d] = b
        elif op == 3: self.registers[d] = (a + b) & 255
        elif op == 4: self.registers[d] = (a - b) & 255
        elif op == 5: self.registers[d] = a ^ b
        elif op == 6: self.registers[d] = a & b
        elif op == 7: self.registers[d] = a | b
        elif op == 8: self.registers[d] = (a << 1) & 255
        elif op == 9: self.registers[d] = a >> 1
        elif op == 10: self.registers[d] = self.memory[immediate & 15]
        elif op == 11: self.memory[immediate & 15] = a
        elif op == 12 and a == 0: next_pc = immediate
        elif op == 13 and a != 0: next_pc = immediate
        elif op == 14: next_pc = immediate
        elif op == 15:
            self.halted, next_pc = True, self.pc
        self.pc = next_pc

    def outputs(self, ma):
        return dict(PC=self.pc, HALTED=int(self.halted), MD=self.memory[ma],
                    **{f"R{i}": v for i, v in enumerate(self.registers)})


def cpu_programs(seed):
    """Fresh synthetic programs, independent from circuit implementation."""
    rng = random.Random(seed)
    e = encode
    operations = ("MOV", "ADD", "SUB", "XOR", "AND", "OR", "SHL", "SHR")
    for group, ops in enumerate((operations[:3], operations[3:6], operations[6:])):
        arithmetic = []
        for op in ops:
            for d, src in itertools.product(range(4), repeat=2):
                # Re-seed before each operation: avoid a chain degenerating to zero.
                a = (255, 128, 1, 85)[d]
                b = (1, 255, 129, 170)[src]
                arithmetic.append(e("LDI", d=d, imm=a))
                if src != d:
                    arithmetic.append(e("LDI", d=src, imm=b))
                arithmetic.append(e(op, d=d, s=src, imm=rng.randrange(256)))
        arithmetic.append(e("HALT", d=3, s=2, imm=171))
        yield f"alu-dependencies-and-aliases-{group}", arithmetic, 160
    memory = []
    for addr in range(16):
        memory += [e("LDI", d=addr % 4, imm=(addr * 61 + 37) & 255), e("ST", d=addr % 4, imm=addr | 0xA0)]
    memory += [e("LD", d=addr % 4, imm=addr | 0x50) for addr in reversed(range(16))]
    memory += [e("LDI", d=2, imm=219), e("ST", d=2, imm=7), e("LD", d=0, imm=7), e("HALT")]
    yield "memory-all-addresses-and-overwrite", memory, 80
    # Sum 1..9, loop-carried dependencies, both outcomes of each branch.
    loop = [e("LDI", d=0, imm=9), e("LDI", d=1, imm=1), e("LDI", d=2, imm=0),
            e("ADD", d=2, s=0), e("SUB", d=0, s=1), e("BNZ", d=0, imm=3),
            e("BZ", d=2, imm=10), e("BZ", d=0, imm=9), e("LDI", d=2, imm=255),
            e("ST", d=2, imm=15), e("HALT")]
    yield "branches-loop-and-side-effects", loop, 64
    wrap = [e("NOP")] * 256
    wrap[0], wrap[1] = e("BNZ", d=0, imm=4), e("JMP", imm=254)
    wrap[254], wrap[255], wrap[4] = e("LDI", d=0, imm=173), e("NOP"), e("HALT")
    yield "pc-wrap-and-jump", wrap, 16
    yield "same-pc-repeated-execution", [e("JMP", imm=0)], 12
    # A halted processor must ignore subsequent changing instructions until reset.
    yield "halt-stability", [e("LDI", d=1, imm=197), e("ST", d=1, imm=3), e("HALT")], 12
    for group in range(3):
        program = [e("LDI", d=i, imm=rng.randrange(256)) for i in range(4)]
        for _ in range(72):
            op = rng.choice(["LDI", "MOV", "ADD", "SUB", "XOR", "AND", "OR", "SHL", "SHR", "ST", "LD", "NOP"])
            program.append(e(op, d=rng.randrange(4), s=rng.randrange(4), imm=rng.randrange(256)))
        program.append(e("HALT"))
        yield f"seeded-program-{group}", program, 96


def cpu_expectations(scenario):
    cpu, expected = None, []
    levels = dict(scenario["inputs"])
    events = {e["tick"]: e for e in scenario["events"]}
    if len(events) != len(scenario["events"]):
        raise ValueError("ambiguous sequential stimulus")
    for tick in range(scenario["ticks"] + 1):
        old_clock = levels["CLK"]
        if tick in events:
            event = events[tick]
            levels[event["input"]] = event["value"]
        if old_clock == 0 and levels["CLK"] == 1:
            if levels["RST"]:
                cpu = CPU()
            elif cpu is not None:
                cpu.step(levels["INST"])
        if cpu is not None:
            expected.append(dict(tick=tick, values=cpu.outputs(levels["MA"])))
    if cpu is None:
        raise ValueError("reset edge required")
    return expected


def cpu_cases(seed):
    for name, program, budget in cpu_programs(seed):
        s = Stimulus(name, dict(CLK=0, RST=1, INST=0, MA=0))
        s.pulse().set(RST=0)
        cpu = CPU()
        for cycle in range(budget):
            word = program[cpu.pc] if cpu.pc < len(program) else encode("HALT")
            if cpu.halted:
                word = encode("LDI" if cycle % 2 else "ST", d=cycle % 4, imm=cycle & 255)
            s.set(INST=word, MA=cycle % 16).pulse()
            cpu.step(word)
            if cpu.halted and cycle >= len(program) + 3:
                break
        for addr in range(16):
            s.set(MA=addr).hold()
        # Check a pulse without a rising edge, then a real reset of nonzero state.
        s.set(RST=1).hold(3).set(RST=0).hold(2)
        s.set(RST=1, INST=encode("LDI", d=3, imm=222)).pulse().set(RST=0)
        for addr in range(16):
            s.set(MA=addr).hold()
        for word in (encode("LDI", d=3, imm=167), encode("ST", d=3, imm=12), encode("LD", d=0, imm=12), encode("HALT")):
            s.set(INST=word).pulse()
        scenario = s.scenario()
        yield scenario, cpu_expectations(scenario)


def cases(oracle):
    if oracle['kind'] == 'cpu8_interrupts':
        from circuit_bench.interrupt_oracle import cases as interrupt_cases
        return interrupt_cases(oracle['seed'])
    factories = {"alu8": alu_cases, "register_file": register_cases,
                 "sorting_network": sorting_cases, "cpu8": cpu_cases}
    return factories[oracle["kind"]](oracle["seed"])
