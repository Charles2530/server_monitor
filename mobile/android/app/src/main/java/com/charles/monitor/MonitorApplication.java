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
    @Override public void onCreate() {
        super.onCreate(); go.Seq.setContext(this);
        ConnectivityManager manager=(ConnectivityManager)getSystemService(CONNECTIVITY_SERVICE);
        manager.registerDefaultNetworkCallback(new ConnectivityManager.NetworkCallback() {
            @Override public void onAvailable(Network network){refreshNetwork();}
            @Override public void onLost(Network network){refreshNetwork();}
            @Override public void onLinkPropertiesChanged(Network network,LinkProperties properties){refreshNetwork();}
        });
    }
    synchronized Client client() throws Exception {
        if(client!=null)return client;
        String name=getSharedPreferences("network",MODE_PRIVATE).getString("hostname",null);
        if(name==null) {
            name="charles-phone-"+UUID.randomUUID().toString().substring(0,8);
            getSharedPreferences("network",MODE_PRIVATE).edit().putString("hostname",name).commit();
        }
        Client created=Tunnel.newClient(getNoBackupFilesDir().getAbsolutePath()+"/tailscale",name);
        try {
            created.updateNetwork(AndroidNetworkSnapshot.capture(this));
            created.start(); client=created; return created;
        } catch(Exception error) {try{created.close();}catch(Exception ignored){} throw error;}
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
