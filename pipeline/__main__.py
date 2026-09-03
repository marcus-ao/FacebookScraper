"""`python -m pipeline` 的入口。真正的子命令在 :mod:`pipeline.cli`。"""
import sys

from pipeline.cli import main

if __name__ == "__main__":
    sys.exit(main())
