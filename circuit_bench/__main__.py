"""Command dispatch. Use `python -m circuit_bench COMMAND --help`."""
import importlib
import sys
COMMANDS={'preflight':'preflight','image':'images','package':'package','experiment':'experiment','grade':'grading',
          'visual':'visual','review':'review','report':'report'}
if len(sys.argv)<2 or sys.argv[1] not in COMMANDS:
    print('Commands: '+', '.join(COMMANDS));raise SystemExit(0 if len(sys.argv)<2 or sys.argv[1] in ('-h','--help') else 2)
command=sys.argv.pop(1)
module=importlib.import_module('circuit_bench.'+COMMANDS[command])
if hasattr(module,'main'):
    raise SystemExit(module.main())
import runpy
runpy.run_module(module.__name__,run_name='__main__')
