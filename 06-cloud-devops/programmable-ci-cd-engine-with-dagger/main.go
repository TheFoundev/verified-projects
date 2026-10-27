package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"time"
)

func samplePipeline() Pipeline {
	return Pipeline{Name: "web", Steps: []Step{
		{Name: "lint", Image: "golang:1.22", Command: "go vet ./..."},
		{Name: "test", Image: "golang:1.22", Command: "go test ./...", Needs: []string{"lint"}},
		{Name: "build", Image: "golang:1.22", Command: "go build -o app .", Needs: []string{"test"}},
		{Name: "deploy", Image: "alpine:3", Command: "echo deploy", Needs: []string{"build"}},
	}}
}

func main() {
	demo := flag.Bool("demo", false, "run the sample pipeline once")
	executorName := flag.String("executor", "dry-run", "dry-run or host")
	cacheDir := flag.String("cache", "", "keep the cache in this directory")
	pipelinePath := flag.String("pipeline", "", "read the pipeline from this JSON file")
	workdir := flag.String("workdir", "", "the directory the steps run in")
	flag.Parse()

	pipeline := samplePipeline()
	if *pipelinePath != "" {
		loaded, err := LoadPipeline(*pipelinePath)
		if err != nil {
			fmt.Fprintln(os.Stderr, "could not read the pipeline:", err)
			os.Exit(1)
		}
		pipeline = loaded
	}

	if *workdir != "" {
		if err := os.MkdirAll(*workdir, 0o755); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
	}

	var executor Executor = &RecordingExecutor{}
	if *executorName == "host" {
		executor = HostExecutor{Workdir: *workdir}
	}

	var cache Cache = NewMemoryCache()
	if *cacheDir != "" {
		file, err := NewFileCache(*cacheDir)
		if err != nil {
			fmt.Fprintln(os.Stderr, "could not open the cache:", err)
			os.Exit(1)
		}
		cache = file
	}

	engine := NewEngine(executor, cache)

	if *demo {
		pipeline = samplePipeline()
	}

	started := time.Now()
	results, err := engine.Run(context.Background(), pipeline)
	if err != nil {
		encoder := json.NewEncoder(os.Stdout)
		encoder.SetIndent("", "  ")
		_ = encoder.Encode(map[string]any{"results": results, "error": err.Error()})
		os.Exit(1)
	}

	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(map[string]any{
		"pipeline": pipeline.Name,
		"executor": executor.Name(),
		"cache":    cache.Name(),
		"duration": time.Since(started).String(),
		"results":  results,
	}); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
