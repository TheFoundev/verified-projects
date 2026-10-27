package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"os/exec"
	"sync"
	"time"
)

// Step is one pipeline step.
type Step struct {
	Name     string   `json:"name"`
	Image    string   `json:"image"`
	Commands []string `json:"commands"`
}

// Pipeline is a parsed pipeline file.
type Pipeline struct {
	Name  string `json:"name"`
	Steps []Step `json:"steps"`
}

// Build is one pipeline execution.
type Build struct {
	ID      string    `json:"id"`
	Repo    string    `json:"repo"`
	Commit  string    `json:"commit"`
	Status  string    `json:"status"`
	Started time.Time `json:"started"`
	Logs    []string  `json:"logs"`

	PipelineSteps []Step `json:"-"`
}

// Server stores builds and triggers new ones.
type Server struct {
	mu     sync.Mutex
	builds map[string]Build
	next   int
	// Runner runs the commands of the steps.
	runner Runner
	// workdir is the directory the commands run in.
	workdir string
}

// SetRunner sets the runner that runs the commands of the steps.
func (server *Server) SetRunner(runner Runner) {
	server.mu.Lock()
	defer server.mu.Unlock()
	server.runner = runner
}

// SetWorkdir sets the directory the commands run in.
func (server *Server) SetWorkdir(workdir string) {
	server.mu.Lock()
	defer server.mu.Unlock()
	server.workdir = workdir
}

func NewServer() *Server {
	return &Server{builds: map[string]Build{}}
}

func parseLines(text string) []string {
	lines := []string{}
	current := ""
	for _, character := range text {
		if character == '\n' {
			lines = append(lines, current)
			current = ""
			continue
		}
		current += string(character)
	}
	if current != "" {
		lines = append(lines, current)
	}
	return lines
}

// ParsePipeline reads the small "steps:" example format.
func ParsePipeline(text string) (Pipeline, error) {
	pipeline := Pipeline{}
	var step *Step
	for _, raw := range parseLines(text) {
		trimmed := trimSpaces(raw)
		switch {
		case len(trimmed) > 5 && trimmed[:5] == "name:" && step == nil:
			pipeline.Name = trimSpaces(trimmed[5:])
		case len(trimmed) > 7 && trimmed[:7] == "- name:":
			pipeline.Steps = append(pipeline.Steps, Step{Name: trimSpaces(trimmed[7:])})
			step = &pipeline.Steps[len(pipeline.Steps)-1]
		case len(trimmed) > 6 && trimmed[:6] == "image:":
			if step != nil {
				step.Image = trimSpaces(trimmed[6:])
			}
		case len(trimmed) > 2 && trimmed[:2] == "- " && step != nil:
			step.Commands = append(step.Commands, trimSpaces(trimmed[2:]))
		}
	}
	if pipeline.Name == "" || len(pipeline.Steps) == 0 {
		return Pipeline{}, fmt.Errorf("pipeline needs a name and at least one step")
	}
	return pipeline, nil
}

func trimSpaces(text string) string {
	start, end := 0, len(text)
	for start < end && (text[start] == ' ' || text[start] == '\t' || text[start] == '\r') {
		start++
	}
	for end > start && (text[end-1] == ' ' || text[end-1] == '\t' || text[end-1] == '\r') {
		end--
	}
	return text[start:end]
}

// Trigger creates a pending build.
func (server *Server) Trigger(pipeline Pipeline, repo, commit string) Build {
	server.mu.Lock()
	defer server.mu.Unlock()
	server.next++
	build := Build{
		ID: fmt.Sprintf("build-%d", server.next), Repo: repo, Commit: commit,
		Status: "pending", Started: time.Now().UTC(), Logs: []string{},
		PipelineSteps: pipeline.Steps,
	}
	server.builds[build.ID] = build
	return build
}

// Run executes a build and records the log lines.
// Runner runs the commands of a step.
//
// The interface is there so the server can run the steps on the machine and
// the tests can run them without one.
type Runner interface {
	Run(command string, workdir string) (exitCode int, output string, err error)
}

// HostRunner runs the commands on the machine.
type HostRunner struct {
	// Shell is the shell the commands run in.
	Shell string
}

// Run runs a command and returns its exit code and its output.
func (runner HostRunner) Run(command string, workdir string) (int, string, error) {
	shell := runner.Shell
	if shell == "" {
		shell = "sh"
	}
	process := exec.CommandContext(context.Background(), shell, "-c", command)
	process.Dir = workdir
	output, err := process.CombinedOutput()
	if err != nil {
		var exitError *exec.ExitError
		if errors.As(err, &exitError) {
			return exitError.ExitCode(), string(output), nil
		}
		return 1, string(output), err
	}
	return 0, string(output), nil
}

func (server *Server) Run(id string) (Build, error) {
	server.mu.Lock()
	defer server.mu.Unlock()
	build, ok := server.builds[id]
	if !ok {
		return Build{}, fmt.Errorf("unknown build %q", id)
	}
	// The steps really run, and a step that fails stops the build.
	runner := server.runner
	if runner == nil {
		runner = HostRunner{}
	}
	workdir := server.workdir
	if workdir == "" {
		workdir = "."
	}

	build.Status = "success"
	for _, step := range build.Steps() {
		for _, command := range step.Commands {
			build.Logs = append(build.Logs, fmt.Sprintf("[%s] $ %s", step.Name, command))
			exitCode, output, err := runner.Run(command, workdir)
			if output != "" {
				build.Logs = append(build.Logs, output)
			}
			if err != nil || exitCode != 0 {
				build.Status = "failed"
				if err != nil {
					build.Logs = append(build.Logs, fmt.Sprintf("the command failed: %v", err))
				} else {
					build.Logs = append(build.Logs, fmt.Sprintf("the command exited with %d", exitCode))
				}
				server.builds[id] = build
				return build, nil
			}
		}
	}
	server.builds[id] = build
	return build, nil
}

// Steps returns the steps recorded at trigger time.
func (build Build) Steps() []Step {
	return build.PipelineSteps
}

func (server *Server) Get(id string) Build {
	server.mu.Lock()
	defer server.mu.Unlock()
	return server.builds[id]
}

func (server *Server) List() []Build {
	server.mu.Lock()
	defer server.mu.Unlock()
	list := []Build{}
	for _, build := range server.builds {
		list = append(list, build)
	}
	return list
}

func (server *Server) ListHandler(writer http.ResponseWriter, request *http.Request) {
	_ = json.NewEncoder(writer).Encode(server.List())
}

func (server *Server) TriggerHandler(writer http.ResponseWriter, request *http.Request) {
	repo := request.URL.Query().Get("repo")
	commit := request.URL.Query().Get("commit")
	pipeline, err := ParsePipeline(samplePipeline)
	if err != nil {
		http.Error(writer, err.Error(), http.StatusBadRequest)
		return
	}
	build := server.Trigger(pipeline, repo, commit)
	build, _ = server.Run(build.ID)
	_ = json.NewEncoder(writer).Encode(build)
}
