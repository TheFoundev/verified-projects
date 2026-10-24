package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"time"
)

func main() {
	service := flag.String("service", "checkout", "target service")
	kind := flag.String("action", "delay", "delay, kill or stress")
	executor := flag.String("executor", "dry-run", "dry-run or local")
	results := flag.String("results", "", "write the result to this JSON file")
	flag.Parse()

	experiment := Experiment{
		Name:      "student-chaos",
		Namespace: "default",
		Actions: []Action{
			{Kind: *kind, Target: *service, Duration: 2 * time.Second, Magnitude: 250},
		},
	}
	if err := Validate(experiment); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	runner := Runner{Now: time.Now}
	switch *executor {
	case "local":
		runner.Executor = LocalExecutor{}
	default:
		runner.Executor = DryRunExecutor{}
	}
	if *results != "" {
		runner.Store = &FileStore{Path: *results}
	}

	result, err := runner.Run(context.Background(), experiment)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(result); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
