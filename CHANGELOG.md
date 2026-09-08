## 1.4.0 (2026-09-08)

### Features
- feat(simo-dev): detect plan drift after auto-fixes
- feat(plan-execute): delegate task execution to Agent dispatch

### Bug Fixes
- fix: recognize a spawned session's own first turn before re-spawning
- fix(simo-dev): tolerate both ## and ### Task N: plan headers
- fix(task-intake): move ticket take-charge to after direction validation
- fix(simo-dev): gate SendMessage reviewer-resume behind a tool-availability check

### Other Changes
- docs(simo-dev): guard worktree-carry against sweeping another session's file
- chore(simo-dev): release 0.1.168
- chore(simo-dev): release 0.1.167
- docs(simo-dev): flag literal-wording violations in plan-write and plan-reviewer
- docs(simo-dev): unify task-intake hand-off gate and widen plan-critique autofix to medium
- chore(simo-dev): release 0.1.165
- chore(simo-dev): release 0.1.163
