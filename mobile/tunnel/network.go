package tunnel

import (
	"encoding/json"
	"errors"
	"net"
	"sync/atomic"

	"tailscale.com/net/netmon"
)

type interfaceSnapshot struct {
	Name         string   `json:"name"`
	Index        int      `json:"index"`
	MTU          int      `json:"mtu"`
	Up           bool     `json:"up"`
	Loopback     bool     `json:"loopback"`
	Multicast    bool     `json:"multicast"`
	PointToPoint bool     `json:"point_to_point"`
	Addresses    []string `json:"addresses"`
}
type networkSnapshot struct {
	Interfaces       []interfaceSnapshot `json:"interfaces"`
	DefaultInterface string              `json:"default_interface"`
	Gateway          string              `json:"gateway"`
}
type interfaceList struct{ rows []netmon.Interface }

var androidInterfaces atomic.Pointer[interfaceList]

func parseInterfaces(payload string) (networkSnapshot, []netmon.Interface, error) {
	var snapshot networkSnapshot
	if len(payload) > 65536 || json.Unmarshal([]byte(payload), &snapshot) != nil {
		return snapshot, nil, errors.New("手机网络状态无效")
	}
	rows := make([]netmon.Interface, 0, len(snapshot.Interfaces))
	for _, source := range snapshot.Interfaces {
		if source.Name == "" || len(source.Name) > 128 || source.Index < 0 || source.MTU < 0 {
			return snapshot, nil, errors.New("手机网络接口无效")
		}
		var flags net.Flags
		if source.Up {
			flags |= net.FlagUp | net.FlagRunning
		}
		if source.Loopback {
			flags |= net.FlagLoopback
		}
		if source.Multicast {
			flags |= net.FlagMulticast
		}
		if source.PointToPoint {
			flags |= net.FlagPointToPoint
		}
		row := netmon.Interface{Interface: &net.Interface{Name: source.Name, Index: source.Index, MTU: source.MTU, Flags: flags}, AltAddrs: make([]net.Addr, 0, len(source.Addresses))}
		for _, address := range source.Addresses {
			ip, network, err := net.ParseCIDR(address)
			if err != nil {
				return snapshot, nil, errors.New("手机网络地址无效")
			}
			network.IP = ip // Preserve host address, not just the masked network prefix.
			row.AltAddrs = append(row.AltAddrs, network)
		}
		rows = append(rows, row)
	}
	return snapshot, rows, nil
}

// UpdateNetwork receives the Android public-API snapshot before Start and on every network change.
func (c *Client) UpdateNetwork(payload string) error {
	snapshot, rows, err := parseInterfaces(payload)
	if err != nil {
		return err
	}
	androidInterfaces.Store(&interfaceList{rows: rows})
	updatePlatformRoute(snapshot.DefaultInterface, snapshot.Gateway)
	if c.started.Load() {
		if system := c.network.Sys(); system != nil {
			if monitor, ok := system.NetMon.GetOK(); ok {
				monitor.InjectEvent()
			}
		}
	}
	return nil
}
