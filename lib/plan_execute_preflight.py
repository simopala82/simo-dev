#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""The deterministic facts simo-dev:plan-execute step 1 needs, in one call.

Usage:
    plan_execute_preflight.py <plan-path> --repo <worktree-abs> [--base <base>]
    plan_execute_preflight.py --repo <worktree-abs> [--base <base>]

The second form (no plan: docs/canonical-flow.md "Ad-hoc edit check") prints only the two
LEDGER_BASE lines. Prints, in this order:
    LEDGER_BASE_FALLBACK: no origin/<base>   only when --base names a branch with no origin/ ref
    LEDGER_BASE=<branch>                     empty when nothing resolves
    steps: <n>                               checkbox lines (`- [ ]`, any indentation)
    batches: 1 2 | 3 | 4                     detect_independent_task_groups.py's batches, or `none`
    depends-on: <KEY> | none | unparsed: <text>   `Depends on:` bold or plain, any case, colon in or out of the bold; a value starting `none` is none
    model: <plan_execute model> | inherit caller's tier
    execution: direct|agent-dispatch?|subagent — <why>

<ledger-base> is --base when origin/<base> exists, else origin/HEAD's branch, else origin/main, else
origin/master. `agent-dispatch?` leaves to the caller the two checks no script can make reliably:
whether the batched tasks are substantial and whether their **Interfaces:** blocks link them.
Exit 2 when the plan file does not exist; 0 otherwise (an invalid model-strategy config is a stderr
note and the inherit label, never a non-zero exit).
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import detect_independent_task_groups as groups  # noqa: E402
import model_strategy  # noqa: E402

DIRECT_MAX_STEPS = 12
CHECKBOX_RE = re.compile(r"^[ \t\r\f\v]*- \[ \]")
DEPENDS_RE = re.compile(r"^[ \t]*(?:[-*>][ \t]+)?(?:\*\*)?Depends on(?:\*\*)?:(?:\*\*)?[ \t]*(.*)$",
                        re.MULTILINE | re.IGNORECASE)
NONE_RE = re.compile(r"^none\b", re.IGNORECASE)
KEY_RE = re.compile(r"^`?([A-Z][A-Z0-9_]+-[0-9]+)\b")


def origin_ref(repo: str, branch: str) -> bool:
    return subprocess.run(["git", "-C", repo, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{branch}"],
                          capture_output=True).returncode == 0


def ledger_base(repo: str, base: str) -> tuple[str, str | None]:
    fallback = None
    if base and not origin_ref(repo, base):
        fallback = f"no origin/{base}"
        base = ""
    if base:
        return base, fallback
    head = subprocess.run(["git", "-C", repo, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
                          capture_output=True, text=True)
    if head.returncode == 0 and head.stdout.strip():
        return head.stdout.strip().removeprefix("origin/"), fallback
    for cand in ("main", "master"):
        if origin_ref(repo, cand):
            return cand, fallback
    return "", fallback


def count_steps(text: str) -> int:
    return sum(1 for line in text.split("\n") if CHECKBOX_RE.match(line))


def plan_header(text: str) -> str:
    """Text before the first `## ` / `### Task` heading, fenced blocks dropped."""
    out, fenced = [], False
    for line in text.split("\n"):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if not fenced and (line.startswith("## ") or line.startswith("### Task")):
            break
        if not fenced:
            out.append(line)
    return "\n".join(out)


def depends_on(text: str) -> str:
    m = DEPENDS_RE.search(plan_header(text))
    if not m:
        return "none"
    value = m.group(1).strip()
    if NONE_RE.match(value):
        return "none"
    key = KEY_RE.match(value)
    return key.group(1) if key else f"unparsed: {value[:120]}"


def decide(steps: int, batches: list[list[int]]) -> str:
    if steps <= DIRECT_MAX_STEPS:
        return f"direct — {steps} steps ≤ {DIRECT_MAX_STEPS}"
    wide = [b for b in batches if len(b) >= 2]
    if wide:
        names = ", ".join(" ".join(map(str, b)) for b in wide)
        return f"agent-dispatch? — {steps} steps, file-disjoint batch(es): {names}"
    return f"subagent — {steps} steps > {DIRECT_MAX_STEPS}, no file-disjoint batch"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("plan", nargs="?", default="")
    p.add_argument("--repo", required=True)
    p.add_argument("--base", default="")
    args = p.parse_args(argv)

    lb, fallback = ledger_base(args.repo, args.base.strip())
    if fallback:
        print(f"LEDGER_BASE_FALLBACK: {fallback}")
    print(f"LEDGER_BASE={lb}")
    if not args.plan:
        return 0

    plan = Path(args.plan)
    if not plan.is_file():
        print(f"plan_execute_preflight: {plan} not found", file=sys.stderr)
        return 2
    text = plan.read_text()

    tasks = groups.parse_task_files(text)
    batches = groups.compute_batches(tasks) if tasks else []
    steps = count_steps(text)
    try:
        model = model_strategy.get_value("plan_execute", model_strategy.config_path())
    except (SystemExit, AttributeError, TypeError, ValueError, OSError):  # bad JSON exits; non-object JSON or non-UTF-8 raises
        print("plan_execute_preflight: model-strategy config unreadable, model left to the caller's tier",
              file=sys.stderr)
        model = ""

    print(f"steps: {steps}")
    print("batches: " + (" | ".join(" ".join(map(str, b)) for b in batches) if batches else "none"))
    print(f"depends-on: {depends_on(text)}")
    print("model: " + (model or "inherit caller's tier"))
    print(f"execution: {decide(steps, batches)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
