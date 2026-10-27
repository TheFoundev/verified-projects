package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
)

func main() {
	host := flag.String("host", "127.0.0.1", "target host")
	ports := flag.String("ports", "22,80,443,8000-8010", "ports to scan")
	timeout := flag.Duration("timeout", DefaultTimeout, "per-port timeout")
	flag.Parse()

	list, err := ParsePorts(*ports)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	results := Scan(*host, list, *timeout)
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(map[string]any{"host": *host, "results": results, "open": OpenPorts(results)}); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
