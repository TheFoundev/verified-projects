package main

import (
	"fmt"
	"net"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

// DefaultTimeout is used when the caller does not set one.
const DefaultTimeout = 400 * time.Millisecond

// PortResult describes the outcome for one port.
type PortResult struct {
	Port  int    `json:"port"`
	State string `json:"state"`
}

// ParsePorts understands "22,80,1000-1005" syntax.
func ParsePorts(spec string) ([]int, error) {
	seen := map[int]bool{}
	for _, part := range strings.Split(spec, ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		if strings.Contains(part, "-") {
			bounds := strings.SplitN(part, "-", 2)
			start, err := strconv.Atoi(strings.TrimSpace(bounds[0]))
			if err != nil {
				return nil, fmt.Errorf("invalid port range %q", part)
			}
			end, err := strconv.Atoi(strings.TrimSpace(bounds[1]))
			if err != nil || end < start {
				return nil, fmt.Errorf("invalid port range %q", part)
			}
			for port := start; port <= end; port++ {
				if port >= 1 && port <= 65535 {
					seen[port] = true
				}
			}
			continue
		}
		port, err := strconv.Atoi(part)
		if err != nil || port < 1 || port > 65535 {
			return nil, fmt.Errorf("invalid port %q", part)
		}
		seen[port] = true
	}
	ports := make([]int, 0, len(seen))
	for port := range seen {
		ports = append(ports, port)
	}
	sort.Ints(ports)
	return ports, nil
}

// Scan dials each port concurrently.
// ParseHosts turns a list of hosts and network ranges into the hosts.
//
// A network is written as 10.0.0.0/24 and is expanded into its addresses.
func ParseHosts(spec string) ([]string, error) {
	hosts := []string{}
	for _, part := range strings.Split(spec, ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		if !strings.Contains(part, "/") {
			hosts = append(hosts, part)
			continue
		}
		expanded, err := expandNetwork(part)
		if err != nil {
			return nil, err
		}
		hosts = append(hosts, expanded...)
	}
	if len(hosts) == 0 {
		return nil, fmt.Errorf("no hosts to scan")
	}
	return hosts, nil
}

// expandNetwork turns a network into its addresses.
func expandNetwork(spec string) ([]string, error) {
	parts := strings.Split(spec, "/")
	if len(parts) != 2 {
		return nil, fmt.Errorf("%q is not a network", spec)
	}
	address := net.ParseIP(parts[0])
	if address == nil {
		return nil, fmt.Errorf("%q is not an address", parts[0])
	}
	bits, err := strconv.Atoi(parts[1])
	if err != nil || bits < 0 || bits > 32 {
		return nil, fmt.Errorf("%q is not a prefix", parts[1])
	}
	network := &net.IPNet{IP: address.To4(), Mask: net.CIDRMask(bits, 32)}
	if network.IP == nil {
		return nil, fmt.Errorf("%q is not an IPv4 network", spec)
	}

	hosts := []string{}
	for current := network.IP.Mask(network.Mask); network.Contains(current); current = nextAddress(current) {
		hosts = append(hosts, current.String())
		if len(hosts) > 65536 {
			return nil, fmt.Errorf("the network is too big")
		}
	}
	return hosts, nil
}

// nextAddress is the address behind the given one.
func nextAddress(address net.IP) net.IP {
	next := make(net.IP, len(address))
	copy(next, address)
	for index := len(next) - 1; index >= 0; index-- {
		next[index]++
		if next[index] != 0 {
			break
		}
	}
	return next
}

func Scan(host string, ports []int, timeout time.Duration) []PortResult {
	results := make([]PortResult, len(ports))
	var wait sync.WaitGroup
	for index, port := range ports {
		wait.Add(1)
		go func(index, port int) {
			defer wait.Done()
			address := net.JoinHostPort(host, strconv.Itoa(port))
			connection, err := net.DialTimeout("tcp", address, timeout)
			if err != nil {
				results[index] = PortResult{Port: port, State: "closed"}
				return
			}
			connection.Close()
			results[index] = PortResult{Port: port, State: "open"}
		}(index, port)
	}
	wait.Wait()
	return results
}

// OpenPorts filters open results in ascending order.
func OpenPorts(results []PortResult) []int {
	open := []int{}
	for _, result := range results {
		if result.State == "open" {
			open = append(open, result.Port)
		}
	}
	sort.Ints(open)
	return open
}
