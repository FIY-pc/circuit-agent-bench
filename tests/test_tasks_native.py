"""Opt-in real-native regression, one reused host and at most one worker JVM.

Set VIBE_BENCH_NATIVE=1 and VIBE_BENCH_TREE, plus optional VIBE_BENCH_HOST.
Run serially under the documented memory/timeout scope. No models are called.
"""
import json
import os
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from circuit_bench.grading import Grader
from circuit_bench.reference_circuits import GENERATORS, VARIANTS, alu8, sorting_network

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.environ.get('VIBE_BENCH_NATIVE') == '1', 'set VIBE_BENCH_NATIVE=1 to run native circuits')
class NativeTaskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.grader = Grader(os.environ['VIBE_BENCH_TREE']).__enter__()
        cls.reports = []

    @classmethod
    def tearDownClass(cls):
        cls.grader.__exit__(None, None, None)
        if path := os.environ.get('VIBE_BENCH_NATIVE_REPORT'):
            Path(path).write_text(json.dumps(cls.reports, indent=2) + '\n')

    def grade(self, task, artifact, variant):
        report = self.grader.grade(ROOT/'tasks'/task, artifact)
        self.reports.append(dict(task=task, variant=variant, report=report))
        self.assertNotEqual(report['status'], 'error', str(report.get('error')))
        return report

    def test_references_and_wrong_circuits(self):
        for task in ('full-adder', 'counter4', *GENERATORS):
            with self.subTest(task=task, variant='reference'):
                result = self.grade(task, ROOT/'tasks'/task/'tests/reference.circ', 'reference')
                self.assertIs(result['passed'], True, result.get('function'))
            for variant in VARIANTS.get(task, ()):
                with self.subTest(task=task, variant=variant), tempfile.TemporaryDirectory() as temp:
                    artifact = Path(temp)/'design.circ'
                    GENERATORS[task](variant).write(artifact)
                    result = self.grade(task, artifact, variant)
                    self.assertEqual(result['status'], 'failed')
                    self.assertIs(result['passed'], False)

    def test_known_native_loader_refusals_are_invalid_artifacts(self):
        for defect, reason in [('unknown-component', 'native_component_loss'),
                               ('missing-library', 'missing_dependency'),
                               ('diagonal-wire', 'invalid_wire_geometry'),
                               ('conflicting-contents', 'invalid_memory_contents')]:
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as temp:
                c = alu8()
                if defect == 'unknown-component':
                    c.component(1, 'Nonexistent Gate', 50, 50)
                elif defect == 'missing-library':
                    ET.SubElement(c.root, 'lib', name='99', desc='file#missing-dependency.circ')
                elif defect == 'diagonal-wire':
                    ET.SubElement(c.main, 'wire', {'from': '(10,10)', 'to': '(20,20)'})
                else:
                    memory = c.component(4, 'ROM', 50, 50)
                    ET.SubElement(memory, 'a', name='contents', val='').text = 'addr/data: 8 8\n0'
                artifact = Path(temp)/'design.circ'
                c.write(artifact)
                result = self.grade('alu8', artifact, defect)
                self.assertEqual((result['status'], result['passed'], result.get('reason')),
                                 ('invalid_artifact', False, reason))

    def test_different_sorting_topology_is_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            artifact = Path(temp)/'design.circ'
            sorting_network('bitonic').write(artifact)
            result = self.grade('sorting-network', artifact, 'alternate-bitonic')
            self.assertIs(result['passed'], True, result.get('function'))

    def test_native_missing_unknown_and_oscillation(self):
        for defect in ('missing-output', 'unknown-output', 'oscillating-output'):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as temp:
                c = alu8()
                pin = next(p for p in c.main.findall('comp') if p.get('name') == 'Pin'
                           and p.find("a[@name='label']").get('val') == 'Y')
                if defect == 'missing-output':
                    c.main.remove(pin)
                else:
                    tunnel = next(p for p in c.main.findall('comp') if p.get('name') == 'Tunnel' and p.get('loc') == pin.get('loc'))
                    floating = c.net(8, 'floating_output')
                    tunnel.find("a[@name='label']").set('val', floating)
                    if defect == 'oscillating-output':
                        # A=0 settles the loop at zero, then A bit 0 enables inversion.
                        # Starting from defined state produces a real native oscillation.
                        inverted = c.gate('NOT', floating)
                        enable = c.split('A')[0]
                        driver = c.mux(c.const(0, 8), inverted, enable)
                        for element in c.main.findall("comp[@name='Tunnel']/a[@name='label']"):
                            if element.get('val') == driver:
                                element.set('val', floating)
                artifact = Path(temp)/'design.circ'
                c.write(artifact)
                result = self.grade('alu8', artifact, defect)
                self.assertEqual(result['status'], 'failed')
                if defect == 'missing-output':
                    self.assertEqual(result['interface']['failures'][0]['kind'], 'missing_port')
                else:
                    failures = [f for group in result['function']['groups'] for f in group['failures']]
                    required = 'unknown_value' if defect == 'unknown-output' else 'oscillating_or_unknown_trace_state'
                    self.assertTrue(any(f['kind'] == required for f in failures), failures[:3])


if __name__ == '__main__':
    unittest.main()
