"""Tests for lib/mr_presentation.py."""
import html
import json
import subprocess
import sys
import importlib.util
from pathlib import Path

LIB = Path(__file__).parent.parent / "mr_presentation.py"
FIX = Path(__file__).parent / "fixtures"

spec = importlib.util.spec_from_file_location("mr_presentation", LIB)
mr_presentation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mr_presentation)
lint_mermaid = mr_presentation.lint_mermaid


def run_lib(args, stdin=None, env=None):
    """Invoke the lib via subprocess, return (rc, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, str(LIB)] + args,
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
    )
    return result.returncode, result.stdout, result.stderr


def test_prepare_create_mode_returns_valid_request():
    import os
    base = dict(os.environ)
    base.pop("DEV_MR_PRES_MODEL", None)
    rc, out, err = run_lib([
        "--mode", "create",
        "--diff-file", str(FIX / "simple_diff.txt"),
        "--context-file", str(FIX / "simple_ctx.json"),
    ], env=base)
    assert rc == 0, f"stderr: {err}"
    request = json.loads(out)
    assert set(request.keys()) >= {"prompt", "schema", "agent_type", "model"}
    assert "PROJ-1234" in request["prompt"]
    assert "goodbye" in request["prompt"]
    assert request["agent_type"] == "general-purpose"
    assert request["model"] == "sonnet"
    assert request["schema"]["type"] == "object"
    assert "impact_paragraph" in request["schema"]["required"]


def test_prepare_review_mode_uses_review_prompt():
    rc, out, err = run_lib([
        "--mode", "review",
        "--diff-file", str(FIX / "simple_diff.txt"),
        "--context-file", str(FIX / "simple_ctx.json"),
    ])
    assert rc == 0, f"stderr: {err}"
    request = json.loads(out)
    assert "neutrally" in request["prompt"] or "neutral description" in request["prompt"]


def test_load_prompt_template_rejects_unknown_mode():
    import pytest
    with pytest.raises(ValueError):
        mr_presentation.load_prompt_template("bogus")


def test_empty_diff_returns_error_rc():
    rc, out, err = run_lib([
        "--mode", "create",
        "--diff-file", "/dev/null",
        "--context-file", str(FIX / "simple_ctx.json"),
    ])
    assert rc != 0
    assert "empty" in err.lower()


def test_assemble_create_mode_produces_markdown_body():
    rc, out, err = run_lib([
        "--assemble",
        "--mode", "create",
        "--presentation-json", str(FIX / "simple_presentation.json"),
        "--context-file", str(FIX / "simple_ctx.json"),
    ])
    assert rc == 0, f"stderr: {err}"
    assert "### Impact & value" in out or "## Impact & value" in out
    assert "Why it matters:" in out
    assert "```mermaid" in out
    assert "flowchart LR" in out
    assert "world!" in out
    assert "PROJ-1234" in out
    assert "Review legend" not in out


def test_assemble_review_mode_includes_legend():
    rc, out, err = run_lib([
        "--assemble",
        "--mode", "review",
        "--presentation-json", str(FIX / "simple_presentation.json"),
        "--context-file", str(FIX / "simple_ctx.json"),
    ])
    assert rc == 0, f"stderr: {err}"
    assert "Review legend" in out
    assert "🟢" in out
    assert "🟡" in out
    assert "🔴" in out


def test_lint_clean_flowchart_has_no_problems():
    diagram = 'flowchart LR\n  A["start"] --> B["rates=[1,2,3]"]\n  classDef changed fill:#f96\n  B:::changed'
    assert lint_mermaid(diagram, "flowchart") == []


def test_lint_clean_sequence_has_no_problems():
    diagram = 'sequenceDiagram\n  A->>B: "POST /pay"\n  B-->>A: "200 OK"'
    assert lint_mermaid(diagram, "sequence") == []


def test_lint_clean_state_has_no_problems():
    diagram = 'stateDiagram-v2\n  [*] --> PENDING\n  PENDING --> CONFIRMED'
    assert lint_mermaid(diagram, "state") == []


def test_lint_flags_wrong_header_for_type():
    diagram = 'flowchart LR\n  A["x"] --> B["y"]'
    problems = lint_mermaid(diagram, "sequence")
    assert any("header" in p for p in problems)


def test_lint_flags_unbalanced_quotes():
    diagram = 'flowchart LR\n  A["start] --> B["end"]'
    assert any("quote" in p.lower() for p in lint_mermaid(diagram, "flowchart"))


def test_lint_flags_backslash_escaped_brackets():
    diagram = 'flowchart LR\n  A["rates=\\[1,2\\]"] --> B["ok"]'
    assert any("backslash" in p.lower() for p in lint_mermaid(diagram, "flowchart"))


def test_lint_flags_unquoted_node_label():
    diagram = 'flowchart LR\n  A[start here] --> B["ok"]'
    assert any("unquoted" in p.lower() for p in lint_mermaid(diagram, "flowchart"))


def test_lint_flags_nested_quote_in_quoted_label():
    diagram = 'flowchart TD\n  A["MRTMP="/tmp/x""]\n'
    problems = lint_mermaid(diagram, "flowchart")
    assert any("nested" in p.lower() and "quote" in p.lower() for p in problems)


def test_lint_flags_quote_inside_unquoted_label():
    diagram = 'flowchart TD\n  A[MRTMP="/tmp/x"]\n'
    problems = lint_mermaid(diagram, "flowchart")
    assert any("quote" in p.lower() and "unquoted" in p.lower() for p in problems)


def test_lint_flags_angle_bracket_in_quoted_label():
    diagram = 'flowchart TD\n  A["parse <T> value"]\n'
    problems = lint_mermaid(diagram, "flowchart")
    assert any("angle bracket" in p.lower() for p in problems)


def test_lint_allows_br_tag_in_quoted_label():
    diagram = 'flowchart TD\n  A["line1<br/>line2"]\n'
    assert lint_mermaid(diagram, "flowchart") == []


def test_lint_allows_ampersand_and_plain_text_in_quoted_label():
    diagram = 'flowchart TD\n  A["Intake & planning"]\n'
    assert lint_mermaid(diagram, "flowchart") == []


def test_lint_allows_mermaid_escaped_quote_in_label():
    diagram = 'flowchart TD\n  A["MRTMP=#quot;/tmp/x#quot;"]\n'
    assert lint_mermaid(diagram, "flowchart") == []


def test_lint_does_not_double_flag_decision_node_with_newlines():
    diagram = 'flowchart TD\n  DG{"Direction gate\ny / redirect / deep-dive\n(default: y)"}\n'
    assert lint_mermaid(diagram, "flowchart") == []


def test_lint_flags_the_originally_observed_broken_shape():
    # The exact shape produced by an agent following the &quot;-suggesting rule
    # this session found already fixed upstream (a6ea983a) — kept here as a
    # regression pin for the *new* checks, not a re-test of the entity fix.
    diagram = (
        'flowchart TD\n'
        '  B3["path resolves from "/"<br/>rc≠ 0 -> degraded MR body"]\n'
        '  A2["MRTMP="/tmp/claude_mr_<repo>_<branch>""]\n'
    )
    problems = lint_mermaid(diagram, "flowchart")
    assert problems, "the observed MR !135-style broken diagram must not lint clean"


def test_lint_empty_diagram():
    assert lint_mermaid("", "flowchart") == ["empty diagram"]


def test_schema_requires_diagram_type():
    rc, out, err = run_lib([
        "--mode", "create",
        "--diff-file", str(FIX / "simple_diff.txt"),
        "--context-file", str(FIX / "simple_ctx.json"),
    ])
    assert rc == 0, f"stderr: {err}"
    request = json.loads(out)
    assert "diagram_type" in request["schema"]["required"]
    assert set(request["schema"]["properties"]["diagram_type"]["enum"]) == {
        "flowchart", "sequence", "before_after", "state"
    }


def test_assemble_appends_mermaid_lint_warnings(tmp_path):
    bad = {
        "title": "feat(X): bad diagram",
        "impact_paragraph": "x" * 90,
        "why_it_matters": ["a", "b"],
        "diagram_type": "flowchart",
        "mermaid": 'flowchart LR\n  A[unquoted label] --> B["ok"]',
        "warnings": [],
    }
    pres = tmp_path / "bad.json"
    pres.write_text(json.dumps(bad))
    rc, out, err = run_lib([
        "--assemble", "--mode", "create",
        "--presentation-json", str(pres),
        "--context-file", str(FIX / "simple_ctx.json"),
    ])
    assert rc == 0, f"stderr: {err}"
    assert "⚠ mermaid:" in out
    assert "unquoted" in out.lower()


def test_create_prompt_includes_type_selection_guidance():
    rc, out, err = run_lib([
        "--mode", "create",
        "--diff-file", str(FIX / "simple_diff.txt"),
        "--context-file", str(FIX / "simple_ctx.json"),
    ])
    assert rc == 0, f"stderr: {err}"
    prompt = json.loads(out)["prompt"]
    assert "Choose the diagram type" in prompt
    assert "sequenceDiagram" in prompt
    assert "before_after" in prompt
    assert ":::changed" in prompt


def test_review_prompt_includes_type_selection_guidance():
    rc, out, err = run_lib([
        "--mode", "review",
        "--diff-file", str(FIX / "simple_diff.txt"),
        "--context-file", str(FIX / "simple_ctx.json"),
    ])
    assert rc == 0, f"stderr: {err}"
    prompt = json.loads(out)["prompt"]
    assert "Choose the diagram type" in prompt
    assert "neutral" in prompt.lower()


def test_lint_clean_before_after_has_no_problems():
    diagram = (
        'flowchart LR\n'
        '  subgraph "Before"\n    A["sync call"] --> B["DB"]\n  end\n'
        '  subgraph "After"\n    C["async call"]:::changed --> D["DB"]\n  end\n'
        '  classDef changed fill:#2563eb,stroke:#1d4ed8,color:#fff'
    )
    assert lint_mermaid(diagram, "before_after") == []


def test_assemble_merges_agent_and_lint_warnings(tmp_path):
    bad = {
        "title": "feat(X): bad diagram",
        "impact_paragraph": "x" * 90,
        "why_it_matters": ["a", "b"],
        "diagram_type": "flowchart",
        "mermaid": 'flowchart LR\n  A[unquoted] --> B["ok"]',
        "warnings": ["binary file not analyzed"],
    }
    pres = tmp_path / "both.json"
    pres.write_text(json.dumps(bad))
    rc, out, err = run_lib([
        "--assemble", "--mode", "create",
        "--presentation-json", str(pres),
        "--context-file", str(FIX / "simple_ctx.json"),
    ])
    assert rc == 0, f"stderr: {err}"
    assert "binary file not analyzed" in out
    assert "⚠ mermaid:" in out


def test_cli_lint_mermaid_clean_returns_empty(tmp_path):
    diagram = tmp_path / "d.mmd"
    diagram.write_text('flowchart LR\n  A["start"] --> B["rates=[1,2,3]"]:::problem')
    rc, out, err = run_lib(["--lint-mermaid", "--mermaid-file", str(diagram), "--diagram-type", "flowchart"])
    assert rc == 0
    assert out.strip() == ""


def test_cli_lint_mermaid_flags_problems(tmp_path):
    diagram = tmp_path / "d.mmd"
    diagram.write_text('flowchart LR\n  A[start here] --> B["ok"]')
    rc, out, err = run_lib(["--lint-mermaid", "--mermaid-file", str(diagram), "--diagram-type", "flowchart"])
    assert rc == 0
    assert "unquoted" in out.lower()


def test_cli_lint_mermaid_wrong_header_flagged(tmp_path):
    diagram = tmp_path / "d.mmd"
    diagram.write_text('flowchart LR\n  A["x"] --> B["y"]')
    rc, out, err = run_lib(["--lint-mermaid", "--mermaid-file", str(diagram), "--diagram-type", "sequence"])
    assert rc == 0
    assert "header" in out.lower()


def test_cli_lint_mermaid_missing_file_clean_error(tmp_path):
    missing = tmp_path / "does_not_exist.mmd"
    rc, out, err = run_lib(["--lint-mermaid", "--mermaid-file", str(missing), "--diagram-type", "flowchart"])
    assert rc == 2
    assert "cannot read" in err.lower()
    assert "Traceback" not in err


def test_review_prompt_substitutes_jira_scope():
    import os
    base = dict(os.environ)
    base.pop("DEV_MR_PRES_MODEL", None)
    rc, out, err = run_lib([
        "--mode", "review",
        "--diff-file", str(FIX / "simple_diff.txt"),
        "--context-file", str(FIX / "jira_scope_ctx.json"),
    ], env=base)
    assert rc == 0, f"stderr: {err}"
    request = json.loads(out)
    assert "Reject >5 req/s per IP." in request["prompt"]
    assert "{jira_scope}" not in request["prompt"]


def test_review_prompt_jira_scope_fallback_when_absent():
    import os
    base = dict(os.environ)
    base.pop("DEV_MR_PRES_MODEL", None)
    rc, out, err = run_lib([
        "--mode", "review",
        "--diff-file", str(FIX / "simple_diff.txt"),
        "--context-file", str(FIX / "simple_ctx.json"),
    ], env=base)
    assert rc == 0, f"stderr: {err}"
    request = json.loads(out)
    assert "(no JIRA ticket linked)" in request["prompt"]
    assert "{jira_scope}" not in request["prompt"]


def test_schema_allows_ticket_drift_fields():
    schema = mr_presentation.load_schema()
    props = schema["properties"]
    assert "ticket_drift" in props and props["ticket_drift"]["type"] == "boolean"
    assert "drift_note" in props and props["drift_note"]["type"] == "string"
    assert "ticket_drift" not in schema["required"]
    assert "drift_note" not in schema["required"]


def test_lint_flags_html_escaped_entities():
    diagram = 'flowchart LR\n  A["ok"] --&gt; B["fine &lt;br/&gt;"]'
    problems = lint_mermaid(diagram, "flowchart")
    assert any("html" in p.lower() or "entit" in p.lower() for p in problems)


def test_normalize_presentation_unescapes_html_entities(tmp_path):
    raw = {
        "title": "feat(X): thing",
        "impact_paragraph": "Uses A &amp; B.",
        "why_it_matters": ["safer &amp; faster"],
        "diagram_type": "state",
        "mermaid": 'stateDiagram-v2\n  PENDING --&gt; CONFIRMED: "ok &lt;br/&gt; done"',
        "warnings": [],
    }
    pres = tmp_path / "raw.json"
    pres.write_text(json.dumps(raw))
    rc, out, err = run_lib(["--normalize-presentation", str(pres)])
    assert rc == 0, f"stderr: {err}"
    normalized = json.loads(pres.read_text())
    assert normalized["mermaid"] == 'stateDiagram-v2\n  PENDING --> CONFIRMED: "ok <br/> done"'
    assert normalized["impact_paragraph"] == "Uses A & B."
    assert normalized["why_it_matters"] == ["safer & faster"]


def test_normalize_presentation_is_noop_on_clean_input(tmp_path):
    clean = {
        "title": "feat(X): thing",
        "impact_paragraph": "Uses A & B.",
        "why_it_matters": ["safer & faster"],
        "diagram_type": "flowchart",
        "mermaid": 'flowchart LR\n  A["x"] --> B["y"]',
        "warnings": [],
    }
    pres = tmp_path / "clean.json"
    pres.write_text(json.dumps(clean))
    rc, out, err = run_lib(["--normalize-presentation", str(pres)])
    assert rc == 0, f"stderr: {err}"
    assert json.loads(pres.read_text()) == clean


def test_normalize_presentation_maps_mermaid_quote_entity_to_mermaid_escape(tmp_path):
    raw = {
        "title": "fix(X): thing",
        "impact_paragraph": "Reads the &quot;staged&quot; key.",
        "why_it_matters": ["fewer &quot;broken&quot; bodies"],
        "diagram_type": "flowchart",
        "mermaid": 'flowchart LR\n  B1["staged = record.get(&quot;staged&quot;) or []"] --&gt; B2["ok &lt;br/&gt; done"]',
        "warnings": [],
    }
    pres = tmp_path / "quoted.json"
    pres.write_text(json.dumps(raw))
    rc, out, err = run_lib(["--normalize-presentation", str(pres)])
    assert rc == 0, f"stderr: {err}"
    mermaid = json.loads(pres.read_text())["mermaid"]

    assert "-->" in mermaid, "the arrow must still be repaired — that is why the unescape exists"
    assert "<br/>" in mermaid, "the line break must still be repaired"
    assert 'get("staged")' not in mermaid, "a bare quote nested inside a quoted label does not parse"
    assert 'get(#quot;staged#quot;)' in mermaid
    assert lint_mermaid(mermaid, "flowchart") == []


def test_normalize_presentation_still_repairs_a_fully_escaped_mermaid(tmp_path):
    """An escaper that ran over the whole diagram escapes the label delimiters too.

    `html.escape` escapes `"` by default, so those `&quot;` are delimiters, not
    in-label quotes: rewriting them to `#quot;` would leave an unquoted label
    holding `[ ] ( )`. Plain unescaping is what repairs this shape.
    """
    clean = 'flowchart LR\n  B1["staged = record.get(\'staged\') or []"] --> B2["ok <br/> done"]'
    raw = {
        "title": "fix(X): thing",
        "impact_paragraph": "x",
        "why_it_matters": [],
        "diagram_type": "flowchart",
        "mermaid": html.escape(clean),
        "warnings": [],
    }
    pres = tmp_path / "fully_escaped.json"
    pres.write_text(json.dumps(raw))
    rc, out, err = run_lib(["--normalize-presentation", str(pres)])
    assert rc == 0, f"stderr: {err}"
    mermaid = json.loads(pres.read_text())["mermaid"]

    assert mermaid == clean
    assert "#quot;" not in mermaid, "the escaped delimiters must come back as delimiters"
    assert lint_mermaid(mermaid, "flowchart") == []


def test_normalize_presentation_handles_a_partially_escaped_mermaid(tmp_path):
    """One label's delimiters escaped, another's intact.

    The surviving `"` around `y` must not license rewriting entities that sit
    outside every quoted span — those are `A`'s delimiters, and `#quot;` would
    leave an unquoted label holding `[ ]`.
    """
    clean = 'flowchart LR\n  A["x or []"] --> B["y"]'
    raw = {
        "title": "fix(X): thing",
        "impact_paragraph": "x",
        "why_it_matters": [],
        "diagram_type": "flowchart",
        "mermaid": 'flowchart LR\n  A[&quot;x or []&quot;] --> B["y"]',
        "warnings": [],
    }
    pres = tmp_path / "partial.json"
    pres.write_text(json.dumps(raw))
    rc, out, err = run_lib(["--normalize-presentation", str(pres)])
    assert rc == 0, f"stderr: {err}"
    mermaid = json.loads(pres.read_text())["mermaid"]

    assert mermaid == clean
    assert "#quot;" not in mermaid
    assert lint_mermaid(mermaid, "flowchart") == []


def test_unescape_mermaid_covers_every_quote_entity_form_it_matches():
    for form in ("&quot;", "&#34;", "&#x22;", "&#X22;"):
        src = f'flowchart LR\n  A["get({form}k{form})"] --&gt; B["y"]'
        out = mr_presentation.unescape_mermaid(src)
        assert 'get(#quot;k#quot;)' in out, form
        assert 'get("k")' not in out, form


def test_normalize_presentation_unescapes_quote_entities_outside_mermaid(tmp_path):
    """Prose fields keep plain unescaping — `&quot;` there becomes a real quote.

    Each value puts its entity *between* two surviving bare quotes, which is
    what makes this test discriminate: applying the `mermaid` rule to prose is
    invisible unless the entity falls inside a quoted span. An entity merely
    next to a quote sits in an even part and is left alone either way, so a
    fixture built that way pins nothing (measured against a mutation that
    routes every field through `unescape_mermaid`).
    """
    raw = {
        "title": "fix(X): thing",
        "impact_paragraph": 'The key is "record.get(&quot;staged&quot;)" in the fix.',
        "why_it_matters": ['drops the "&quot;broken&quot;" bodies'],
        "diagram_type": "flowchart",
        "mermaid": 'flowchart LR\n  A["x"] --> B["y"]',
        "warnings": ['saw "an &quot;odd&quot; one" here'],
    }
    pres = tmp_path / "prose.json"
    pres.write_text(json.dumps(raw))
    rc, out, err = run_lib(["--normalize-presentation", str(pres)])
    assert rc == 0, f"stderr: {err}"
    normalized = json.loads(pres.read_text())
    assert normalized["impact_paragraph"] == 'The key is "record.get("staged")" in the fix.'
    assert normalized["why_it_matters"] == ['drops the ""broken"" bodies']
    assert normalized["warnings"] == ['saw "an "odd" one" here']
    assert "#quot;" not in json.dumps(normalized)


def test_normalize_presentation_missing_required_key_errors(tmp_path):
    incomplete = {"title": "feat(X): thing", "impact_paragraph": "x", "diagram_type": "flowchart"}
    pres = tmp_path / "incomplete.json"
    pres.write_text(json.dumps(incomplete))
    rc, out, err = run_lib(["--normalize-presentation", str(pres)])
    assert rc != 0
    assert "mermaid" in err.lower() or "why_it_matters" in err.lower()


def test_normalize_presentation_invalid_json_errors(tmp_path):
    pres = tmp_path / "broken.json"
    pres.write_text("not json {")
    rc, out, err = run_lib(["--normalize-presentation", str(pres)])
    assert rc != 0
    assert "Traceback" not in err


def test_prepare_create_mode_substitutes_changed_files_placeholder():
    rc, out, err = run_lib([
        "--mode", "create",
        "--diff-file", str(FIX / "simple_diff.txt"),
        "--context-file", str(FIX / "simple_ctx.json"),
    ])
    assert rc == 0, f"stderr: {err}"
    request = json.loads(out)
    assert "{changed_files}" not in request["prompt"]
    assert "(no file list)" in request["prompt"]  # simple_ctx.json fixture has no changed_files key


def test_prepare_create_mode_includes_real_changed_files_value(tmp_path):
    ctx = json.loads((FIX / "simple_ctx.json").read_text())
    ctx["changed_files"] = "src/foo.py\nsrc/foo_test.py"
    ctx_path = tmp_path / "ctx_with_changed_files.json"
    ctx_path.write_text(json.dumps(ctx))
    rc, out, err = run_lib([
        "--mode", "create",
        "--diff-file", str(FIX / "simple_diff.txt"),
        "--context-file", str(ctx_path),
    ])
    assert rc == 0, f"stderr: {err}"
    request = json.loads(out)
    assert "src/foo.py" in request["prompt"]
    assert "src/foo_test.py" in request["prompt"]
    assert "(no file list)" not in request["prompt"]


def test_assemble_includes_files_to_review_when_key_files_present(tmp_path):
    pres = json.loads((FIX / "simple_presentation.json").read_text())
    pres["key_files"] = [
        {"path": "lib/mr_presentation.py", "role": "core logic"},
        {"path": "lib/tests/test_mr_presentation.py", "role": "test"},
    ]
    pres_path = tmp_path / "with_key_files.json"
    pres_path.write_text(json.dumps(pres))
    rc, out, err = run_lib([
        "--assemble", "--mode", "create",
        "--presentation-json", str(pres_path),
        "--context-file", str(FIX / "simple_ctx.json"),
    ])
    assert rc == 0, f"stderr: {err}"
    assert "### Files to review" in out
    assert "- `lib/mr_presentation.py` — core logic" in out
    assert "- `lib/tests/test_mr_presentation.py` — test" in out
    assert out.index("`lib/mr_presentation.py`") < out.index("`lib/tests/test_mr_presentation.py`")


def test_assemble_omits_files_to_review_when_key_files_absent():
    rc, out, err = run_lib([
        "--assemble", "--mode", "create",
        "--presentation-json", str(FIX / "simple_presentation.json"),
        "--context-file", str(FIX / "simple_ctx.json"),
    ])
    assert rc == 0, f"stderr: {err}"
    assert "### Files to review" not in out


def test_assemble_omits_files_to_review_when_key_files_empty(tmp_path):
    """Schema (Task 1) documents key_files: [] as the deliberate output for a single/obvious-file
    diff -- distinct from the field being absent entirely (covered above), same skip-if-empty
    code path (the walrus binds to a falsy [] too), but an explicitly documented case worth its
    own regression test."""
    pres = json.loads((FIX / "simple_presentation.json").read_text())
    pres["key_files"] = []
    pres_path = tmp_path / "empty_key_files.json"
    pres_path.write_text(json.dumps(pres))
    rc, out, err = run_lib([
        "--assemble", "--mode", "create",
        "--presentation-json", str(pres_path),
        "--context-file", str(FIX / "simple_ctx.json"),
    ])
    assert rc == 0, f"stderr: {err}"
    assert "### Files to review" not in out


def test_assemble_omits_files_to_review_in_review_mode_even_when_key_files_present(tmp_path):
    pres = json.loads((FIX / "simple_presentation.json").read_text())
    pres["key_files"] = [{"path": "lib/mr_presentation.py", "role": "core logic"}]
    pres_path = tmp_path / "review_with_key_files.json"
    pres_path.write_text(json.dumps(pres))
    rc, out, err = run_lib([
        "--assemble", "--mode", "review",
        "--presentation-json", str(pres_path),
        "--context-file", str(FIX / "simple_ctx.json"),
    ])
    assert rc == 0, f"stderr: {err}"
    assert "### Files to review" not in out  # explicit mode=="create" gate, not just an untouched prompt
    assert "Review legend" in out  # sanity: review mode's own section still renders


def test_schema_allows_key_files():
    schema = mr_presentation.load_schema()
    props = schema["properties"]
    assert "key_files" in props and props["key_files"]["type"] == "array"
    assert "key_files" not in schema["required"]


def test_assemble_skips_malformed_key_files_item_instead_of_crashing(tmp_path):
    """--normalize-presentation only checks REQUIRED_PRESENTATION_KEYS (key_files isn't one),
    and the plain-text-JSON fallback documented in commit/SKILL.md step 7 bypasses schema
    enforcement entirely -- so a key_files item missing 'path' or 'role' can reach --assemble."""
    pres = json.loads((FIX / "simple_presentation.json").read_text())
    pres["key_files"] = [
        {"role": "missing path"},
        {"path": "lib/mr_presentation.py", "role": "core logic"},
    ]
    pres_path = tmp_path / "malformed_key_files.json"
    pres_path.write_text(json.dumps(pres))
    rc, out, err = run_lib([
        "--assemble", "--mode", "create",
        "--presentation-json", str(pres_path),
        "--context-file", str(FIX / "simple_ctx.json"),
    ])
    assert rc == 0, f"stderr: {err}"
    assert "- `lib/mr_presentation.py` — core logic" in out
    assert "missing path" not in out
