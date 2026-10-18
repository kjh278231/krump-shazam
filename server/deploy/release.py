"""Deploys come only from main as pushed to origin (CLAUDE.md). No override.

stage.py and the aws_setup.py steps that change what runs (data, image, function) call these first:
  pushed_main()     this checkout is on main, its tracked files are committed, and HEAD is origin/main
                    after a fetch; else exit with the reasons
  require_staged()  that, and build/ was staged from that same commit (stage.py writes build/commit.txt)
A worktree can never be on main (the main checkout holds it), so deploys run from the main checkout.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAGED = ROOT / "build" / "commit.txt"


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=True).stdout.strip()


def pushed_main() -> str:
    """HEAD's commit if it is main as pushed to origin."""
    git("fetch", "--quiet", "origin", "main")
    problems = []
    if git("rev-parse", "--abbrev-ref", "HEAD") != "main":
        problems.append("not on branch main: deploy from the main checkout, not a worktree or another branch")
    if git("status", "--porcelain", "--untracked-files=no"):
        problems.append("tracked files have uncommitted changes: commit them to main and push first")
    head, remote = git("rev-parse", "HEAD"), git("rev-parse", "origin/main")
    if head != remote:
        problems.append(f"HEAD {head[:7]} is not origin/main {remote[:7]}: push main (or pull) first")
    if problems:
        sys.exit("Refusing to deploy (deploys come only from main as pushed to origin):\n  - " + "\n  - ".join(problems))
    return head


def require_staged() -> str:
    head = pushed_main()
    staged = STAGED.read_text().strip() if STAGED.exists() else ""
    if staged != head:
        sys.exit(f"Refusing to deploy: build/ was staged from {staged[:7] or 'an unknown commit'}, not "
                 f"origin/main {head[:7]}. Run server/deploy/stage.py again.")
    return head
