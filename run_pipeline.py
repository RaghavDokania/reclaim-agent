"""
Runs one full pass of the pipeline: diagnose -> decide -> act.

Safe to re-run repeatedly -- each stage only touches rows in the status
it cares about (needs_diagnosis / diagnosed / action_taken-and-eligible),
so re-running just picks up whatever has newly become eligible (e.g.
delayed retries whose next_action_at has now arrived, or payments that
bounced back from a failed action for another decide/act pass).

Run:
    python run_pipeline.py
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
STAGES = [
    ("diagnose", "run_diagnosis.py"),
    ("decide", "run_decisions.py"),
    ("act", "run_actions.py"),
]


def run_stage(folder: str, script: str):
    print(f"\n=== {folder}/{script} ===", flush=True)
    result = subprocess.run(
        [sys.executable, script],
        cwd=ROOT / folder,
    )
    if result.returncode != 0:
        print(f"!!! {folder}/{script} exited with code {result.returncode} -- stopping pipeline", flush=True)
        sys.exit(result.returncode)


def main():
    for folder, script in STAGES:
        run_stage(folder, script)


if __name__ == "__main__":
    main()
