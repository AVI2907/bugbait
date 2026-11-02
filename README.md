# BugBait

**Your tests pass. But would they catch a bug?**

BugBait is a team of AI agents, powered by Meta's Muse Spark, that plants small, realistic bugs in your code to find the holes in your test suite. Then it writes tests that close those holes, and keeps only tests it has verified actually work.

On [`inflection`](https://github.com/jpvanhal/inflection), a popular library with 455 tests, the existing suite caught **1 of 8** planted bugs. BugBait's verified tests raised that to **8 of 8** in under a minute.

## How it works

```
 your project ──► Saboteur agent ──► planted bugs ──► run your tests on each one
                  (Muse Spark)                               │
                                         caught ◄────────────┤
                                                             ▼
                                                     bugs that got through
                                                             │
                                  Fixer agent (Muse Spark) ──┤ writes a test for each
                                                             ▼
                              verify: passes on your code AND fails on the bug?
                                        yes ─► keep it        no ─► retry with feedback
                                                             │
                              before/after score · weakest spots · pull request
```

1. **Saboteur.** Reads your code and tests, then proposes subtle, realistic bugs (off-by-one errors, boundary mistakes, flipped conditions) that your tests are likely to miss. It rates each bug's realism, and artificial bugs are filtered out.
2. **Sandbox.** Every bug runs in its own throwaway copy of the project against your full test suite. Your files are never touched.
3. **Fixer.** For each bug that got through, it writes a test. A test is accepted only if it **passes on the original code and fails on the buggy code**. Otherwise the Fixer sees the pytest output and tries again. It can also decide a change is harmless and doesn't deserve a test.
4. **Results.** You get a before/after score, the weakest spots in your code (the functions where bugs slip through most), and a test file ready to add, or a pull request.

## What bugs get planted

The Saboteur plants small, realistic mistakes — the kind that survive code review. Every bug changes one
small piece of code, keeps it syntactically valid, and gets a realism rating (5 = developers do this all
the time, 1 = artificial). Bugs below your realism threshold are dropped before they ever run.

| Kind | Python example | JavaScript example | Java example |
|---|---|---|---|
| off-by-one | `range(n)` → `range(n + 1)` | `i < len` → `i <= len` | `i < n` → `i <= n` |
| boundary | `age >= 18` → `age > 18` | `age >= 18` → `age > 18` | `age >= 18` → `age > 18` |
| wrong operator | `a and b` → `a or b` | `a \|\| b` → `a && b` | `a && b` → `a \|\| b` |
| inverted condition | `if valid:` → `if not valid:` | `if (ok)` → `if (!ok)` | `if (ok)` → `if (!ok)` |
| wrong default | `def f(n=10)` → `def f(n=0)` | `n = 10` → `n = 0` | `int n = 10` → `int n = 0` |
| swapped arguments | `f(a, b)` → `f(b, a)` | `f(a, b)` → `f(b, a)` | `f(a, b)` → `f(b, a)` |
| missing case | a guard clause removed | a guard clause removed | a guard clause removed |
| wrong value | `timeout=30` → `timeout=300` | `timeout: 30` → `timeout: 300` | `timeout = 30` → `timeout = 300` |
| error handling | `except ValueError` → `except Exception` | `throw new Error(…)` → `return null` | `catch (IOException e)` → `catch (Exception e)` |

Each planted bug runs against your full test suite. **Caught** = at least one test failed (good).
**Survived** = every test still passed (a gap — the Fixer writes a verified test for it). Your score is
simply *caught ÷ planted*, before and after BugBait's tests.

## Use it on any project

BugBait works on Python (**pytest**/unittest), JavaScript (**node:test**) and Java (**Maven**) projects.
Give it any of these:

| | |
|---|---|
| A GitHub repo | `https://github.com/owner/repo` or `owner/repo` |
| A folder on your computer | `~/code/my-project` |
| A built-in example | `inflection`, `calculator`, `javascript`, `javacalc` |

Java notes: BugBait picks the JDK your `pom.xml` asks for (and tells you how to install it if missing).
Multi-module repos without a top-level `pom.xml` ask you to open one service folder. Gradle projects
work via their `gradlew` wrapper (or a `gradle` install) — Android projects additionally need the
Android SDK with `ANDROID_HOME` set.

For downloaded and local projects, BugBait creates a separate Python environment, installs the project's dependencies, and sets aside any tests that already fail, so they don't hide the results.

## Quick start

```bash
git clone https://github.com/AVI2907/bugbait.git
cd bugbait
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then add your META_API_KEY
```

### 1. Dashboard

```bash
python app.py               # opens http://localhost:5050
```

Paste a GitHub link or click **Choose folder…**, tick the files to attack, and click **Run with Muse Spark**. Watch each bug get planted, caught or not caught, and then fixed. **Replay** re-animates a saved run with no API calls.

### 2. VS Code

Open the `bugbait` folder in VS Code and press **F5** (or go to Run → Start Debugging → "Run BugBait extension"). In the new window that opens, open any Python project and click **BugBait** in the status bar.

- Lines where planted bugs got past your tests are underlined, with an explanation.
- A results panel shows the live run, the score, and the weakest spots.
- **Add BugBait tests to project** copies the verified tests next to your existing tests.
- **Open a pull request** sends them to GitHub.

To install the extension permanently: `cd vscode-extension && npx @vscode/vsce package`, then in VS Code run **Extensions: Install from VSIX…**.

### 3. Command line

```bash
python main.py inflection                          # example project
python main.py https://github.com/un33k/python-slugify --list-files
python main.py ~/code/my-project --files app/models.py --bugs 8
python main.py inflection --no-fix                 # measure only
python main.py calculator --offline                # no API key needed
```

## Pull requests

Add `GITHUB_TOKEN` to `.env` (a fine-grained token with **Contents** and **Pull requests** set to read & write). After a run, click **Open pull request** in the dashboard or VS Code, or run `python github_pr.py <project-name>`.

If you can push to the repo, the PR goes there. If you can't, BugBait forks it and opens the PR **in your fork**, so maintainers of projects you don't own never get unexpected pull requests. The PR description lists every gap found, why it slipped through, and the weakest spots.

## Open-weight models

Each agent can use a different provider. To run the Saboteur on **Muse Spark Open** via Baseten while the Fixer stays on the Meta Model API:

```
SABOTEUR_PROVIDER=baseten
BASETEN_API_KEY=...
```

Any OpenAI-compatible endpoint (for example Modal) works with `SABOTEUR_PROVIDER=custom`. See `.env.example`. Each run's report records which model played which role.

## Safety

- BugBait never edits your project. All work happens on temporary copies, and dependency installs run from a copy too.
- Tests run on your machine, so only point BugBait at code you trust. Installing a project's dependencies can run that project's code.
- The only writes to your project are the ones you choose: **Add tests to project** or **Open pull request**.

## Host it

The dashboard runs anywhere containers run. It reads `HOST`/`PORT` from the environment
(defaults to localhost:5050 with the browser auto-open, unchanged for local use):

```bash
docker build -t bugbait .
docker run -p 5050:5050 -e META_API_KEY=... [-e GITHUB_TOKEN=...] bugbait
```

The landing page in `docs/` is GitHub Pages ready: repo Settings → Pages → Deploy from branch → `main` / `docs`.

## Project layout

| File | What it does |
|---|---|
| `engine.py` | The pipeline: baseline → Saboteur → sandbox runs → Fixer → rescore → report |
| `languages.py` | Language plugins (Python, JavaScript): file patterns, test detection, syntax checks |
| `agents/saboteur.py`, `agents/fixer.py` | The two Muse Spark agents |
| `projects.py` | Loading projects from GitHub, folders or examples, and per-project Python environments |
| `runner.py` | Throwaway sandboxes that run pytest |
| `github_pr.py` | Opening pull requests |
| `app.py`, `static/index.html` | Web dashboard |
| `main.py` | Command line (and `--json` mode for VS Code) |
| `vscode-extension/` | VS Code extension |
| `tests/` | Unit tests for BugBait itself (`python -m pytest tests/`) |
| `targets/` | Example projects. `inflection` is MIT-licensed and unmodified (see its `SOURCE.md`) |

## Built for the Meta Global AI Developer Hackathon

All of BugBait was built during the hackathon (October 27 to November 4, 2026). The commit history shows how it came together.
