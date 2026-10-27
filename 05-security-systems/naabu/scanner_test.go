package main

import "testing"

func TestTheHostsOfAList(t *testing.T) {
	hosts, err := ParseHosts("127.0.0.1, 10.0.0.1")
	if err != nil {
		t.Fatal(err)
	}
	if len(hosts) != 2 {
		t.Fatalf("unexpected hosts %#v", hosts)
	}
}

func TestANetworkIsExpanded(t *testing.T) {
	hosts, err := ParseHosts("10.0.0.0/30")
	if err != nil {
		t.Fatal(err)
	}
	// A /30 network has four addresses.
	if len(hosts) != 4 {
		t.Fatalf("unexpected hosts %#v", hosts)
	}
	if hosts[0] != "10.0.0.0" {
		t.Fatalf("unexpected first host %q", hosts[0])
	}
}

func TestABrokenNetworkIsRefused(t *testing.T) {
	if _, err := ParseHosts("10.0.0.0/not a prefix"); err == nil {
		t.Fatal("expected an error for a broken prefix")
	}
	if _, err := ParseHosts("999.0.0.1/24"); err == nil {
		t.Fatal("expected an error for an address that does not exist")
	}
	if _, err := ParseHosts(""); err == nil {
		t.Fatal("expected an error for an empty list")
	}
}

func TestThePortsAreParsed(t *testing.T) {
	ports, err := ParsePorts("80,443,8000-8002")
	if err != nil {
		t.Fatal(err)
	}
	if len(ports) != 5 {
		t.Fatalf("unexpected ports %#v", ports)
	}
}
