package com.charles.monitor;

import android.app.Application;
import android.net.ConnectivityManager;
import android.net.LinkProperties;
import android.net.Network;
import com.charles.monitor.nativebridge.tunnel.Client;
import com.charles.monitor.nativebridge.tunnel.Tunnel;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/** One tsnet instance per process, even when Android recreates the Activity. */
public final class MonitorApplication extends Application {
    final ExecutorService worker=Executors.newSingleThreadExecutor();
    private final ExecutorService networkEvents=Executors.newSingleThreadExecutor();
    private volatile Client client;
    private volatile String networkError;
    StartupDiagnostics diagnostics;
    private boolean watchingNetwork;
    @Override public void onCreate() {
        super.onCreate();
        diagnostics=new StartupDiagnostics(this);
        diagnostics.installHandler();
        // Keep the launch/diagnostic screen independent of native library loading.
    }
    private void watchNetwork() {
        if(watchingNetwork)return;
        ConnectivityManager manager=(ConnectivityManager)getSystemService(CONNECTIVITY_SERVICE);
        manager.registerDefaultNetworkCallback(new ConnectivityManager.NetworkCallback() {
            @Override public void onAvailable(Network network){refreshNetwork();}
            @Override public void onLost(Network network){refreshNetwork();}
            @Override public void onLinkPropertiesChanged(Network network,LinkProperties properties){refreshNetwork();}
        });
        watchingNetwork=true;
    }
    synchronized Client client() throws Exception {
        if(client!=null)return client;
        String name=getSharedPreferences("network",MODE_PRIVATE).getString("hostname",null);
        if(name==null) {
            name="charles-phone-"+UUID.randomUUID().toString().substring(0,8);
            getSharedPreferences("network",MODE_PRIVATE).edit().putString("hostname",name).commit();
        }
        Client created=null;
        try {
            diagnostics.stage("重定向原生启动日志");
            diagnostics.beginNativeOutput();
            diagnostics.stage("加载 Go JNI 原生库");
            go.Seq.setContext(this);
            diagnostics.stage("创建 Tailscale 客户端");
            created=Tunnel.newClient(getNoBackupFilesDir().getAbsolutePath()+"/tailscale",name);
            diagnostics.stage("读取 Android 网络接口");
            created.updateNetwork(AndroidNetworkSnapshot.capture(this));
            diagnostics.stage("注册网络变化回调");
            watchNetwork();
            diagnostics.stage("启动 Tailscale");
            created.start(); client=created;
            diagnostics.stage("网络模块已启动");
            return created;
        } catch(Exception | LinkageError error) {
            diagnostics.failure(error);
            if(created!=null)try{created.close();}catch(Exception ignored){}
            throw error;
        } finally {diagnostics.endNativeOutput();}
    }
    void refreshNetwork() {
        networkEvents.execute(() -> {
            Client current=client;if(current==null)return;
            try {current.updateNetwork(AndroidNetworkSnapshot.capture(this));networkError=null;}
            catch(Exception error){networkError="手机网络状态读取失败，请检查网络后重试。";}
        });
    }
    String networkError(){return networkError;}
}
