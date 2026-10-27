package main

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"
)

// Step is one CI step.
type Step struct {
	Name    string   `json:"name"`
	Image   string   `json:"image"`
	Command string   `json:"command"`
	Needs   []string `json:"needs"`
	// Retries is how often a failing step is tried again.
	Retries int `json:"retries,omitempty"`
	// Timeout is how long the step may run, in seconds.
	Timeout int `json:"timeout,omitempty"`
	// Env holds the environment variables of the step.
	Env map[string]string `json:"env,omitempty"`
	// Secret marks a variable whose value is never written to the log.
	Secret bool `json:"secret,omitempty"`
}

// Pipeline is a named step graph.
type Pipeline struct {
	Name  string `json:"name"`
	Steps []Step `json:"steps"`
}

// Result is the outcome for one step.
type Result struct {
	Step     string   `json:"step"`
	Status   string   `json:"status"`
	Log      string   `json:"log"`
	Cached   bool     `json:"cached"`
	Duration string   `json:"duration,omitempty"`
	Attempts int      `json:"attempts,omitempty"`
	ExitCode int      `json:"exitCode,omitempty"`
	Reason   string   `json:"reason,omitempty"`
	Outputs  []string `json:"outputs,omitempty"`
}

// Executor runs the command of a step.
//
// The engine never starts a process itself, so the same pipeline can run on
// the host, inside a container or against a remote runner.
type Executor interface {
	// Run executes the step and returns the exit code and the output.
	Run(ctx context.Context, step Step) (exitCode int, output string, err error)
	// Name identifies the executor.
	Name() string
}

// Cache remembers the steps that already ran.
type Cache interface {
	// Has reports whether a digest is in the cache.
	Has(digest string) bool
	// Put puts a digest into the cache.
	Put(digest string) error
	// Name identifies the cache.
	Name() string
}

// ---------------------------------------------------------------------------
// The digests
// ---------------------------------------------------------------------------

// digestOf hashes a step and the digests of the steps it needs.
//
// The dependencies are hashed by their CONTENT and not by their name, so a
// change upstream changes the key of everything downstream. Hashing the
// names would serve a stale result after a change upstream.
func digestOf(step Step, dependencyDigests []string) string {
	parts := append([]string{step.Name, step.Image, step.Command}, dependencyDigests...)
	keys := make([]string, 0, len(step.Env))
	for key := range step.Env {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys {
		value := step.Env[key]
		if step.Secret {
			value = "***"
		}
		parts = append(parts, key+"="+value)
	}
	sum := sha256.Sum256([]byte(strings.Join(parts, "|")))
	return hex.EncodeToString(sum[:])
}

// ---------------------------------------------------------------------------
// The order
// ---------------------------------------------------------------------------

// Order returns steps in dependency order.
func Order(pipeline Pipeline) ([]string, error) {
	dependencies := map[string][]string{}
	for _, step := range pipeline.Steps {
		if _, exists := dependencies[step.Name]; exists {
			return nil, fmt.Errorf("the step %q is defined twice", step.Name)
		}
		dependencies[step.Name] = step.Needs
	}
	resolved := []string{}
	done := map[string]bool{}
	for len(resolved) < len(pipeline.Steps) {
		progress := false
		for _, step := range pipeline.Steps {
			if done[step.Name] {
				continue
			}
			ready := true
			for _, need := range step.Needs {
				if _, known := dependencies[need]; !known {
					return nil, fmt.Errorf("unknown dependency %q", need)
				}
				if !done[need] {
					ready = false
					break
				}
			}
			if ready {
				resolved = append(resolved, step.Name)
				done[step.Name] = true
				progress = true
			}
		}
		if !progress {
			return nil, errors.New("pipeline has a cycle")
		}
	}
	return resolved, nil
}

// ---------------------------------------------------------------------------
// The executors
// ---------------------------------------------------------------------------

// HostExecutor runs the command of a step on the machine, in a directory of
// its own.
type HostExecutor struct {
	// Workdir is where the steps run. A temporary directory is used when it
	// is empty.
	Workdir string
	// Shell is the shell the command runs in.
	Shell string
}

func (executor HostExecutor) Name() string { return "host" }

// Run executes the command of the step.
func (executor HostExecutor) Run(ctx context.Context, step Step) (int, string, error) {
	workdir := executor.Workdir
	if workdir == "" {
		directory, err := os.MkdirTemp("", "step-")
		if err != nil {
			return 1, "", err
		}
		defer os.RemoveAll(directory)
		workdir = directory
	}

	shell := executor.Shell
	if shell == "" {
		shell = "sh"
	}

	command := exec.CommandContext(ctx, shell, "-c", step.Command)
	command.Dir = workdir
	command.Env = os.Environ()
	for key, value := range step.Env {
		command.Env = append(command.Env, key+"="+value)
	}

	output, err := command.CombinedOutput()
	if err != nil {
		var exitError *exec.ExitError
		if errors.As(err, &exitError) {
			return exitError.ExitCode(), string(output), nil
		}
		return 1, string(output), err
	}
	return 0, string(output), nil
}

// RecordingExecutor records what it was asked to do and never runs anything.
// It is the executor of the dry run.
type RecordingExecutor struct {
	mu    sync.Mutex
	calls []Step
}

func (executor *RecordingExecutor) Name() string { return "recording" }

// Run records the step.
func (executor *RecordingExecutor) Run(_ context.Context, step Step) (int, string, error) {
	executor.mu.Lock()
	defer executor.mu.Unlock()
	executor.calls = append(executor.calls, step)
	return 0, fmt.Sprintf("would run %q in %s", step.Command, step.Image), nil
}

// Calls returns the steps that were run.
func (executor *RecordingExecutor) Calls() []Step {
	executor.mu.Lock()
	defer executor.mu.Unlock()
	return append([]Step{}, executor.calls...)
}

// ---------------------------------------------------------------------------
// The caches
// ---------------------------------------------------------------------------

// MemoryCache keeps the digests in memory.
type MemoryCache struct {
	mu      sync.Mutex
	digests map[string]bool
}

func NewMemoryCache() *MemoryCache {
	return &MemoryCache{digests: map[string]bool{}}
}

func (cache *MemoryCache) Name() string { return "memory" }

// Has reports whether the digest is in the cache.
func (cache *MemoryCache) Has(digest string) bool {
	cache.mu.Lock()
	defer cache.mu.Unlock()
	return cache.digests[digest]
}

// Put puts the digest into the cache.
func (cache *MemoryCache) Put(digest string) error {
	cache.mu.Lock()
	defer cache.mu.Unlock()
	cache.digests[digest] = true
	return nil
}

// FileCache keeps the digests in a directory, one file per digest.
type FileCache struct {
	dir string
	mu  sync.Mutex
}

// NewFileCache creates a cache in a directory. The directory is made when it
// is not there.
func NewFileCache(dir string) (*FileCache, error) {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	return &FileCache{dir: dir}, nil
}

func (cache *FileCache) Name() string { return "file" }

// Has reports whether the digest is in the cache.
func (cache *FileCache) Has(digest string) bool {
	cache.mu.Lock()
	defer cache.mu.Unlock()
	_, err := os.Stat(filepath.Join(cache.dir, digest))
	return err == nil
}

// Put puts the digest into the cache.
func (cache *FileCache) Put(digest string) error {
	cache.mu.Lock()
	defer cache.mu.Unlock()
	path := filepath.Join(cache.dir, digest)
	return os.WriteFile(path, []byte(time.Now().UTC().Format(time.RFC3339)+"\n"), 0o644)
}

// ---------------------------------------------------------------------------
// The engine
// ---------------------------------------------------------------------------

// Engine runs a pipeline.
type Engine struct {
	executor Executor
	cache    Cache
	// now is injectable so the tests do not depend on the clock.
	now func() time.Time
}

// NewEngine creates an engine. A nil executor means the dry run and a nil
// cache means nothing is cached.
func NewEngine(executor Executor, cache Cache) *Engine {
	if executor == nil {
		executor = &RecordingExecutor{}
	}
	if cache == nil {
		cache = NewMemoryCache()
	}
	return &Engine{executor: executor, cache: cache, now: time.Now}
}

// Run executes the steps of the pipeline in order.
//
// A step is skipped when the cache has its digest, and the digest of a step
// is built from the digests of the steps it needs, so a change upstream
// invalidates everything downstream.
func (engine *Engine) Run(ctx context.Context, pipeline Pipeline) ([]Result, error) {
	order, err := Order(pipeline)
	if err != nil {
		return nil, err
	}

	byName := map[string]Step{}
	for _, step := range pipeline.Steps {
		byName[step.Name] = step
	}

	results := []Result{}
	digests := map[string]string{}

	for _, name := range order {
		step := byName[name]

		dependencyDigests := make([]string, 0, len(step.Needs))
		for _, need := range step.Needs {
			dependencyDigests = append(dependencyDigests, digests[need])
		}
		sort.Strings(dependencyDigests)

		digest := digestOf(step, dependencyDigests)
		digests[name] = digest

		if engine.cache.Has(digest) {
			results = append(results, Result{
				Step: name, Status: "cached", Cached: true,
				Log: "cache hit", Duration: "0s",
			})
			continue
		}

		result := engine.runStep(ctx, step)
		results = append(results, result)

		if result.Status != "success" {
			return results, fmt.Errorf("the step %q failed", name)
		}
		if err := engine.cache.Put(digest); err != nil {
			return results, err
		}
	}

	return results, nil
}

// runStep runs one step, with its retries and its timeout.
func (engine *Engine) runStep(ctx context.Context, step Step) Result {
	attempts := step.Retries + 1
	if attempts < 1 {
		attempts = 1
	}

	stepContext := ctx
	if step.Timeout > 0 {
		var cancel context.CancelFunc
		stepContext, cancel = context.WithTimeout(ctx, time.Duration(step.Timeout)*time.Second)
		defer cancel()
	}

	var last Result
	for attempt := 1; attempt <= attempts; attempt++ {
		started := engine.now()
		exitCode, output, err := engine.executor.Run(stepContext, step)
		elapsed := engine.now().Sub(started)

		last = Result{
			Step:     step.Name,
			Status:   "success",
			Log:      output,
			Duration: elapsed.String(),
			Attempts: attempt,
			ExitCode: exitCode,
		}
		if err != nil {
			last.Status = "failed"
			last.Reason = err.Error()
		} else if exitCode != 0 {
			last.Status = "failed"
			last.Reason = fmt.Sprintf("the command exited with %d", exitCode)
		}

		if last.Status == "success" {
			return last
		}
		if stepContext.Err() != nil {
			last.Reason = "the step timed out"
			return last
		}
	}
	return last
}

// ---------------------------------------------------------------------------
// The pipeline file
// ---------------------------------------------------------------------------

// LoadPipeline reads a pipeline from a JSON file.
func LoadPipeline(path string) (Pipeline, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Pipeline{}, err
	}
	var pipeline Pipeline
	if err := json.Unmarshal(data, &pipeline); err != nil {
		return Pipeline{}, err
	}
	return pipeline, nil
}

// SavePipeline writes a pipeline to a JSON file.
func SavePipeline(path string, pipeline Pipeline) error {
	data, err := json.MarshalIndent(pipeline, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, data, 0o644)
}

// PrintResults writes the results of a run to a writer.
func PrintResults(writer io.Writer, results []Result) error {
	buffer := bufio.NewWriter(writer)
	defer buffer.Flush()
	for _, result := range results {
		fmt.Fprintf(buffer, "%-12s %-8s %s\n", result.Step, result.Status, result.Log)
	}
	return nil
}

// a comment to change the file
