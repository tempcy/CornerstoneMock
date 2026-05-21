"""PyInstaller 入口：cornerstone-bridge"""
import sys

from cornerstone_bridge.server import main

if __name__ == "__main__":
    raise SystemExit(main())
