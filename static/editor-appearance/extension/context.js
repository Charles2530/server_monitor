'use strict';
const vscode = require('vscode');

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
async function reviewChanges() {
  try {
    const git = vscode.extensions.getExtension('vscode.git');
    if (git && !git.isActive) await git.activate();
    const available = await vscode.commands.getCommands(true);
    const items = [
      { label: '$(diff) 工作区改动', command: 'git.viewChanges' },
      { label: '$(check) 已暂存改动', command: 'git.viewStagedChanges' },
    ].filter(item => available.includes(item.command));
    if (!items.length) return vscode.window.showWarningMessage('当前 Git 差异视图不可用，请确认已启用内置 Git 扩展。');
    const item = await vscode.window.showQuickPick(items, { placeHolder: '审阅工作区改动' });
    if (item) await vscode.commands.executeCommand(item.command);
  } catch (_) {
    vscode.window.showErrorMessage('无法打开差异视图，请确认当前文件夹属于 Git 仓库。');
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
    vscode.commands.registerCommand('ilab.reviewChanges', reviewChanges),
    vscode.commands.registerCommand('ilab.codex.addTerminalSelection', () => addTerminalSelection(context)));
  review.show();
  for (const [name, mode] of [['addReference', 'auto'], ['addFileReference', 'file'],
    ['addFolderReference', 'file'], ['addSelectionReference', 'selection']]) {
    context.subscriptions.push(vscode.commands.registerCommand('ilab.codex.' + name,
      (clicked, selected) => addReference(clicked, selected, mode)));
  }
};
