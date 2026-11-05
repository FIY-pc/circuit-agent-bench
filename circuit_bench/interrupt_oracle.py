"""Compact-8 interrupt semantics and hidden programs, independent of circuits.

Interrupt entry is a complete architectural step: no instruction commits on that
edge. A Python frame stack defines return state; no hardware implementation is
assumed. CPU implements the independently specified base ISA.
"""
from dataclasses import dataclass, field
import random

from circuit_bench.task_oracles import CPU, Stimulus, cpu_cases, encode, ports

INTERFACE = ports(
    [('CLK', 1), ('RST', 1), ('INST', 16), ('MA', 4), ('IRQ', 3)],
    [('PC', 8), *[(f'R{i}', 8) for i in range(4)], ('HALTED', 1), ('MD', 8),
     ('IE', 1), ('MASK', 3), ('PENDING', 3), ('LEVEL', 2), ('DEPTH', 2), ('EPC', 8), ('ACK', 3)])
EI, DI, IRET = 0x0001, 0x0002, 0x0003


def setmask(value):
    if not 0 <= value <= 7:
        raise ValueError('interrupt mask must fit three bits')
    return 0x0010 | value


@dataclass
class InterruptCPU(CPU):
    ie: int = 0
    mask: int = 0
    pending: int = 0
    level: int = 0
    ack: int = 0
    frames: list[tuple[int, int, int, int]] = field(default_factory=list)

    def step(self, word, irq=0):
        self.pending |= irq
        self.ack = 0
        # Priority is selected from the old architectural enable/mask/level.
        eligible = [i for i in range(3) if self.ie and self.mask & (1 << i)
                    and self.pending & (1 << i) and i + 1 > self.level]
        if eligible:
            source = max(eligible)
            resume = ((self.pc + 1) & 255) if self.halted else self.pc
            self.frames.append((resume, self.ie, self.mask, self.level))
            assert len(self.frames) <= 3
            self.pending &= ~(1 << source)
            self.ack = 1 << source
            self.level, self.ie = source + 1, 0
            self.pc, self.halted = 0x80 + 0x20 * source, False
            return
        if self.halted:
            return
        if word == IRET and self.frames:
            self.pc, self.ie, self.mask, self.level = self.frames.pop()
        elif word == EI:
            self.ie, self.pc = 1, (self.pc + 1) & 255
        elif word == DI:
            self.ie, self.pc = 0, (self.pc + 1) & 255
        elif 0x0010 <= word <= 0x0017:
            self.mask, self.pc = word & 7, (self.pc + 1) & 255
        else:
            super().step(word)

    def outputs(self, ma):
        return dict(super().outputs(ma), IE=self.ie, MASK=self.mask, PENDING=self.pending,
                    LEVEL=self.level, DEPTH=len(self.frames), EPC=self.frames[-1][0] if self.frames else 0,
                    ACK=self.ack)


def expectations(scenario):
    levels, cpu, expected = dict(scenario['inputs']), None, []
    events = {e['tick']: e for e in scenario['events']}
    if len(events) != len(scenario['events']):
        raise ValueError('ambiguous sequential stimulus')
    for tick in range(scenario['ticks'] + 1):
        before = levels['CLK']
        if tick in events:
            event = events[tick]
            levels[event['input']] = event['value']
        if before == 0 and levels['CLK'] == 1:
            if levels['RST']:
                cpu = InterruptCPU()
            elif cpu is not None:
                cpu.step(levels['INST'], levels['IRQ'])
        if cpu is not None:
            expected.append(dict(tick=tick, values=cpu.outputs(levels['MA'])))
    if cpu is None:
        raise ValueError('reset edge required')
    return expected


def program(main, handlers=None):
    memory = {i: word for i, word in enumerate(main)}
    for source, words in enumerate(handlers or [[IRET]] * 3):
        memory.update({0x80 + source * 0x20 + i: word for i, word in enumerate(words)})
    return memory


def run_case(name, image, budget, schedule, *, reset_nested=False):
    """Fetch from a program image using an ISA interpreter, then replay raw inputs.

    schedule(cycle, architectural_state) returns IRQ levels sampled that edge.
    Expectation replay uses the raw events, independently from this fetch plan.
    """
    s = Stimulus(name, dict(CLK=0, RST=1, INST=0, MA=0, IRQ=0))
    s.pulse().set(RST=0)
    cpu = InterruptCPU()
    for cycle in range(budget):
        irq = schedule(cycle, cpu)
        word = image.get(cpu.pc, encode('HALT'))
        s.set(INST=word, IRQ=irq, MA=cycle % 16).pulse()
        cpu.step(word, irq)
        if reset_nested and len(cpu.frames) == 3:
            break
    # An IRQ pulse without an edge is not sampled, even when IE=1.
    s.set(IRQ=0).hold().set(IRQ=7).hold(3).set(IRQ=0).hold()
    for address in range(16):
        s.set(MA=address).hold()
    # Reset pulses alone preserve state. A reset edge dominates IRQ and IRET.
    s.set(RST=1).hold(2).set(RST=0).hold()
    s.set(RST=1, IRQ=7, INST=IRET).pulse().set(RST=0, IRQ=0)
    for address in range(16):
        s.set(MA=address).hold()
    # A newly installed restart program checks empty IRET and normal execution.
    for word in (IRET, encode('LDI', d=0, imm=93), encode('ST', d=0, imm=13),
                 encode('LD', d=2, imm=13), encode('HALT')):
        s.set(INST=word).pulse()
    scenario = s.scenario()
    return scenario, expectations(scenario)


def once_at(entries):
    """One pulse per (current level, PC, optional halted state) trigger."""
    fired = set()
    def schedule(cycle, cpu):
        for index, (level, pc, halted, irq) in enumerate(entries):
            if index not in fired and cpu.level == level and cpu.pc == pc and cpu.halted == halted:
                fired.add(index)
                return irq
        return 0
    return schedule


def cases(seed):
    # Retain every base-ISA program and timing test, with interrupts disabled.
    for scenario, _ in cpu_cases(seed):
        scenario = {**scenario, 'name': 'base-' + scenario['name'], 'inputs': {**scenario['inputs'], 'IRQ': 0},
                    'events': [dict(e) for e in scenario['events']]}
        for event in scenario['events']:
            if event['input'] == 'INST' and (event['value'] in (EI, DI, IRET) or 0x10 <= event['value'] <= 0x17):
                event['value'] = 0  # formerly NOP encodings reserved by the extension
        yield scenario, expectations(scenario)

    e = encode
    counting_handlers = [[e('ADD', d=i, s=3), e('ST', d=i, imm=i), IRET] for i in range(3)]
    image = program([e('LDI', d=3, imm=1), setmask(0), EI, 0, setmask(7), e('JMP', imm=5)], counting_handlers)
    yield run_case('masked-pulses-persist-and-highest-wins', image, 32, once_at([(0, 3, False, 7)]))
    # A request arriving on EI cannot preempt EI, but is eligible next edge.
    image = program([e('LDI', d=3, imm=1), setmask(7), EI, e('HALT')], counting_handlers)
    yield run_case('enable-boundary-and-disabled-pending', image, 18, once_at([(0, 2, False, 3)]))

    nested_handlers = [
        [e('LDI', d=0, imm=11), EI, e('ST', d=0, imm=1), IRET],
        [e('LDI', d=1, imm=22), setmask(6), EI, e('ST', d=1, imm=2), IRET],
        [setmask(4), e('LDI', d=2, imm=33), e('ST', d=2, imm=3), IRET],
    ]
    image = program([e('LDI', d=3, imm=1), setmask(7), EI, e('ST', d=3, imm=0), e('HALT')], nested_handlers)
    triggers = [(0, 3, False, 1), (1, 0x82, False, 2), (2, 0xA3, False, 4)]
    yield run_case('three-level-nesting-and-frame-restoration', image, 38, once_at(triggers))
    yield run_case('reset-during-deepest-handler', image, 38, once_at(triggers), reset_nested=True)

    # Each interrupted instruction must later execute exactly once after IRET.
    image = program([e('LDI', d=3, imm=1), setmask(7), EI, e('LDI', d=0, imm=9),
                     e('ST', d=0, imm=4), e('BZ', d=1, imm=8), e('ST', d=3, imm=10),
                     e('LDI', d=0, imm=222), e('JMP', imm=10), e('ST', d=3, imm=11),
                     DI, EI, e('HALT'), e('LDI', d=2, imm=91), e('ST', d=2, imm=9), e('HALT')],
                    [[e('ADD', d=2, s=3), e('ST', d=2, imm=6), IRET], [IRET], [IRET]])
    triggers = [(0, pc, False, 1) for pc in (3, 4, 5, 8, 10, 11)] + [(0, 12, True, 4)]
    yield run_case('precise-register-store-branch-disable-and-halt', image, 72, once_at(triggers))

    image = program([setmask(7), EI, e('HALT')], [[EI, IRET], [IRET], [IRET]])
    yield run_case('higher-request-preempts-return', image, 24,
                   once_at([(0, 2, False, 1), (1, 0x81, False, 4)]))
    image = program([e('LDI', d=3, imm=1), setmask(7), EI, e('JMP', imm=3)],
                    [[EI, 0, IRET], [EI, 0, IRET], [EI, 0, 0, IRET]])
    yield run_case('held-requests-coalesce-and-do-not-self-nest', image, 48,
                   lambda cycle, cpu: 7 if 3 <= cycle <= 16 else 0)

    image = program([setmask(7), EI, e('JMP', imm=255)])
    image[255] = e('HALT')
    yield run_case('halt-wakeup-wraps-saved-pc', image, 18, once_at([(0, 255, True, 2)]))
    image[255] = 0
    yield run_case('running-boundary-saves-pc-before-wrap', image, 18, once_at([(0, 255, False, 4)]))

    rng = random.Random(seed ^ 0x6C38)
    for group in range(2):
        main = [setmask(7), EI]
        for _ in range(30):
            main.append(e(rng.choice(('LDI', 'ADD', 'SUB', 'XOR', 'LD', 'ST', 'SHL', 'SHR')),
                          d=rng.randrange(4), s=rng.randrange(4), imm=rng.randrange(256)))
        main += [e('JMP', imm=2)]
        handlers = [[e('ST', d=i, imm=12+i), EI, e('ADD', d=i, s=(i+1)%4),
                     e('LD', d=i, imm=12+i), IRET] for i in range(3)]
        schedule = [rng.randrange(8) if rng.random() < 0.22 else 0 for _ in range(160)]
        yield run_case(f'seeded-interrupt-execution-{group}', program(main, handlers), len(schedule),
                       lambda cycle, cpu, schedule=schedule: schedule[cycle])
