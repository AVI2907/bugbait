// BugBait for VS Code.
// Runs the BugBait engine (python main.py <project> --json) and shows the results inside the editor:
// a live results panel, warnings on the lines where planted bugs got past your tests,
// and a command to add the verified tests to your project.

const vscode = require("vscode");
const cp = require("child_process");
const fs = require("fs");
const path = require("path");

let diagnostics, statusItem, output, panel;
let running = null;        // the child process of the current run
let lastRun = null;        // { project, bugs: Map, events: [], scoreAfter, root }

function activate(context) {
  diagnostics = vscode.languages.createDiagnosticCollection("bugbait");
  output = vscode.window.createOutputChannel("BugBait");
  statusItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  statusItem.text = "$(bug) BugBait";
  statusItem.tooltip = "Find out what your tests would miss";
  statusItem.command = "bugbait.testProject";
  statusItem.show();

  const reg = (id, fn) => context.subscriptions.push(vscode.commands.registerCommand(id, fn));
  reg("bugbait.testProject", () => testProject(context));
  reg("bugbait.testFile", uri => testFile(context, uri));
  reg("bugbait.showResults", () => showPanel(context));
  reg("bugbait.addTests", () => addTests());
  reg("bugbait.openPR", () => openPR());
  reg("bugbait.stop", () => stop());
  reg("bugbait.clear", () => { diagnostics.clear(); });
  reg("bugbait.locate", () => locateBugBait(true));

  context.subscriptions.push(diagnostics, output, statusItem,
    vscode.languages.registerCodeActionsProvider({ language: "python" }, new BugBaitActions(),
      { providedCodeActionKinds: [vscode.CodeActionKind.QuickFix] }));
}

function deactivate() { stop(); }

// ---------- finding BugBait and Python ----------

function isBugBaitDir(dir) {
  return dir && fs.existsSync(path.join(dir, "main.py")) && fs.existsSync(path.join(dir, "engine.py"));
}

async function locateBugBait(force = false) {
  const cfg = vscode.workspace.getConfiguration("bugbait");
  let dir = cfg.get("path");
  if (!force && isBugBaitDir(dir)) return dir;

  // When the extension runs from inside the BugBait repo (development mode), use that.
  const parent = path.resolve(__dirname, "..");
  if (!force && isBugBaitDir(parent)) return parent;

  const pick = await vscode.window.showOpenDialog({
    canSelectFolders: true, canSelectFiles: false, canSelectMany: false,
    openLabel: "Use this BugBait folder",
    title: "Where is BugBait? Choose the folder that contains main.py",
  });
  if (!pick) return null;
  dir = pick[0].fsPath;
  if (!isBugBaitDir(dir)) {
    vscode.window.showErrorMessage("That folder doesn't contain BugBait (main.py and engine.py). Choose the bugbait folder.");
    return null;
  }
  await cfg.update("path", dir, vscode.ConfigurationTarget.Global);
  return dir;
}

function pythonFor(bugbaitDir) {
  const configured = vscode.workspace.getConfiguration("bugbait").get("python");
  if (configured) return configured;
  for (const venv of ["venv", ".venv"]) {
    for (const sub of ["bin/python", "Scripts/python.exe"]) {
      const p = path.join(bugbaitDir, venv, sub);
      if (fs.existsSync(p)) return p;
    }
  }
  return process.platform === "win32" ? "python" : "python3";
}

// Run main.py and call onEvent for every JSON line it prints.
function runEngine(bugbaitDir, args, onEvent) {
  return new Promise(resolve => {
    const python = pythonFor(bugbaitDir);
    output.appendLine(`$ ${python} main.py ${args.join(" ")}`);
    const child = cp.spawn(python, ["main.py", ...args, "--json"], {
      cwd: bugbaitDir, env: { ...process.env, PYTHONUNBUFFERED: "1" },
      detached: process.platform !== "win32",
    });
    running = child;
    let buf = "", stderr = "";
    child.stdout.on("data", chunk => {
      buf += chunk.toString();
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
        if (!line) continue;
        try { onEvent(JSON.parse(line)); } catch { output.appendLine(line); }
      }
    });
    child.stderr.on("data", chunk => { stderr += chunk; output.append(chunk.toString()); });
    child.on("error", err => {
      onEvent({ event: "error", message: `Couldn't start Python (${python}): ${err.message}. ` +
        `Set "bugbait.python" in Settings, or create BugBait's venv.` });
    });
    child.on("close", code => {
      if (running === child) running = null;
      if (code && !stderr.includes("KeyboardInterrupt") && stderr.trim() && code !== 1) {
        onEvent({ event: "error", message: "BugBait stopped unexpectedly. See Output → BugBait for details." });
      }
      resolve(code);
    });
  });
}

function stop() {
  if (!running) return;
  try {
    if (process.platform === "win32") running.kill();
    else process.kill(-running.pid, "SIGTERM");   // also stops the test runs it started
  } catch { running.kill(); }
  running = null;
  setStatus(null);
  vscode.window.showInformationMessage("BugBait run stopped.");
}

function setStatus(text) {
  statusItem.text = text ? `$(sync~spin) BugBait: ${text}` : "$(bug) BugBait";
  statusItem.command = text ? "bugbait.showResults" : "bugbait.testProject";
}

// ---------- commands ----------

async function pickWorkspaceFolder() {
  const folders = vscode.workspace.workspaceFolders || [];
  if (!folders.length) {
    vscode.window.showWarningMessage("Open a project folder first (File → Open Folder), then run BugBait.");
    return null;
  }
  if (folders.length === 1) return folders[0].uri.fsPath;
  const pick = await vscode.window.showWorkspaceFolderPick({ placeHolder: "Which project should BugBait test?" });
  return pick ? pick.uri.fsPath : null;
}

async function testProject(context) {
  if (running) return vscode.window.showWarningMessage("BugBait is already running.", "Show progress", "Stop")
    .then(a => a === "Stop" ? stop() : a && showPanel(context));
  const bugbait = await locateBugBait(); if (!bugbait) return;
  const root = await pickWorkspaceFolder(); if (!root) return;

  // Ask which files to attack
  let files = null;
  await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: "BugBait: reading the project…" },
    () => runEngine(bugbait, [root, "--list-files"], e => {
      if (e.event === "file_list") files = e;
      else if (e.event === "setup") output.appendLine(e.message);
      else if (e.event === "error") vscode.window.showErrorMessage("BugBait: " + e.message);
    }));
  if (!files) return;
  if (!files.files.length) return vscode.window.showWarningMessage("BugBait didn't find any Python source files to test here.");

  const items = files.files.map(f => ({ label: f.path, description: `${f.lines} lines`, picked: files.suggested.includes(f.path) }));
  const chosen = await vscode.window.showQuickPick(items, {
    canPickMany: true, title: "BugBait: which files should the Saboteur attack?",
    placeHolder: "Fewer files means a faster run. Press Enter to start.",
  });
  if (!chosen || !chosen.length) return;
  startRun(context, bugbait, root, chosen.map(c => c.label));
}

async function testFile(context, uri) {
  if (running) return vscode.window.showWarningMessage("BugBait is already running.");
  const file = uri?.fsPath || vscode.window.activeTextEditor?.document.uri.fsPath;
  if (!file || !file.endsWith(".py")) return vscode.window.showWarningMessage("Open a Python file to test it with BugBait.");
  const folder = vscode.workspace.getWorkspaceFolder(vscode.Uri.file(file));
  if (!folder) return vscode.window.showWarningMessage("This file isn't inside an open project folder.");
  const bugbait = await locateBugBait(); if (!bugbait) return;
  const root = folder.uri.fsPath;
  startRun(context, bugbait, root, [path.relative(root, file).split(path.sep).join("/")]);
}

function startRun(context, bugbait, root, files) {
  const cfg = vscode.workspace.getConfiguration("bugbait");
  const args = [root, "--files", ...files, "--bugs", String(cfg.get("bugsPerFile") || 5)];
  if (!cfg.get("writeTests")) args.push("--no-fix");

  diagnostics.clear();
  lastRun = { root, bugs: new Map(), events: [], scoreAfter: null, bugbait };
  showPanel(context, true);

  vscode.window.withProgress({
    location: vscode.ProgressLocation.Notification, title: "BugBait", cancellable: true,
  }, (progress, token) => {
    token.onCancellationRequested(stop);
    return runEngine(bugbait, args, e => handleEvent(e, progress));
  }).then(() => setStatus(null));
}

// ---------- events ----------

function handleEvent(e, progress) {
  if (!lastRun) return;
  lastRun.events.push(e);
  post(e);
  const say = msg => { progress?.report({ message: msg }); setStatus(msg.length > 40 ? msg.slice(0, 40) + "…" : msg); };

  switch (e.event) {
    case "setup": output.appendLine(e.message); say(e.message); break;
    case "start": lastRun.project = e.project; say(`starting on ${e.project.name}`); break;
    case "baseline":
      if (e.passed) say(`${e.tests} existing tests pass`);
      else { output.appendLine(e.output || ""); vscode.window.showErrorMessage("BugBait: the project's own tests don't pass yet, so it can't measure them. See Output → BugBait.", "Show output").then(a => a && output.show()); }
      break;
    case "saboteur": say(`planting bugs in ${e.file}`); break;
    case "bugs": for (const b of e.bugs) lastRun.bugs.set(b.id, b); say(`testing ${e.bugs.length} planted bugs`); break;
    case "bug_result": lastRun.bugs.get(e.id).survived = e.survived; updateDiagnostics(); break;
    case "score_before": say(`tests caught ${e.caught} of ${e.total}`); break;
    case "fixer_start": say(`writing tests for ${e.count} bugs`); break;
    case "fix_result": lastRun.bugs.get(e.id).fix = e; updateDiagnostics(); break;
    case "bug_after": lastRun.bugs.get(e.id).after = e.survived; updateDiagnostics(); break;
    case "score_after": {
      lastRun.scoreAfter = e;
      const before = [...lastRun.bugs.values()].filter(b => !b.survived).length;
      vscode.window.showInformationMessage(
        `BugBait: your tests caught ${before} of ${e.total} planted bugs. With ${e.tests_added} new verified test${e.tests_added === 1 ? "" : "s"}: ${e.caught} of ${e.total}.`,
        "Add tests to project", ...(lastRun.project?.github ? ["Open pull request"] : []), "Show results").then(a => {
          if (a === "Add tests to project") addTests();
          if (a === "Open pull request") openPR();
          if (a === "Show results") vscode.commands.executeCommand("bugbait.showResults");
        });
      break;
    }
    case "done":
      if (!lastRun.scoreAfter) {
        const all = [...lastRun.bugs.values()];
        const missed = all.filter(b => b.survived).length;
        if (all.length) vscode.window.showInformationMessage(`BugBait: your tests caught ${all.length - missed} of ${all.length} planted bugs.`);
      }
      break;
    case "error": vscode.window.showErrorMessage("BugBait: " + e.message); break;
  }
}

function updateDiagnostics() {
  if (!lastRun?.project) return;
  const byFile = new Map();
  for (const b of lastRun.bugs.values()) {
    if (!b.survived || !b.context) continue;
    const line = b.context.line - 1;
    const lines = b.old.split("\n");
    const firstText = (b.context.lines[b.context.line - b.context.start] || "");
    const col = Math.max(0, firstText.indexOf(lines[0].trim()));
    const endLine = line + lines.length - 1;
    const range = new vscode.Range(line, col, endLine, lines.length > 1 ? lines[lines.length - 1].length : col + lines[0].trim().length);

    let severity = vscode.DiagnosticSeverity.Warning;
    let msg = `Your tests don't catch this: if \`${b.old.trim()}\` became \`${b.new.trim()}\`, every test would still pass.`;
    if (b.explanation) msg += ` ${b.explanation}`;
    if (b.after === false) { severity = vscode.DiagnosticSeverity.Information; msg = `BugBait wrote a verified test that catches a bug here (\`${b.new.trim()}\`). Run "BugBait: Add BugBait tests to project" to keep it.`; }
    else if (b.fix?.status === "skipped") { severity = vscode.DiagnosticSeverity.Hint; msg = `BugBait: a change here goes unnoticed, but it's harmless. ${b.fix.reason || ""}`; }

    const d = new vscode.Diagnostic(range, msg, severity);
    d.source = "BugBait"; d.code = `bug ${b.id}`;
    const file = path.join(lastRun.project.root, b.file);
    if (!byFile.has(file)) byFile.set(file, []);
    byFile.get(file).push(d);
  }
  diagnostics.clear();
  for (const [file, list] of byFile) diagnostics.set(vscode.Uri.file(file), list);
}

class BugBaitActions {
  provideCodeActions(document, range, context) {
    const ours = context.diagnostics.filter(d => d.source === "BugBait");
    if (!ours.length) return [];
    const show = new vscode.CodeAction("Show BugBait results", vscode.CodeActionKind.QuickFix);
    show.command = { command: "bugbait.showResults", title: "Show BugBait results" };
    show.diagnostics = ours;
    const actions = [show];
    if (lastRun?.scoreAfter) {
      const add = new vscode.CodeAction("Add BugBait's verified tests to the project", vscode.CodeActionKind.QuickFix);
      add.command = { command: "bugbait.addTests", title: "Add tests" };
      add.diagnostics = ours;
      actions.unshift(add);
    }
    return actions;
  }
}

// ---------- adding the generated tests to the project ----------

async function addTests() {
  const s = lastRun?.scoreAfter;
  if (!s) return vscode.window.showInformationMessage("Run BugBait first. Tests appear here after the Fixer writes them.");
  const from = path.join(lastRun.bugbait, s.saved_to);
  const to = path.join(lastRun.project.root, s.test_path);
  if (!fs.existsSync(from)) return vscode.window.showErrorMessage(`Couldn't find the generated tests at ${from}.`);
  if (fs.existsSync(to)) {
    const ok = await vscode.window.showWarningMessage(`${s.test_path} already exists. Replace it?`, { modal: true }, "Replace");
    if (ok !== "Replace") return;
  }
  fs.mkdirSync(path.dirname(to), { recursive: true });
  fs.copyFileSync(from, to);
  const doc = await vscode.workspace.openTextDocument(to);
  await vscode.window.showTextDocument(doc);
  vscode.window.showInformationMessage(`Added ${s.tests_added} verified test${s.tests_added === 1 ? "" : "s"} to ${s.test_path}.`);
}

// ---------- pull request ----------

async function openPR() {
  const s = lastRun?.scoreAfter, p = lastRun?.project;
  if (!s || !p) return vscode.window.showInformationMessage("Run BugBait first. A pull request needs the verified tests from a run.");
  if (!p.github) return vscode.window.showWarningMessage("This project isn't connected to GitHub (no git remote found). Use \"Add BugBait tests to project\" instead.");
  const ok = await vscode.window.showInformationMessage(
    `Open a pull request on ${p.github} with ${s.tests_added} verified test${s.tests_added === 1 ? "" : "s"}? ` +
    `If you can't push there, BugBait uses your fork.`, { modal: true }, "Open pull request");
  if (ok !== "Open pull request") return;
  let url = null, err = "";
  await vscode.window.withProgress({ location: vscode.ProgressLocation.Notification, title: "BugBait: opening a pull request…" },
    () => new Promise(resolve => {
      const child = cp.spawn(pythonFor(lastRun.bugbait), ["github_pr.py", p.name], { cwd: lastRun.bugbait });
      let out = "";
      child.stdout.on("data", c => out += c);
      child.stderr.on("data", c => err += c);
      child.on("error", e => { err = e.message; resolve(); });
      child.on("close", () => { url = (out.match(/https:\/\/github\.com\/\S+/) || [])[0] || null; resolve(); });
    }));
  if (url) {
    post({ event: "pr", url });
    const a = await vscode.window.showInformationMessage(`Pull request opened: ${url}`, "Open in browser");
    if (a) vscode.env.openExternal(vscode.Uri.parse(url));
  } else {
    output.appendLine(err);
    vscode.window.showErrorMessage("BugBait: couldn't open the pull request. " + (err.trim().split("\n").pop() || ""));
  }
}

// ---------- results panel ----------

function showPanel(context, reset = false) {
  if (panel) {
    panel.reveal(vscode.ViewColumn.Beside, true);
    if (reset) panel.webview.postMessage({ event: "reset" });
    else replayInto();
    return;
  }
  panel = vscode.window.createWebviewPanel("bugbait", "BugBait", { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
    { enableScripts: true, retainContextWhenHidden: true, localResourceRoots: [vscode.Uri.file(path.join(context.extensionPath, "media"))] });
  const html = fs.readFileSync(path.join(context.extensionPath, "media", "panel.html"), "utf8");
  const nonce = Math.random().toString(36).slice(2) + Math.random().toString(36).slice(2);
  panel.webview.html = html.replace(/\{\{nonce\}\}/g, nonce).replace(/\{\{csp\}\}/g, panel.webview.cspSource);
  panel.webview.onDidReceiveMessage(m => {
    if (m.type === "ready") replayInto();
    if (m.type === "open") openAt(m.file, m.line);
    if (m.type === "addTests") addTests();
    if (m.type === "openPR") openPR();
    if (m.type === "run") vscode.commands.executeCommand("bugbait.testProject");
  });
  panel.onDidDispose(() => { panel = null; });
}

function post(e) { if (panel) panel.webview.postMessage(e); }
function replayInto() {
  if (!panel) return;
  panel.webview.postMessage({ event: "reset" });
  for (const e of lastRun?.events || []) panel.webview.postMessage(e);
}

async function openAt(file, line) {
  if (!lastRun?.project) return;
  const doc = await vscode.workspace.openTextDocument(path.join(lastRun.project.root, file));
  const pos = new vscode.Position(Math.max(0, (line || 1) - 1), 0);
  await vscode.window.showTextDocument(doc, { viewColumn: vscode.ViewColumn.One, selection: new vscode.Range(pos, pos) });
}

module.exports = { activate, deactivate };
