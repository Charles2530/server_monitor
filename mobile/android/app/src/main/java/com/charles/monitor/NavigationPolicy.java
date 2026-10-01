package com.charles.monitor;

import java.net.URI;

/** Kept free of Android dependencies so destination checks have JVM unit tests. */
final class NavigationPolicy {
    static boolean local(String value, String origin) {
        try {
            URI url = new URI(value), base = new URI(origin);
            return "http".equals(url.getScheme()) && "127.0.0.1".equals(url.getHost())
                    && url.getPort() == base.getPort() && url.getRawUserInfo() == null;
        } catch (Exception e) { return false; }
    }
    static boolean official(String value) {
        try {
            URI url = new URI(value);
            return "https".equals(url.getScheme()) && url.getRawUserInfo() == null
                    && (url.getPort() == -1 || url.getPort() == 443)
                    && ("console.sensecore.cn".equals(url.getHost())
                    || "console.ms-sc-01.maoshanwangtech.com".equals(url.getHost()));
        } catch (Exception e) { return false; }
    }
    static boolean login(String value) {
        try {
            URI url = new URI(value); String host = url.getHost();
            return "https".equals(url.getScheme()) && url.getRawUserInfo() == null
                    && (url.getPort() == -1 || url.getPort() == 443) && host != null
                    && (host.equals("tailscale.com") || host.endsWith(".tailscale.com"));
        } catch (Exception e) { return false; }
    }
}
