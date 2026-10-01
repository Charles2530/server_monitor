package com.charles.monitor;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Message;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebStorage;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import com.charles.monitor.nativebridge.tunnel.Client;
import com.google.zxing.integration.android.IntentIntegrator;
import com.google.zxing.integration.android.IntentResult;
import java.io.ByteArrayInputStream;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import org.json.JSONObject;

public class MainActivity extends Activity {
    private final Handler ui = new Handler(Looper.getMainLooper());
    private ExecutorService worker;
    private final List<WebView> pages = new ArrayList<>();
    private final List<Button> actions = new ArrayList<>();
    private Client tunnel;
    private CredentialVault vault;
    private JSONObject device;
    private LinearLayout root, form;
    private TextView status;
    private EditText pairing;
    private String localOrigin = "", loginURL = "";
    private boolean busy, resumed;
    private volatile boolean destroyed;
    private boolean autoOpen = true;
    private final Runnable poll = new Runnable() {
        @Override public void run() {
            if (resumed && !busy && pages.isEmpty() && tunnel != null) checkStatus(false);
            if (resumed) ui.postDelayed(this, 15000);
        }
    };

    @Override public void onCreate(Bundle bundle) {
        super.onCreate(bundle);
        getWindow().setStatusBarColor(Color.rgb(243,247,245));
        worker = ((MonitorApplication)getApplication()).worker;
        vault = new CredentialVault(this);
        root = new LinearLayout(this); root.setOrientation(LinearLayout.VERTICAL);
        // Android 15 edge-to-edge: keep controls out of status/navigation bars.
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            view.setPadding(insets.getSystemWindowInsetLeft(), insets.getSystemWindowInsetTop(),
                    insets.getSystemWindowInsetRight(), insets.getSystemWindowInsetBottom()); return insets;
        });
        setContentView(root); showForm();
        run(() -> {
            tunnel = ((MonitorApplication)getApplication()).client();
            String saved = vault.load(); if (saved != null) device = new JSONObject(saved);
            post(() -> { message("网络模块已启动，请首次登录 Tailscale。"); checkStatus(true); });
        });
    }

    private TextView text(String value, int size) {
        TextView view = new TextView(this); view.setText(value); view.setTextSize(size);
        view.setPadding(0,12,0,12); form.addView(view); return view;
    }
    private Button button(String label, Runnable action) {
        Button b = new Button(this); b.setText(label); b.setOnClickListener(v -> action.run());
        form.addView(b); actions.add(b); return b;
    }
    private void showForm() {
        root.removeAllViews(); actions.clear();
        ScrollView scroll = new ScrollView(this); form = new LinearLayout(this);
        form.setOrientation(LinearLayout.VERTICAL); form.setPadding(30,20,30,20);
        scroll.addView(form); root.addView(scroll);
        text("Charles 云监控", 25);
        text("连接另一台常开 Mac · 首次登录网络并配对", 14);
        status = text("正在启动…", 15);
        button("首次登录 Tailscale", () -> {
            if (tunnel == null) return;
            run(() -> { tunnel.login(); ui.postDelayed(() -> checkStatus(true),100); });
        });
        button("打开网络授权页面", () -> {
            if (NavigationPolicy.login(loginURL)) openBrowser(loginURL);
            else message("还没有授权地址，请先点击首次登录，稍后刷新状态。");
        });
        pairing = new EditText(this); pairing.setHint("粘贴电脑生成的配对 JSON，或扫描二维码");
        pairing.setMinLines(3); pairing.setMaxLines(7); form.addView(pairing);
        button("扫描电脑上的配对二维码", () -> new IntentIntegrator(this)
                .setDesiredBarcodeFormats(IntentIntegrator.QR_CODE).setPrompt("扫描监控电脑生成的配对二维码")
                .setBeepEnabled(false).setOrientationLocked(false).initiateScan());
        button("配对这台电脑", () -> pair(pairing.getText().toString()));
        button("刷新连接 / 审批状态", () -> checkStatus(true));
        button("进入监控", () -> checkStatusAndOpen());
        button("更换监控电脑", () -> new AlertDialog.Builder(this).setTitle("更换监控电脑")
                .setMessage("请先核对所有结果未知的任务操作。继续会清除手机配对和网页记录；旧电脑上的权限需在旧电脑撤销。")
                .setNegativeButton("取消", null).setPositiveButton("清除并更换", (d,w) -> forget()).show());
        text("网络连接使用 Tailscale 官方账号。手机不保存云平台 IAM 密码。Terminal 将在浏览器打开，可能需要官方登录。", 13);
    }

    private interface Work { void execute() throws Exception; }
    private void run(Work action) {
        if (busy || destroyed) return;
        busy = true; for (Button b : actions) b.setEnabled(false);
        worker.execute(() -> {
            try { if (!destroyed) action.execute(); }
            catch (Exception e) { post(() -> message(e.getMessage() == null ? "连接失败，请重试。" : e.getMessage())); }
            finally { post(() -> { busy = false; for (Button b : actions) b.setEnabled(true); }); }
        });
    }
    private void post(Runnable action) { ui.post(() -> { if (!destroyed) action.run(); }); }
    private void message(String value) { if (!destroyed && status != null) status.setText(value); }
    private void checkStatus(boolean manual) {
        if (tunnel == null || busy) return;
        run(() -> {
            String failure=((MonitorApplication)getApplication()).networkError();
            if(failure!=null)throw new IllegalStateException(failure);
            JSONObject network = new JSONObject(tunnel.status());
            String auth = network.optString("auth_url", ""), state = network.optString("state", "未知");
            JSONObject access = null;
            if (state.equals("Running") && device != null)
                access = new JSONObject(tunnel.deviceStatus(device.getString("gateway"),device.getString("token")));
            final JSONObject result = access;
            post(() -> {
                loginURL = auth;
                String detail = result == null ? "尚未配对电脑" : result.optString("state").equals("approved")
                        ? "电脑已连接 · " + (result.optInt("can_write") == 1 ? "可管理任务" : "仅查看")
                        : "等待电脑批准，设备 ID：" + result.optString("id");
                message("网络：" + state + "\n" + (device == null ? "请在网络 Running 后扫码配对" : device.optString("gateway")) + "\n" + detail);
                if (autoOpen && result != null && result.optString("state").equals("approved")) {
                    autoOpen = false; ui.postDelayed(() -> checkStatusAndOpen(),100);
                }
            });
        });
    }
    private void pair(String payload) {
        if (tunnel == null || busy) return;
        run(() -> {
            if (!new JSONObject(tunnel.status()).optString("state").equals("Running"))
                throw new IllegalStateException("请先完成 Tailscale 登录，等待网络状态 Running。");
            String destination = new JSONObject(payload).getString("gateway").replaceAll("/+$", "");
            if (device != null && !destination.equals(device.getString("gateway")))
                throw new IllegalStateException("已有其他主机的配对，请使用“更换监控电脑”。同一主机可直接重新配对并保留操作记录。");
            String result = tunnel.pair(payload, android.os.Build.MANUFACTURER + " " + android.os.Build.MODEL);
            vault.save(result); device = new JSONObject(result);
            post(() -> { pairing.setText(""); message("配对已申请，请在电脑批准设备 ID：" + device.optString("device_id")); });
        });
    }
    private void checkStatusAndOpen() {
        if (tunnel == null || device == null) { message("请先完成网络登录和电脑配对。"); return; }
        run(() -> {
            JSONObject access = new JSONObject(tunnel.deviceStatus(device.getString("gateway"),device.getString("token")));
            if (!access.optString("state").equals("approved")) throw new IllegalStateException("请先在电脑批准这部手机。");
            if (!device.getString("host_id").equals(access.optString("host_id")))
                throw new IllegalStateException("电脑身份已变化，请重新配对；不会使用旧主机的任务记录。");
            if (destroyed) return;
            String start = tunnel.open(device.getString("gateway"), device.getString("token"), device.getString("host_id"));
            if (destroyed) { tunnel.stopProxy(); return; }
            post(() -> openMonitor(start));
        });
    }
    private void forget() {
        autoOpen = false;
        run(() -> { if (tunnel != null) tunnel.stopProxy(); vault.clear(); device = null;
            post(() -> { destroyPages(); CookieManager.getInstance().removeAllCookies(null); WebStorage.getInstance().deleteAllData();
                showForm(); message("已清除配对，可以绑定新的监控电脑。"); });
        });
    }
    private void openMonitor(String start) {
        if (destroyed) return;
        destroyPages(); root.removeAllViews();
        Uri uri = Uri.parse(start); localOrigin = "http://127.0.0.1:" + uri.getPort();
        Button back = new Button(this); back.setText("‹ 连接设置 / 更换电脑");
        back.setOnClickListener(v -> { autoOpen=false; destroyPages(); showForm(); checkStatus(true); }); root.addView(back);
        WebView web = newWebView(); pages.add(web); root.addView(web,new LinearLayout.LayoutParams(-1,0,1)); web.loadUrl(start);
    }
    private WebView newWebView() {
        WebView web = new WebView(this); WebSettings settings = web.getSettings();
        settings.setJavaScriptEnabled(true); settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false); settings.setAllowContentAccess(false);
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW); settings.setSupportMultipleWindows(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web,false);
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request) {
                String url=request.getUrl().toString();
                if (NavigationPolicy.local(url,localOrigin)) return false;
                if (request.isForMainFrame() && NavigationPolicy.official(url)) {
                    openBrowser(url); closePopup(view);
                }
                return true;
            }
            @Override public WebResourceResponse shouldInterceptRequest(WebView view,WebResourceRequest request) {
                if (NavigationPolicy.local(request.getUrl().toString(),localOrigin)) return null;
                return new WebResourceResponse("text/plain","UTF-8",403,"Forbidden",null,new ByteArrayInputStream(new byte[0]));
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override public boolean onCreateWindow(WebView view,boolean dialog,boolean gesture,Message result) {
                if (!gesture || pages.size()>=3) return false;
                WebView child = newWebView(); pages.add(child); view.setVisibility(View.GONE);
                root.addView(child,new LinearLayout.LayoutParams(-1,0,1));
                ((WebView.WebViewTransport)result.obj).setWebView(child); result.sendToTarget(); return true;
            }
            @Override public void onCloseWindow(WebView window) { closePopup(window); }
        });
        return web;
    }
    private void closePopup(WebView view) {
        if (pages.size()>1 && pages.get(pages.size()-1)==view) {
            pages.remove(view); root.removeView(view); view.destroy(); pages.get(pages.size()-1).setVisibility(View.VISIBLE);
        }
    }
    private void destroyPages() { for(WebView web:pages) { ((ViewGroup)web.getParent()).removeView(web); web.destroy(); } pages.clear(); }
    private void openBrowser(String url) {
        try { startActivity(new Intent(Intent.ACTION_VIEW,Uri.parse(url))); }
        catch (Exception e) { message("未找到可用浏览器。"); }
    }
    @Override protected void onActivityResult(int requestCode,int resultCode,Intent data) {
        IntentResult result = IntentIntegrator.parseActivityResult(requestCode,resultCode,data);
        if(result!=null) { if(result.getContents()!=null) pairing.setText(result.getContents()); }
        else super.onActivityResult(requestCode,resultCode,data);
    }
    @Override protected void onResume() { super.onResume(); ((MonitorApplication)getApplication()).refreshNetwork(); resumed=true; ui.removeCallbacks(poll); ui.postDelayed(poll,1500); for(WebView web:pages)web.onResume(); }
    @Override protected void onPause() { resumed=false; ui.removeCallbacks(poll); for(WebView web:pages)web.onPause(); super.onPause(); }
    @Override public void onBackPressed() {
        if (pages.size()>1) closePopup(pages.get(pages.size()-1));
        else if(!pages.isEmpty() && pages.get(0).canGoBack())pages.get(0).goBack();
        else if(!pages.isEmpty()){autoOpen=false;destroyPages();showForm();checkStatus(true);}
        else super.onBackPressed();
    }
    @Override protected void onDestroy() {
        destroyed=true;resumed=false;ui.removeCallbacks(poll);destroyPages();
        if(tunnel!=null){tunnel.cancelRequests();tunnel.stopProxy();}
        super.onDestroy();
    }
}
