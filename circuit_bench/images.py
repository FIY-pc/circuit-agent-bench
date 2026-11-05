"""Build an image using an explicitly selected integration."""
import argparse
import importlib
import sys
from .adapters import BUILTINS


def main():
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument('--adapter', choices=BUILTINS, required=True)
    if '--adapter' not in sys.argv and any(x in sys.argv for x in ('-h', '--help')):
        parser.print_help()
        print('Use --adapter NAME --help for integration-specific build options.')
        return
    args, rest = parser.parse_known_args()
    module = importlib.import_module(BUILTINS[args.adapter] + '.images')
    sys.argv = [sys.argv[0], *rest]
    return module.main()


if __name__ == '__main__':
    main()
