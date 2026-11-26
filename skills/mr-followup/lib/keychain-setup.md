# GitLab token in macOS keychain — one-time setup

So the sibling `fetch_discussions.py` can read the GitLab token from any subshell.

## The problem

macOS keychain entries carry a per-item Access Control List (ACL): only binaries listed there can read the password. An entry created via Keychain Access.app typically trusts only `/usr/bin/security` and the interactive shell that created it. When `python3` invokes `security` as a subprocess the call is denied — and macOS reports the denial as `SecKeychainSearchCopyNext: The specified item could not be found in the keychain.` (item-existence is intentionally hidden from untrusted callers). Symptoms: a direct `security find-generic-password -s gitlab-api-token -w` works in your terminal, but `$(security ...)` or the Python helper exits 44.

## The recipe

Create the entry with `-T` flags pre-seeding the ACL for the actual binaries that will read it. The interactive `read -rs` prompt avoids leaking the token into your shell history; the `${VAR:+...}` guards skip the `-T` flag when a binary isn't on `$PATH` (an empty `-T ""` would otherwise be passed, which `security` interprets as an ACL directive, not a no-op). The flag and its value are two separate expansions on purpose — do not collapse them into `${PY:+-T "$PY"}`, which zsh expands to a single glued word (a gotcha worth logging in your own notes file, if you keep one):

```bash
read -rs -p "GitLab PAT: " TOKEN; echo
PY=$(command -v python3 || true)
GLAB=$(command -v glab || true)
security add-generic-password \
  -s "gitlab-api-token" \
  -a "$USER" \
  -w "$TOKEN" \
  -T /usr/bin/security \
  ${PY:+-T} ${PY:+"$PY"} \
  ${GLAB:+-T} ${GLAB:+"$GLAB"} \
  -U
unset TOKEN
```

`-U` updates the entry in place if it already exists. `command -v python3` resolves to whichever interpreter is on `$PATH` (typically `/opt/homebrew/bin/python3` on Apple Silicon, `/usr/bin/python3` on pre-Ventura macOS once Xcode CLI tools are installed) — the ACL must trust the actual interpreter that runs the helper, otherwise the lookup still gets denied. After this, both `$(security ...)` substitutions and the Python helper read the token cleanly with no GUI prompt.

## Why no `-T /bin/bash`?

The helper calls `security` as a Python subprocess, not from bash. Trusting bash globally would let any bash script on the machine read the token. Keep the ACL tight to the actual readers: `security`, `python3`, and `glab` (which has its own token resolution chain and may consult the keychain).

## Verify

```bash
R="${CLAUDE_PLUGIN_ROOT}"; [ -d "$R" ] || R="$HOME/.claude/local-marketplace/simo-dev"; [ -d "$R" ] || R="$(find -H "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/plugins/cache" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | grep "/simo-dev/[^/]*$" | sort -V | tail -1)"
RES=$(python3 "$R/skills/mr-followup/lib/fetch_discussions.py" myorg/geo/backend <some-mr-iid> gitlab.example.com); echo "rc=$? ${RES:0:200}"
```

Pass = `rc=0` plus MR discussion markdown (or `No open review comments found.`). Fail = `rc=2` with a `REVIEW COMMENTS UNAVAILABLE` marker and the "no token found" error on stderr — the token is set but unreadable from a subshell, so fix the ACL below. Check the rc, not just the text: a token the helper cannot use is a failed fetch, not an MR without comments.

## Repairing an existing entry

If the entry already exists with a bad ACL, either re-run the `add-generic-password` command above with `-U` (recreates the ACL), or open Keychain Access.app → search `gitlab-api-token` → Get Info → Access Control → grant access to your active `python3` binary, or check **Allow all applications to access this item** (least secure but simplest).
