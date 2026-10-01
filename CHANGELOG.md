## 1.13.0 (2026-10-01)

### Features
- feat(simo-dev): offer next ready sub-task and gate umbrella close after merge

### Bug Fixes
- fix(simo-dev): guard empty MR title and target branch before create
- fix(simo-dev): move state files and git identity to UTF-8 end to end
- fix(simo-dev): skip the no-pipeline wait when a GitLab project has no CI config
- fix(simo-dev): never create a GitLab MR without an assignee
- fix(simo-dev): make seven scripts UTF-8 end to end on a cp1252 stdout
- fix(simo-dev): register hooks in public mirror and survive cp1252 consoles
- fix(simo-dev): load deferred tools before gating on their availability
- fix(simo-dev): dependency check uses the base hit for Done tickets too
- fix(simo-dev): dependency check sees merged-but-not-closed tickets
- fix: jira_link creates the blocks link in the right direction and detects it

### Other Changes
- chore(simo-dev): release 0.1.200
- docs(simo-dev): adopt harness worktrees in place, never move them
- chore(simo-dev): release 0.1.197
- Merge branch 'chore/remove-dead-fake-date-fixture' into 'master'
- chore: remove dead fake_date test fixture
- chore(simo-dev): release 0.1.196
- Merge branch 'chore/simo-dev-release-0-1-195' into 'master'
- Merge branch 'fix/utf8-state-and-git-identity' into 'master'
- chore(simo-dev): release 0.1.195
- chore(simo-dev): release 0.1.194
- docs(simo-dev): load deferred tools before every availability gate
- chore(simo-dev): release 0.1.193
- chore(simo-dev): release 0.1.192
- Merge branch 'chore/simo-dev-release-scratchpad' into 'master'
- chore(simo-dev): release 0.1.191
- docs: note subagent scratchpad collision, make reviewers use own mktemp dir
