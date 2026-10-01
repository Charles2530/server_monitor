package com.charles.monitor;

/** Status wording and login decisions kept independent of Android widgets. */
final class ConnectionState {
    static boolean shouldRequestLogin(String network) {
        return "NeedsLogin".equals(network);
    }

    static String pairingDetail(boolean saved, String access, boolean writable, String id) {
        if (access == null) return saved ? "电脑配对已保存，等待网络恢复后验证连接。" : "尚未配对电脑";
        return "approved".equals(access) ? "电脑已连接 · " + (writable ? "可管理任务" : "仅查看")
                : "等待电脑批准，设备 ID：" + id;
    }

    static String networkHint(String network) {
        if ("Running".equals(network)) return "私有网络已连接";
        if ("NeedsLogin".equals(network)) return "网络需要授权，请点击“连接 / 登录 Tailscale”。原电脑配对会保留。";
        if ("NeedsMachineAuth".equals(network)) return "等待 Tailscale 管理员批准此设备。";
        return "正在恢复私有网络，请稍候；无需重复登录或扫码。";
    }
}
