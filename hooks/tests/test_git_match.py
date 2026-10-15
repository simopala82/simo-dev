#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
"""Direct unit tests for git_match.py — previously exercised only indirectly through
mr-followup-remind.sh's and review-loop-warn.sh's own bash test suites."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import git_match  # noqa: E402


def test_plain_commit_matches():
    assert git_match.git_subcommand_re("commit").search("git commit -m x")


def test_worktree_dash_c_form_matches():
    assert git_match.git_subcommand_re("commit").search("git -C /tmp/wt commit -m x")


def test_rtk_wrapped_matches():
    assert git_match.git_subcommand_re("push").search("rtk git push")


def test_prefix_subcommand_does_not_match():
    """The trailing boundary class stops a prefix match on another subcommand."""
    assert not git_match.git_subcommand_re("push").search("git pushed-nothing")


def test_prose_mention_does_not_match():
    """SEP requires a real separator; prose merely mentioning the command must not match."""
    assert not git_match.git_subcommand_re("push").search("echo run git push later >> notes.txt")


def test_plain_form_is_newline_blind():
    """The plain form must NOT match a subcommand that only appears after an embedded
    newline with no other real separator before it — that blindness is what protects a
    caller that parses the match from a decoy line inside a heredoc."""
    assert not git_match.git_subcommand_re("commit").search("echo hello\ngit commit -m x")


def test_multiline_form_matches_across_newlines():
    assert git_match.git_subcommand_re_multiline("push").search("git add a.txt\ngit push -u origin b")


def test_leftmost_match_on_multiline_form_can_hit_a_decoy():
    """Documents the multiline form's own known hazard (why callers that PARSE the match
    must never use it): the leftmost match wins, even if it is a decoy line inside a
    heredoc body -- the boundary class needs a real separator (here, the newline that
    ends the decoy line) right after the subcommand, which a same-line quoted decoy
    does not provide."""
    m = git_match.git_subcommand_re_multiline("commit").search(
        "cat <<EOF\ngit -C /decoy commit\nEOF\ntrue && git -C /real commit"
    )
    assert m and "/decoy" in m.group(0)


def test_unquote_strips_independently_per_side():
    assert git_match.unquote('"foo\'') == "foo"
    assert git_match.unquote('"foo"') == "foo"
    assert git_match.unquote("bare") == "bare"


def test_strip_heredocs_drops_body_keeps_opener():
    cmd = "git commit -F - <<'EOF'\nsubject\ngit -C /decoy commit\nEOF\necho done"
    assert git_match.strip_heredocs(cmd) == "git commit -F - <<'EOF'\necho done"


def test_strip_heredocs_dash_form_allows_tab_indented_terminator():
    cmd = "cat <<-END\n\tbody\n\tEND\ntrue"
    assert git_match.strip_heredocs(cmd) == "cat <<-END\ntrue"


def test_strip_heredocs_two_on_one_line_consumed_in_order():
    cmd = "cmd <<A <<\"B\"\na1\nA\nb1\nB\nafter"
    assert git_match.strip_heredocs(cmd) == "cmd <<A <<\"B\"\nafter"


def test_strip_heredocs_quoted_word_with_dash():
    cmd = "cat <<'END-X'\nbody\nEND-X\nafter"
    assert git_match.strip_heredocs(cmd) == "cat <<'END-X'\nafter"


def test_strip_heredocs_leaves_here_string_alone():
    cmd = "cat <<<\"word\"\ngit commit -m x"
    assert git_match.strip_heredocs(cmd) == cmd


def test_strip_heredocs_leaves_arithmetic_shift_alone():
    cmd = "echo $((x<<y))\ngit commit -m x"
    assert git_match.strip_heredocs(cmd) == cmd


def test_strip_heredocs_backslash_quoted_word():
    cmd = "cat <<\\EOF\nbody\nEOF\nafter"
    assert git_match.strip_heredocs(cmd) == "cat <<\\EOF\nafter"


def test_strip_heredocs_ignores_opener_in_comment():
    cmd = "# use <<EOF later\ngit commit -m x"
    assert git_match.strip_heredocs(cmd) == cmd


def test_strip_heredocs_without_heredoc_is_identity():
    cmd = 'W="/r"\ngit -C "$W" commit -m x'
    assert git_match.strip_heredocs(cmd) == cmd


def test_first_unquoted_match_skips_multiline_quoted_decoy():
    pat = git_match.git_subcommand_re_multiline("commit")
    text = 'echo "note\ngit -C /decoy commit\n" >/dev/null; git -C /real commit -m x'
    m = git_match.first_unquoted_match(pat, text)
    assert m and "/real" in m.group(0)


def test_first_unquoted_match_skipped_match_does_not_eat_next_separator():
    pat = git_match.git_subcommand_re_multiline("commit")
    m = git_match.first_unquoted_match(pat, "# stage, then && git commit\ngit -C /real commit -m x")
    assert m and "/real" in m.group(0)


def test_first_unquoted_match_none_when_only_quoted():
    pat = git_match.git_subcommand_re_multiline("commit")
    assert git_match.first_unquoted_match(pat, "echo 'a\ngit commit\n'") is None


def test_first_unquoted_match_ignores_apostrophe_in_comment():
    pat = git_match.git_subcommand_re_multiline("commit")
    for text in ('W="/r"\n# don\'t forget\ngit -C "$W" commit -m x',
                 "git add a.txt  # harvest's file\ngit commit -m x"):
        assert git_match.first_unquoted_match(pat, text), text


def test_first_unquoted_match_handles_ansi_c_quote_escape():
    pat = git_match.git_subcommand_re_multiline("commit")
    assert git_match.first_unquoted_match(pat, "printf $'it\\'s'\ngit commit -m x")


def test_first_unquoted_match_hash_inside_word_is_not_a_comment():
    pat = git_match.git_subcommand_re_multiline("commit")
    assert git_match.first_unquoted_match(pat, "echo a#'b\ngit commit\n'") is None


def test_resolve_vars_plain_and_braced():
    prefix = 'W="/repo/wt"\n'
    assert git_match.resolve_vars("$W", prefix) == "/repo/wt"
    assert git_match.resolve_vars("${W}/sub", prefix) == "/repo/wt/sub"


def test_resolve_vars_last_assignment_wins():
    assert git_match.resolve_vars("$W", "W=/a; W='/b'; ") == "/b"


def test_resolve_vars_value_stops_at_separator():
    assert git_match.resolve_vars("$W", "W=/a&& ") == "/a"
    assert git_match.resolve_vars("$W", 'W="/a"; R=/b\n') == "/a"


def test_resolve_vars_export_form():
    assert git_match.resolve_vars("$W", "export W=/a && ") == "/a"


def test_resolve_vars_unresolved_returns_none():
    assert git_match.resolve_vars("$W", "X=/a\n") is None


def test_resolve_vars_value_holding_another_var_returns_none():
    assert git_match.resolve_vars("$V", 'V="${HOME}/x"\n') is None


def test_resolve_vars_skips_quoted_and_commented_assignments():
    assert git_match.resolve_vars("$W", 'W=/real\necho "\nW=/decoy\n"\n') == "/real"
    assert git_match.resolve_vars("$W", "W=/real; # note; W=/decoy\n") == "/real"


def test_resolve_vars_ignores_mid_word_assignment_lookalike():
    assert git_match.resolve_vars("$W", "echo XW=/a\n") is None


def test_strip_heredocs_ignores_opener_inside_multiline_quote():
    cmd = 'echo "a\nb <<EOF c"\ngit commit -m x'
    assert git_match.strip_heredocs(cmd) == cmd


def test_strip_heredocs_bare_word_with_dash_and_dot():
    assert git_match.strip_heredocs("cat <<END-X\nbody\nEND-X\nafter") == "cat <<END-X\nafter"
    assert git_match.strip_heredocs("cat <<EOF.x\nbody\nEOF.x\nafter") == "cat <<EOF.x\nafter"


def test_strip_heredocs_spaced_numeric_shift_is_not_an_opener():
    cmd = "echo $(( x << 2 ))\ngit commit -m x"
    assert git_match.strip_heredocs(cmd) == cmd


def test_strip_heredocs_is_linear_on_quoted_openers():
    import time
    cmd = 'echo "\n' + "y = x << z\n" * 4000 + '"\ngit commit -m x'
    t0 = time.perf_counter()
    assert git_match.strip_heredocs(cmd) == cmd
    assert time.perf_counter() - t0 < 1.0
