package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"time"
)

const sampleWorkflow = `name: ci
on: push
jobs:
  - name: test
    steps:
      - name: checkout
        run: git clone
      - name: test
        run: go test ./...
`

func main() {
	message := flag.String("message", "initial commit", "commit message")
	branch := flag.String("branch", "main", "the branch to commit to")
	state := flag.String("state", "", "keep the repository in this file")
	workflowPath := flag.String("workflow", "", "read the workflow from this file")
	run := flag.Bool("run", false, "run the steps of the workflow")
	workdir := flag.String("workdir", "", "the directory the steps run in")
	flag.Parse()

	repository := NewRepository()
	if *state != "" {
		loaded, err := Load(*state)
		if err != nil {
			fmt.Fprintln(os.Stderr, "could not read the repository:", err)
			os.Exit(1)
		}
		repository = loaded
	}

	files := map[string]string{"README.md": "# demo\n", "main.go": "package main\n"}
	commit := repository.Commit(*branch, *message, files)

	text := sampleWorkflow
	if *workflowPath != "" {
		data, err := os.ReadFile(*workflowPath)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		text = string(data)
	}
	workflow, err := ParseWorkflow(text)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}

	results := []StepResult{}
	if *run {
		runner := Runner{Workdir: *workdir}
		results = runner.Run(workflow)
	} else {
		results = RunWorkflow(workflow)
	}

	if *state != "" {
		if err := repository.Save(*state); err != nil {
			fmt.Fprintln(os.Stderr, "could not write the repository:", err)
			os.Exit(1)
		}
	}

	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	_ = encoder.Encode(map[string]any{
		"commit": commit, "history": repository.Log(*branch, 5),
		"branches": repository.Branches(),
		"workflow": workflow.Name, "results": results,
		"when": time.Now().UTC().Format(time.RFC3339),
	})
}
