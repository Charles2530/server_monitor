# 无人登录启动实施计划

**目标：** 将已部署监控及手机入口迁移到普通用户身份运行的系统服务，保留数据和回退能力。

1. 新增 `scripts/boot_services.py`：生成主监控/网关/可选飞书及 userspace tailscaled 的系统 plist；提供 dry-run、install、rollback、status；仅 install/rollback 要求 root。先写并运行 `tests/test_boot_services.py`，验证不以 root 执行业务、仅使用指定服务、重复安装保护和失败回退。
2. 现有安装器检测系统 plist 后拒绝创建并行 LaunchAgent；状态和管理入口识别 system 域，给出系统服务管理命令。运行针对性测试。
3. 使用已校验 SHA256 的 Go 工具链构建 Tailscale v1.102.4，将二进制和认证状态留在 Git 外；先验证新的私有 Serve，再迁移启动方式。保留原 GUI 身份用于回退。
4. 更新 README 和无人登录部署文档；全量测试、独立复查、凭据排除后推送 GitHub main。
5. 准备可审核的管理员安装命令，请用户在本机输入密码执行；检查 system 域服务、两站点、私有 HTTPS、原手机权限。未授权重启机器，不声称已完成真实重启验收。
