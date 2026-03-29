# scripts/__main__.py
"""daemon 启动入口：python -m scripts"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.clawsocial_daemon import _main
import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    _main(args.workspace, args.port)
