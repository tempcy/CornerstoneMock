"""PyInstaller 入口：cornerstone-cli"""
<<<<<<< HEAD
from cornerstone_cli.console_io import configure_stdio_utf8

configure_stdio_utf8()
=======
import sys
>>>>>>> 3fa2e1c7c126607004b404060edf4d5e3dc3bd97

from cornerstone_cli.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
