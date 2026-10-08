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
exports.activate = context => {
  for (const [name, mode] of [['addReference', 'auto'], ['addFileReference', 'file'],
    ['addFolderReference', 'file'], ['addSelectionReference', 'selection']]) {
    context.subscriptions.push(vscode.commands.registerCommand('ilab.codex.' + name,
      (clicked, selected) => addReference(clicked, selected, mode)));
  }
};
