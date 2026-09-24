## 1.12.0 (2026-09-24)

### Features
- feat(simo-dev): do in-scope improvements in-task, record them in an Also-fixed ledger

### Bug Fixes
- fix(simo-dev): key MRTMP on the real repo name, not the worktree dir
- fix: skip plans that vanish between glob and read
- fix(simo-dev): make Jira Blocked unblock opt-in via --clear-blocked
- fix(simo-dev): autopilot robust_merge tri-state, merge API only, cancel armed auto-merge
- fix(simo-dev): render the changed files list once in the MR prompt

### Other Changes
- Merge branch 'test/transition-call-site-flags' into 'master'
- test(simo-dev): pin transition_cmd call-site flags
- chore(simo-dev): release 0.1.186
- Merge branch 'fix/mrtmp-real-repo-name' into 'master'
- test: cover jira_scope empty-string fallback in review prompt
- chore(simo-dev): release 0.1.183
- Merge branch 'fix/autopilot-robust-merge-tri-state' into 'master'
- test(mr-presentation): make jira_scope fallback assertion discriminating
