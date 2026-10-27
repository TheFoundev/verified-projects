package main

import (
	"os"
	"strings"
	"testing"
)

func TestParsePipeline(t *testing.T) {
	pipeline, err := ParsePipeline(samplePipeline)
	if err != nil {
		t.Fatal(err)
	}
	if pipeline.Name != "test" || len(pipeline.Steps) != 2 {
		t.Fatalf("unexpected pipeline %#v", pipeline)
	}
	if pipeline.Steps[1].Commands[0] != "go test ./..." {
		t.Fatalf("unexpected command %#v", pipeline.Steps[1])
	}
	if _, err := ParsePipeline("steps:\n"); err == nil {
		t.Fatal("expected an error for an empty pipeline")
	}
}

// A runner that answers without running anything.
type fakeRunner struct {
	calls   []string
	failing string
}

func (runner *fakeRunner) Run(command string, workdir string) (int, string, error) {
	runner.calls = append(runner.calls, command)
	if runner.failing != "" && command == runner.failing {
		return 1, "it failed", nil
	}
	return 0, "ok", nil
}

func TestTriggerAndRun(t *testing.T) {
	server := NewServer()
	runner := &fakeRunner{}
	server.SetRunner(runner)
	pipeline, _ := ParsePipeline(samplePipeline)
	build := server.Trigger(pipeline, "foundev/web", "abc")
	if build.Status != "pending" {
		t.Fatalf("unexpected status %s", build.Status)
	}
	finished, err := server.Run(build.ID)
	if err != nil {
		t.Fatal(err)
	}
	if finished.Status != "success" {
		t.Fatalf("unexpected build %#v", finished)
	}
	if len(runner.calls) != 2 {
		t.Fatalf("the steps did not run: %#v", runner.calls)
	}
	if _, err := server.Run("missing"); err == nil {
		t.Fatal("expected an unknown build error")
	}
}

func TestAFailingStepStopsTheBuild(t *testing.T) {
	server := NewServer()
	runner := &fakeRunner{failing: "go test ./..."}
	server.SetRunner(runner)

	pipeline, _ := ParsePipeline(samplePipeline)
	build := server.Trigger(pipeline, "foundev/web", "abc")
	finished, err := server.Run(build.ID)
	if err != nil {
		t.Fatal(err)
	}
	if finished.Status != "failed" {
		t.Fatalf("the build should have failed: %#v", finished)
	}
}

func TestTheStepsRunOnTheMachine(t *testing.T) {
	if _, err := os.Stat("/bin/sh"); err != nil {
		t.Skip("there is no shell on this machine")
	}
	directory := t.TempDir()
	runner := HostRunner{}
	exitCode, output, err := runner.Run("echo hello", directory)
	if err != nil {
		t.Fatal(err)
	}
	if exitCode != 0 {
		t.Fatalf("unexpected exit code %d", exitCode)
	}
	if !strings.Contains(output, "hello") {
		t.Fatalf("unexpected output %q", output)
	}
}
