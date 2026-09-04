# Pre-merge QA verify gate — capability detection, heuristic, gate procedure

Loaded on demand from `mr-followup` step 7.5, once step 7 exits clean (no unresolved medium+ findings).

## 1. Capability detection

Active only if the current repo has a release skill exposing a **QA deploy flow that accepts an arbitrary branch**, not just RC/hotfix branches.

No matching skill available for the repo → **skip silently**, no question asked, no behavior change. This list is not exhaustive — a repo with an equivalent release skill (same capability, different name) qualifies too.

## 2. Trigger heuristic

Not always-on — would slow down trivial merges. Decision table:

| Release skill | Diff touches sensitive area | Action |
|---|---|---|
| Not detected | — | Skip silently |
| Detected | Yes (confident match) | Propose the gate, ask Y/n |
| Detected | Inconclusive | Ask explicitly whether the user wants the gate anyway |
| Detected | No (confident non-match) | Skip silently |

"Sensitive area" path patterns (case-insensitive, adjust per repo):

- Ranking/scoring/search query code: `*Repository.cs`, `*Query*`, `*Scoring*`, `*Ranking*`
- Data pipelines: `Importer/`, `Generator/`
- Runtime-behavior config: appsettings / feature-flag files that change behavior without a code change

## 3. Gate procedure (on yes)

1. **Deploy** — push the current branch to QA via the detected release skill's arbitrary-branch flow. Deploy the same track(s) the diff touches.

   **Multi-track deploy gotcha**: a subsystem-only ranking/query change may need BOTH the standard AND a preview/staging track deployed, if the service exposes more than one. A scheduled eval job can hit the preview track's endpoint directly via a dedicated env var, rather than falling back to standard — deploying only the standard track then leaves the eval pointed at stale code. Check the job's target env var before assuming a single deploy covers it.

2. **Eval/perf** — wait for the scheduled QA eval+perf run, or trigger on-demand if the release skill supports it.

3. **Compare vs baseline** — last valid pre-deploy result from the **same scheduler/environment**.

   **QA-vs-PROD scheduler gotcha**: many repos run near-identical scheduled pipelines for different environments (QA vs PROD). Distinguish by the pipeline's effective target/variables, not by schedule time alone — two pipelines can run minutes apart and target different environments.

4. **Confirm** — present the comparison (current vs baseline) and ask an explicit go/no-go before the user proceeds to the merge. On no-go: report, do not proceed; the merge gate is unaffected either way (still a separate human action).

## 4. Interop with step 8

A passing run of this gate does NOT skip step 8's verification-notes comment — the release-time transitions (`Ready for Deploy` / `Done`) still need it. Instead, add the `Pre-merge QA gate: passed <date>` line to the comment so the release-time verify agent can lighten its behavior check.
