package tunnel

import (
	"net"
	"testing"
)

func TestAndroidInterfaceSnapshotDoesNotNeedNetlink(t *testing.T) {
	snapshot, rows, err := parseInterfaces(`{"default_interface":"wlan0","gateway":"192.168.1.1","interfaces":[{"name":"wlan0","index":4,"mtu":1500,"up":true,"multicast":true,"addresses":["192.168.1.7/24","2001:db8::1/64"]},{"name":"lo","index":1,"mtu":65536,"up":true,"loopback":true,"addresses":[]}]}`)
	if err != nil || len(rows) != 2 || snapshot.DefaultInterface != "wlan0" {
		t.Fatal(snapshot, rows, err)
	}
	addresses, err := rows[0].Addrs()
	if err != nil || addresses[0].(*net.IPNet).IP.String() != "192.168.1.7" {
		t.Fatal(addresses, err)
	}
	if !rows[0].IsUp() || rows[1].AltAddrs == nil {
		t.Fatal("flags or empty non-nil AltAddrs lost; must never fall back to Go netlink")
	}
	if _, _, err := parseInterfaces(`{"interfaces":[{"name":"wlan0","addresses":["bad"]}]}`); err == nil {
		t.Fatal("bad address accepted")
	}
}
