"""PyInstaller 入口：cornerstone-cli"""
from cornerstone_cli.console_io import configure_stdio_utf8

configure_stdio_utf8()

from cornerstone_cli.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
