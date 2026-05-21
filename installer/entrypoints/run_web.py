"""PyInstaller 入口：cornerstone-web"""
import sys

from cornerstone_web.server import main

if __name__ == "__main__":
    raise SystemExit(main())
