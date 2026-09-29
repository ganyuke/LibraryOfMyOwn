"""Child-process entry point for one PDF build: python -m libmyown.pdf_runner job.json"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from libmyown.pdf import run_build_job


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m libmyown.pdf_runner JOB_JSON", file=sys.stderr)
        return 2
    job = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    run_build_job(job)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
