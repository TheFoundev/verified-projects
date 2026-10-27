package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// TaskStep is one command in a task.
type TaskStep struct {
	Name    string `json:"name"`
	Command string `json:"command"`
	Image   string `json:"image,omitempty"`
	// Retries is how often a failing step is tried again.
	Retries int `json:"retries,omitempty"`
	// Timeout is how long the step may run, in seconds.
	Timeout int `json:"timeout,omitempty"`
	// Script is the script the command runs in.
	Script string `json:"script,omitempty"`
}

// Task is a reusable unit of work.
type Task struct {
	Name     string            `json:"name"`
	Steps    []TaskStep        `json:"steps"`
	Params   []string          `json:"params,omitempty"`
	Results  []string          `json:"results,omitempty"`
	Retries  int               `json:"retries,omitempty"`
	Timeout  int               `json:"timeout,omitempty"`
	Metadata map[string]string `json:"metadata,omitempty"`
}

// Param is one value of a task.
type Param struct {
	Name  string `json:"name"`
	Value string `json:"value"`
}

// PipelineTask references a task in a pipeline.
type PipelineTask struct {
	Name     string            `json:"name"`
	TaskRef  string            `json:"taskRef"`
	RunAfter []string          `json:"runAfter"`
	Params   map[string]string `json:"params"`
	// Finally marks a task that runs when the pipeline is over, whether it
	// succeeded or not.
	Finally bool `json:"finally,omitempty"`
}

// Pipeline is an ordered graph of tasks.
type Pipeline struct {
	Name  string         `json:"name"`
	Tasks []PipelineTask `json:"tasks"`
	// Finally holds the tasks that always run at the end.
	Finally []PipelineTask `json:"finally,omitempty"`
}

// TaskStatus records one execution.
type TaskStatus struct {
	Name     string            `json:"name"`
	Task     string            `json:"task"`
	Status   string            `json:"status"`
	Steps    int               `json:"steps"`
	Duration string            `json:"duration,omitempty"`
	Reason   string            `json:"reason,omitempty"`
	Log      string            `json:"log,omitempty"`
	Results  map[string]string `json:"results,omitempty"`
}

// ---------------------------------------------------------------------------
// The order
// ---------------------------------------------------------------------------

// Resolve orders the tasks of a pipeline, honouring runAfter.
//
// The order is a topological order: a task runs after the tasks it names.
// A task that names a task that is not in the pipeline, and a cycle, are
// errors.
func Resolve(pipeline Pipeline, tasks map[string]Task) ([]PipelineTask, error) {
	byName := map[string]PipelineTask{}
	for _, candidate := range pipeline.Tasks {
		if candidate.Name == "" {
			return nil, fmt.Errorf("a task of the pipeline %q has no name", pipeline.Name)
		}
		if _, exists := byName[candidate.Name]; exists {
			return nil, fmt.Errorf("the task %q is in the pipeline twice", candidate.Name)
		}
		if _, ok := tasks[candidate.TaskRef]; !ok {
			return nil, fmt.Errorf("task reference %q not found", candidate.TaskRef)
		}
		byName[candidate.Name] = candidate
	}

	resolved := []PipelineTask{}
	done := map[string]bool{}
	for len(resolved) < len(pipeline.Tasks) {
		progress := false
		for _, candidate := range pipeline.Tasks {
			if done[candidate.Name] {
				continue
			}
			ready := true
			for _, need := range candidate.RunAfter {
				if _, ok := byName[need]; !ok {
					return nil, fmt.Errorf("runAfter %q not found", need)
				}
				if !done[need] {
					ready = false
					break
				}
			}
			if ready {
				resolved = append(resolved, candidate)
				done[candidate.Name] = true
				progress = true
			}
		}
		if !progress {
			return nil, fmt.Errorf("pipeline %q has a cycle", pipeline.Name)
		}
	}
	return resolved, nil
}

// ---------------------------------------------------------------------------
// The parameters
// ---------------------------------------------------------------------------

// Substitute replaces the parameters of a command.
//
// Tekton writes a parameter as $(params.name). A parameter that is not
// given is an error, because a command with a hole in it is worse than a
// command that does not run.
func Substitute(command string, params map[string]string) (string, error) {
	result := command
	for name, value := range params {
		result = strings.ReplaceAll(result, "$(params."+name+")", value)
		result = strings.ReplaceAll(result, "$(params."+name+".path)", value)
	}
	if strings.Contains(result, "$(params.") {
		missing := result[strings.Index(result, "$(params."):]
		missing = missing[:strings.Index(missing, ")")+1]
		return "", fmt.Errorf("the parameter %s was not given", missing)
	}
	return result, nil
}

// ---------------------------------------------------------------------------
// The runner
// ---------------------------------------------------------------------------

// Runner executes the tasks of a pipeline.
type Runner struct {
	// Workspace is the directory the tasks share. A temporary directory is
	// used when it is empty.
	Workspace string
	// Shell is the shell the commands run in.
	Shell string
	// Now is injectable so the tests do not depend on the clock.
	Now func() time.Time
}

func (runner *Runner) now() time.Time {
	if runner.Now != nil {
		return runner.Now()
	}
	return time.Now()
}

// Execute resolves and runs every task.
//
// A task that fails stops the pipeline, and the tasks that are marked as
// finally still run. The results of a task are read from the workspace.
func (runner *Runner) Execute(ctx context.Context, pipeline Pipeline, tasks map[string]Task) ([]TaskStatus, error) {
	ordered, err := Resolve(pipeline, tasks)
	if err != nil {
		return nil, err
	}

	workspace := runner.Workspace
	if workspace == "" {
		directory, err := os.MkdirTemp("", "pipeline-")
		if err != nil {
			return nil, err
		}
		defer os.RemoveAll(directory)
		workspace = directory
	}
	if err := os.MkdirAll(workspace, 0o755); err != nil {
		return nil, err
	}

	statuses := []TaskStatus{}
	stopped := false

	for _, item := range ordered {
		if stopped {
			statuses = append(statuses, TaskStatus{
				Name: item.Name, Task: item.TaskRef, Status: "skipped",
				Reason: "an earlier task failed",
			})
			continue
		}
		status := runner.runTask(ctx, item, tasks[item.TaskRef], workspace)
		statuses = append(statuses, status)
		if status.Status != "succeeded" {
			stopped = true
		}
	}

	// The finally tasks always run.
	for _, item := range pipeline.Finally {
		status := runner.runTask(ctx, item, tasks[item.TaskRef], workspace)
		statuses = append(statuses, status)
	}

	return statuses, nil
}

// runTask runs the steps of one task.
func (runner *Runner) runTask(ctx context.Context, item PipelineTask, task Task, workspace string) TaskStatus {
	started := runner.now()
	status := TaskStatus{Name: item.Name, Task: task.Name, Status: "succeeded", Steps: len(task.Steps)}

	params := map[string]string{}
	for name, value := range item.Params {
		params[name] = value
	}

	for _, step := range task.Steps {
		command, err := Substitute(step.Command, params)
		if err != nil {
			status.Status = "failed"
			status.Reason = err.Error()
			status.Duration = runner.now().Sub(started).String()
			return status
		}

		output, exitCode, err := runner.runStep(ctx, step, command, workspace)
		status.Log += output
		if err != nil || exitCode != 0 {
			status.Status = "failed"
			if err != nil {
				status.Reason = err.Error()
			} else {
				status.Reason = fmt.Sprintf("the command exited with %d", exitCode)
			}
			status.Duration = runner.now().Sub(started).String()
			return status
		}
	}

	status.Results = runner.readResults(task, workspace)
	status.Duration = runner.now().Sub(started).String()
	return status
}

// runStep runs one command of a task, with its retries and its timeout.
func (runner *Runner) runStep(ctx context.Context, step TaskStep, command, workspace string) (string, int, error) {
	attempts := step.Retries + 1
	if attempts < 1 {
		attempts = 1
	}

	shell := runner.Shell
	if shell == "" {
		shell = "sh"
	}
	script := step.Script
	if script == "" {
		script = shell
	}

	var output string
	var exitCode int
	var err error

	for attempt := 1; attempt <= attempts; attempt++ {
		stepContext := ctx
		if step.Timeout > 0 {
			var cancel context.CancelFunc
			stepContext, cancel = context.WithTimeout(ctx, time.Duration(step.Timeout)*time.Second)
			defer cancel()
		}

		process := exec.CommandContext(stepContext, script, "-c", command)
		process.Dir = workspace
		bytes, runError := process.CombinedOutput()
		output = string(bytes)
		exitCode = 0
		err = nil
		if runError != nil {
			var exitError *exec.ExitError
			if asExitError(runError, &exitError) {
				exitCode = exitError.ExitCode()
			} else {
				err = runError
			}
		}

		if err == nil && exitCode == 0 {
			return output, 0, nil
		}
		if stepContext.Err() != nil {
			return output, exitCode, fmt.Errorf("the step timed out")
		}
	}

	return output, exitCode, err
}

// readResults reads the result files a task wrote in the workspace.
func (runner *Runner) readResults(task Task, workspace string) map[string]string {
	results := map[string]string{}
	for _, name := range task.Results {
		path := filepath.Join(workspace, name)
		data, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		results[name] = strings.TrimSpace(string(data))
	}
	return results
}

// asExitError is errors.As for an exit error, kept in one place.
func asExitError(err error, target **exec.ExitError) bool {
	if exitError, ok := err.(*exec.ExitError); ok {
		*target = exitError
		return true
	}
	return false
}

// ---------------------------------------------------------------------------
// The definitions
// ---------------------------------------------------------------------------

// Definition is a Tekton manifest: a task, a pipeline or a pipeline run.
type Definition struct {
	Kind     string `json:"kind"`
	Metadata struct {
		Name string `json:"name"`
	} `json:"metadata"`
	Spec Spec `json:"spec"`
}

// Spec is the part of a manifest the engine cares about.
type Spec struct {
	Params    []Param        `json:"params"`
	Steps     []TaskStep     `json:"steps"`
	Tasks     []PipelineTask `json:"tasks"`
	Finally   []PipelineTask `json:"finally"`
	Resources struct {
		Workspace string `json:"workspace"`
	} `json:"resources"`
}

// LoadDefinition reads a manifest from a JSON file.
//
// The manifests of this project are JSON, which the Tekton tools also
// understand once they are converted.
func LoadDefinition(path string) (Definition, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Definition{}, err
	}
	var definition Definition
	if err := json.Unmarshal(data, &definition); err != nil {
		return Definition{}, err
	}
	return definition, nil
}

// LoadDefinitionSet reads a directory of manifests.
func LoadDefinitionSet(directory string) (map[string]Task, Pipeline, error) {
	tasks := map[string]Task{}
	pipeline := Pipeline{}

	entries, err := os.ReadDir(directory)
	if err != nil {
		return nil, pipeline, err
	}
	names := make([]string, 0, len(entries))
	for _, entry := range entries {
		names = append(names, entry.Name())
	}
	sort.Strings(names)

	for _, name := range names {
		definition, err := LoadDefinition(filepath.Join(directory, name))
		if err != nil {
			return nil, pipeline, err
		}
		switch definition.Kind {
		case "Task":
			task := Task{Name: definition.Metadata.Name, Steps: definition.Spec.Steps}
			for _, param := range definition.Spec.Params {
				task.Params = append(task.Params, param.Name)
			}
			tasks[task.Name] = task
		case "Pipeline":
			pipeline = Pipeline{
				Name:    definition.Metadata.Name,
				Tasks:   definition.Spec.Tasks,
				Finally: definition.Spec.Finally,
			}
		}
	}
	return tasks, pipeline, nil
}
