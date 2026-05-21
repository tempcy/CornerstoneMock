"""PyInstaller 入口：cornerstone-cli"""
import sys

from cornerstone_cli.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
