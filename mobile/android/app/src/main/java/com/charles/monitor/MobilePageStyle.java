package com.charles.monitor;

import android.content.res.AssetManager;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import org.json.JSONObject;

/** Bundled assets only enhance the root monitor document on our exact loopback origin. */
final class MobilePageStyle {
    private final String css, javascript;

    MobilePageStyle(String css, String javascript) {
        this.css = css; this.javascript = javascript;
    }

    static MobilePageStyle fromAssets(AssetManager assets) throws IOException {
        return new MobilePageStyle(read(assets, "mobile.css"), read(assets, "mobile.js"));
    }

    private static String read(AssetManager assets, String name) throws IOException {
        try (InputStream input = assets.open(name); ByteArrayOutputStream output = new ByteArrayOutputStream()) {
            byte[] buffer = new byte[4096];
            for (int count; (count = input.read(buffer)) != -1;) output.write(buffer, 0, count);
            return new String(output.toByteArray(), StandardCharsets.UTF_8);
        }
    }

    String scriptFor(String url, String origin) {
        if (!monitorDocument(url, origin)) return null;
        return "(function(){"
                + "if(location.origin!==" + quote(origin) + "||location.pathname!=='/'"
                + "||!document.querySelector('.sidebar')||!document.getElementById('main'))return;"
                + "if(!document.getElementById('ilab-mobile-style')){"
                + "var style=document.createElement('style');style.id='ilab-mobile-style';"
                + "style.textContent=" + quote(css) + ";document.head.appendChild(style);}"
                + "if(!document.getElementById('ilab-mobile-script')){"
                + "var marker=document.createElement('meta');marker.id='ilab-mobile-script';"
                + "document.head.appendChild(marker);"
                // This is trusted APK source, executed by WebView.evaluateJavascript itself.
                // A DOM inline script or eval would be blocked by the server's script-src CSP.
                + "(function(){\n" + javascript + "\n})();}"
                + "})();";
    }

    static boolean monitorDocument(String url, String origin) {
        if (!NavigationPolicy.local(url, origin)) return false;
        try { return "/".equals(new URI(url).getRawPath()); }
        catch (Exception error) { return false; }
    }

    private static String quote(String value) {
        return JSONObject.quote(value).replace("\u2028", "\\u2028").replace("\u2029", "\\u2029");
    }
}
