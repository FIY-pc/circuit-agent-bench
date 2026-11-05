"""Hand-derived interrupt transitions and program-coverage regression checks."""
import unittest

from circuit_bench.grading import initial_report, record_error
from circuit_bench.host import HostError
from circuit_bench.interrupt_oracle import InterruptCPU, EI, DI, IRET, setmask, cases
from circuit_bench.task_oracles import encode


class InterruptTests(unittest.TestCase):
    def test_pending_masks_and_old_enable_boundary(self):
        cpu = InterruptCPU()
        cpu.step(setmask(7))
        cpu.step(EI, 7)
        self.assertEqual((cpu.pc, cpu.ie, cpu.pending, cpu.ack), (2, 1, 7, 0))
        cpu.step(encode('ST', d=0, imm=0))
        self.assertEqual((cpu.pc, cpu.ie, cpu.pending, cpu.ack, cpu.level), (0xC0, 0, 3, 4, 3))
        self.assertEqual(cpu.frames, [(2, 1, 7, 0)])
        cpu.step(IRET)
        self.assertEqual((cpu.pc, cpu.ie, cpu.pending, cpu.level), (2, 1, 3, 0))
        cpu.step(DI)  # pending source 2 preempts DI before it disables interrupts
        self.assertEqual((cpu.pc, cpu.ie, cpu.ack), (0xA0, 0, 2))

    def test_precise_store_suppression_and_resume(self):
        cpu = InterruptCPU(pc=27, ie=1, mask=7, registers=[211, 0, 0, 0])
        store = encode('ST', d=0, imm=6)
        cpu.step(store, 1)
        self.assertEqual((cpu.memory[6], cpu.frames[0][0]), (0, 27))
        cpu.step(IRET)
        self.assertEqual(cpu.pc, 27)
        cpu.step(store)
        self.assertEqual((cpu.memory[6], cpu.pc), (211, 28))

    def test_three_frames_restore_mask_level_enable_and_pc(self):
        cpu = InterruptCPU(pc=49, ie=1, mask=7)
        cpu.step(0, 1)
        cpu.step(EI)
        cpu.step(0, 2)
        cpu.step(setmask(6))
        cpu.step(EI)
        cpu.step(0, 4)
        self.assertEqual(cpu.frames, [(49, 1, 7, 0), (0x81, 1, 7, 1), (0xA2, 1, 6, 2)])
        cpu.step(setmask(4))
        cpu.step(IRET)
        self.assertEqual((cpu.pc, cpu.ie, cpu.mask, cpu.level, len(cpu.frames)), (0xA2, 1, 6, 2, 2))
        cpu.step(IRET)
        self.assertEqual((cpu.pc, cpu.ie, cpu.mask, cpu.level), (0x81, 1, 7, 1))
        cpu.step(IRET)
        self.assertEqual((cpu.pc, cpu.ie, cpu.mask, cpu.level, cpu.frames), (49, 1, 7, 0, []))

    def test_current_priority_cannot_reenter_and_pending_coalesces(self):
        cpu = InterruptCPU(pc=7, ie=1, mask=7)
        cpu.step(0, 4)
        cpu.step(EI, 4)
        for _ in range(4):
            cpu.step(0, 4)
            self.assertEqual((cpu.level, len(cpu.frames), cpu.pending, cpu.ack), (3, 1, 4, 0))
        cpu.step(IRET, 0)
        self.assertEqual(cpu.pending, 4)
        cpu.step(0)
        self.assertEqual((cpu.ack, cpu.pending, len(cpu.frames)), (4, 0, 1))

    def test_interrupt_preempts_return_and_halt_wrap(self):
        cpu = InterruptCPU(pc=8, ie=1, mask=7)
        cpu.step(0, 1)
        cpu.step(EI)
        cpu.step(IRET, 4)
        self.assertEqual((cpu.pc, cpu.frames[-1][0], cpu.frames[-1][3]), (0xC0, 0x81, 1))
        cpu.step(IRET)
        self.assertEqual((cpu.pc, len(cpu.frames)), (0x81, 1))
        cpu.step(IRET)
        self.assertEqual((cpu.pc, len(cpu.frames)), (8, 0))
        cpu = InterruptCPU(pc=255, ie=1, mask=7, halted=True)
        cpu.step(encode('LDI', imm=255), 2)
        self.assertEqual((cpu.frames[0][0], cpu.halted, cpu.registers[0]), (0, False, 0))
        cpu.step(IRET)
        self.assertEqual(cpu.pc, 0)

    def test_programs_reach_deep_nesting_and_restore_before_reset(self):
        all_cases = dict((s['name'], (s, expected)) for s, expected in cases(938105))
        scenario, expected = all_cases['three-level-nesting-and-frame-restoration']
        values = [point['values'] for point in expected]
        self.assertEqual(max(v['DEPTH'] for v in values), 3)
        self.assertTrue(any(v['LEVEL'] == 2 and v['IE'] == 1 and v['MASK'] == 6 and v['PC'] == 0xA3 for v in values))
        self.assertTrue(any(v['HALTED'] and v['R0'] == 11 and v['R1'] == 22 and v['R2'] == 33 for v in values))
        for scenario, expected in all_cases.values():
            self.assertLessEqual(scenario['ticks'], 10000)
            self.assertLessEqual(len(scenario['events']), 6000)
            self.assertEqual(len({e['tick'] for e in scenario['events']}), len(scenario['events']))
            self.assertEqual(len(expected[-1]['values']), 14)
            self.assertEqual(expected[-1]['values']['DEPTH'], 0)
        self.assertNotEqual(list(cases(1)), list(cases(2)))


class LoadFailureTests(unittest.TestCase):
    def test_only_document_codes_or_exact_loader_evidence_classified(self):
        invalid = [
            ('INVALID_DOCUMENT', 'malformed component'),
            ('INVALID_INPUT', '电路 "main" 的 ROM @ (50,50) 的 contents 同时包含 val 属性和文本；原生格式应删除 val 属性并保留文本内容。'),
            ('DEPENDENCY_CHANGED', '缺少组件库：missing.circ。需与电路文件放在同一目录。'),
            ('DEPENDENCY_CHANGED', 'Logisim 无法载入这个文件：bad component（电路 "main" 加载后覆盖不完整：comp 1/2，wire 0/0）'),
        ]
        for code, message in invalid:
            result = record_error(initial_report(), 'interface', HostError('observe', dict(code=code, message=message)))
            self.assertEqual((result['status'], result['passed']), ('invalid_artifact', False))
        for code, message in [
            ('RUNTIME_UNAVAILABLE', 'missing Java runtime'), ('PROCESS_EXIT', 'compiler exited'),
            ('DEPENDENCY_CHANGED', 'runtime jar changed'), ('UNKNOWN', 'compiler error'),
            ('UNKNOWN', 'Cannot invoke MemContents because contents is null'),
            ('IO', 'No such file'), ('INTERNAL', 'native failure'), ('INVALID_INPUT', 'bad verifier request'),
        ]:
            result = record_error(initial_report(), 'interface', HostError('observe', dict(code=code, message=message)))
            self.assertEqual((result['status'], result['passed']), ('error', None))
        result = record_error(initial_report(), 'host_start', HostError('open', dict(code='INVALID_DOCUMENT', message='startup failure')))
        self.assertEqual((result['status'], result['passed']), ('error', None))


if __name__ == '__main__':
    unittest.main()
