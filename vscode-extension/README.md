# BugBait for VS Code

Find out what your tests would miss, without leaving your editor.

BugBait's AI agents (powered by Meta's Muse Spark) plant small, realistic bugs in your Python code and run your tests against each one. Every bug your tests miss is marked right on the line where it was planted. Then the Fixer agent writes a test for each gap, and BugBait keeps only tests that are verified to catch the bug.

## Commands

- **BugBait: Test this project.** Choose files and run BugBait on the open folder. You can also click **BugBait** in the status bar.
- **BugBait: Test the current file.** Right-click a Python file or the editor.
- **BugBait: Show results.** Opens the live results panel.
- **BugBait: Add BugBait tests to project.** Copies the verified tests next to your existing tests.
- **BugBait: Stop the current run.**

## Setup

1. Set up BugBait itself (see the main README): a `venv` with `pip install -r requirements.txt` and your `META_API_KEY` in `.env`.
2. The first time you run a command, the extension asks where your `bugbait` folder is. You can change it later in Settings under `bugbait.path`.

## Settings

- `bugbait.path` sets the BugBait folder.
- `bugbait.python` sets the Python used to run BugBait. It defaults to BugBait's own venv.
- `bugbait.bugsPerFile` sets how many bugs to plant per file (default 5).
- `bugbait.writeTests` turns the Fixer agent on or off.

BugBait never edits your code. It works on temporary copies, and only writes to your project when you choose **Add tests to project**.
