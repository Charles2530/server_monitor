package tunnel

import "tailscale.com/net/netmon"

func init() {
	// Go's netlink-based net.Interfaces/Addrs is prohibited for modern Android apps.
	netmon.RegisterInterfaceGetter(func() ([]netmon.Interface, error) {
		if current := androidInterfaces.Load(); current != nil {
			return current.rows, nil
		}
		return []netmon.Interface{}, nil
	})
}
func updatePlatformRoute(iface, gateway string) {
	netmon.UpdateLastKnownDefaultRouteInterface(iface)
	netmon.UpdateLastKnownDefaultGateway(gateway)
}
