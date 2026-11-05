"""Interrupt controller circuit construction, independent of the ISA oracle.

The public implementation interface is architectural state, not this topology.
Twelve small state registers implement a three-frame interrupt stack here.
"""


def controller(c, clk, rst, inst, pc, halted, variant=None):
    irq = c.pin('IRQ', 3)
    pending, mask = c.net(3, 'pending_state'), c.net(3, 'mask_state')
    ie, level, depth = c.net(1, 'ie_state'), c.net(2, 'level_state'), c.net(2, 'depth_state')
    irq_bits, pending_bits, mask_bits = c.split(irq), c.split(pending), c.split(mask)
    zero, one = c.const(0), c.const(1)
    available, eligible = [], []
    for i in range(3):
        sampled = irq_bits[i]
        if variant == 'drops-masked-requests':
            sampled = c.combine('AND', [sampled, ie, mask_bits[i]])
        request = c.gate('OR', pending_bits[i], sampled)
        available.append(request)
        higher = c.eq(level, 0) if variant == 'no-nesting' else c.compare(level, c.const(i+1, 2))[2]
        eligible.append(c.combine('AND', [request, higher, ie, one if variant == 'ignores-mask' else mask_bits[i]]))
    order = range(3) if variant == 'low-priority-first' else reversed(range(3))
    selected, already = [None]*3, zero
    for i in order:
        selected[i] = c.gate('AND', eligible[i], c.gate('NOT', already))
        already = c.gate('OR', already, eligible[i])
    accept = c.combine('OR', selected)
    selected_level, vector = c.const(0, 2), c.const(0, 8)
    for i in range(3):
        selected_level = c.mux(selected_level, c.const(i+1, 2), selected[i])
        vector = c.mux(vector, c.const(0x80 + i*0x20, 8), selected[i])
    next_pending = c.join([c.gate('AND', bit, c.gate('NOT', taken)) for bit, taken in zip(available, selected)])
    c.register(next_pending, clk, rst, out=pending)
    ack = c.register(c.join(selected), clk, rst)

    running = c.combine('AND', [c.gate('NOT', halted), c.gate('NOT', accept)])
    is_ei, is_di, is_ret = [c.eq(inst, word) for word in (1, 2, 3)]
    low_mask, prefix = c.split(inst, [3, 13])
    set_mask = c.gate('AND', running, c.eq(prefix, 2))  # exactly words 0010..0017
    do_ei, do_di = c.gate('AND', running, is_ei), c.gate('AND', running, is_di)
    do_ret = c.combine('AND', [running, is_ret, c.gate('NOT', c.eq(depth, 0))])
    redirect = c.gate('OR', accept, do_ret)

    plus_one = c.arithmetic('Adder', pc, c.const(1, 8))[0]
    saved_pc = plus_one if variant == 'epc-plus-one' else c.mux(pc, plus_one, halted)
    frame_pc, frame_mask, frame_ie, frame_level = [], [], [], []
    for i in range(3):
        push = c.gate('AND', accept, c.eq(depth, i))
        frame_pc.append(c.register(saved_pc, clk, rst, accept if variant == 'single-saved-pc' else push))
        frame_mask.append(c.register(mask, clk, rst, push))
        frame_ie.append(c.register(ie, clk, rst, push))
        frame_level.append(c.register(level, clk, rst, push))
    top_pc = c.select([c.const(0, 8), *frame_pc], depth)
    top_mask = c.select([c.const(0, 3), *frame_mask], depth)
    top_ie = c.select([zero, *frame_ie], depth)
    top_level = c.select([c.const(0, 2), *frame_level], depth)

    inc = c.arithmetic('Adder', depth, c.const(1, 2))[0]
    dec = c.arithmetic('Subtractor', depth, c.const(1, 2))[0]
    c.register(c.mux(dec, inc, accept), clk, rst, redirect, depth)
    c.register(c.mux(top_level, selected_level, accept), clk, rst, redirect, level)
    next_ie = c.mux(ie, one, do_ei)
    next_ie = c.mux(next_ie, zero, do_di)
    if variant != 'ie-not-restored':
        next_ie = c.mux(next_ie, top_ie, do_ret)
    next_ie = c.mux(next_ie, zero, accept)
    c.register(next_ie, clk, rst, out=ie)
    next_mask = c.mux(mask, low_mask, set_mask)
    if variant != 'mask-not-restored':
        next_mask = c.mux(next_mask, top_mask, do_ret)
    c.register(next_mask, clk, rst, out=mask)
    for name, net, width in [('IE', ie, 1), ('MASK', mask, 3), ('PENDING', pending, 3),
                              ('LEVEL', level, 2), ('DEPTH', depth, 2), ('EPC', top_pc, 8), ('ACK', ack, 3)]:
        c.pin(name, width, True, net)
    return dict(accept=accept, redirect=redirect, target=c.mux(top_pc, vector, accept))
