"""Command line for the audit distribution; training commands are not registered."""
import argparse


def main(argv=None):
    from . import audit_cli
    parser = argparse.ArgumentParser(prog='temperq')
    audit_cli.add_parser(parser.add_subparsers(dest='cmd', required=True))
    return audit_cli.main(parser.parse_args(argv))


if __name__ == '__main__':
    raise SystemExit(main())
