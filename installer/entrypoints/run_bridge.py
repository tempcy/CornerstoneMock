"""PyInstaller 入口：cornerstone-bridge"""
import sys
import traceback

from cornerstone_cli.console_io import configure_stdio_utf8

configure_stdio_utf8()

from cornerstone_bridge.server import main

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc(file=sys.stderr)
        raise
