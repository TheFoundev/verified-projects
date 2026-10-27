package main

import (
	"context"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestResolveOrder(t *testing.T) {
	pipeline, tasks := sampleDefinitions()
	ordered, err := Resolve(pipeline, tasks)
	if err != nil {
		t.Fatal(err)
	}
	if ordered[0].Name != "build" {
		t.Fatalf("build should run first, got %v", ordered)
	}
	statuses, err := Execute(pipeline, tasks)
	if err != nil {
		t.Fatal(err)
	}
	if len(statuses) != 3 || statuses[0].Status != "succeeded" {
		t.Fatalf("unexpected statuses %#v", statuses)
	}
}

func TestMissingReferenceAndCycle(t *testing.T) {
	tasks := map[string]Task{"a": {Name: "a"}}
	missing := Pipeline{Name: "x", Tasks: []PipelineTask{{Name: "a", TaskRef: "missing"}}}
	if _, err := Resolve(missing, tasks); err == nil {
		t.Fatal("expected a missing task error")
	}
	cyclic := Pipeline{Name: "x", Tasks: []PipelineTask{
		{Name: "a", TaskRef: "a", RunAfter: []string{"b"}},
		{Name: "b", TaskRef: "b", RunAfter: []string{"a"}},
	}}
	if _, err := Resolve(cyclic, tasks); err == nil {
		t.Fatal("expected a cycle error")
	}
}

func TestATaskThatIsNamedTwiceIsRefused(t *testing.T) {
	tasks := map[string]Task{"a": {Name: "a"}}
	pipeline := Pipeline{Name: "x", Tasks: []PipelineTask{
		{Name: "a", TaskRef: "a"},
		{Name: "a", TaskRef: "a"},
	}}
	if _, err := Resolve(pipeline, tasks); err == nil {
		t.Fatal("expected an error for a task that is named twice")
	}
}

func TestTheParametersAreSubstituted(t *testing.T) {
	command, err := Substitute("echo $(params.version)", map[string]string{"version": "1.2"})
	if err != nil {
		t.Fatal(err)
	}
	if command != "echo 1.2" {
		t.Fatalf("unexpected command %q", command)
	}
	if _, err := Substitute("echo $(params.missing)", map[string]string{}); err == nil {
		t.Fatal("expected an error for a parameter that was not given")
	}
}

func TestTheTasksReallyRun(t *testing.T) {
	workspace := t.TempDir()
	tasks := map[string]Task{
		"write": {Name: "write", Steps: []TaskStep{{Name: "write", Command: "echo hello > out.txt"}}},
	}
	pipeline := Pipeline{Name: "p", Tasks: []PipelineTask{{Name: "write", TaskRef: "write"}}}

	runner := Runner{Workspace: workspace}
	statuses, err := runner.Execute(context.Background(), pipeline, tasks)
	if err != nil {
		t.Fatal(err)
	}
	if len(statuses) != 1 || statuses[0].Status != "succeeded" {
		t.Fatalf("unexpected statuses %#v", statuses)
	}
	if _, err := os.Stat(filepath.Join(workspace, "out.txt")); err != nil {
		t.Fatal("the command did not run")
	}
}

func TestAFailingTaskStopsThePipeline(t *testing.T) {
	workspace := t.TempDir()
	tasks := map[string]Task{
		"first":  {Name: "first", Steps: []TaskStep{{Name: "one", Command: "exit 3"}}},
		"second": {Name: "second", Steps: []TaskStep{{Name: "two", Command: "echo two"}}},
	}
	pipeline := Pipeline{Name: "p", Tasks: []PipelineTask{
		{Name: "first", TaskRef: "first"},
		{Name: "second", TaskRef: "second", RunAfter: []string{"first"}},
	}}

	runner := Runner{Workspace: workspace}
	statuses, err := runner.Execute(context.Background(), pipeline, tasks)
	if err != nil {
		t.Fatal(err)
	}
	if statuses[0].Status != "failed" {
		t.Fatalf("the first task should have failed: %#v", statuses[0])
	}
	if statuses[1].Status != "skipped" {
		t.Fatalf("the second task should have been skipped: %#v", statuses[1])
	}
}

func TestTheFinallyTasksAlwaysRun(t *testing.T) {
	workspace := t.TempDir()
	tasks := map[string]Task{
		"first": {Name: "first", Steps: []TaskStep{{Name: "one", Command: "exit 1"}}},
		"clean": {Name: "clean", Steps: []TaskStep{{Name: "clean", Command: "echo cleaned"}}},
	}
	pipeline := Pipeline{
		Name:    "p",
		Tasks:   []PipelineTask{{Name: "first", TaskRef: "first"}},
		Finally: []PipelineTask{{Name: "clean", TaskRef: "clean"}},
	}

	runner := Runner{Workspace: workspace}
	statuses, err := runner.Execute(context.Background(), pipeline, tasks)
	if err != nil {
		t.Fatal(err)
	}
	if len(statuses) != 2 {
		t.Fatalf("unexpected statuses %#v", statuses)
	}
	if statuses[1].Status != "succeeded" {
		t.Fatalf("the finally task should have run: %#v", statuses[1])
	}
}

func TestAStepIsRetried(t *testing.T) {
	workspace := t.TempDir()
	script := filepath.Join(workspace, "flaky.sh")
	os.WriteFile(script, []byte("#!/bin/sh\ncount=$(cat count 2>/dev/null || echo 0)\ncount=$((count + 1))\necho $count > count\n[ $count -ge 2 ]\n"), 0o755)

	tasks := map[string]Task{
		"flaky": {Name: "flaky", Steps: []TaskStep{{Name: "flaky", Command: "sh \"" + filepath.ToSlash(script) + "\"", Retries: 2}}},
	}
	pipeline := Pipeline{Name: "p", Tasks: []PipelineTask{{Name: "flaky", TaskRef: "flaky"}}}

	runner := Runner{Workspace: workspace}
	statuses, err := runner.Execute(context.Background(), pipeline, tasks)
	if err != nil {
		t.Fatal(err)
	}
	if statuses[0].Status != "succeeded" {
		t.Fatalf("the task should have succeeded on the second try: %#v", statuses[0])
	}
}

func TestAStepThatTimesOutFails(t *testing.T) {
	workspace := t.TempDir()
	tasks := map[string]Task{
		"slow": {Name: "slow", Steps: []TaskStep{{Name: "slow", Command: "sleep 5", Timeout: 1}}},
	}
	pipeline := Pipeline{Name: "p", Tasks: []PipelineTask{{Name: "slow", TaskRef: "slow"}}}

	runner := Runner{Workspace: workspace}
	statuses, _ := runner.Execute(context.Background(), pipeline, tasks)
	if statuses[0].Status != "failed" {
		t.Fatalf("the task should have failed: %#v", statuses[0])
	}
}

func TestTheResultsAreRead(t *testing.T) {
	workspace := t.TempDir()
	tasks := map[string]Task{
		"report": {
			Name:    "report",
			Steps:   []TaskStep{{Name: "report", Command: "echo 42 > result.txt"}},
			Results: []string{"result.txt"},
		},
	}
	pipeline := Pipeline{Name: "p", Tasks: []PipelineTask{{Name: "report", TaskRef: "report"}}}

	runner := Runner{Workspace: workspace}
	statuses, _ := runner.Execute(context.Background(), pipeline, tasks)
	if statuses[0].Results["result.txt"] != "42" {
		t.Fatalf("unexpected results %#v", statuses[0].Results)
	}
}

func TestTheDefinitionsAreReadFromADirectory(t *testing.T) {
	directory := t.TempDir()
	os.WriteFile(filepath.Join(directory, "build.json"), []byte(`{
		"kind": "Task",
		"metadata": {"name": "build"},
		"spec": {"steps": [{"name": "compile", "command": "echo build"}]}
	}`), 0o644)
	os.WriteFile(filepath.Join(directory, "pipeline.json"), []byte(`{
		"kind": "Pipeline",
		"metadata": {"name": "release"},
		"spec": {"tasks": [{"name": "build", "taskRef": "build"}]}
	}`), 0o644)

	tasks, pipeline, err := LoadDefinitionSet(directory)
	if err != nil {
		t.Fatal(err)
	}
	if len(tasks) != 1 || pipeline.Name != "release" {
		t.Fatalf("unexpected definitions %#v %#v", tasks, pipeline)
	}
	if _, ok := tasks["build"]; !ok {
		t.Fatalf("the task was not read: %#v", tasks)
	}
}

func TestTheDurationIsMeasured(t *testing.T) {
	workspace := t.TempDir()
	moment := time.Date(2026, 4, 23, 8, 0, 0, 0, time.UTC)
	runner := Runner{
		Workspace: workspace,
		Now:       func() time.Time { return moment },
	}
	tasks := map[string]Task{"a": {Name: "a", Steps: []TaskStep{{Name: "a", Command: "echo a"}}}}
	pipeline := Pipeline{Name: "p", Tasks: []PipelineTask{{Name: "a", TaskRef: "a"}}}

	statuses, _ := runner.Execute(context.Background(), pipeline, tasks)
	if statuses[0].Duration != "0s" {
		t.Fatalf("unexpected duration %q", statuses[0].Duration)
	}
}
