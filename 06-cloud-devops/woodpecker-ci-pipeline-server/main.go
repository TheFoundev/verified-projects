package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"net/http"
	"os"
)

const samplePipeline = `name: test
steps:
  - name: install
    image: golang:1.22
    commands:
      - go mod download
  - name: test
    image: golang:1.22
    commands:
      - go test ./...
`

func main() {
	listen := flag.String("listen", "", "optional HTTP listen address")
	flag.Parse()

	server := NewServer()
	pipeline, err := ParsePipeline(samplePipeline)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	build := server.Trigger(pipeline, "foundev/web", "abc123")
	server.Run(build.ID)

	if *listen != "" {
		mux := http.NewServeMux()
		mux.HandleFunc("/builds", server.ListHandler)
		mux.HandleFunc("/trigger", server.TriggerHandler)
		fmt.Println("serving pipeline API on", *listen)
		if err := http.ListenAndServe(*listen, mux); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	_ = encoder.Encode(map[string]any{"pipeline": pipeline.Name, "build": server.Get(build.ID)})
}
