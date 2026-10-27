package main

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func testPipeline() Pipeline {
	return Pipeline{Name: "demo", Steps: []Step{
		{Name: "lint", Image: "golang", Command: "gofmt -l ."},
		{Name: "test", Image: "golang", Command: "go test ./...", Needs: []string{"lint"}},
		{Name: "build", Image: "golang", Command: "go build ./...", Needs: []string{"test"}},
		{Name: "deploy", Image: "alpine", Command: "echo deploy", Needs: []string{"build"}},
	}}
}

func TestOrderAndCycle(t *testing.T) {
	order, err := Order(testPipeline())
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"lint", "test", "build", "deploy"}
	for index := range want {
		if order[index] != want[index] {
			t.Fatalf("unexpected order %v", order)
		}
	}
	cyclic := Pipeline{Name: "bad", Steps: []Step{
		{Name: "a", Needs: []string{"b"}},
		{Name: "b", Needs: []string{"a"}},
	}}
	if _, err := Order(cyclic); err == nil {
		t.Fatal("expected a cycle error")
	}
}

func TestAnUnknownDependencyIsRefused(t *testing.T) {
	pipeline := Pipeline{Name: "bad", Steps: []Step{{Name: "a", Needs: []string{"nope"}}}}
	if _, err := Order(pipeline); err == nil {
		t.Fatal("expected an unknown dependency error")
	}
}

func TestAStepThatIsDefinedTwiceIsRefused(t *testing.T) {
	pipeline := Pipeline{Name: "bad", Steps: []Step{{Name: "a"}, {Name: "a"}}}
	if _, err := Order(pipeline); err == nil {
		t.Fatal("expected a duplicate step error")
	}
}

func TestRunCaches(t *testing.T) {
	engine := NewEngine(&RecordingExecutor{}, NewMemoryCache())
	first, err := engine.Run(context.Background(), testPipeline())
	if err != nil {
		t.Fatal(err)
	}
	if first[0].Cached {
		t.Fatal("first run should not be cached")
	}
	second, err := engine.Run(context.Background(), testPipeline())
	if err != nil {
		t.Fatal(err)
	}
	for _, result := range second {
		if !result.Cached {
			t.Fatalf("expected a cache hit for %s", result.Step)
		}
	}
}

// The bug this project had: the cache key of a step was built from the
// NAMES of the steps it needs, so a change upstream did not change the key
// of the step below it and a stale result was served.
func TestAChangeUpstreamInvalidatesTheStepBelow(t *testing.T) {
	engine := NewEngine(&RecordingExecutor{}, NewMemoryCache())

	first := testPipeline()
	if _, err := engine.Run(context.Background(), first); err != nil {
		t.Fatal(err)
	}

	// The lint step changes, so everything below it has to run again.
	changed := testPipeline()
	changed.Steps[0].Command = "gofmt -w ."

	results, err := engine.Run(context.Background(), changed)
	if err != nil {
		t.Fatal(err)
	}

	for _, result := range results {
		if result.Cached {
			t.Fatalf("the step %q should not have been cached", result.Step)
		}
	}
}

// A step that does not change keeps its cache entry.
func TestAnUnchangedStepStaysCached(t *testing.T) {
	engine := NewEngine(&RecordingExecutor{}, NewMemoryCache())

	if _, err := engine.Run(context.Background(), testPipeline()); err != nil {
		t.Fatal(err)
	}

	// The docs step does not depend on anything, so it stays cached while
	// everything below the lint step has to run again.
	pipeline := testPipeline()
	pipeline.Steps = append(pipeline.Steps, Step{Name: "docs", Image: "alpine", Command: "echo docs"})
	if _, err := engine.Run(context.Background(), pipeline); err != nil {
		t.Fatal(err)
	}

	changed := testPipeline()
	changed.Steps[0].Command = "gofmt -w ."
	changed.Steps = append(changed.Steps, Step{Name: "docs", Image: "alpine", Command: "echo docs"})

	results, err := engine.Run(context.Background(), changed)
	if err != nil {
		t.Fatal(err)
	}
	docs := results[len(results)-1]
	if !docs.Cached {
		t.Fatal("the step that did not change should be cached")
	}
	for _, result := range results[:len(results)-1] {
		if result.Cached {
			t.Fatalf("the step %q should have run again", result.Step)
		}
	}
}

func TestTheStepsReallyRun(t *testing.T) {
	directory := t.TempDir()
	executor := HostExecutor{Workdir: directory}
	engine := NewEngine(executor, NewMemoryCache())

	pipeline := Pipeline{Name: "real", Steps: []Step{
		{Name: "write", Image: "alpine", Command: "echo hello && echo hello > out.txt"},
	}}

	results, err := engine.Run(context.Background(), pipeline)
	if err != nil {
		t.Fatal(err)
	}
	if results[0].Status != "success" {
		t.Fatalf("the step did not succeed: %#v", results[0])
	}
	if results[0].Log == "" {
		t.Fatal("the output of the step was not captured")
	}

	data, err := os.ReadFile(filepath.Join(directory, "out.txt"))
	if err != nil {
		t.Fatal("the command did not run")
	}
	if strings.TrimSpace(string(data)) != "hello" {
		t.Fatalf("unexpected file contents %q", string(data))
	}
}

func TestAFailingStepFailsThePipeline(t *testing.T) {
	engine := NewEngine(HostExecutor{}, NewMemoryCache())

	pipeline := Pipeline{Name: "bad", Steps: []Step{
		{Name: "first", Image: "alpine", Command: "echo one"},
		{Name: "second", Image: "alpine", Command: "exit 3"},
		{Name: "third", Image: "alpine", Command: "echo three"},
	}}

	results, err := engine.Run(context.Background(), pipeline)
	if err == nil {
		t.Fatal("expected the pipeline to fail")
	}
	if len(results) != 2 {
		t.Fatalf("the pipeline should stop at the failing step, got %#v", results)
	}
	if results[1].Status != "failed" {
		t.Fatalf("unexpected result %#v", results[1])
	}
	if results[1].ExitCode != 3 {
		t.Fatalf("unexpected exit code %d", results[1].ExitCode)
	}
	if results[1].Reason == "" {
		t.Fatal("the failure has no reason")
	}
}

func TestAFailingStepIsRetried(t *testing.T) {
	directory := t.TempDir()
	script := filepath.Join(directory, "flaky.sh")
	// Fails the first time and succeeds the second.
	os.WriteFile(script, []byte("#!/bin/sh\ncount=$(cat \"$PWD/count\" 2>/dev/null || echo 0)\ncount=$((count + 1))\necho $count > \"$PWD/count\"\n[ $count -ge 2 ]\n"), 0o755)

	engine := NewEngine(HostExecutor{Workdir: directory}, NewMemoryCache())
	pipeline := Pipeline{Name: "flaky", Steps: []Step{
		{Name: "flaky", Image: "alpine", Command: "sh \"" + filepath.ToSlash(script) + "\"", Retries: 2},
	}}

	results, err := engine.Run(context.Background(), pipeline)
	if err != nil {
		t.Fatal(err)
	}
	if results[0].Status != "success" {
		t.Fatalf("the step should have succeeded on the second try: %#v", results[0])
	}
	if results[0].Attempts != 2 {
		t.Fatalf("unexpected number of attempts %d", results[0].Attempts)
	}
}

func TestAStepThatTimesOutFails(t *testing.T) {
	engine := NewEngine(HostExecutor{}, NewMemoryCache())
	pipeline := Pipeline{Name: "slow", Steps: []Step{
		{Name: "slow", Image: "alpine", Command: "sleep 5", Timeout: 1},
	}}

	results, err := engine.Run(context.Background(), pipeline)
	if err == nil {
		t.Fatal("expected the pipeline to fail")
	}
	if results[0].Status != "failed" {
		t.Fatalf("unexpected result %#v", results[0])
	}
}

func TestTheEnvironmentOfAStepIsSet(t *testing.T) {
	directory := t.TempDir()
	engine := NewEngine(HostExecutor{Workdir: directory}, NewMemoryCache())
	pipeline := Pipeline{Name: "env", Steps: []Step{
		{Name: "env", Image: "alpine", Command: "echo $GREETING > out.txt", Env: map[string]string{"GREETING": "hi"}},
	}}

	if _, err := engine.Run(context.Background(), pipeline); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(filepath.Join(directory, "out.txt"))
	if err != nil {
		t.Fatal(err)
	}
	if strings.TrimSpace(string(data)) != "hi" {
		t.Fatalf("unexpected file contents %q", string(data))
	}
}

func TestTheSecretOfAStepIsNotWrittenToTheLog(t *testing.T) {
	engine := NewEngine(HostExecutor{}, NewMemoryCache())
	pipeline := Pipeline{Name: "secret", Steps: []Step{
		{Name: "secret", Image: "alpine", Command: "echo done", Env: map[string]string{"TOKEN": "s3cret"}, Secret: true},
	}}

	results, err := engine.Run(context.Background(), pipeline)
	if err != nil {
		t.Fatal(err)
	}
	for _, result := range results {
		if strings.Contains(result.Log, "s3cret") {
			t.Fatal("the secret was written to the log")
		}
	}
}

func TestTheFileCacheSurvivesANewEngine(t *testing.T) {
	directory := t.TempDir()
	cache, err := NewFileCache(filepath.Join(directory, "cache"))
	if err != nil {
		t.Fatal(err)
	}

	engine := NewEngine(&RecordingExecutor{}, cache)
	if _, err := engine.Run(context.Background(), testPipeline()); err != nil {
		t.Fatal(err)
	}

	// A new engine with the same cache has everything.
	second := NewEngine(&RecordingExecutor{}, cache)
	results, err := second.Run(context.Background(), testPipeline())
	if err != nil {
		t.Fatal(err)
	}
	for _, result := range results {
		if !result.Cached {
			t.Fatalf("expected a cache hit for %s", result.Step)
		}
	}
}

func TestTheDryRunDoesNotRunAnything(t *testing.T) {
	executor := &RecordingExecutor{}
	engine := NewEngine(executor, NewMemoryCache())
	results, err := engine.Run(context.Background(), testPipeline())
	if err != nil {
		t.Fatal(err)
	}
	if len(executor.Calls()) != 4 {
		t.Fatalf("unexpected calls %#v", executor.Calls())
	}
	if !strings.Contains(results[0].Log, "would run") {
		t.Fatalf("unexpected log %q", results[0].Log)
	}
}

func TestAPipelineIsReadAndWritten(t *testing.T) {
	path := filepath.Join(t.TempDir(), "pipeline.json")
	pipeline := testPipeline()
	if err := SavePipeline(path, pipeline); err != nil {
		t.Fatal(err)
	}
	loaded, err := LoadPipeline(path)
	if err != nil {
		t.Fatal(err)
	}
	if loaded.Name != pipeline.Name || len(loaded.Steps) != len(pipeline.Steps) {
		t.Fatalf("unexpected pipeline %#v", loaded)
	}
	if _, err := LoadPipeline(filepath.Join(t.TempDir(), "missing.json")); err == nil {
		t.Fatal("expected an error for a file that is not there")
	}
}

func TestTheResultsArePrinted(t *testing.T) {
	engine := NewEngine(&RecordingExecutor{}, NewMemoryCache())
	results, err := engine.Run(context.Background(), testPipeline())
	if err != nil {
		t.Fatal(err)
	}
	var builder strings.Builder
	if err := PrintResults(&builder, results); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(builder.String(), "lint") {
		t.Fatalf("unexpected output %q", builder.String())
	}
}

func TestTheContextStopsThePipeline(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	engine := NewEngine(HostExecutor{}, NewMemoryCache())
	_, err := engine.Run(ctx, testPipeline())
	if err == nil {
		t.Fatal("expected the pipeline to fail")
	}
}
