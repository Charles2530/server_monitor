'use strict';
const vscode = require('vscode');
function optionalRequire(name) { try { return require(name); } catch (_) { return null; } }
const pathModule = optionalRequire('path');
const execFile = optionalRequire('child_process')?.execFile;

function resolvePath(value) { return pathModule?.resolve ? pathModule.resolve(value) : String(value); }
function joinPath(...values) { return pathModule?.join ? pathModule.join(...values) : values.join('/'); }

function runGit(args, allowExitOne = false) {
  if (!execFile) return Promise.reject(new Error('Git 进程接口不可用'));
  return new Promise((resolve, reject) => execFile('git', args, {encoding: 'utf8', maxBuffer: 8 * 1024 * 1024}, (error, stdout = '', stderr = '') => {
    const result = {stdout: String(stdout), stderr: String(stderr), code: error?.code || 0};
    if (error && !(allowExitOne && result.code === 1)) {
      error.stdout = result.stdout; error.stderr = result.stderr;
      reject(error); return;
    }
    resolve(result);
  }));
}

function workspaceRoot() {
  const uri = vscode.window.activeTextEditor?.document?.uri;
  const folder = uri && vscode.workspace.getWorkspaceFolder?.(uri);
  return folder?.uri?.fsPath || vscode.workspace.workspaceFolders?.[0]?.uri?.fsPath || null;
}

function listOutput(text) {
  return String(text || '').split(/\0|\r?\n/).map(item => item.trim()).filter(Boolean);
}

async function prepareGitRoot() {
  const root = workspaceRoot();
  if (!root) throw new Error('没有打开工作区');
  const normalized = resolvePath(root);
  const safe = await runGit(['config', '--global', '--get-all', 'safe.directory'], true);
  const safeDirectories = listOutput(safe.stdout).map(item => resolvePath(item));
  if (!safeDirectories.includes(normalized)) await runGit(['config', '--global', '--add', 'safe.directory', normalized]);
  const resolved = await runGit(['-C', normalized, 'rev-parse', '--show-toplevel']);
  return resolvePath(resolved.stdout.trim() || normalized);
}

const REVIEW_FILTERS = [
  {label: '全部文件', key: 'all'},
  {label: 'Python（.py / .pyi）', suffixes: ['.py', '.pyi']},
  {label: 'JavaScript / TypeScript', suffixes: ['.js', '.jsx', '.ts', '.tsx']},
  {label: '配置与文档', suffixes: ['.json', '.jsonc', '.yaml', '.yml', '.toml', '.ini', '.md', '.txt']},
  {label: '自定义扩展名…', key: 'custom'}
];

function normalizeSuffixes(value) {
  return String(value || '').split(/[\s,]+/).map(item => item.trim().toLowerCase()).filter(Boolean)
    .map(item => item.startsWith('.') ? item : '.' + item);
}

async function changedPaths(root, scope) {
  const tracked = await runGit(['-C', root, 'diff', ...(scope === 'staged' ? ['--cached'] : []), '--name-only', '-z']);
  const untracked = scope === 'working'
    ? await runGit(['-C', root, 'ls-files', '--others', '--exclude-standard', '-z'])
    : {stdout: ''};
  const trackedFiles = listOutput(tracked.stdout);
  const untrackedFiles = listOutput(untracked.stdout);
  return {paths: [...new Set([...trackedFiles, ...untrackedFiles])], untracked: new Set(untrackedFiles)};
}

async function filteredPatch(root, scope, files, untracked) {
  const trackedFiles = files.filter(file => !untracked.has(file));
  const tracked = trackedFiles.length
    ? await runGit(['-C', root, 'diff', ...(scope === 'staged' ? ['--cached'] : []), '--binary', '--', ...trackedFiles])
    : {stdout: ''};
  const pieces = [tracked.stdout].filter(Boolean);
  if (scope === 'working') {
    for (const file of files.filter(item => untracked.has(item))) {
      const result = await runGit(['-C', root, 'diff', '--no-index', '--binary', '--', '/dev/null', joinPath(root, file)], true);
      if (result.stdout) pieces.push(result.stdout);
    }
  }
  return pieces.join('\n');
}

// Runs in the workspace extension host: these are container paths, never Mac paths.
function fileUri(value) {
  if (!value || !['file', 'vscode-remote'].includes(value.scheme)) return null;
  return value.scheme === 'file' ? value : vscode.Uri.file(value.path);
}
function targets(clicked, selected) {
  const primary = fileUri(clicked);
  if (clicked && !primary) return [];
  const values = primary && Array.isArray(selected) && selected.some(item => fileUri(item)?.fsPath === primary.fsPath)
    ? selected : [primary || vscode.window.activeTextEditor?.document.uri];
  const seen = new Set();
  return values.map(fileUri).filter(item => item && !seen.has(item.fsPath) && seen.add(item.fsPath));
}
async function addReference(clicked, selected, mode = 'auto') {
  const editor = vscode.window.activeTextEditor;
  const items = mode === 'selection' ? [fileUri(editor?.document.uri)].filter(Boolean) : targets(clicked, selected);
  const selection = items.length === 1 && editor && !editor.selection.isEmpty && fileUri(editor.document.uri)?.fsPath === items[0].fsPath;
  if (!items.length || (mode === 'selection' && !selection)) {
    vscode.window.showWarningMessage(mode === 'selection' ? '请先在文件中选中要引用的代码。' : '请选择工作区内的文件或文件夹。');
    return;
  }
  try {
    const extension = vscode.extensions.getExtension('openai.chatgpt');
    if (!extension) {
      vscode.window.showWarningMessage('Codex 插件尚未安装完成，请重新打开编辑器后重试。');
      return;
    }
    if (!extension.isActive) await extension.activate();
    if (selection && (mode === 'auto' || mode === 'selection')) {
      // Native handler captures one-based start/end lines before focusing Codex.
      await vscode.commands.executeCommand('chatgpt.addToThread');
    } else {
      // URI mentions are independent; dispatch them together without reading files or enumerating folders.
      await Promise.all(items.map(item => vscode.commands.executeCommand('chatgpt.addFileToThread', item)));
    }
  } catch (_) {
    vscode.window.showErrorMessage('添加 Codex 引用失败，请确认插件已加载后重试。');
  }
}
let capturingTerminal = false;
async function addTerminalSelection(context) {
  if (capturingTerminal) return;
  const terminal = vscode.window.activeTerminal;
  if (!terminal) return vscode.window.showWarningMessage('请先在终端中选中要引用的日志。');
  if (!vscode.extensions.getExtension('openai.chatgpt')) {
    return vscode.window.showWarningMessage('Codex 插件尚未安装完成，请重新打开编辑器后重试。');
  }
  capturingTerminal = true;
  try {
    const clipboard = vscode.env.clipboard;
    // This action explicitly copies; the text-only API cannot restore rich clipboard data.
    // Clear stale text so a vanished selection never attaches the previous clipboard.
    await clipboard.writeText('');
    await vscode.commands.executeCommand('workbench.action.terminal.copySelection');
    const text = await clipboard.readText();
    if (!text.trim()) return vscode.window.showWarningMessage('请先在终端中选中要引用的日志。');
    if (Buffer.byteLength(text, 'utf8') > 65536) {
      return vscode.window.showWarningMessage('选中的日志超过 64 KiB，请缩小选区后重试。');
    }
    // Keep snapshots outside the project, on the same host where Codex reads files.
    const file = vscode.Uri.joinPath(context.globalStorageUri, `terminal-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.txt`);
    const cwd = terminal.shellIntegration?.cwd?.fsPath;
    await vscode.workspace.fs.createDirectory(context.globalStorageUri);
    await vscode.workspace.fs.writeFile(file, Buffer.from(`终端：${terminal.name}\n${cwd ? `目录：${cwd}\n` : ''}\n${text}`, 'utf8'));
    // Desktop storage may use vscode-userdata; Codex expects a host filesystem URI.
    await addReference(vscode.Uri.file(file.fsPath), undefined, 'file');
  } catch (_) {
    vscode.window.showErrorMessage('引用终端日志失败，请确认剪贴板可用后重试。');
  } finally {
    capturingTerminal = false;
  }
}
async function reviewChanges(context) {
  try {
    const git = vscode.extensions.getExtension('vscode.git');
    if (git && !git.isActive) await git.activate();
    const available = await vscode.commands.getCommands(true);
    const scopes = [
      { label: '$(diff) 工作区改动', key: 'working', command: 'git.viewChanges' },
      { label: '$(check) 已暂存改动', key: 'staged', command: 'git.viewStagedChanges' },
    ].filter(item => available.includes(item.command));
    if (!scopes.length) return vscode.window.showWarningMessage('当前 Git 差异视图不可用，请确认已启用内置 Git 扩展。');
    const scope = await vscode.window.showQuickPick(scopes, {placeHolder: '选择要审阅的改动范围'});
    if (!scope) return;
    const filter = await vscode.window.showQuickPick(REVIEW_FILTERS, {placeHolder: '选择要审阅的文件类型'});
    if (!filter) return;
    if (filter.key === 'all') {
      await prepareGitRoot();
      await vscode.commands.executeCommand(scope.command);
      return;
    }
    let suffixes = filter.suffixes;
    if (filter.key === 'custom') {
      suffixes = normalizeSuffixes(await vscode.window.showInputBox({prompt: '输入扩展名，多个扩展名用逗号或空格分隔', placeHolder: 'py, proto'}));
      if (!suffixes.length) return vscode.window.showWarningMessage('请输入至少一个有效的文件扩展名。');
    }
    const root = await prepareGitRoot();
    const changes = await changedPaths(root, scope.key);
    const files = changes.paths.filter(file => suffixes.some(suffix => file.toLowerCase().endsWith(suffix)));
    if (!files.length) return vscode.window.showWarningMessage('当前范围没有匹配所选文件类型的改动。');
    const patch = await filteredPatch(root, scope.key, files, changes.untracked);
    if (!patch.trim()) return vscode.window.showWarningMessage('当前范围没有可打开的匹配差异。');
    const file = vscode.Uri.joinPath(context.globalStorageUri, `review-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.diff`);
    await vscode.workspace.fs.createDirectory(context.globalStorageUri);
    await vscode.workspace.fs.writeFile(file, Buffer.from(patch, 'utf8'));
    await vscode.commands.executeCommand('vscode.open', fileUri(file));
  } catch (error) {
    const detail = error?.stderr || error?.message || '';
    vscode.window.showErrorMessage(`无法打开差异视图${detail ? `：${detail.trim()}` : '，请确认当前文件夹属于 Git 仓库且当前用户有读写权限。'}`);
  }
}
exports.activate = context => {
  const recent = vscode.window.createStatusBarItem('ilab.recentProjects', vscode.StatusBarAlignment.Left, 1);
  recent.name = '历史项目'; recent.text = '$(history) 历史项目';
  recent.tooltip = '切换此容器中最近打开的文件夹或工作区';
  recent.command = 'workbench.action.openRecent';
  context.subscriptions.push(recent);
  recent.show();
  const review = vscode.window.createStatusBarItem('ilab.reviewChanges', vscode.StatusBarAlignment.Left, 0);
  review.name = '审阅改动'; review.text = '$(git-compare) 审阅改动';
  review.tooltip = '打开工作区或已暂存改动的 Git 差异视图';
  review.command = 'ilab.reviewChanges';
  context.subscriptions.push(review,
    vscode.commands.registerCommand('ilab.reviewChanges', () => reviewChanges(context)),
    vscode.commands.registerCommand('ilab.codex.addTerminalSelection', () => addTerminalSelection(context)));
  review.show();
  for (const [name, mode] of [['addReference', 'auto'], ['addFileReference', 'file'],
    ['addFolderReference', 'file'], ['addSelectionReference', 'selection']]) {
    context.subscriptions.push(vscode.commands.registerCommand('ilab.codex.' + name,
      (clicked, selected) => addReference(clicked, selected, mode)));
  }
};
