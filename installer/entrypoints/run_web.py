"""PyInstaller 入口：cornerstone-web"""
from cornerstone_cli.console_io import configure_stdio_utf8

configure_stdio_utf8()

from cornerstone_web.server import main

if __name__ == "__main__":
    raise SystemExit(main())
