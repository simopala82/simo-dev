#!/usr/bin/env python3
"""mr_presentation: prepare Agent-tool requests and assemble markdown for MR flows.

Two CLI surfaces:
- Default (no --assemble): print a JSON request {prompt, schema, agent_type, model}
  ready for the caller skill to dispatch via Agent tool.
- --assemble: assemble final markdown body from a presentation dict + context.

Stdlib only (CLAUDE.md preference). Python 3.10+.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent
SCHEMA_PATH = HERE / "schemas" / "mr_presentation.json"
PROMPT_CREATE = HERE / "prompts" / "mr_presentation_create.txt"
PROMPT_REVIEW = HERE / "prompts" / "mr_presentation_review.txt"

DIFF_TRUNCATE_AT = 50_000


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def load_prompt_template(mode: str) -> str:
    if mode not in ("create", "review"):
        raise ValueError(f"unknown mode: {mode!r}")
    path = PROMPT_CREATE if mode == "create" else PROMPT_REVIEW
    return path.read_text(encoding="utf-8")


def truncate_diff(diff: str) -> tuple[str, bool]:
    """Return (possibly-truncated diff, was_truncated bool)."""
    if len(diff) <= DIFF_TRUNCATE_AT:
        return diff, False
    return diff[:DIFF_TRUNCATE_AT] + "\n[...truncated, " + str(len(diff) - DIFF_TRUNCATE_AT) + " chars omitted...]\n", True


EXPECTED_HEADERS = {
    "flowchart": "flowchart",
    "before_after": "flowchart",
    "sequence": "sequenceDiagram",
    "state": "stateDiagram-v2",
}


def lint_mermaid(mermaid: str, diagram_type: str) -> list[str]:
    """Lightweight lexical sanity check on a mermaid diagram. Never raises.

    Returns human-readable problems; empty list means clean. Warning-only:
    the caller surfaces these, it does not rewrite or drop the diagram.
    """
    text = mermaid.strip()
    if not text:
        return ["empty diagram"]

    problems: list[str] = []

    first_line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    expected = EXPECTED_HEADERS.get(diagram_type)
    if expected and not first_line.startswith(expected):
        problems.append(
            f"header '{first_line[:40]}' does not match diagram_type '{diagram_type}' (expected '{expected}')"
        )

    if text.count('"') % 2 != 0:
        problems.append("unbalanced double-quotes")

    for open_ch, close_ch, name in (("[", "]", "square"), ("{", "}", "curly"), ("(", ")", "round")):
        if text.count(open_ch) != text.count(close_ch):
            problems.append(f"unbalanced {name} brackets")

    if "\\[" in text or "\\]" in text:
        problems.append("backslash-escaped brackets (\\[ or \\]) — backslash is not an escape character in mermaid")

    if re.search(r"&(lt|gt|amp|quot|#39);", text):
        problems.append(
            "HTML-escaped entities present (&lt; / &gt; / &amp; / &quot; / &#39;) — replace each with what mermaid "
            "reads: `#quot;` for an in-label quote, `#lt;` / `#gt;` for a literal angle bracket, a literal `<br/>` "
            "for a line break, the literal character otherwise"
        )

    if diagram_type in ("flowchart", "before_after"):
        # Match node labels: ID[ or ID{ followed by content not starting with "
        for m in re.finditer(r"[A-Za-z0-9_]+([\[{])([^\"}\]]{2,})([\]}])", text):
            inner = m.group(2).strip()
            if inner:
                problems.append(f"unquoted node label: {m.group(0)[:40]}")
                break

        # A quoted label ID["..."] followed by more text before the real
        # closer means a bare quote got nested inside it — e.g. an entity
        # like &quot; unescaped into a literal " that lands mid-label instead
        # of at its edge. `text.count('"') % 2` (above) stays even in this
        # case, and the "unquoted node label" loop above never sees it
        # because its content class excludes `"` outright — so both existing
        # checks pass this shape silently. Lexical only: this looks at the
        # first quoted span per label, not a real parse of the label's true
        # extent, so text after a nested quote is not inspected further.
        close_for = {"[": "]", "{": "}"}
        for m in re.finditer(r"[A-Za-z0-9_]+([\[{])\"([^\"]*)\"", text):
            close = close_for[m.group(1)]
            pos = m.end()
            if pos >= len(text) or text[pos] != close:
                problems.append(f"nested/unescaped quote in label: {m.group(0)[:40]}")
            for am in re.finditer(r"<[^<>\n]*>", m.group(2)):
                if not re.fullmatch(r"/?\s*br\s*/?", am.group(0)[1:-1], re.IGNORECASE):
                    problems.append(f"literal angle bracket in label (not <br/>): {am.group(0)[:40]}")

        # An unquoted label (ID[foo — no quote right after the opener) that
        # still contains a bare quote later on: e.g. ID[MRTMP="/tmp/x"]. The
        # existing "unquoted node label" check above requires >=2 chars with
        # NO quote to fire, so a body containing one never matches it either.
        for m in re.finditer(r"[A-Za-z0-9_]+([\[{])[^\[\]{}\"]+\"", text):
            problems.append(f"quote inside unquoted label: {m.group(0)[:40]}")

    return problems


REQUIRED_PRESENTATION_KEYS = ("impact_paragraph", "why_it_matters", "mermaid", "diagram_type")

# Quote entities in the forms an HTML escaper emits. `html.unescape` accepts
# further variants that also resolve to `"` (measured: `&QUOT;`, `&quot`,
# `&#034;`, `&#x022;`, `&#34`); none were produced by the escaping paths
# observed here.
MERMAID_QUOTE_ENTITY = re.compile(r"&(?:quot|#34|#x22|#X22);")


def unescape_mermaid(text: str) -> str:
    """HTML-unescape a mermaid diagram, routing quote entities inside a quoted span to mermaid's own escape.

    A `&quot;` here is ambiguous: a literal quote the agent wanted *inside* a
    quoted construct, or one of that construct's own delimiters, rewritten by an
    escaper along with everything else (`html.escape` escapes `"` by default).
    Usually the construct is a node label, but a quoted subgraph title,
    `participant … as "X"` and `click A "url"` all delimit the same way. What
    settles it is *where* the entity sits: inside a span still delimited by
    surviving bare quotes it is a literal quote, and becomes `#quot;`:
    mermaid's own escape, which `encodeEntities` in mermaid's
    `packages/mermaid/src/utils.ts` rewrites before parsing. Anywhere else it is
    left to `html.unescape`, which is what restores escaped delimiters, `--&gt;`
    and `&lt;br/&gt;` alike.

    Splitting on `"` makes that a test of location rather than a guess about the
    diagram as a whole, and the difference is not academic: three shapes were
    measured, and a whole-diagram test got two right and the third wrong. An
    in-label entity resolved to a bare `"` nests quotes inside an already-quoted
    label and stops mermaid parsing (observed 2026-08-20 on this repo's MR !135);
    an escaped delimiter rewritten to `#quot;` leaves the label unquoted while
    its body still holds brackets, which is how a diagram with one label escaped
    and another intact came out under the whole-diagram test -- worse than
    leaving it alone.

    Shapes it does not help, observed rather than exhaustive. Only *quote*
    entities are scoped this way, so an in-label `&lt;`/`&gt;` still resolves to
    a bare `<`/`>` wherever it sits: `A["parse &lt;T&gt; value"]` becomes
    `A["parse <T> value"]`, and `<T>` then reads as an HTML tag and disappears
    from the rendering. That one is deliberate -- separating `&lt;br/&gt;`, which
    wants the literal tag, from `&lt;T&gt;`, which wants `#lt;`/`#gt;`, needs a
    whitelist of intended tags, so the instruction surfaces carry the rule
    instead and this function does not guess. Then: an escaped in-label quote
    inside an escaped-delimiter label has no surviving quote to locate it by, and
    the same goes for one in a label that was never quoted at all, which comes
    out as bare quotes in an unquoted label. Those two come out as they did
    before this function existed -- this function does not repair them, and
    that has not changed. What has changed: `lint_mermaid` now flags all three
    shapes (a bare quote nested inside an already-quoted label, a bare quote
    inside a label that was never quoted, a literal angle bracket inside a
    label) as warnings, so a diagram carrying one of them no longer lints
    clean. Warning-only, same as everything else `lint_mermaid` does -- it
    does not rewrite or drop the diagram; repairing these three shapes is
    still this function's job, `lint_mermaid` only stops staying silent about
    them. An
    input with unbalanced bare quotes makes the
    trailing part read as "inside"; whether anything is reported afterwards
    depends on the balance of the *output*, which the rewrite can change.
    """
    parts = text.split('"')
    for i in range(1, len(parts), 2):
        parts[i] = MERMAID_QUOTE_ENTITY.sub("#quot;", parts[i])
    return html.unescape('"'.join(parts))


def unescape_strings(obj):
    """Recursively HTML-unescape every string in a JSON-like structure.

    Covers the case where the Agent tool's StructuredOutput call wasn't
    available and the agent returned its response as plain text instead —
    some models HTML-escape special characters (`-->` -> `--&gt;`) in that
    path, which breaks mermaid syntax. No-op on already-clean input.

    A `mermaid` key goes through `unescape_mermaid` instead: there a quote
    entity sitting inside a quoted span must not become a bare `"`.
    """
    if isinstance(obj, str):
        return html.unescape(obj)
    if isinstance(obj, list):
        return [unescape_strings(x) for x in obj]
    if isinstance(obj, dict):
        return {
            k: unescape_mermaid(v) if k == "mermaid" and isinstance(v, str) else unescape_strings(v)
            for k, v in obj.items()
        }
    return obj


def render_prompt(template: str, diff: str, context: dict) -> str:
    truncated, _was_truncated = truncate_diff(diff)
    substitutions = {
        "{ticket_summary}": context.get("ticket_summary", "(no ticket)"),
        "{commit_log}": context.get("commit_log", "(no commit log)"),
        "{file_stat}": context.get("file_stat", "(no stat)"),
        "{changed_files}": context.get("changed_files", "(no file list)"),
        "{branch_meta}": json.dumps(context.get("branch_meta", {})),
        "{jira_scope}": context.get("jira_scope", "") or "(no JIRA ticket linked)",
        "{diff_truncated}": truncated,
    }
    pattern = re.compile("|".join(re.escape(p) for p in substitutions))
    return pattern.sub(lambda m: substitutions[m.group(0)], template)


def prepare_request(diff: str, context: dict, mode: str) -> dict:
    if not diff.strip():
        raise ValueError("Empty diff, nothing to present")
    template = load_prompt_template(mode)
    return {
        "prompt": render_prompt(template, diff, context),
        "schema": load_schema(),
        "agent_type": "general-purpose",
        "model": os.environ.get("DEV_MR_PRES_MODEL", "sonnet"),
    }


def assemble_markdown(presentation: dict, context: dict, mode: str) -> str:
    """Assemble final markdown from presentation dict + context.

    mode=create -> MR description body (no findings section).
    mode=review -> the review skill's md file shell (Impact + legend; findings appended by caller).
    """
    lines: list[str] = []
    impact = presentation["impact_paragraph"]
    bullets = presentation["why_it_matters"]
    mermaid = presentation["mermaid"]

    lines.append("### Impact & value")
    lines.append("")
    lines.append(impact)
    lines.append("")
    if bullets:
        lines.append("Why it matters:")
        for b in bullets:
            lines.append(f"- {b}")
        lines.append("")
    lines.append("End-to-end at a glance:")
    lines.append("")
    lines.append("```mermaid")
    lines.append(mermaid)
    lines.append("```")
    lines.append("")

    if mode == "create" and (key_files := presentation.get("key_files")):
        lines.append("### Files to review")
        lines.append("")
        for kf in key_files:
            if not isinstance(kf, dict) or not kf.get("path"):
                continue
            lines.append(f"- `{kf['path']}` — {kf.get('role', '')}")
        lines.append("")

    if mode == "review":
        lines.append("---")
        lines.append("")
        lines.append("### Review legend")
        lines.append("- 🟢 resolved — no action")
        lines.append("- 🟡 mitigated / non-blocking — merge ok, follow-up advised")
        lines.append("- 🔴 still critical — should block merge")
        lines.append("")

    lint_problems = lint_mermaid(mermaid, presentation.get("diagram_type", "flowchart"))
    warnings = list(presentation.get("warnings", []))
    warnings += [f"⚠ mermaid: {p}" for p in lint_problems]
    if warnings:
        lines.append("---")
        lines.append("")
        lines.append("**Warnings raised while summarizing:**")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["create", "review"], required=False, default="create")
    p.add_argument("--diff-file", type=Path)
    p.add_argument("--context-file", type=Path)
    p.add_argument("--assemble", action="store_true")
    p.add_argument("--presentation-json", type=Path)
    p.add_argument("--normalize-presentation", type=Path)
    p.add_argument("--lint-mermaid", action="store_true")
    p.add_argument("--mermaid-file", type=Path)
    p.add_argument("--diagram-type", default="flowchart",
                   choices=["flowchart", "sequence", "before_after", "state"])
    args = p.parse_args()

    if args.lint_mermaid:
        if not args.mermaid_file:
            print("ERROR: --lint-mermaid needs --mermaid-file", file=sys.stderr)
            return 2
        try:
            mermaid = args.mermaid_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(f"ERROR: cannot read --mermaid-file {args.mermaid_file}: {e}", file=sys.stderr)
            return 2
        problems = lint_mermaid(mermaid, args.diagram_type)
        for problem in problems:
            print(problem)
        return 0

    if args.normalize_presentation:
        path = args.normalize_presentation
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            print(f"ERROR: cannot parse --normalize-presentation {path}: {e}", file=sys.stderr)
            return 1
        missing = [k for k in REQUIRED_PRESENTATION_KEYS if k not in data]
        if missing:
            print(f"ERROR: presentation missing required keys: {', '.join(missing)}", file=sys.stderr)
            return 1
        path.write_text(json.dumps(unescape_strings(data), indent=2), encoding="utf-8")
        return 0

    if args.assemble:
        if not args.presentation_json or not args.context_file:
            print("ERROR: --assemble needs --presentation-json and --context-file", file=sys.stderr)
            return 2
        presentation = json.loads(args.presentation_json.read_text(encoding="utf-8"))
        context = json.loads(args.context_file.read_text(encoding="utf-8"))
        markdown = assemble_markdown(presentation, context, args.mode)
        print(markdown)
        return 0

    if not args.diff_file or not args.context_file:
        print("ERROR: --diff-file and --context-file are required in prepare mode", file=sys.stderr)
        return 2

    # A raw git diff can carry non-UTF-8 bytes (a Latin-1 source file); it only feeds an LLM prompt.
    diff = args.diff_file.read_text(encoding="utf-8", errors="replace")
    context = json.loads(args.context_file.read_text(encoding="utf-8"))

    try:
        request = prepare_request(diff, context, args.mode)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    print(json.dumps(request, indent=2))
    return 0


if __name__ == "__main__":
    # A Windows pipe/redirect defaults to the ANSI code page (cp1252), which cannot encode
    # most non-ASCII text. stderr keeps backslashreplace: reconfigure() without errors= resets it to strict.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.exit(main())
