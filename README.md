# 🐞 BugBait

**Your tests pass. But would they catch a bug?**

[![Site](https://img.shields.io/badge/site-live-success)](https://avi2907.github.io/bugbait/)
[![Languages](https://img.shields.io/badge/languages-10-blue)](#-languages)
[![Python](https://img.shields.io/badge/python-3.10%2B-yellow)](#-quick-start)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED)](#-host-it)

BugBait is a team of AI agents, powered by Meta's Muse Spark, that **plants small, realistic bugs in your code** to expose the holes in your test suite — then **writes verified tests that close those holes**.

> On [`inflection`](https://github.com/jpvanhal/inflection), a popular library with 455 tests, the existing suite caught **1 of 8** planted bugs. BugBait's verified tests raised that to **8 of 8** in under a minute.

## 🔁 How it works

```
your project ──► Saboteur plants bugs ──► your tests run on each one
                                              │              │
                                         caught ✅      survived 🕳️ (a gap)
                                                             │
                                              Fixer writes a test for each gap
                                              kept only if it passes on your
                                              code AND fails on the bug
```

| Step | What happens |
|---|---|
| **1. Baseline** | Your test suite runs untouched. Tests already failing are set aside so they can't skew the score. |
| **2. Saboteur** | An agent studies your code and plants subtle bugs (off-by-one, flipped conditions, dropped checks), each rated for realism. Artificial bugs are filtered out. |
| **3. Sandbox** | Every bug runs against your full suite on a throwaway copy. Your files are never touched. |
| **4. Fixer** | For each surviving bug, an agent writes a test — accepted only if it **passes on your code and fails on the bug**, else retried with feedback. Harmless changes get no test. |
| **5. Results** | Before/after score, weakest spots, and a test file ready to add — or a pull request. |

**Your score** is simply *caught ÷ planted*, measured before and after BugBait's tests.

## 🐛 Bugs that get planted

One-line changes that keep the code valid — the kind that survive code review:

| Kind | Example |
|---|---|
| off-by-one · boundary | `i < n` → `i <= n` · `age >= 18` → `age > 18` |
| wrong operator · inverted condition | `a && b` → `a \|\| b` · `if (ok)` → `if (!ok)` |
| wrong default · wrong value | `n = 10` → `n = 0` · `timeout = 30` → `timeout = 300` |
| swapped arguments · missing case | `f(a, b)` → `f(b, a)` · a guard clause removed |
| error handling | `catch (IOException)` → `catch (Exception)` |

👉 [See examples in all 10 languages](https://avi2907.github.io/bugbait/#bugs)

## 🌍 Languages

| Language | Test runner | Needs installed |
|---|---|---|
| Python | pytest / unittest | Python 3.10+ |
| JavaScript · TypeScript | node:test | Node.js (22.18+ for TS, zero build step) |
| Go | go test | `go` + `go.mod` |
| Java | Maven & Gradle | JDK (+ Android SDK for Android apps) |
| Rust | cargo test | `cargo` + `Cargo.toml` |
| Ruby | minitest & RSpec | the `minitest` or `rspec` gem |
| PHP | PHPUnit | PHP 8.1+ + PHPUnit |
| C# | dotnet test (xUnit, NUnit, MSTest) | .NET SDK + `.sln`/`.csproj` |
| Kotlin | kotlinc + JUnit | `kotlinc` + JDK (test libs download once, automatically) |

Point BugBait at a **GitHub repo** (`owner/repo`), a **local folder**, or a **built-in example** (`calculator`, `rustcalc`, `phpcalc`, `kotlincalc`, … — try one with `--offline`, no API key needed).

## 🏗️ Architecture

Two interfaces, one engine. Every run flows through the same pipeline no matter where you start it:

```
        ┌─────────────┐  ┌─────────────┐
        │  Dashboard  │  │     CLI     │
        │ Flask + JS  │  │  main.py    │
        └──────┬──────┘  └──────┬──────┘
               └────────┼───────┘
                        ▼
              ┌────────────────────┐
              │       Engine       │  baseline → bugs → run → fix → rescore
              └─────────┬──────────┘
        ┌───────────────┼──────────────┐
        ▼              ▼              ▼
  ┌───────────┐  ┌───────────┐  ┌───────────┐
  │ Saboteur  │  │  Sandbox  │  │   Fixer   │
  │  agent    │  │  runner   │  │   agent   │
  └───────────┘  └───────────┘  └───────────┘
        │              │              │
        └──────────────┼──────────────┘
                       ▼
   score · weakest spots · verified tests · PR comment
```

| Layer | What it does |
|---|---|
| **Interfaces** | Flask dashboard (point-and-click + score history) and CLI (scripting, `--staged`, `--fail-under`, pre-commit hook, machine-readable `--json`). |
| **Engine** | Owns the pipeline: measures the baseline, fans bugs out across workers, rescores after the Fixer, and writes one JSON report per run. |
| **Agents** | Saboteur and Fixer call Muse Spark through a provider layer (`llm.py`) — Meta Model API by default, swappable per agent to Baseten or any OpenAI-compatible endpoint. |
| **Sandbox runner** | Copies the project to a temp folder per bug, runs the native test command for the detected language (pytest, `node --test`, `go test`, Maven/Gradle, cargo, RSpec/minitest, PHPUnit, `dotnet test`, kotlinc+JUnit), parses results. Nothing touches your files. |
| **Outputs** | Before/after score, weakest-spot ranking, a merged verified-test file, GitHub PRs, in-place PR comments, and a reusable GitHub Action. |

## 🧰 Built with

**Core** — Python 3.10+, Flask, pytest · **AI** — Meta Muse Spark (Meta Model API), Baseten + custom OpenAI-compatible providers · **Packaging** — Docker, GitHub Action (`action.yml`) · **Test runners driven** — pytest, node:test, go test, Maven, Gradle, cargo, RSpec, minitest, PHPUnit, dotnet, kotlinc + JUnit · **Site** — hand-written HTML/CSS on GitHub Pages.

## 🚀 Quick start

```bash
git clone https://github.com/AVI2907/bugbait.git && cd bugbait
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # then add your META_API_KEY
```

**🖥️ Dashboard** — `python app.py` → http://localhost:5050. Paste a repo link or pick a folder, tick files, and watch each bug get planted, caught-or-not, then fixed. **Replay** re-animates any saved run free.

**⌨️ Command line:**

```bash
python main.py calculator --offline                # demo, no key needed
python main.py ~/code/my-project --files app/models.py --bugs 8
python main.py . --staged --no-fix                 # measure only, staged files
python main.py . --fail-under 70                   # exit 1 unless 70%+ caught
python main.py --install-hook ~/code/my-project   # block weak commits
```

## 🤖 Gates & pull requests

Add `GITHUB_TOKEN` to `.env`, then **Open pull request** from the dashboard (`python github_pr.py <project>` on the CLI). Can't push to the repo? BugBait forks first — strangers never get surprise PRs.

Score every PR automatically:

```yaml
- uses: avi2907/bugbait@v1
  with:
    fail-under: 70
    api-key: ${{ secrets.META_API_KEY }}
```

The report lands as an in-place PR comment (updated, never spammed). **Options → Score history** compares any two runs side by side.

## 🔮 Future work

A **VS Code extension** (surviving bugs underlined in your editor, one-click runs) is on the roadmap — see [ROADMAP.md](ROADMAP.md) for the full plan.

<details>
<summary><strong>🧱 Project layout</strong></summary>

| File | What it does |
|---|---|
| `engine.py` | Pipeline: baseline → Saboteur → sandbox runs → Fixer → rescore → report |
| `languages.py` | Language plugins: file patterns, test detection, syntax checks |
| `agents/` | The Saboteur and Fixer agents |
| `projects.py`, `runner.py` | Project loading, Python environments, throwaway sandboxes |
| `app.py`, `main.py` | Dashboard and CLI (`--json` prints machine-readable events) |
| `github_pr.py`, `pr_comment.py`, `action.yml`, `precommit.py` | PRs, PR comments, GitHub Action, pre-commit hook |
| `tests/`, `targets/` | Unit tests (`python -m pytest tests/`) and example projects |

</details>

<details>
<summary><strong>🔒 Safety & hosting</strong></summary>

- BugBait never edits your project — everything runs on temporary copies. Only point it at code you trust, since dependency installs can run project code.
- Each agent can use a different model provider (`SABOTEUR_PROVIDER=baseten`, `=custom` for any OpenAI-compatible endpoint). See `.env.example`.
- Host the dashboard anywhere containers run: `docker build -t bugbait .` + `docker run -p 5050:5050 -e META_API_KEY=... bugbait`. The landing page in `docs/` is GitHub Pages ready (`main` / `docs`).

</details>

---
*Built for the Meta Global AI Developer Hackathon (Oct 27 – Nov 4, 2026). The commit history shows how it came together.*
