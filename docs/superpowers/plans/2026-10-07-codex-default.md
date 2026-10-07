# Codex 默认面板调整

目标：隐藏内置 Chat，默认打开 Codex。使用现有声明式 appearance 插件，不增加启动脚本，不改用户凭据。用户已明确要求此行为，直接实施并验证。

- [x] 将 configurationDefaults 中 chat.disableAIFeatures 与 chatgpt.openOnStartup 设为 true。
- [x] 将 appearance Python 常量、VSIX identity、package.json 版本同步到 1.1.0，以触发既有容器的可选插件更新。
- [x] 运行 appearance、Codex、bootstrap、latency 回归并检查安装包。
- [x] 仅部署上述两个文件，验证实际编辑器默认页面。保留个人设置优先级。

## 验证结果

2026-10-07：27 项 appearance/Codex/latency 回归通过；VSIX 三处版本与两项默认值一致。仅部署 server/ide_appearance.py 与 static/editor-appearance/extension/package.json，本机服务已恢复 HTTP 200。

当前 CCI 容器后台可选安装未成功后，通过现有受验证的 bootstrap 重试，返回 appearance.installed=true、version=1.1.0、codex.installed=true。重载测试页面后，内置 Chat 标签及顶部入口消失，右侧 Codex 标签自动选中并加载 webview；未修改用户代码或凭据。既有监控会话的外观状态仍可能暂时保留旧提示，后续安装检查刷新。
