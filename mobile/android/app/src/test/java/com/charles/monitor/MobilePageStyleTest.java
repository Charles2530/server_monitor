package com.charles.monitor;

import org.json.JSONTokener;
import org.junit.Test;
import static org.junit.Assert.*;

public class MobilePageStyleTest {
    private static final String ORIGIN = "http://127.0.0.1:1234";

    @Test public void onlyLocalMonitorDocumentReceivesAssets() {
        MobilePageStyle style = new MobilePageStyle("body{}", "window.mobile=true;");
        assertNotNull(style.scriptFor(ORIGIN + "/?site=sensecore", ORIGIN));
        for (String url : new String[]{ORIGIN + "/terminal", ORIGIN + "/login", ORIGIN + "/static/index.html",
                "http://127.0.0.1:9999/", "https://login.tailscale.com/", "https://example.test/",
                "http://user@127.0.0.1:1234/", "about:blank", "javascript:alert(1)"}) {
            assertNull(url, style.scriptFor(url, ORIGIN));
        }
    }

    @Test public void cssIsQuotedWithoutChangingUnicode() throws Exception {
        String css = "body::before{content:\"中文 ' \\\\ </style> \\n\";}\n/*\u2028\u2029*/";
        String script = new MobilePageStyle(css, "").scriptFor(ORIGIN + "/", ORIGIN);
        assertNotNull(script);
        assertEquals(css, assignment(script, "style.textContent="));
        assertFalse(script.contains("\u2028"));
        assertFalse(script.contains("\u2029"));
    }

    @Test public void trustedJavascriptIsPreservedAsNativeExecutionBody() {
        String javascript = "window.sample=\"quotes \\\\ </script> 中文\";\n// final comment";
        String script = new MobilePageStyle("", javascript).scriptFor(ORIGIN + "/", ORIGIN);
        assertTrue(script.contains("(function(){\n" + javascript + "\n})();"));
        assertFalse(script.contains("createElement('script')"));
        assertFalse(script.contains("eval("));
    }

    private Object assignment(String script, String marker) throws Exception {
        int start = script.indexOf(marker);
        assertTrue("Missing assignment: " + marker, start >= 0);
        return new JSONTokener(script.substring(start + marker.length())).nextValue();
    }
}
