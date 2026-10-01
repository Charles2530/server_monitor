package com.charles.monitor;

/** Keep shared diagnostics small and strip network credentials and URLs. */
final class DiagnosticText {
    static String clean(String value) {
        if (value == null) return "";
        String safe = value.replaceAll("(?i)https?://[^\\s<>\"']+", "[URL]")
                .replaceAll("(?i)(bearer\\s+)[^\\s,;]+", "$1[REDACTED]")
                .replaceAll("(?i)((?:token|password|ticket|authkey|auth_key)\\s*[=:]\\s*)[^\\s,;]+", "$1[REDACTED]")
                .replaceAll("tskey-[A-Za-z0-9_-]+", "[REDACTED]");
        return safe.length() > 24000 ? safe.substring(0, 12000) + "\n…截断…\n" + safe.substring(safe.length()-12000) : safe;
    }
}
