"""Generate verifier regression fixtures from standard Logisim components.

Run: python -m circuit_bench.reference_circuits --output tasks
The generator is deliberately independent of task_oracles and its programs.
These are functional fixtures, not visual-quality targets or agent input.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import xml.etree.ElementTree as ET

LIBS = ('Wiring', 'Gates', 'Plexers', 'Arithmetic', 'Memory', 'I/O', 'Base')


class Circuit:
    def __init__(self):
        self.root = ET.Element('project', source='2.15.0.2.exe', version='1.0')
        for i, name in enumerate(LIBS):
            ET.SubElement(self.root, 'lib', desc='#' + name, name=str(i))
        ET.SubElement(self.root, 'main', name='main')
        self.main = ET.SubElement(self.root, 'circuit', name='main')
        self.count = self.serial = 0
        self.widths, self.constants = {}, {}

    def net(self, width=1, name=None):
        self.serial += 1
        name = name or f'n{self.serial}'
        if name in self.widths and self.widths[name] != width:
            raise ValueError('net width conflict: ' + name)
        self.widths[name] = width
        return name

    def place(self, lib, name, **attrs):
        # Each independent component occupies a cell; no crossing wires.
        i = self.count
        self.count += 1
        x, y = 400 + (i // 12) * 500, 200 + (i % 12) * 180
        self.component(lib, name, x, y, **attrs)
        return x, y

    def component(self, lib, name, x, y, **attrs):
        element = ET.SubElement(self.main, 'comp', lib=str(lib), name=name, loc=f'({x},{y})')
        for key, value in attrs.items():
            ET.SubElement(element, 'a', name=key, val=str(value).lower() if isinstance(value, bool) else str(value))
        return element

    def connect(self, x, y, net, output=False):
        self.component(0, 'Tunnel', x, y, label=net, width=self.widths[net], facing='west' if output else 'east')

    def pin(self, name, width, output=False, net=None):
        net = net or self.net(width, name)
        x, y = self.place(0, 'Pin', label=name, width=width, output=output,
                          facing='west' if output else 'east', tristate=False)
        self.connect(x, y, net, not output)
        return net

    def const(self, value, width=1):
        if (value, width) not in self.constants:
            net = self.net(width, f'k{width}_{value}')
            x, y = self.place(0, 'Constant', width=width, value=hex(value))
            self.connect(x, y, net, True)
            self.constants[value, width] = net
        return self.constants[value, width]

    def gate(self, kind, a, b=None):
        width = self.widths[a]
        out = self.net(width)
        x, y = self.place(1, kind + ' Gate', width=width, size=30, **({'inputs': 2} if b is not None else {}))
        self.connect(x, y, out, True)
        if b is None:
            self.connect(x-30, y, a)
        else:
            dx = 40 if kind in ('XOR', 'XNOR') else 30
            self.connect(x-dx, y-10, a)
            self.connect(x-dx, y+10, b)
        return out

    def combine(self, kind, signals):
        result = signals[0]
        for other in signals[1:]:
            result = self.gate(kind, result, other)
        return result

    def mux(self, a, b, sel):
        out = self.net(self.widths[a])
        x, y = self.place(2, 'Multiplexer', width=self.widths[a], select=1, enable=False)
        for dx, dy, net, output in ((-30,-10,a,False),(-30,10,b,False),(-20,20,sel,False),(0,0,out,True)):
            self.connect(x+dx, y+dy, net, output)
        return out

    def split(self, signal, sizes=None):
        sizes = sizes or [1] * self.widths[signal]
        assert sum(sizes) == self.widths[signal]
        attrs = {}
        bit = 0
        for group, width in enumerate(sizes):
            for _ in range(width):
                attrs['bit' + str(bit)] = group
                bit += 1
        x, y = self.place(0, 'Splitter', incoming=self.widths[signal], fanout=len(sizes), facing='east', appear='left', **attrs)
        self.connect(x, y, signal)
        outputs = [self.net(width) for width in sizes]
        for i, out in enumerate(outputs):
            self.connect(x+20, y-10*len(sizes)+10*i, out, True)
        return outputs

    def join(self, bits):
        out = self.net(len(bits))
        x, y = self.place(0, 'Splitter', incoming=len(bits), fanout=len(bits), facing='east', appear='left',
                          **{f'bit{i}': i for i in range(len(bits))})
        self.connect(x, y, out, True)
        for i, bit in enumerate(bits):
            self.connect(x+20, y-len(bits)*10+i*10, bit)
        return out

    def select(self, signals, selector):
        assert len(signals) == 1 << self.widths[selector]
        bits = self.split(selector) if self.widths[selector] > 1 else [selector]
        signals = list(signals)
        for bit in bits:
            signals = [self.mux(signals[i], signals[i+1], bit) for i in range(0, len(signals), 2)]
        return signals[0]

    def arithmetic(self, kind, a, b):
        width = self.widths[a]
        out, carry = self.net(width), self.net()
        zero = self.const(0)
        x, y = self.place(3, kind, width=width)
        for dx, dy, net, output in ((-40,-10,a,False),(-40,10,b,False),(-20,-20,zero,False),
                                    (0,0,out,True),(-20,20,carry,True)):
            self.connect(x+dx, y+dy, net, output)
        return out, carry

    def compare(self, a, b, mode='unsigned'):
        outputs = [self.net() for _ in range(3)]
        x, y = self.place(3, 'Comparator', width=self.widths[a], mode=mode)
        self.connect(x-40, y-10, a)
        self.connect(x-40, y+10, b)
        for dy, net in zip((-10, 0, 10), outputs):
            self.connect(x, y+dy, net, True)
        return outputs  # greater, equal, less

    def eq(self, signal, value):
        return self.compare(signal, self.const(value, self.widths[signal]))[1]

    def register(self, d, clock, reset, enable=None, out=None, variant=None):
        width = self.widths[d]
        zero, one = self.const(0), self.const(1)
        enable = enable or one
        out = out or self.net(width)
        if variant == 'async-reset':
            data, clear, en = d, reset, enable
        else:
            data = self.mux(d, self.const(0, width), reset)
            clear, en = zero, self.gate('OR', reset, enable)
        x, y = self.place(4, 'Register', width=width, trigger='falling' if variant == 'falling-edge' else 'rising', Select='high')
        for dx, dy, net, output in ((0,0,out,True),(-30,0,data,False),(-20,20,clock,False),
                                    (-10,20,clear,False),(-30,10,en,False),(-30,-10,one,False),(-10,-20,zero,False)):
            self.connect(x+dx, y+dy, net, output)
        return out

    def write(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        ET.indent(self.root)
        ET.ElementTree(self.root).write(path, encoding='utf-8', xml_declaration=True)


def alu8(variant=None):
    c = Circuit()
    a, b, op = c.pin('A', 8), c.pin('B', 8), c.pin('OP', 3)
    add, carry = c.arithmetic('Adder', a, b)
    sub, borrow = c.arithmetic('Adder' if variant == 'subtract-adds' else 'Subtractor', a, b)
    bits = c.split(a)
    left = c.join([c.const(0), *bits[:7]])
    right = c.join([*bits[1:], bits[-1] if variant == 'arithmetic-right' else c.const(0)])
    values = [add, sub, c.gate('AND', a, b), c.gate('OR', a, b), c.gate('XOR', a, b), c.gate('NOT', a), left, right]
    y = c.select(values, op)
    flags = [carry, c.const(0) if variant == 'no-borrow' else borrow, *[c.const(0)]*4, bits[7], bits[0]]
    c.pin('Y', 8, True, y)
    c.pin('C', 1, True, c.select(flags, op))
    c.pin('Z', 1, True, c.eq(y, 0))
    return c


def register_file(variant=None):
    c = Circuit()
    clk, rst, we = c.pin('CLK', 1), c.pin('RST', 1), c.pin('WE', 1)
    wa, wd, ra, rb = c.pin('WA', 3), c.pin('WD', 8), c.pin('RA', 3), c.pin('RB', 3)
    registers = []
    for addr in range(8):
        selected = c.eq(wa, addr)
        en = selected if variant == 'ignores-write-enable' else c.gate('AND', we, selected)
        if variant == 'zero-register' and addr == 0:
            en = c.const(0)
        registers.append(c.register(wd, clk, rst, en, variant=variant))
    c.pin('QA', 8, True, c.select(registers, ra))
    c.pin('QB', 8, True, c.select(registers, ra if variant == 'aliased-read-port' else rb))
    return c


def sorting_network(variant=None):
    c = Circuit()
    values = [c.pin(f'X{i}', 8) for i in range(8)]
    if variant == 'bitonic':
        # A second topology used to show the grader does not demand the reference.
        for size in (2, 4, 8):
            stride = size // 2
            while stride:
                result = list(values)
                for i in range(8):
                    j = i ^ stride
                    if j > i:
                        a, b = values[i], values[j]
                        cmp = c.compare(a, b)
                        swap = cmp[0 if (i & size) == 0 else 2]
                        result[i], result[j] = c.mux(a, b, swap), c.mux(b, a, swap)
                values = result
                stride //= 2
        for i, value in enumerate(values):
            c.pin(f'Y{i}', 8, True, value)
        return c
    # Eight odd-even transposition layers; an independently authored construction.
    for stage in range(8 if variant != 'missing-last-stage' else 7):
        result = list(values)
        for i in range(stage % 2, 7, 2):
            a, b = values[i:i+2]
            swap = c.compare(a, b, 'twosComplement' if variant == 'signed-order' else 'unsigned')[0]
            result[i], result[i+1] = c.mux(a, b, swap), c.mux(b, a, swap)
        values = result
    if variant == 'duplicate-output':
        values[3] = values[2]
    for i, value in enumerate(values):
        c.pin(f'Y{i}', 8, True, value)
    return c


def cpu8(variant=None, *, interrupts=False):
    c = Circuit()
    clk, rst, inst, ma = c.pin('CLK', 1), c.pin('RST', 1), c.pin('INST', 16), c.pin('MA', 4)
    immediate, source, dest, opcode = c.split(inst, [8, 2, 2, 4])
    address = c.split(immediate, [4, 4])[0]
    pc, halted = c.net(8, 'pc_state'), c.net(1, 'halt_state')
    if interrupts:
        from circuit_bench.interrupt_reference import controller
        control = controller(c, clk, rst, inst, pc, halted, variant)
    registers = [c.net(8, f'reg{i}') for i in range(4)]
    memory = [c.net(8, f'mem{i}') for i in range(16)]
    a, b = c.select(registers, dest), c.select(registers, source)
    load = c.select(memory, address)
    add = c.arithmetic('Adder', a, b)[0]
    sub = c.arithmetic('Adder' if variant == 'subtract-adds' else 'Subtractor', a, b)[0]
    bits = c.split(a)
    left, right = c.join([c.const(0), *bits[:7]]), c.join([*bits[1:], c.const(0)])
    zero8 = c.const(0, 8)
    values = [zero8, immediate, b, add, sub, c.gate('XOR', a, b), c.gate('AND', a, b),
              c.gate('OR', a, b), left, right, load, *[zero8]*5]
    result = c.select(values, opcode)
    decode = [c.eq(opcode, i) for i in range(16)]
    running = c.const(1) if variant == 'ignores-halt' else c.gate('NOT', halted)
    if interrupts and variant != 'entry-executes-instruction':
        running = c.gate('AND', running, c.gate('NOT', control['accept']))
    write = c.gate('AND', running, c.combine('OR', decode[1:11]))
    for i, out in enumerate(registers):
        enable = c.gate('AND', write, c.eq(dest, i))
        c.register(result, clk, rst, enable, out, variant)
    store = c.const(0) if variant == 'drops-stores' else c.gate('AND', running, decode[11])
    for i, out in enumerate(memory):
        c.register(a, clk, rst, c.gate('AND', store, c.eq(address, i)), out, variant)
    iszero = c.eq(a, 0)
    if variant == 'inverted-branches':
        iszero = c.gate('NOT', iszero)
    take = c.combine('OR', [decode[14], c.gate('AND', decode[12], iszero),
                            c.gate('AND', decode[13], c.gate('NOT', iszero))])
    increment = c.arithmetic('Adder', pc, c.const(1, 8))[0]
    nextpc = c.mux(increment, immediate, take)
    pc_enable = c.gate('AND', running, c.gate('NOT', decode[15]))
    if interrupts:
        nextpc = c.mux(nextpc, control['target'], control['redirect'])
        pc_enable = c.gate('OR', pc_enable, control['redirect'])
        halt_next = c.gate('OR', halted, c.gate('AND', running, decode[15]))
        if variant != 'halt-never-wakes':
            halt_next = c.mux(halt_next, c.const(0), control['accept'])
    c.register(nextpc, clk, rst, pc_enable, pc, variant)
    if not interrupts:
        halt_next = c.gate('OR', halted, decode[15])
    c.register(halt_next, clk, rst, out=halted, variant=variant)
    for name, value, width in [('PC', pc, 8), ('HALTED', halted, 1), *[(f'R{i}', value, 8) for i, value in enumerate(registers)],
                               ('MD', c.select(memory, ma), 8)]:
        c.pin(name, width, True, value)
    return c


def cpu8_interrupts(variant=None):
    return cpu8(variant, interrupts=True)


GENERATORS = {'cpu8-interrupts': cpu8_interrupts, 'alu8': alu8, 'register-file': register_file, 'sorting-network': sorting_network, 'cpu8': cpu8}
VARIANTS = {
    'cpu8-interrupts': ('low-priority-first', 'ignores-mask', 'drops-masked-requests',
        'no-nesting', 'epc-plus-one', 'single-saved-pc', 'mask-not-restored',
        'ie-not-restored', 'entry-executes-instruction', 'halt-never-wakes',
        'subtract-adds', 'drops-stores'),
    'alu8': ('subtract-adds', 'arithmetic-right', 'no-borrow'),
    'register-file': ('ignores-write-enable', 'zero-register', 'aliased-read-port', 'async-reset', 'falling-edge'),
    'sorting-network': ('missing-last-stage', 'signed-order', 'duplicate-output'),
    'cpu8': ('subtract-adds', 'drops-stores', 'inverted-branches', 'ignores-halt', 'async-reset', 'falling-edge'),
}


def generate(output):
    for task, generator in GENERATORS.items():
        generator().write(Path(output) / task / 'tests/reference.circ')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('tasks'))
    generate(parser.parse_args().output)
