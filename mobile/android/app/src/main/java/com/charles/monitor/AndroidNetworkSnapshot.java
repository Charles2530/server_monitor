package com.charles.monitor;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.LinkProperties;
import android.net.Network;
import android.net.RouteInfo;
import java.net.InterfaceAddress;
import java.net.NetworkInterface;
import java.util.Collections;
import java.util.Enumeration;
import org.json.JSONArray;
import org.json.JSONObject;

/** Public Android/Java APIs replace Go netlink enumeration, blocked on SDK 30+. */
final class AndroidNetworkSnapshot {
    static String capture(Context context) throws Exception {
        JSONObject result=new JSONObject(); JSONArray interfaces=new JSONArray();
        Enumeration<NetworkInterface> all=NetworkInterface.getNetworkInterfaces();
        if(all!=null) for(NetworkInterface iface:Collections.list(all)) {
            try {
                JSONObject row=new JSONObject(); row.put("name",iface.getName()); row.put("index",iface.getIndex());
                row.put("mtu",iface.getMTU()); row.put("up",iface.isUp()); row.put("loopback",iface.isLoopback());
                row.put("multicast",iface.supportsMulticast()); row.put("point_to_point",iface.isPointToPoint());
                JSONArray addresses=new JSONArray();
                for(InterfaceAddress address:iface.getInterfaceAddresses())
                    addresses.put(address.getAddress().getHostAddress().split("%",2)[0]+"/"+address.getNetworkPrefixLength());
                row.put("addresses",addresses); interfaces.put(row);
            } catch(java.net.SocketException ignored) { /* Interface disappeared during a network transition. */ }
        }
        result.put("interfaces",interfaces); result.put("default_interface",""); result.put("gateway","");
        ConnectivityManager manager=(ConnectivityManager)context.getSystemService(Context.CONNECTIVITY_SERVICE);
        Network active=manager.getActiveNetwork(); LinkProperties properties=active==null?null:manager.getLinkProperties(active);
        if(properties!=null) {
            result.put("default_interface",properties.getInterfaceName()==null?"":properties.getInterfaceName());
            for(RouteInfo route:properties.getRoutes()) if(route.isDefaultRoute() && route.getGateway()!=null && !route.getGateway().isAnyLocalAddress()) {
                result.put("gateway",route.getGateway().getHostAddress().split("%",2)[0]); break;
            }
        }
        return result.toString();
    }
}
