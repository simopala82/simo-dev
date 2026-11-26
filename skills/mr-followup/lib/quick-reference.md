# Quick reference — GitLab vs GitHub commands

| Step                  | GitLab                                                              | GitHub                                                          |
|-----------------------|---------------------------------------------------------------------|-----------------------------------------------------------------|
| Find target           | `python3 .../lib/mr_followup.py detect --repo <worktree-abs>`        | same (it runs `gh pr view <branch> --json …` + `gh pr list --head`) |
| Pipeline status       | `glab pipeline list \| grep <branch>`                               | `gh pr checks <num> --json name,state,conclusion`               |
| Jobs (name+status)    | `glab api projects/:enc/pipelines/:id/jobs`                         | `gh pr checks <num> --json name,state`                          |
| Unresolved threads    | REST `/merge_requests/:iid/discussions`                             | GraphQL `pullRequest.reviewThreads` (isResolved, isOutdated)    |
| Auth                  | `GITLAB_TOKEN` env var or keychain `gitlab-api-token`               | `gh auth login` (inherited by the Python helper)                |
| Local review          | Agent `simo-dev:code-reviewer` on `git diff origin/$BASE...HEAD`    | same                                                            |

## Manual slug / PR-number fallbacks

```bash
# GitLab slug
glab repo view 2>/dev/null | grep -i url | head -1
git remote get-url origin | sed -E 's#^(git@[^:]+:|https?://[^/]+/)##; s#\.git$##'

# GitHub PR number for current branch
gh pr list --head "$(git branch --show-current)" --state open --json number -q '.[].number'
```
