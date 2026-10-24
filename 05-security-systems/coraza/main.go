package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"
)

func main() {
	method := flag.String("method", "GET", "HTTP method")
	path := flag.String("path", "/", "request path")
	query := flag.String("query", "", "query string")
	body := flag.String("body", "", "request body")
	flag.Parse()

	request := Request{Method: *method, Path: *path, Query: *query, Body: *body}
	findings := Inspect(request)
	decision := "allow"
	if Blocked(findings) {
		decision = "block"
	}
	output := map[string]any{
		"decision": decision,
		"score":    Score(findings),
		"findings": findings,
	}
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(output); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	if decision == "block" {
		fmt.Fprintln(os.Stderr, "request blocked:", strings.Join(Evidence(findings), "; "))
	}
}
