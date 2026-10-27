package main

import (
	"crypto/sha1"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
)

// Object is one content-addressed entry.
type Object struct {
	Hash    string `json:"hash"`
	Type    string `json:"type"`
	Content string `json:"content"`
}

// Commit points at a tree and a parent commit.
type Commit struct {
	Hash    string            `json:"hash"`
	Branch  string            `json:"branch"`
	Message string            `json:"message"`
	Parent  string            `json:"parent"`
	Tree    string            `json:"tree"`
	Files   map[string]string `json:"files"`
	Author  string            `json:"author"`
	When    string            `json:"when"`
}

// Repository stores objects and branch heads.
type Repository struct {
	mu      sync.RWMutex
	objects map[string]Object
	refs    map[string]string
	commits []Commit
}

func NewRepository() *Repository {
	return &Repository{objects: map[string]Object{}, refs: map[string]string{}, commits: []Commit{}}
}

// gitObjectHash is the hash git gives an object.
//
// Git hashes the type, the size and the content of an object, separated by
// a null byte: "blob 12\0hello world". With that format the objects of
// this repository have the same hashes as the objects of git.
func gitObjectHash(kind, content string) string {
	header := fmt.Sprintf("%s %d", kind, len(content))
	sum := sha1.Sum([]byte(header + "\x00" + content))
	return hex.EncodeToString(sum[:])
}

// hashOf is kept for the objects of this repository.
func hashOf(kind, content string) string {
	return gitObjectHash(kind, content)
}

func (repository *Repository) store(kind, content string) string {
	hash := hashOf(kind, content)
	repository.objects[hash] = Object{Hash: hash, Type: kind, Content: content}
	return hash
}

// Object returns an object by its hash.
func (repository *Repository) Object(hash string) (Object, bool) {
	repository.mu.RLock()
	defer repository.mu.RUnlock()
	object, ok := repository.objects[hash]
	return object, ok
}

// Commit stores file blobs, a tree listing and the commit object.
func (repository *Repository) Commit(branch, message string, files map[string]string) Commit {
	return repository.CommitAs(branch, message, files, "foundev", "")
}

// CommitAs stores a commit with an author and a date.
func (repository *Repository) CommitAs(branch, message string, files map[string]string, author, when string) Commit {
	repository.mu.Lock()
	defer repository.mu.Unlock()

	names := make([]string, 0, len(files))
	for name := range files {
		names = append(names, name)
	}
	sort.Strings(names)

	treeLines := ""
	for _, name := range names {
		blob := repository.store("blob", files[name])
		treeLines += fmt.Sprintf("%s %s\n", blob, name)
	}
	tree := repository.store("tree", treeLines)

	parent := repository.refs[branch]
	payload := fmt.Sprintf("branch:%s\nparent:%s\ntree:%s\nmessage:%s\nauthor:%s\nwhen:%s\n",
		branch, parent, tree, message, author, when)
	hash := repository.store("commit", payload)

	commit := Commit{
		Hash: hash, Branch: branch, Message: message, Parent: parent,
		Tree: tree, Files: files, Author: author, When: when,
	}
	repository.refs[branch] = hash
	repository.commits = append(repository.commits, commit)
	return commit
}

// Log returns commits for a branch, newest first.
func (repository *Repository) Log(branch string, limit int) []Commit {
	repository.mu.RLock()
	defer repository.mu.RUnlock()
	history := []Commit{}
	for index := len(repository.commits) - 1; index >= 0; index-- {
		commit := repository.commits[index]
		if commit.Branch != branch {
			continue
		}
		history = append(history, commit)
		if limit > 0 && len(history) >= limit {
			break
		}
	}
	return history
}

// Head returns the commit a branch points at.
func (repository *Repository) Head(branch string) string {
	repository.mu.RLock()
	defer repository.mu.RUnlock()
	return repository.refs[branch]
}

// Branches returns the branches and the commit they point at.
func (repository *Repository) Branches() map[string]string {
	repository.mu.RLock()
	defer repository.mu.RUnlock()
	branches := map[string]string{}
	for name, hash := range repository.refs {
		branches[name] = hash
	}
	return branches
}

// CreateBranch points a new branch at a commit. A branch that is already
// there is not changed.
func (repository *Repository) CreateBranch(name, from string) error {
	repository.mu.Lock()
	defer repository.mu.Unlock()
	if name == "" {
		return fmt.Errorf("a branch needs a name")
	}
	if _, exists := repository.refs[name]; exists {
		return fmt.Errorf("the branch %q is already there", name)
	}
	if from == "" {
		return fmt.Errorf("a branch needs a commit to start from")
	}
	if _, ok := repository.objects[from]; !ok {
		return fmt.Errorf("the commit %q does not exist", from)
	}
	repository.refs[name] = from
	return nil
}

// Checkout moves a branch to another commit.
func (repository *Repository) Checkout(branch, commit string) error {
	repository.mu.Lock()
	defer repository.mu.Unlock()
	if _, ok := repository.objects[commit]; !ok {
		return fmt.Errorf("the commit %q does not exist", commit)
	}
	repository.refs[branch] = commit
	return nil
}

// Diff returns the files that changed between two commits.
func (repository *Repository) Diff(from, to string) map[string][2]string {
	repository.mu.RLock()
	defer repository.mu.RUnlock()

	before := map[string]string{}
	after := map[string]string{}
	for _, commit := range repository.commits {
		if commit.Hash == from {
			before = commit.Files
		}
		if commit.Hash == to {
			after = commit.Files
		}
	}

	changes := map[string][2]string{}
	for name, content := range after {
		if previous, ok := before[name]; !ok || previous != content {
			changes[name] = [2]string{"", content}
		}
	}
	for name, content := range before {
		if _, ok := after[name]; !ok {
			changes[name] = [2]string{content, ""}
		}
	}
	return changes
}

// ---------------------------------------------------------------------------
// Persistence
// ---------------------------------------------------------------------------

// Save writes the repository to a file.
func (repository *Repository) Save(path string) error {
	repository.mu.RLock()
	defer repository.mu.RUnlock()

	state := struct {
		Objects map[string]Object `json:"objects"`
		Refs    map[string]string `json:"refs"`
		Commits []Commit          `json:"commits"`
	}{repository.objects, repository.refs, repository.commits}

	data, err := json.MarshalIndent(state, "", "  ")
	if err != nil {
		return err
	}
	if directory := filepath.Dir(path); directory != "" {
		if err := os.MkdirAll(directory, 0o755); err != nil {
			return err
		}
	}
	return os.WriteFile(path, data, 0o644)
}

// Load reads a repository from a file. A file that is not there gives an
// empty repository.
func Load(path string) (*Repository, error) {
	repository := NewRepository()
	data, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return repository, nil
	}
	if err != nil {
		return nil, err
	}

	state := struct {
		Objects map[string]Object `json:"objects"`
		Refs    map[string]string `json:"refs"`
		Commits []Commit          `json:"commits"`
	}{}
	if err := json.Unmarshal(data, &state); err != nil {
		return nil, err
	}
	repository.objects = state.Objects
	repository.refs = state.Refs
	repository.commits = state.Commits
	if repository.objects == nil {
		repository.objects = map[string]Object{}
	}
	if repository.refs == nil {
		repository.refs = map[string]string{}
	}
	return repository, nil
}

// ---------------------------------------------------------------------------
// The workflow file
// ---------------------------------------------------------------------------

// Step is one workflow command.
type Step struct {
	Name  string            `json:"name"`
	Run   string            `json:"run"`
	Uses  string            `json:"uses,omitempty"`
	Needs []string          `json:"needs,omitempty"`
	Env   map[string]string `json:"env,omitempty"`
}

// Job groups steps.
type Job struct {
	Name  string   `json:"name"`
	Needs []string `json:"needs,omitempty"`
	Steps []Step   `json:"steps"`
}

// Workflow is a parsed action definition.
type Workflow struct {
	Name string `json:"name"`
	On   string `json:"on"`
	Jobs []Job  `json:"jobs"`
}

// ParseWorkflow reads a workflow file.
//
// It is a small YAML subset, read from the indentation: the keys of the
// workflow are at the left, a job is at two spaces, the keys of a job at
// four, a step at six and the keys of a step at eight. Both shapes of the
// actions files are understood: a job as a list entry ("- name: build") and
// as a key ("build:").
func ParseWorkflow(text string) (Workflow, error) {
	workflow := Workflow{}
	jobIndex := -1
	stepIndex := -1
	inEnv := false

	for _, line := range splitLines(text) {
		trimmed := trim(line)
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			continue
		}
		indent := leadingSpaces(line)

		// A list entry keeps its dashes; the rest of the line is the content.
		content := trimmed
		for strings.HasPrefix(content, "-") {
			content = trim(content[1:])
		}
		isEntry := content != trimmed

		switch {
		case indent == 0 && hasPrefix(content, "name:"):
			workflow.Name = trimAfter(content, "name:")
		case indent == 0 && hasPrefix(content, "on:"):
			workflow.On = trimAfter(content, "on:")
		case indent == 0 && hasPrefix(content, "jobs:"):
			jobIndex = -1
		case indent == 2 && isEntry && hasPrefix(content, "name:"):
			workflow.Jobs = append(workflow.Jobs, Job{Name: trimAfter(content, "name:")})
			jobIndex = len(workflow.Jobs) - 1
			stepIndex = -1
		case indent == 2 && !isEntry && strings.HasSuffix(content, ":"):
			name := trim(strings.TrimSuffix(content, ":"))
			if name == "" {
				return Workflow{}, fmt.Errorf("a job needs a name")
			}
			workflow.Jobs = append(workflow.Jobs, Job{Name: name})
			jobIndex = len(workflow.Jobs) - 1
			stepIndex = -1
		case indent == 4 && hasPrefix(content, "needs:"):
			if jobIndex < 0 {
				return Workflow{}, fmt.Errorf("a needs before any job")
			}
			workflow.Jobs[jobIndex].Needs = splitList(trimAfter(content, "needs:"))
		case indent == 4 && content == "steps:":
			stepIndex = -1
			inEnv = false
		case indent >= 6 && isEntry && hasPrefix(content, "name:"):
			if jobIndex < 0 {
				return Workflow{}, fmt.Errorf("a step before any job")
			}
			workflow.Jobs[jobIndex].Steps = append(workflow.Jobs[jobIndex].Steps, Step{Name: trimAfter(content, "name:")})
			stepIndex = len(workflow.Jobs[jobIndex].Steps) - 1
			inEnv = false
		case indent >= 6 && isEntry && hasPrefix(content, "run:"):
			if jobIndex < 0 {
				return Workflow{}, fmt.Errorf("a run before any job")
			}
			// A list entry always starts a new step.
			workflow.Jobs[jobIndex].Steps = append(workflow.Jobs[jobIndex].Steps, Step{})
			stepIndex = len(workflow.Jobs[jobIndex].Steps) - 1
			workflow.Jobs[jobIndex].Steps[stepIndex].Run = trimAfter(content, "run:")
		case indent >= 6 && isEntry && hasPrefix(content, "uses:"):
			if jobIndex < 0 {
				return Workflow{}, fmt.Errorf("a uses before any job")
			}
			// A list entry always starts a new step.
			workflow.Jobs[jobIndex].Steps = append(workflow.Jobs[jobIndex].Steps, Step{})
			stepIndex = len(workflow.Jobs[jobIndex].Steps) - 1
			workflow.Jobs[jobIndex].Steps[stepIndex].Uses = trimAfter(content, "uses:")
		case indent >= 6 && !isEntry && hasPrefix(content, "name:"):
			if jobIndex < 0 {
				return Workflow{}, fmt.Errorf("a step before any job")
			}
			workflow.Jobs[jobIndex].Steps = append(workflow.Jobs[jobIndex].Steps, Step{Name: trimAfter(content, "name:")})
			stepIndex = len(workflow.Jobs[jobIndex].Steps) - 1
			inEnv = false
		case indent >= 6 && !isEntry && hasPrefix(content, "run:"):
			if jobIndex < 0 || stepIndex < 0 {
				return Workflow{}, fmt.Errorf("a run before any step")
			}
			workflow.Jobs[jobIndex].Steps[stepIndex].Run = trimAfter(content, "run:")
		case indent >= 6 && !isEntry && hasPrefix(content, "uses:"):
			if jobIndex < 0 || stepIndex < 0 {
				return Workflow{}, fmt.Errorf("a uses before any step")
			}
			workflow.Jobs[jobIndex].Steps[stepIndex].Uses = trimAfter(content, "uses:")
		case indent >= 6 && !isEntry && hasPrefix(content, "needs:"):
			if jobIndex < 0 || stepIndex < 0 {
				return Workflow{}, fmt.Errorf("a needs before any step")
			}
			workflow.Jobs[jobIndex].Steps[stepIndex].Needs = splitList(trimAfter(content, "needs:"))
		case indent >= 6 && !isEntry && hasPrefix(content, "env:"):
			if jobIndex < 0 || stepIndex < 0 {
				return Workflow{}, fmt.Errorf("an env before any step")
			}
			workflow.Jobs[jobIndex].Steps[stepIndex].Env = map[string]string{}
			inEnv = true
		case inEnv && indent > 6:
			if jobIndex < 0 || stepIndex < 0 {
				return Workflow{}, fmt.Errorf("an env value before any step")
			}
			key, value, found := cut(content, "=")
			if !found {
				return Workflow{}, fmt.Errorf("an env value has to be written as KEY=value")
			}
			workflow.Jobs[jobIndex].Steps[stepIndex].Env[key] = value
		}
	}

	if workflow.Name == "" || len(workflow.Jobs) == 0 {
		return Workflow{}, fmt.Errorf("a workflow needs a name and a job")
	}
	return workflow, nil
}

// splitList splits a comma separated list.
func splitList(value string) []string {
	items := []string{}
	for _, part := range strings.Split(value, ",") {
		part = strings.TrimSpace(part)
		if part != "" {
			items = append(items, part)
		}
	}
	return items
}

// cut splits a string at the first occurrence of a separator.
func cut(text, separator string) (string, string, bool) {
	index := strings.Index(text, separator)
	if index < 0 {
		return text, "", false
	}
	return text[:index], text[index+len(separator):], true
}

// ---------------------------------------------------------------------------
// The runner
// ---------------------------------------------------------------------------

// StepResult records one executed step.
type StepResult struct {
	Job      string `json:"job"`
	Step     string `json:"step"`
	Status   string `json:"status"`
	Log      string `json:"log"`
	ExitCode int    `json:"exitCode,omitempty"`
	Reason   string `json:"reason,omitempty"`
	Duration string `json:"duration,omitempty"`
	Skipped  bool   `json:"skipped,omitempty"`
}

// Runner executes the jobs of a workflow.
type Runner struct {
	// Workdir is where the steps run. A temporary directory is used when it
	// is empty.
	Workdir string
	// Shell is the shell the commands run in.
	Shell string
}

// Run executes the workflow.
//
// A job waits for the jobs it needs, a step waits for the steps it needs,
// and a failing step stops the job. The steps that come after it are
// skipped.
func (runner *Runner) Run(workflow Workflow) []StepResult {
	results := []StepResult{}
	done := map[string]bool{}
	failed := map[string]bool{}

	for _, job := range workflow.Jobs {
		if runner.jobBlocked(job, done, failed) {
			for _, step := range job.Steps {
				results = append(results, StepResult{
					Job: job.Name, Step: step.Name, Status: "skipped", Skipped: true,
					Reason: "a job it needs did not succeed",
				})
			}
			failed[job.Name] = true
			continue
		}

		jobFailed := false
		for _, step := range job.Steps {
			if jobFailed {
				results = append(results, StepResult{
					Job: job.Name, Step: step.Name, Status: "skipped", Skipped: true,
					Reason: "an earlier step of the job failed",
				})
				continue
			}
			if runner.stepBlocked(step, done, failed) {
				results = append(results, StepResult{
					Job: job.Name, Step: step.Name, Status: "skipped", Skipped: true,
					Reason: "a step it needs did not succeed",
				})
				continue
			}

			result := runner.runStep(job.Name, step)
			results = append(results, result)
			if result.Status != "success" {
				jobFailed = true
				failed[job.Name] = true
			} else {
				done[job.Name+"."+step.Name] = true
			}
		}
		if jobFailed {
			failed[job.Name] = true
		} else {
			done[job.Name] = true
		}
	}

	return results
}

// jobBlocked reports whether a job has to be skipped.
func (runner *Runner) jobBlocked(job Job, done, failed map[string]bool) bool {
	for _, need := range job.Needs {
		if failed[need] || !done[need] {
			return true
		}
	}
	return false
}

// stepBlocked reports whether a step has to be skipped.
func (runner *Runner) stepBlocked(step Step, done, failed map[string]bool) bool {
	for _, need := range step.Needs {
		if failed[need] || !done[need] {
			return true
		}
	}
	return false
}

// runStep runs one step.
func (runner *Runner) runStep(job string, step Step) StepResult {
	result := StepResult{Job: job, Step: step.Name}

	if step.Uses != "" && step.Run == "" {
		// A step that uses an action is not run here; the action is not
		// implemented, and the step is reported as such.
		result.Status = "skipped"
		result.Skipped = true
		result.Reason = "the action " + step.Uses + " is not implemented"
		return result
	}

	if step.Run == "" {
		result.Status = "skipped"
		result.Skipped = true
		result.Reason = "the step has nothing to run"
		return result
	}

	workdir := runner.Workdir
	if workdir == "" {
		directory, err := os.MkdirTemp("", "step-")
		if err != nil {
			result.Status = "failed"
			result.Reason = err.Error()
			return result
		}
		defer os.RemoveAll(directory)
		workdir = directory
	}

	shell := runner.Shell
	if shell == "" {
		shell = "sh"
	}

	command := exec.Command(shell, "-c", step.Run)
	command.Dir = workdir
	for key, value := range step.Env {
		command.Env = append(command.Env, key+"="+value)
	}

	output, err := command.CombinedOutput()
	result.Log = string(output)
	if err != nil {
		var exitError *exec.ExitError
		if errors.As(err, &exitError) {
			result.Status = "failed"
			result.ExitCode = exitError.ExitCode()
			result.Reason = fmt.Sprintf("the command exited with %d", exitError.ExitCode())
			return result
		}
		result.Status = "failed"
		result.Reason = err.Error()
		return result
	}

	result.Status = "success"
	return result
}

// RunWorkflow records each step result without running anything.
//
// It is the dry run of the runner.
func RunWorkflow(workflow Workflow) []StepResult {
	results := []StepResult{}
	for _, job := range workflow.Jobs {
		for _, step := range job.Steps {
			results = append(results, StepResult{
				Job: job.Name, Step: step.Name, Status: "success", Log: step.Run,
			})
		}
	}
	return results
}

// ---------------------------------------------------------------------------
// The small text helpers
// ---------------------------------------------------------------------------

// splitLines splits a text into its lines.
func splitLines(text string) []string {
	lines := []string{}
	for _, line := range strings.Split(text, "\n") {
		lines = append(lines, strings.TrimSuffix(line, "\r"))
	}
	return lines
}

// leadingSpaces counts the spaces a line starts with.
func leadingSpaces(text string) int {
	count := 0
	for count < len(text) && text[count] == ' ' {
		count++
	}
	return count
}

// trim removes the spaces at the start and the end of a string.
func trim(text string) string {
	return strings.TrimSpace(text)
}

// hasPrefix reports whether a string starts with a prefix.
func hasPrefix(text, prefix string) bool {
	return len(text) >= len(prefix) && text[:len(prefix)] == prefix
}

// trimAfter removes a prefix from the start of a string.
func trimAfter(text, prefix string) string {
	return trim(text[len(prefix):])
}
