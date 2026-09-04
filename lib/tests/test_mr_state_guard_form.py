"""Guards the shape of every `check_mr_state.py` call site in the plugin tree.

The helper's contract is "prints exactly one line" on every exit code (its module
docstring). A caller that reads the rc alone therefore cannot tell a real answer from
no answer, and two of the three call sites took `git push` on rc 0 AND on rc 3 -- so an
empty `$MR_STATE` arriving on either pushed blind, which is the orphan-branch push of
incident 2026-07-17. `!136` fixed the intake site; the two push guards followed.

Four mechanical assertions per call site, no classification:
  1. the invocation goes through `python3 "$G"`, the form the other three assume;
  2. `G` is bound before the first `"$G"` dereference -- shell state dies between Bash
     calls, and an unbound `G` makes `python3 ""` exit 1 with empty stdout (measured
     2026-08-22, CPython 3.14: it resolves the empty string to the cwd and reports
     "can't find '__main__' module"; rc 2 is the *missing path* case), which is exactly
     the arrival assertion 4 exists to catch;
  3. the `-f "$G"` existence test precedes the `python3 "$G"` invocation;
  4. the `[ -z "$MR_STATE" ]` arrival test precedes the first `$MR_STATE_RC`
     dereference.

Assertion 4 is the one that closes the fail-open. Assertion 3 is diagnosability -- rc 2
already routes a missing script correctly, but conflates it with a bad token, and the
operator action differs. It is pinned here because a behavioral harness cannot pin it:
deleting the `[ -f "$G" ]` branch leaves such a harness fully green, since `python3
<missing>` exits 2 with empty stdout and assertion 4's own test then catches it
(mutation-tested 2026-08-22).

Assertion 1 exists because the selector must NOT key on the canonical form. An earlier
version selected regions containing `python3 "$G"`, which is one of the properties being
asserted -- so a new call site that inlines the plugin root in the invocation instead of
binding G, with a rc-0/rc-3 push and no arrival test, was simply not selected and no
assertion saw it (constructed and confirmed 2026-08-22). The selector now keys on the
helper's name plus any `python3`, and a non-conforming invocation is a failure rather than
an exclusion.

(That pre-MR spelling is not written out here on purpose: `test_plugin_root_form.py` scans
this file as one region and flags any dereference of the root variable that this file does
not bind -- quoting the bad form would trip it.)

Position, not reachability: every assertion compares indices in the region text. A test
placed textually before the rc dispatch but inside a branch that never runs -- an arrival
test rewritten `if false && [ -z "$MR_STATE" ]` -- still passes (constructed 2026-08-22).
Behaviour is what the harness in the MR that added this file measures; this file pins shape.

What the scan wrongly sees, as well as what it misses: a documentation fence that spells a
full invocation out is selected and judged like a call site. That is deliberate -- a reader
copies such a block -- but it means a doc example must be written in the canonical form or
moved out of a fence.

Scope, observed and not closed: `.md` fenced blocks and `.sh` files. `.py` is excluded
because no Python file in the tree calls the helper as a subprocess -- the six that name
it are the helper, its unit test, this file, `check_dependency_ticket.py` (a comment) and
two `fetch_*.py`. A call site written in Python, or in a `.md` file outside a fence, would
not be seen. The vacuity guard below is what stops that from turning into a silent pass: a
scan that matches nothing fails.

The region model is the one `test_plugin_root_form.py` uses and the reason is the same --
a fenced block is what a shell actually runs, so `G` bound in one block is gone by the
next. Indices are compared over the region's joined text, so a binding and a use on one
physical line are ordered correctly.
"""
import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent.parent

GUARD = "check_mr_state.py"
FENCE = re.compile(r"^\s*```(\w*)")
SCANNED_SUFFIXES = {".md", ".sh"}
SKIPPED_DIRS = {"__pycache__", ".pytest_cache"}

BINDS_G = re.compile(r'^\s*G=', re.M)
USES_G = '"$G"'
TESTS_G_EXISTS = '-f "$G"'
INVOKES_G = 'python3 "$G"'
TESTS_ARRIVAL = '[ -z "$MR_STATE" ]'
USES_RC = "$MR_STATE_RC"

# Selecting on the helper's NAME plus a bare `python3` was too loose: a documentation fence
# that merely mentions both — "the guard lives at lib/<helper> and is run with python3" — was
# reported as a non-conforming call site (constructed and confirmed 2026-08-22). This wants an
# actual invocation: `python3` followed by the variable, or by a path ending in the helper.
# Still not a judgment about intent — a doc block that spells a full invocation out is selected,
# and should be, since a reader copies it.
RUNS_GUARD = re.compile(
    r'python3\s+"?(\$\{?G\}?|[^\s"]*' + re.escape(GUARD) + r')'
)

# Every call site must be reachable by this many assertions; three sites exist today
# (`skills/commit/SKILL.md` step 5, and `skills/mr-followup/SKILL.md` twice -- the intake
# state-check and the push guard in the iteration loop). A drop below three means a site
# was renamed, deleted or moved out of scope; update this number deliberately, do not
# lower it to make a red test green.
MIN_CALL_SITES = 3


def _scanned_files():
    for path in sorted(PLUGIN_ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
            continue
        if SKIPPED_DIRS & set(path.parts):
            continue
        yield path


def _code_regions(path):
    """Yield (start_lineno, text) per code region: whole file for .sh, each fence for .md."""
    inside = path.suffix == ".sh"
    start, buf = 1, []
    for num, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
        if path.suffix == ".md" and FENCE.match(line):
            if inside and buf:
                yield start, "\n".join(buf)
            inside, start, buf = not inside, num + 1, []
            continue
        if inside:
            buf.append(line)
    if inside and buf:
        yield start, "\n".join(buf)


def _call_sites():
    """Regions that invoke the helper -- deliberately NOT keyed on the canonical
    `python3 "$G"`, which assertion 1 tests. See the module docstring."""
    for path in _scanned_files():
        for start, text in _code_regions(path):
            if GUARD in text and RUNS_GUARD.search(text):
                yield f"{path.relative_to(PLUGIN_ROOT)}:{start}", text


def test_the_scan_finds_the_known_call_sites():
    """Vacuity guard: every assertion below iterates the same set, so an empty set passes them all."""
    found = [name for name, _ in _call_sites()]
    assert len(found) >= MIN_CALL_SITES, (
        f"expected at least {MIN_CALL_SITES} {GUARD} call sites, found {len(found)}: {found}. "
        f"A site was renamed, deleted, or moved outside {sorted(SCANNED_SUFFIXES)} / outside a "
        f"fence -- where none of the assertions in this file can see it."
    )


def test_call_sites_invoke_the_guard_through_g():
    """The other three assertions are stated in terms of `$G`; a site that dereferences the
    plugin root inline satisfies none of them and must fail here rather than slip the scan."""
    offenders = [name for name, text in _call_sites() if INVOKES_G not in text]
    assert not offenders, (
        f"call site(s) run {GUARD} without the canonical `{INVOKES_G}` invocation, so the "
        "existence and arrival tests below cannot apply to them:\n  " + "\n  ".join(offenders)
    )


def test_call_sites_bind_g_before_using_it():
    offenders = []
    for name, text in _call_sites():
        bind = BINDS_G.search(text)
        use = text.find(USES_G)
        if use < 0:
            continue  # reported by the assertion above
        if bind is None or bind.start() > use:
            why = "never bound in this region" if bind is None else "bound only afterwards"
            offenders.append(f"{name} ({why})")
    assert not offenders, (
        "call site(s) dereference G without binding it first -- an unbound G runs "
        'python3 "" , which exits 1 with empty stdout:\n  ' + "\n  ".join(offenders)
    )


def test_call_sites_test_the_guard_exists_before_invoking_it():
    offenders = []
    for name, text in _call_sites():
        invoke = text.find(INVOKES_G)
        if invoke < 0:
            continue  # reported by the invocation assertion
        exists = text.find(TESTS_G_EXISTS)
        if exists < 0 or exists > invoke:
            why = "no -f test" if exists < 0 else "tested only afterwards"
            offenders.append(f"{name} ({why})")
    assert not offenders, (
        f"call site(s) invoke the guard without a preceding {TESTS_G_EXISTS} test -- rc 2 does "
        "stop them, but it conflates a missing script with a bad token and the operator action "
        "differs:\n  " + "\n  ".join(offenders)
    )


def test_call_sites_test_arrival_before_reading_the_rc():
    """The fail-open this file exists for: rc 0 and rc 3 are push branches at two of the sites."""
    offenders = []
    for name, text in _call_sites():
        arrival = text.find(TESTS_ARRIVAL)
        rc = text.find(USES_RC)
        if arrival < 0 or (rc >= 0 and arrival > rc):
            why = "no empty-output test" if arrival < 0 else "tested only after the rc dispatch"
            offenders.append(f"{name} ({why})")
    assert not offenders, (
        f"call site(s) read {USES_RC} before testing {TESTS_ARRIVAL}. The helper prints exactly "
        "one line on every code, so an empty answer means it never ran -- and a script truncated "
        "to valid syntax exits 0, which the rc-0 arm reads as a live, open MR:\n  "
        + "\n  ".join(offenders)
    )
