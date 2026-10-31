# BugBait Roadmap

Working top-down. Checked = built.

## Phase 1 — Connect GitHub (done, except Device Flow)
- [x] Connect with token (verify, store in `.env`, show login)
- [x] Repo picker (own/starred/org repos, search, private repos work)
- [x] Authenticated clone so private repos and PRs work end to end
- [ ] Login with GitHub (OAuth Device Flow) upgrade

## Phase 2 — Test report card
- [ ] Instant no-AI analysis: zero-assertion tests, duplicates, sleeps, mock-everything
- [ ] Show alongside the mutation score in dashboard, CLI and VS Code

## Phase 3 — GitHub Action + PR comments
- [ ] Action that runs BugBait and comments the score + weakest spots on PRs
- [ ] Merge gates (status check fails when the score drops)
- [ ] Score badge + scheduled runs

## Phase 4 — Cheaper, faster runs
- [ ] Incremental runs (only attack files changed in the PR/branch)
- [ ] Budget caps (max spend per run, graceful stop)

## Phase 5 — More languages
- [ ] JavaScript/TypeScript (jest/vitest) runner
- [ ] Severity per surviving bug (critical/major/cosmetic)

## Phase 6 — Deeper experience
- [ ] Watch mode (`--watch`, live dashboard updates)
- [ ] VS Code: CodeLens, gutter icons, hover cards
- [ ] Shareable HTML report + sample gallery
- [ ] `bugbait.toml` config file + SARIF output

## Phase 7 — Team & product
- [ ] Trends dashboard (score over time per repo/file)
- [ ] Scheduled digests (email/Slack)
- [ ] Hosted cloud runs / GitHub App / education mode
