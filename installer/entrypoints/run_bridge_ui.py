"""PyInstaller 入口：cornerstone-bridge-ui"""
import sys
import traceback

from cornerstone_bridge.ui.main import main

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc(file=sys.stderr)
        raise
