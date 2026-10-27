## 1.16.0 (2026-10-27)

### Features
- feat(simo-dev): stacked target for review-loop, freshness guards and mr-followup
- feat(simo-dev): target the parent branch for stacked MRs in commit
- feat(simo-dev): add post-merge release hook (mr-followup step 8.7)
- feat(simo-dev): task-intake renames the session after the ticket

### Bug Fixes
- fix(simo-dev): reviewers never glob /tmp for an earlier probe script
- fix(simo-dev): wait for the handback to reach disk before gating a review record
- fix(simo-dev): record reviews from a detached worker so a killed hook keeps them
- fix(simo-dev): guard start_session source_branch and align sub-task starts
- fix(simo-dev): call start_session instead of the retired spawn_task
- fix: recognise marked desktop-app worktrees in freshness hook and simo-status

### Other Changes
- chore(simo-dev): release 0.1.227
- chore(simo-dev): release 0.1.226
- chore(simo-dev): release 0.1.225
- chore(simo-dev): release 0.1.224
- chore(simo-dev): release 0.1.220
- docs(simo-dev): task-intake adopts a desktop tree left on a merged task branch
