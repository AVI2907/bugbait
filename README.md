# BugBait

**Your tests pass. But would they catch a bug?**

BugBait is a team of AI agents, powered by Meta's Muse Spark, that plants small, realistic bugs in your code to find the holes in your test suite. Then it writes tests that close those holes — keeping only tests it has verified actually work.

On [`inflection`](https://github.com/jpvanhal/inflection), a popular library with 455 tests, the existing suite caught **1 of 8** planted bugs. BugBait's verified tests raised that to **8 of 8** in under a minute.

## How it works

```
your project ──► Saboteur plants bugs ──► your tests run on each one
                                              │              │
                                         caught (good)   got through (a gap)
                                                             │
                                              Fixer writes a test for each gap
                                              keep it only if it passes on your
                                              code AND fails on the bug
```

1. **Saboteur** proposes subtle, realistic bugs (off-by-one errors, flipped conditions) and rates each one's realism; artificial bugs are filtered out.
2. **Sandbox** runs every bug on a throwaway copy of your project. Your files are never touched.
3. **Fixer** writes a test per surviving bug, retrying with feedback until it passes on your code and fails on the bug — or decides the change is harmless.
4. **Results**: before/after score, weakest spots (where bugs slip through most), and a test file ready to add — or a pull request.

## What bugs get planted

Small, realistic mistakes — the kind that survive code review. Each one changes a single expression, keeps the code valid, and gets a realism rating (bugs below your threshold never run).

| Kind | Example |
|---|---|
| off-by-one | `i < n` → `i <= n` |
| boundary | `age >= 18` → `age > 18` |
| wrong operator | `a && b` → `a \|\| b` |
| inverted condition | `if (ok)` → `if (!ok)` |
| wrong default | `n = 10` → `n = 0` |
| swapped arguments | `f(a, b)` → `f(b, a)` |
| missing case | a guard clause removed |
| wrong value | `timeout = 30` → `timeout = 300` |
| error handling | `catch (IOException)` → `catch (Exception)` |

See the [landing page](https://avi2907.github.io/bugbait/#bugs) for examples in all 10 languages. **Caught** = a test failed (good). **Survived** = everything still passed (a gap). Your score is *caught ÷ planted*, before and after BugBait's tests.

## Languages

Python (**pytest**/unittest) · JavaScript & TypeScript (**node:test**) · Go · Java (**Maven** & **Gradle**) · Rust · Ruby (**minitest** & **RSpec**) · PHP (**PHPUnit**) · C# (**dotnet test**) · Kotlin (**kotlinc** + JUnit).

Point BugBait at a GitHub repo (`owner/repo`), a local folder, or a built-in example (`calculator`, `rustcalc`, `phpcalc`, `kotlincalc`, …). Each language needs its toolchain installed (`node`, `go`, `cargo`, `php`, `dotnet`, `kotlinc` + JDK, …).

## Quick start

```bash
git clone https://github.com/AVI2907/bugbait.git && cd bugbait
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then add your META_API_KEY
```

**Dashboard** — `python app.py` opens http://localhost:5050. Paste a GitHub link or choose a folder, tick files, and run. **Replay** re-animates a saved run with no API calls.

**VS Code** — open the folder, press **F5**, then click **BugBait** in the status bar. Surviving bugs are underlined in your editor; one click adds the tests or opens a PR.

**Command line:**

```bash
python main.py calculator --offline                # demo, no API key needed
python main.py ~/code/my-project --files app/models.py --bugs 8
python main.py . --staged --no-fix                 # test staged files only
python main.py . --fail-under 70                   # exit 1 unless 70%+ caught
python main.py --install-hook ~/code/my-project   # block weak commits
```

## Pull requests & gates

Add `GITHUB_TOKEN` to `.env`, then **Open pull request** from the dashboard or VS Code (`python github_pr.py <project>` on the CLI). Can't push? BugBait forks first, so strangers never get surprise PRs.

Score every PR automatically with the reusable Action (`avi2907/bugbait@v1`) — the report lands as an in-place PR comment. Options (top right) → Score history compares any two runs.

## Open-weight models

Each agent can use a different provider (`SABOTEUR_PROVIDER=baseten`, `=custom` for any OpenAI-compatible endpoint). See `.env.example`.

## Safety

BugBait never edits your project — everything runs on temporary copies. Only point it at code you trust (dependency installs can run project code). The only writes are the ones you choose: add tests or open a PR.

## Host it

```bash
docker build -t bugbait .
docker run -p 5050:5050 -e META_API_KEY=... [-e GITHUB_TOKEN=...] bugbait
```

The landing page in `docs/` is GitHub Pages ready: Settings → Pages → Deploy from branch → `main` / `docs`.

## Project layout

| File | What it does |
|---|---|
| `engine.py` | Pipeline: baseline → Saboteur → sandbox runs → Fixer → rescore → report |
| `languages.py` | Language plugins: file patterns, test detection, syntax checks |
| `agents/` | The Saboteur and Fixer agents |
| `projects.py`, `runner.py` | Project loading, Python environments, throwaway sandboxes |
| `app.py`, `main.py` | Web dashboard and command line (`--json` feeds VS Code) |
| `github_pr.py`, `pr_comment.py`, `action.yml`, `precommit.py` | PRs, PR comments, GitHub Action, pre-commit hook |
| `vscode-extension/` | VS Code extension |
| `tests/`, `targets/` | Unit tests (`python -m pytest tests/`) and example projects |

## Built for the Meta Global AI Developer Hackathon

All of BugBait was built during the hackathon (October 27 to November 4, 2026). The commit history shows how it came together.
