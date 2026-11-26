#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# ///
r"""simo-dev:commit step 7 (MR body) and step 9 (MR creation), decided from repo and forge state.

  mr_create.py prepare    --repo W [--target T] [--draft 1|0|""]
  mr_create.py body       --repo W
  mr_create.py create     --repo W [--forge gitlab|github]
  mr_create.py transition --repo W
  mr_create.py mrtmp      --repo W

Stdout is `key: value` lines only (`note:`, `warning:`, `ahead:` repeat); sibling scripts' stderr
passes through. rc 0 done; 1 `STOP: <reason>`, nothing created by this call; 2 bad args (argparse,
and also what python exits with when the plugin root was unbound and the file cannot be opened:
re-issue the block); 3 `ASK: scope_fetch_cmd prefix=<P> key=<K>` (prepare only, nothing written:
ask, `tracker_config.py set`, re-run); 4 `STOP: create call failed` (create only: the MR may exist
anyway); 5 `STOP:` after the create call (create only: an MR exists but failed verification; its
URL is never printed as a `url:` line).

--repo: the absolute path of the worktree, its own toplevel (samefile), on a branch; anything else
STOPs before git or the forge is asked. An unsubstituted `W="<...>"` used to collapse the scratch dir
to `/tmp/claude_mr_._`, where files left by an earlier such run let the title/target checks pass.

Scratch dir (mrtmp): `/tmp/claude_mr_<repo>_<branch, / as ->`, <repo> = basename(dirname(`git
rev-parse --path-format=absolute --git-common-dir`)): byte-identical to the shell form mr-followup,
mr-review and commit lib/verification-notes.md derive, so they read the same files. Keyed by
repo+branch so concurrent sessions do not clobber each other (incident 2026-06-10); a worktree dir
name is not a repo name (twin worktrees collided, 2026-09-24). Files: mr_ctx.json, mr_diff.txt,
mr_draft, mr_request.json, mr_pres.json, mr_body.md. The path is printed unresolved (`/tmp/...`).

prepare
  1. Ticket key (derive_ticket_key.py --cwd W). A key whose prefix has no scope_fetch_cmd -> ASK,
     before anything is fetched or written: build_mr_ctx.py would silently leave ticket_summary empty.
  2. `git fetch origin --prune --quiet`: a stale origin/<target> skews the merge-base and the diff;
     --prune drops a deleted parent branch, which would otherwise still be picked as a stacked target.
  3. mkdir mrtmp, print `mrtmp:`, delete the six files: `body` falls back when mr_pres.json is
     absent, so a file left by an earlier run must not survive into this one.
  4. build_mr_ctx.py > mr_ctx.json. Target order: --target, else a stacked parent, else origin/HEAD.
     Its `note:` stderr lines become `note:` lines; rc 2 (--target not on origin) / rc 3 (two stacked
     parents at the same distance) -> `STOP: target: <its STOP>`; other rc -> STOP. Empty target (no
     --target, no stacked parent, no origin/HEAD) -> STOP here: before this script the prose only
     stopped at step 9, after stack_record had run with an empty target and the diff had failed.
  5. `target:`, `target_source:`; stack_record.py record (stores the parent for mr-followup step 1;
     failure -> `warning:`, continue).
  6. Local base ahead: local refs/heads/<T> is an ancestor of HEAD and origin/<T>..<T> is non-empty
     -> `ahead:` lines + STOP, no bypass: those commits would show up in the MR diff as unrelated
     changes. No local <T> -> safe (nothing local to be ahead). `merge-base --is-ancestor` rc 1 ->
     safe; any other rc is a git failure -> STOP, never safe (the same contract as
     check_local_base_ahead.py, which mr-review's branch-vs-default mode runs). Runs
     before the diff: build_mr_ctx.py's own merge-base range carries the same leak with no
     `...HEAD` to grep for (.claude/rules/local-plugins.md, ninth case).
  7. mr_diff.txt = `git diff origin/<T>...HEAD` (three-dot: merge-base diff, matches the forge view).
  8. mr_draft: --draft 1/0 wins (skips the plan check); else check_mr_draft.py reads the
     `**MR draft:**` header of the docs/plans/*.md files this branch adds, from HEAD (rc 3: an
     uncommitted, gitignored or non-bare value, WARNING names the plan -> STOP; other non-zero ->
     STOP). Written only on success, so a STOP leaves no mr_draft behind.
  9. mr_presentation.py --mode create > mr_request.json, by file handle, never through a shell
     variable: zsh's echo stopped at a literal `\c` in the JSON and truncated a ~42KB request to a
     few hundred bytes (incident 2026-07-27). Success -> `agent_type:`, `model:`, `request:` last.
     Failure (an empty diff included) or an empty agent_type/model -> `fallback: <why>`, no
     `request:`, rc 0: the caller skips the dispatch and `body` writes the minimal body.

body
  Needs mr_ctx.json (else STOP). mr_pres.json present -> mr_presentation.py --normalize-presentation
  (HTML-unescapes entities an agent introduced, e.g. `--&gt;` / `&lt;br/&gt;` from the plain-text
  answer path; in the mermaid field a quote entity inside a still-quoted label becomes `#quot;`,
  not a bare `"`, which would nest quotes and stop mermaid parsing; checks the required keys), then
  --assemble --mode create > mr_body.md. Any failure (no file, bad JSON, normalize rc != 0,
  assemble rc != 0) -> the minimal body: mr_pres.json = {"title": <HEAD subject>}, mr_body.md =
  commit log + file stat from mr_ctx.json, one `warning:`. The assemble rc was unchecked in the
  prose: normalize checks key presence only, so a `why_it_matters` that is not a list crashed
  assemble and the MR shipped an empty description at rc 0. Then also_fixed_ledger.py section and
  the prefix's pr_body_ticket_line (tracker_config.py ticket-line) are appended; failures ->
  `warning: LEDGER_FAILED …` / `warning: TICKET_LINE_FAILED …`, continue; re-running body rebuilds
  mr_body.md from scratch. A presentation that assembles but carries no title (schemas/mr_presentation.json
  lists `title` outside `required`) gets the HEAD subject written into mr_pres.json's `title` and one
  `warning:`; the assembled body is kept. Before this script create then stopped on an empty title
  after a successful dispatch. `title:` last.

create
  Inputs first, before any forge call: title (mr_pres.json), target (mr_ctx.json), mr_body.md (the
  prose's `--description "$(cat …)"` turned a missing body into an empty description at rc 0).
  Forge: --forge, else detect_remote.detect(W) (DETECT_REMOTE_URL overrides origin, for tests);
  unresolvable -> STOP. Every glab/gh call runs with cwd=W and no -R:
  - branch: without --source-branch/--head both tools default to the current branch of the process
    cwd (incident 2026-07-27: !35 shipped against an unrelated session branch, no error; closed and
    recreated as !36 with --source-branch explicit).
  - project: the cwd's remote decides the source project. On glab 1.110.0 -R sets the target (it
    replaced the deprecated --target-project) and glab creates against the head repo by design, so
    from another repo's cwd -R turns a same-project MR into a cross-project one: 422 `Source project
    is not a fork of the target project` means wrong repo, not permissions or a missing fork. The
    source-side overrides (-H/--head, GITLAB_HEAD_REPO) want a slug this script never derives, and
    a bare owner/repo pins the path but not the host. commit step 6 keeps its own `cd` for the same
    reason.
  GitLab assignee: `glab api user`, up to 3 tries (the transient forge error commit step 5
  documents), STOP after; resolved before the create: inside the create's own command substitution
  a failed lookup became `--assignee ""` and glab created the MR unassigned at rc 0 (observed
  2026-09-30, `net/http: TLS handshake timeout`). GitHub: `--assignee @me`, resolved inside gh
  (derived, not measured). Title and target are always passed: glab rejects a blank title (`title
  can't be blank`), but an empty --target-branch falls back to the project's target-branch rules or
  default branch with no error (glab mr_create.go, getTargetBranch), and gh without --base uses
  gh-merge-base or the default branch (gh pr create --help; that `--base ""` behaves alike is
  derived, not measured). --draft only when mr_draft is exactly `1`.
  Create rc != 0 or not runnable -> rc 4: an MR may exist anyway ("already exists" after a transient
  step-6 miss, or a create whose response was lost): never retry the create, re-run step 6.
  Verify: the view of <branch> must name it as its source branch (GitLab source_branch, GitHub
  headRefName); with mr_draft 1 it must be a Draft (GitLab draft, falling back to
  work_in_progress; GitHub isDraft), else one mark-Draft attempt (`glab mr update <branch> --draft`
  / `gh pr ready <branch> --undo`) and one re-view; failure -> rc 5. The target is not verified, so
  an MR against the wrong target would pass: hence the explicit flag above.
  Then `forge:`, `url:` (GitLab web_url / GitHub url, the field verification-notes.md uses) and,
  best-effort (failure -> `warning:`, rc stays 0): flow_telemetry.py event gate=mr_created
  answer=created, also_fixed_ledger.py clear (the MR body carries the Also-fixed section now; never
  on the --no-mr path, which never reaches step 9), the CodeReview transition.

transition
  tracker_config.py run <PREFIX> transition_cmd <KEY> CodeReview --leaf-only --sync-parent
  --clear-blocked; KEY = mr_ctx.json branch_meta.jira_key (empty under DEV_FLOW_NO_TRACKER or
  .claude/no-tracker -> `transition: skipped (no ticket key)`). Idempotent, forward-only,
  best-effort: any non-zero (a --leaf-only refusal on an umbrella, a --sync-parent exit 7 when the
  parent has every sub-task at/past Done) -> `warning:`, rc 0. `tracker_config.py run` also exits 0
  when transition_cmd is unconfigured, so `transition: <KEY> -> CodeReview` means "ran or no-op".
  Run alone by commit lib/failure-paths.md when a failed create turns out to have created the MR.

mrtmp
  Prints and creates the scratch dir, for the Protected-Branch Path (commit lib/alt-paths.md),
  which skips step 7.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

LIB = Path(__file__).resolve().parent
DETECT_REMOTE = LIB.parent / "skills" / "mr-followup" / "lib" / "detect_remote.py"
FILES = ("mr_ctx.json", "mr_diff.txt", "mr_draft", "mr_request.json", "mr_pres.json", "mr_body.md")
CODEREVIEW_FLAGS = ("--leaf-only", "--sync-parent", "--clear-blocked")


class Stop(Exception):
    def __init__(self, reason: str, rc: int = 1) -> None:
        super().__init__(reason)
        self.reason = reason
        self.rc = rc


def put(key: str, value: object) -> None:
    print(f"{key}: {str(value).replace(chr(10), ' ')}", flush=True)


def last_line(text: str | None) -> str:
    lines = (text or "").strip().splitlines()
    return lines[-1] if lines else ""


def git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)


def sib(name: str, *args: str, out=None, capture_err: bool = False) -> subprocess.CompletedProcess:
    """Run a sibling lib script: stdout to `out` (an open file) or captured; stderr passes through unless captured."""
    return subprocess.run([sys.executable, str(LIB / name), *args],
                          stdout=out if out is not None else subprocess.PIPE,
                          stderr=subprocess.PIPE if capture_err else None, text=True)


def forge(repo: str, *argv: str) -> subprocess.CompletedProcess | None:
    """One glab/gh call in the worktree: cwd=repo, never -R. None when the binary cannot be run."""
    try:
        return subprocess.run(list(argv), cwd=repo, capture_output=True, text=True)
    except OSError as e:
        print(f"mr_create: {argv[0]}: {e}", file=sys.stderr)
        return None


def relay(res: subprocess.CompletedProcess | None) -> None:
    if res is not None:
        sys.stderr.write(res.stdout or "")
        sys.stderr.write(res.stderr or "")


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def field(data, *keys: str) -> str:
    for key in keys:
        data = data.get(key) if isinstance(data, dict) else None
    return data if isinstance(data, str) else ""


def resolve_repo(repo: str) -> tuple[str, str]:
    bad = f"--repo must be the absolute path of a worktree toplevel, got {repo!r} (an unsubstituted W?)"
    if not os.path.isabs(repo) or not os.path.isdir(repo):
        raise Stop(bad)
    top = git(repo, "rev-parse", "--show-toplevel").stdout.strip()
    if not top or not os.path.samefile(top, repo):
        raise Stop(bad)
    branch = git(repo, "branch", "--show-current").stdout.strip()
    if not branch:
        raise Stop(f"detached HEAD in {repo}: no branch to create an MR from")
    return repo, branch


def mrtmp_path(repo: str, branch: str) -> Path:
    common = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    return Path(f"/tmp/claude_mr_{os.path.basename(os.path.dirname(common))}_{branch.replace('/', '-')}")


def local_base_ahead(repo: str, target: str) -> list[str]:
    ref = f"refs/heads/{target}"
    if git(repo, "rev-parse", "--verify", "--quiet", ref).returncode != 0:
        return []
    anc = git(repo, "merge-base", "--is-ancestor", ref, "HEAD")
    if anc.returncode == 1:
        return []
    if anc.returncode != 0:
        raise Stop(f"local base check failed: git merge-base --is-ancestor {ref} HEAD rc {anc.returncode}: {last_line(anc.stderr)}")
    log = git(repo, "log", "--oneline", f"origin/{target}..{ref}")
    if log.returncode != 0:
        raise Stop(f"local base check failed: git log origin/{target}..{ref} rc {log.returncode}: {last_line(log.stderr)}")
    return [line for line in log.stdout.splitlines() if line]


def prepare(repo: str, branch: str, target_arg: str, draft_arg: str) -> int:
    key = sib("derive_ticket_key.py", branch, "--cwd", repo).stdout.strip()
    if key:
        prefix = key.split("-", 1)[0]
        if not sib("tracker_config.py", "get", prefix, "scope_fetch_cmd").stdout.strip():
            put("ASK", f"scope_fetch_cmd prefix={prefix} key={key}")
            return 3
    if git(repo, "fetch", "origin", "--prune", "--quiet").returncode != 0:
        put("warning", "git fetch origin failed — origin/<target> may be stale (continuing)")
    tmp = mrtmp_path(repo, branch)
    tmp.mkdir(parents=True, exist_ok=True)
    put("mrtmp", tmp)
    for name in FILES:
        (tmp / name).unlink(missing_ok=True)

    ctx_file = tmp / "mr_ctx.json"
    with open(ctx_file, "w", encoding="utf-8") as fh:
        res = sib("build_mr_ctx.py", "--repo", repo, "--target", target_arg, out=fh, capture_err=True)
    stop_line = ""
    for line in res.stderr.splitlines():
        if line.startswith("note: "):
            put("note", line[len("note: "):])
        elif line.startswith("STOP: ") and not stop_line:
            stop_line = line[len("STOP: "):]
        else:
            print(line, file=sys.stderr)
    if res.returncode != 0:
        ctx_file.unlink(missing_ok=True)
        if res.returncode in (2, 3):
            raise Stop(f"target: {stop_line or f'build_mr_ctx.py rc {res.returncode}'}")
        raise Stop(f"build_mr_ctx.py failed (rc {res.returncode}, stderr above) — MR not generated")
    ctx = read_json(ctx_file)
    target = field(ctx, "branch_meta", "target")
    if not target:
        raise Stop("could not resolve the MR target branch (no --target, no stacked parent, no origin/HEAD) — pass --target")
    put("target", target)
    put("target_source", field(ctx, "branch_meta", "target_source"))

    rec = sib("stack_record.py", "record", "--repo", repo, "--target", target)
    if rec.returncode != 0:
        put("warning", f"stack_record.py record failed (rc {rec.returncode}) — mr-followup cannot spot a "
                       "squash-merged parent for this branch (continuing)")

    ahead = local_base_ahead(repo, target)
    if ahead:
        for line in ahead:
            put("ahead", line)
        raise Stop(f"local base ahead: local '{target}' is ahead of origin/{target}, and those commits are in "
                   "this branch's ancestry — they would show up in the MR diff as unrelated changes")

    diff_file = tmp / "mr_diff.txt"
    with open(diff_file, "wb") as fh:
        diff = subprocess.run(["git", "-C", repo, "diff", f"origin/{target}...HEAD"], stdout=fh)
    if diff.returncode != 0:
        diff_file.unlink(missing_ok=True)
        raise Stop(f"git diff origin/{target}...HEAD failed (rc {diff.returncode}, stderr above)")

    draft = draft_arg
    if draft not in ("0", "1"):
        det = sib("check_mr_draft.py", "--repo", repo, "--target", target)
        if det.returncode == 3:
            raise Stop("a plan's MR draft field is uncommitted or not a bare yes/no (WARNING above) — "
                       "commit lib/failure-paths.md \"Step 7: other prepare stops\"")
        if det.returncode != 0:
            raise Stop(f"check_mr_draft.py failed (rc {det.returncode}, stderr above) — MR not generated")
        draft = det.stdout.strip()
    (tmp / "mr_draft").write_text(draft)
    put("draft", draft)

    req_file = tmp / "mr_request.json"
    with open(req_file, "w", encoding="utf-8") as fh:
        req = sib("mr_presentation.py", "--mode", "create", "--diff-file", str(diff_file),
                  "--context-file", str(ctx_file), out=fh, capture_err=True)
    data = read_json(req_file) if req.returncode == 0 else None
    agent, model = field(data, "agent_type"), field(data, "model")
    if not (agent and model):
        req_file.unlink(missing_ok=True)
        why = f"rc {req.returncode}" + (f": {last_line(req.stderr)}" if last_line(req.stderr) else "")
        put("fallback", f"mr_presentation.py --mode create failed ({why}) — skip the dispatch, run body")
        return 0
    put("agent_type", agent)
    put("model", model)
    put("request", req_file)
    return 0


def minimal_body(ctx: dict) -> str:
    log = "\n".join(f"- {line}" for line in field(ctx, "commit_log").splitlines() if line.strip())
    stat = field(ctx, "file_stat").rstrip()
    return f"### Commits\n\n{log}\n\n### Files changed\n\n```\n{stat}\n```\n"


def body(repo: str, branch: str) -> int:
    tmp = mrtmp_path(repo, branch)
    ctx_file, pres_file, body_file = tmp / "mr_ctx.json", tmp / "mr_pres.json", tmp / "mr_body.md"
    ctx = read_json(ctx_file)
    if not isinstance(ctx, dict):
        raise Stop(f"missing or unreadable {ctx_file} — run step 7's prepare first")
    why = ""
    if not pres_file.exists():
        why = "no mr_pres.json: no request, or the dispatch failed"
    else:
        norm = sib("mr_presentation.py", "--normalize-presentation", str(pres_file))
        if norm.returncode != 0:
            why = f"--normalize-presentation rc {norm.returncode}"
        else:
            with open(body_file, "w", encoding="utf-8") as fh:
                asm = sib("mr_presentation.py", "--assemble", "--mode", "create", "--presentation-json",
                          str(pres_file), "--context-file", str(ctx_file), out=fh)
            if asm.returncode != 0:
                why = f"--assemble rc {asm.returncode}"
    if why:
        subject = git(repo, "log", "-1", "--format=%s").stdout.strip()
        pres_file.write_text(json.dumps({"title": subject}), encoding="utf-8")
        body_file.write_text(minimal_body(ctx), encoding="utf-8")
        put("warning", f"MR body fell back to the minimal form ({why})")
        put("body", "fallback")
    else:
        pres = read_json(pres_file)
        if not field(pres, "title"):
            pres["title"] = git(repo, "log", "-1", "--format=%s").stdout.strip()
            pres_file.write_text(json.dumps(pres, indent=2), encoding="utf-8")
            put("warning", "the presentation had no title — using the HEAD commit subject")
        put("body", "presentation")
    with open(body_file, "a", encoding="utf-8") as fh:
        led = sib("also_fixed_ledger.py", "section", "--repo", repo, out=fh)
    if led.returncode != 0:
        put("warning", f"LEDGER_FAILED rc={led.returncode} — mr_body.md lacks the Also fixed section; fix and re-run body")
    with open(body_file, "a", encoding="utf-8") as fh:
        line = sib("tracker_config.py", "ticket-line", field(ctx, "branch_meta", "jira_key"), out=fh)
    if line.returncode != 0:
        put("warning", f"TICKET_LINE_FAILED rc={line.returncode} — mr_body.md lacks the ticket line; fix and re-run body")
    put("title", field(read_json(pres_file), "title"))
    return 0


def detect_remote(repo: str) -> tuple[str, str, str] | None:
    """(forge, slug, api_host) from detect_remote.detect, or None (its message already on stderr)."""
    spec = importlib.util.spec_from_file_location("detect_remote", DETECT_REMOTE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.detect(repo)


def detect_forge(repo: str) -> str:
    found = detect_remote(repo)
    return found[0] if found else ""


def gitlab_username(repo: str) -> str:
    for _ in range(3):
        res = forge(repo, "glab", "api", "user")
        if res is None:
            continue
        if res.returncode != 0:
            sys.stderr.write(res.stderr or "")
            continue
        try:
            name = json.loads(res.stdout).get("username")
        except (json.JSONDecodeError, AttributeError):
            name = None
        if isinstance(name, str) and name:
            return name
    raise Stop("could not resolve the GitLab username after 3 tries (glab api user failed or returned no "
               "username) — MR not created; re-run create")


def view_fields(repo: str, kind: str, branch: str) -> dict | None:
    """The MR/PR view of <branch> (or an IID / PR number), normalized across forges; None when the call
    fails or answers no JSON object (its output relayed to stderr)."""
    argv = (["glab", "mr", "view", branch, "-F", "json"] if kind == "gitlab"
            else ["gh", "pr", "view", branch, "--json", "number,state,isDraft,baseRefName,headRefName,url"])
    res = forge(repo, *argv)
    if res is None or res.returncode != 0:
        relay(res)
        return None
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        relay(res)
        return None
    if kind == "gitlab":
        keys = ("source_branch", "web_url", "target_branch", "iid", "state")
        draft = data.get("draft") or data.get("work_in_progress")
    else:
        keys = ("headRefName", "url", "baseRefName", "number", "state")
        draft = data.get("isDraft")
    source, url, base, number, state = (data.get(k) for k in keys)
    return {"source": source, "draft": draft is True, "url": url or "", "base": base or "",
            "number": "" if number is None else str(number), "state": state or ""}


def view_mr(repo: str, kind: str, branch: str) -> dict | None:
    view = view_fields(repo, kind, branch)
    if view is None or view["source"] != branch:
        return None
    return view


def transition(tmp: Path) -> None:
    ctx = read_json(tmp / "mr_ctx.json")
    if not isinstance(ctx, dict):
        put("warning", f"transition skipped: {tmp}/mr_ctx.json unreadable")
        return
    codereview(field(ctx, "branch_meta", "jira_key"))


def codereview(key: str) -> None:
    """The CodeReview transition of <key>: idempotent, forward-only, best-effort."""
    if not key:
        put("transition", "skipped (no ticket key)")
        return
    res = sib("tracker_config.py", "run", key.split("-", 1)[0], "transition_cmd", key, "CodeReview", *CODEREVIEW_FLAGS)
    sys.stderr.write(res.stdout or "")
    if res.returncode != 0:
        put("warning", f"transition_cmd failed for {key} -> CodeReview (rc {res.returncode}, continuing)")
    else:
        put("transition", f"{key} -> CodeReview")


def after_create(repo: str, tmp: Path) -> None:
    event = sib("flow_telemetry.py", "event", "--type", "gate", "--cwd", repo, "--field", "skill=simo-dev:commit",
                "--field", "gate=mr_created", "--field", "answer=created")
    if event.returncode != 0:
        put("warning", f"flow_telemetry.py event failed (rc {event.returncode}, continuing)")
    clear = sib("also_fixed_ledger.py", "clear", "--repo", repo)
    if clear.returncode != 0:
        put("warning", f"LEDGER_FAILED rc={clear.returncode} — the Also-fixed ledger was not cleared; run "
                       "also_fixed_ledger.py clear once fixed")
    transition(tmp)


def create(repo: str, branch: str, forge_arg: str) -> int:
    tmp = mrtmp_path(repo, branch)
    title = field(read_json(tmp / "mr_pres.json"), "title")
    if not title:
        raise Stop(f"could not resolve the MR title (missing or empty title in {tmp}/mr_pres.json: step 7 did not "
                   "complete) — MR not created")
    target = field(read_json(tmp / "mr_ctx.json"), "branch_meta", "target")
    if not target:
        raise Stop(f"could not resolve the MR target branch (missing or empty branch_meta.target in {tmp}/mr_ctx.json: "
                   "step 7 did not complete) — MR not created; an empty target would silently fall back to the default branch")
    body_file = tmp / "mr_body.md"
    if not body_file.is_file():
        raise Stop(f"could not read the MR body ({body_file} missing: step 7's body did not run) — MR not created")
    kind = forge_arg or detect_forge(repo)
    if not kind:
        raise Stop("could not detect the forge from origin (detect_remote stderr above) — re-run with --forge gitlab|github")
    draft_file = tmp / "mr_draft"
    draft = draft_file.is_file() and draft_file.read_text().rstrip("\n") == "1"
    flag = ["--draft"] if draft else []
    if kind == "gitlab":
        argv = ["glab", "mr", "create", "--title", title,
                "--description", body_file.read_text(encoding="utf-8").rstrip("\n"),
                "--assignee", gitlab_username(repo), "--source-branch", branch, "--squash-before-merge",
                "--remove-source-branch", "--target-branch", target, *flag, "--yes"]
    else:
        argv = ["gh", "pr", "create", "--title", title, "--body-file", str(body_file), "--base", target,
                "--head", branch, "--assignee", "@me", *flag]
    res = forge(repo, *argv)
    relay(res)
    if res is None or res.returncode != 0:
        why = "could not run" if res is None else f"rc {res.returncode}"
        raise Stop(f"create call failed ({why}, stderr above) — an MR may exist anyway: never retry the create, "
                   "re-run step 6", 4)
    view = view_mr(repo, kind, branch)
    if view is None:
        raise Stop(f"MR source_branch mismatch or no MR found for branch '{branch}' — do not hand the URL to the user", 5)
    if draft and not view["draft"]:
        relay(forge(repo, *(["glab", "mr", "update", branch, "--draft"] if kind == "gitlab"
                            else ["gh", "pr", "ready", branch, "--undo"])))
        view = view_mr(repo, kind, branch)
        if view is None or not view["draft"]:
            raise Stop(f"Draft was requested but the MR for '{branch}' is still not a Draft after one mark-Draft "
                       "attempt — do not hand the URL to the user", 5)
        put("draft_marked", "yes")
    put("forge", kind)
    put("url", view["url"])
    after_create(repo, tmp)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("prepare", "body", "create", "transition", "mrtmp"):
        p = sub.add_parser(name)
        p.add_argument("--repo", required=True)
        if name == "prepare":
            p.add_argument("--target", default="")
            p.add_argument("--draft", default="", choices=["", "0", "1"])
        if name == "create":
            p.add_argument("--forge", default="", choices=["", "gitlab", "github"])
    args = parser.parse_args(argv)
    try:
        repo, branch = resolve_repo(args.repo)
        if args.cmd == "prepare":
            return prepare(repo, branch, args.target, args.draft)
        if args.cmd == "body":
            return body(repo, branch)
        if args.cmd == "create":
            return create(repo, branch, args.forge)
        tmp = mrtmp_path(repo, branch)
        if args.cmd == "transition":
            transition(tmp)
            return 0
        tmp.mkdir(parents=True, exist_ok=True)
        put("mrtmp", tmp)
        return 0
    except Stop as stop:
        put("STOP", stop.reason)
        return stop.rc


if __name__ == "__main__":
    sys.exit(main())
