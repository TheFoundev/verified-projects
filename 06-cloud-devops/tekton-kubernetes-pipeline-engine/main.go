package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"time"
)

func sampleDefinitions() (Pipeline, map[string]Task) {
	tasks := map[string]Task{
		"build": {Name: "build", Steps: []TaskStep{{Name: "compile", Command: "go build ./..."}}},
		"test":  {Name: "test", Steps: []TaskStep{{Name: "unit", Command: "go test ./..."}}},
		"scan":  {Name: "scan", Steps: []TaskStep{{Name: "trivy", Command: "trivy image api"}}},
	}
	pipeline := Pipeline{Name: "release", Tasks: []PipelineTask{
		{Name: "build", TaskRef: "build"},
		{Name: "test", TaskRef: "test", RunAfter: []string{"build"}},
		{Name: "scan", TaskRef: "scan", RunAfter: []string{"build"}},
	}}
	return pipeline, tasks
}

func main() {
	pipelineName := flag.String("pipeline", "release", "pipeline name")
	definitions := flag.String("definitions", "", "read the tasks and the pipeline from this directory")
	run := flag.Bool("run", false, "run the steps of the tasks")
	workspace := flag.String("workspace", "", "the directory the tasks share")
	flag.Parse()

	pipeline, tasks := sampleDefinitions()
	if *definitions != "" {
		loadedTasks, loadedPipeline, err := LoadDefinitionSet(*definitions)
		if err != nil {
			fmt.Fprintln(os.Stderr, "could not read the definitions:", err)
			os.Exit(1)
		}
		tasks = loadedTasks
		pipeline = loadedPipeline
	}

	started := time.Now()
	var statuses []TaskStatus
	var err error

	if *run {
		runner := Runner{Workspace: *workspace}
		statuses, err = runner.Execute(context.Background(), pipeline, tasks)
	} else {
		statuses, err = Execute(pipeline, tasks)
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}

	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	_ = encoder.Encode(map[string]any{
		"pipeline": *pipelineName,
		"duration": time.Since(started).String(),
		"statuses": statuses,
	})
}

// Execute resolves the pipeline and reports what every task would do.
//
// It is the dry run of the runner.
func Execute(pipeline Pipeline, tasks map[string]Task) ([]TaskStatus, error) {
	ordered, err := Resolve(pipeline, tasks)
	if err != nil {
		return nil, err
	}
	statuses := []TaskStatus{}
	for _, item := range ordered {
		task := tasks[item.TaskRef]
		statuses = append(statuses, TaskStatus{
			Name: item.Name, Task: task.Name, Status: "succeeded", Steps: len(task.Steps),
		})
	}
	return statuses, nil
}
