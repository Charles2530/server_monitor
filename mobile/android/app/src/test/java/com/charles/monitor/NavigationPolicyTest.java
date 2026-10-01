package com.charles.monitor;
import org.junit.Test;
import static org.junit.Assert.*;
public class NavigationPolicyTest {
    @Test public void localOriginIsExact() {
        assertTrue(NavigationPolicy.local("http://127.0.0.1:1234/?site=sensecore", "http://127.0.0.1:1234"));
        for (String u : new String[]{"http://127.0.0.1:9999/", "http://evil.test:1234", "http://user@127.0.0.1:1234", "file:///etc/passwd", "javascript:alert(1)"})
            assertFalse(NavigationPolicy.local(u, "http://127.0.0.1:1234"));
    }
    @Test public void onlyKnownOfficialAndLoginDomains() {
        assertTrue(NavigationPolicy.official("https://console.sensecore.cn/cn-sh-01/acp/terminal"));
        assertTrue(NavigationPolicy.login("https://login.tailscale.com/a/test"));
        assertFalse(NavigationPolicy.official("https://console.sensecore.cn.evil.test/"));
        assertFalse(NavigationPolicy.login("https://tailscale.com@evil.test/"));
        assertFalse(NavigationPolicy.login("https://evil-tailscale.com/"));
        assertFalse(NavigationPolicy.official("https://console.sensecore.cn:8443/"));
    }
}
