"""Mathematical oracle and fail-closed grading checks; no native host required."""
import copy
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from circuit_bench.grading import check_trace
from circuit_bench import task_oracles as oracle
from circuit_bench.package import package

ROOT = Path(__file__).resolve().parents[1]


class OracleTests(unittest.TestCase):
    def test_alu_boundaries(self):
        for a in range(256):
            self.assertEqual(oracle.alu(a, a, 1), {'Y': 0, 'C': 0, 'Z': 1})
            self.assertEqual(oracle.alu(a, a ^ 255, 4)['Y'], 255)
        self.assertEqual(oracle.alu(255, 1, 0), {'Y': 0, 'C': 1, 'Z': 1})
        self.assertEqual(oracle.alu(0, 1, 1), {'Y': 255, 'C': 1, 'Z': 0})
        self.assertEqual(oracle.alu(129, 91, 7), {'Y': 64, 'C': 1, 'Z': 0})

    def test_cpu_public_example(self):
        cpu = oracle.CPU()
        program = [int(s, 16) for s in '1007 1405 3100 B003 1800 A803 F000'.split()]
        for _ in range(7):
            cpu.step(program[cpu.pc])
        self.assertEqual(cpu.registers, [12, 5, 12, 0])
        self.assertEqual(cpu.memory, [0, 0, 0, 12] + [0]*12)
        self.assertEqual((cpu.pc, cpu.halted), (6, True))
        cpu.step(0x10FF)
        self.assertEqual(cpu.registers[0], 12)

    def test_cpu_loop_and_wrap_known_results(self):
        programs = {name: (p, budget) for name, p, budget in oracle.cpu_programs(5)}
        cpu = oracle.CPU()
        program, budget = programs['branches-loop-and-side-effects']
        for _ in range(budget):
            cpu.step(program[cpu.pc])
        self.assertEqual((cpu.registers, cpu.memory[15], cpu.pc, cpu.halted), ([0, 1, 45, 0], 45, 10, True))
        cpu = oracle.CPU()
        program, budget = programs['pc-wrap-and-jump']
        seen = []
        for _ in range(6):
            seen.append(cpu.pc)
            cpu.step(program[cpu.pc])
        self.assertEqual(seen, [0, 1, 254, 255, 0, 4])
        self.assertEqual((cpu.pc, cpu.registers[0], cpu.halted), (4, 173, True))

    def test_sequential_hold_reset_priority(self):
        s = oracle.Stimulus('manual', dict(CLK=0, RST=1, INST=0, MA=0))
        s.pulse().set(RST=0, INST=0x10AB).pulse().set(RST=1).hold(2)
        partial = oracle.cpu_expectations(s.scenario())
        self.assertEqual(partial[-1]['values']['R0'], 171)
        s.set(INST=0x10FF).pulse()
        final = oracle.cpu_expectations(s.scenario())[-1]['values']
        self.assertEqual((final['PC'], final['R0']), (0, 0))

    def test_all_cases_valid_reproducible_and_bounded(self):
        for kind in oracle.INTERFACES:
            spec = {'kind': kind, 'seed': 74123}
            cases = list(oracle.cases(spec))
            self.assertEqual(cases, list(oracle.cases(spec)))
            inputs = {p['name'] for p in oracle.INTERFACES[kind] if p['direction'] == 'input'}
            outputs = {p['name'] for p in oracle.INTERFACES[kind] if p['direction'] == 'output'}
            for scenario, expected in cases:
                self.assertEqual(set(scenario['inputs']), inputs)
                self.assertLessEqual(scenario['ticks'], 10000)
                self.assertLessEqual(len(scenario['events']), 6000)
                self.assertTrue(expected)
                self.assertEqual(expected[-1]['tick'], scenario['ticks'])
                self.assertTrue(all(set(p['values']) == outputs for p in expected))
                self.assertTrue(all(1 <= e['tick'] <= scenario['ticks'] for e in scenario['events']))

    def test_seed_changes_hidden_stimulus(self):
        for kind in oracle.INTERFACES:
            self.assertNotEqual(list(oracle.cases(dict(kind=kind, seed=1))), list(oracle.cases(dict(kind=kind, seed=2))))

    def test_agent_package_contains_only_public_starting_files(self):
        for task in sorted((ROOT/'tasks').iterdir()):
            with self.subTest(task=task.name), tempfile.TemporaryDirectory() as temp:
                output = Path(temp)/'task'
                package(task, output, 'example-agent:fixed', 'example-verifier:fixed')
                envfiles = {str(p.relative_to(output/'environment')) for p in (output/'environment').rglob('*') if p.is_file()}
                self.assertEqual(envfiles, {'Dockerfile', 'task.json', 'input/design.circ'})
                blank = ET.parse(output/'environment/input/design.circ').getroot()
                self.assertEqual(len(blank.findall('.//comp')), 0)
                self.assertNotIn('seed', (output/'instruction.md').read_text().lower())
                self.assertNotIn('reference.circ', (output/'environment/Dockerfile').read_text())


class TraceTests(unittest.TestCase):
    def setUp(self):
        self.scenario = dict(name='tiny', ticks=1)
        self.expected = [dict(tick=i, values={'Q': 1}) for i in (0,1)]
        self.report = dict(completed=True, samples=[dict(tick=i, oscillating=False, signals={'Q': {'value': 1, 'bits': '0001'}}) for i in (0,1)])

    def check(self, report):
        return check_trace(report, self.scenario, self.expected)

    def test_good(self):
        self.assertEqual(self.check(self.report)['status'], 'passed')

    def test_missing_unknown_inconsistent_and_short(self):
        for kind in ('missing', 'unknown', 'inconsistent', 'short', 'duplicate', 'incomplete', 'oscillation'):
            with self.subTest(kind=kind):
                r = copy.deepcopy(self.report)
                if kind == 'missing': r['samples'][1]['signals'] = {}
                elif kind == 'unknown': r['samples'][1]['signals']['Q']['bits'] = 'xxxx'
                elif kind == 'inconsistent': r['samples'][1]['signals']['Q']['bits'] = '0010'
                elif kind == 'short': r['samples'].pop()
                elif kind == 'duplicate': r['samples'][1]['tick'] = 0
                elif kind == 'incomplete': r['completed'] = False
                elif kind == 'oscillation': r['samples'][0]['oscillating'] = True
                self.assertEqual(self.check(r)['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
