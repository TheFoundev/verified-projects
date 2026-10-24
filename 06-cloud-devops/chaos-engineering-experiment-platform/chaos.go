package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sort"
	"strings"
	"sync"
	"time"
)

// Action is one fault to apply.
type Action struct {
	Kind      string        `json:"kind"`
	Target    string        `json:"target"`
	Duration  time.Duration `json:"duration"`
	Magnitude int           `json:"magnitude"`
}

// Experiment is a named group of actions.
type Experiment struct {
	Name       string      `json:"name"`
	Namespace  string      `json:"namespace"`
	Hypothesis *Hypothesis `json:"hypothesis,omitempty"`
	Actions    []Action    `json:"actions"`
}

// Hypothesis is the steady-state condition the experiment checks.
type Hypothesis struct {
	Service string  `json:"service"`
	Metric  string  `json:"metric"`
	Op      string  `json:"op"` // "lt", "gt" or "eq"
	Value   float64 `json:"value"`
}

// Event records one phase of one action.
type Event struct {
	Action string `json:"action"`
	Target string `json:"target"`
	Phase  string `json:"phase"`
	At     string `json:"at"`
	Note   string `json:"note"`
}

// HypothesisCheck is the outcome of one steady-state evaluation.
type HypothesisCheck struct {
	At     string  `json:"at"`
	Phase  string  `json:"phase"`
	Metric string  `json:"metric"`
	Value  float64 `json:"value"`
	OK     bool    `json:"ok"`
	Note   string  `json:"note"`
}

// Result is the experiment timeline and summary.
type Result struct {
	Experiment   string            `json:"experiment"`
	Events       []Event           `json:"events"`
	Affected     []string          `json:"affected"`
	Duration     string            `json:"duration"`
	StartedAt    string            `json:"started_at"`
	FinishedAt   string            `json:"finished_at"`
	Aborted      bool              `json:"aborted"`
	AbortReason  string            `json:"abort_reason,omitempty"`
	SteadyState  []HypothesisCheck `json:"steady_state,omitempty"`
	ActionsRun   int               `json:"actions_run"`
	ActionsTotal int               `json:"actions_total"`
}

// FaultExecutor applies and removes a fault.
//
// The engine never talks to Kubernetes directly: it goes through this
// interface, so the same runner works against a dry run, against a local
// process and against Chaos Mesh.
type FaultExecutor interface {
	Apply(ctx context.Context, action Action) error
	Recover(ctx context.Context, action Action) error
	// Name identifies the executor in the result and in the log.
	Name() string
}

// MetricReader reads the value of a metric for a service.
type MetricReader interface {
	Read(ctx context.Context, service, metric string) (float64, error)
}

// Store keeps the results of past experiments.
type Store interface {
	Save(result Result) error
	List() ([]Result, error)
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

// Validate checks supported actions and non-empty fields.
func Validate(experiment Experiment) error {
	if experiment.Name == "" {
		return errors.New("experiment needs a name")
	}
	if len(experiment.Actions) == 0 {
		return errors.New("experiment needs at least one action")
	}
	supported := map[string]bool{"delay": true, "kill": true, "stress": true}
	for _, action := range experiment.Actions {
		if !supported[action.Kind] {
			return fmt.Errorf("unsupported action %q", action.Kind)
		}
		if action.Target == "" {
			return fmt.Errorf("action %q needs a target", action.Kind)
		}
		if action.Duration < 0 {
			return fmt.Errorf("action %q has a negative duration", action.Kind)
		}
	}
	if experiment.Hypothesis != nil {
		if err := validateHypothesis(*experiment.Hypothesis); err != nil {
			return err
		}
	}
	return nil
}

func validateHypothesis(hypothesis Hypothesis) error {
	if hypothesis.Service == "" {
		return errors.New("hypothesis needs a service")
	}
	if hypothesis.Metric == "" {
		return errors.New("hypothesis needs a metric")
	}
	switch hypothesis.Op {
	case "lt", "gt", "eq":
	default:
		return fmt.Errorf("unsupported hypothesis operator %q", hypothesis.Op)
	}
	return nil
}

// Compare applies a hypothesis operator to a value.
func Compare(op string, value, threshold float64) bool {
	switch op {
	case "lt":
		return value < threshold
	case "gt":
		return value > threshold
	case "eq":
		return value == threshold
	default:
		return false
	}
}

// ---------------------------------------------------------------------------
// Executors
// ---------------------------------------------------------------------------

// DryRunExecutor records what it would do and changes nothing.
type DryRunExecutor struct{}

func (DryRunExecutor) Name() string { return "dry-run" }

func (DryRunExecutor) Apply(_ context.Context, action Action) error {
	return nil
}

func (DryRunExecutor) Recover(_ context.Context, action Action) error {
	return nil
}

// LocalExecutor applies faults to the machine the engine runs on.
//
//   - delay  really waits for the duration of the action
//   - stress really burns CPU for the duration of the action
//   - kill   sends SIGTERM to the process id in the target
//
// It is meant for experiments against a local process, not for a production
// machine.
type LocalExecutor struct {
	// Sleep is injectable so the tests do not have to wait.
	Sleep func(time.Duration)
	// CPUWorkers is how many goroutines burn CPU during a stress action.
	CPUWorkers int
}

func (LocalExecutor) Name() string { return "local" }

func (e LocalExecutor) sleep(d time.Duration) {
	if e.Sleep != nil {
		e.Sleep(d)
		return
	}
	time.Sleep(d)
}

func (e LocalExecutor) Apply(ctx context.Context, action Action) error {
	switch action.Kind {
	case "delay":
		e.sleep(action.Duration)
	case "stress":
		workers := e.CPUWorkers
		if workers <= 0 {
			workers = 1
		}
		done := make(chan struct{})
		for i := 0; i < workers; i++ {
			go func() {
				for {
					select {
					case <-done:
						return
					default:
						_ = action.Magnitude
					}
				}
			}()
		}
		go func() {
			e.sleep(action.Duration)
			close(done)
		}()
	case "kill":
		return killProcess(action.Target)
	}
	return nil
}

func (e LocalExecutor) Recover(ctx context.Context, action Action) error {
	return nil
}

// killProcess sends SIGTERM to a process id. The target has to be a number.
func killProcess(target string) error {
	pid, err := parsePID(target)
	if err != nil {
		return err
	}
	process, err := os.FindProcess(pid)
	if err != nil {
		return fmt.Errorf("cannot find process %d: %w", pid, err)
	}
	if err := process.Signal(os.Interrupt); err != nil {
		return fmt.Errorf("cannot signal process %d: %w", pid, err)
	}
	return nil
}

func parsePID(target string) (int, error) {
	pid := 0
	if _, err := fmt.Sscanf(strings.TrimSpace(target), "%d", &pid); err != nil {
		return 0, fmt.Errorf("target %q is not a process id", target)
	}
	if pid <= 0 {
		return 0, fmt.Errorf("target %q is not a process id", target)
	}
	return pid, nil
}

// ---------------------------------------------------------------------------
// Metric readers
// ---------------------------------------------------------------------------

// StaticMetricReader always answers with the same value. It is the reader the
// demo uses, and it makes the steady-state logic testable.
type StaticMetricReader struct {
	Values map[string]float64
}

func (r StaticMetricReader) Read(_ context.Context, service, metric string) (float64, error) {
	key := service + "/" + metric
	if value, ok := r.Values[key]; ok {
		return value, nil
	}
	return 0, fmt.Errorf("no metric %q for service %q", metric, service)
}

// ---------------------------------------------------------------------------
// Stores
// ---------------------------------------------------------------------------

// MemoryStore keeps the results in memory.
type MemoryStore struct {
	mu      sync.Mutex
	results []Result
}

func (s *MemoryStore) Save(result Result) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.results = append(s.results, result)
	return nil
}

func (s *MemoryStore) List() ([]Result, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Result, len(s.results))
	copy(out, s.results)
	return out, nil
}

// FileStore writes the results to a JSON file.
type FileStore struct {
	Path string
	mu   sync.Mutex
}

func (s *FileStore) Save(result Result) error {
	s.mu.Lock()
	defer s.mu.Unlock()

	results, err := s.List()
	if err != nil {
		return err
	}
	results = append(results, result)

	data, err := json.MarshalIndent(results, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(s.Path, data, 0o644)
}

func (s *FileStore) List() ([]Result, error) {
	data, err := os.ReadFile(s.Path)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var results []Result
	if err := json.Unmarshal(data, &results); err != nil {
		return nil, err
	}
	return results, nil
}

// ---------------------------------------------------------------------------
// The runner
// ---------------------------------------------------------------------------

// Runner executes an experiment.
type Runner struct {
	Executor FaultExecutor
	Metrics  MetricReader
	Store    Store
	// Now and Sleep are injectable so the tests run instantly.
	Now   func() time.Time
	Sleep func(time.Duration)
}

func (r Runner) now() time.Time {
	if r.Now != nil {
		return r.Now()
	}
	return time.Now()
}

func (r Runner) sleep(d time.Duration) {
	if r.Sleep != nil {
		r.Sleep(d)
		return
	}
	time.Sleep(d)
}

func (r Runner) note(action Action) string {
	switch action.Kind {
	case "delay":
		return fmt.Sprintf("add %dms latency", action.Magnitude)
	case "kill":
		return "terminate the process once and let it restart"
	case "stress":
		return fmt.Sprintf("consume %d cpu shares", action.Magnitude)
	}
	return ""
}

// checkSteadyState reads the metric and records the outcome.
func (r Runner) checkSteadyState(ctx context.Context, experiment Experiment, phase string) (HypothesisCheck, bool) {
	hypothesis := experiment.Hypothesis
	if hypothesis == nil {
		return HypothesisCheck{}, true
	}

	value, err := r.Metrics.Read(ctx, hypothesis.Service, hypothesis.Metric)
	check := HypothesisCheck{
		At:     r.now().Format(time.RFC3339Nano),
		Phase:  phase,
		Metric: hypothesis.Metric,
		Value:  value,
	}
	if err != nil {
		check.OK = false
		check.Note = err.Error()
		return check, false
	}

	check.OK = Compare(hypothesis.Op, value, hypothesis.Value)
	if !check.OK {
		check.Note = fmt.Sprintf("steady state broken: %s %v is not %s %v",
			hypothesis.Metric, value, hypothesis.Op, hypothesis.Value)
	}
	return check, check.OK
}

// Run applies the actions of an experiment and records the timeline.
//
// The steady-state hypothesis is checked before the first action and after
// every recovery. When it breaks the experiment stops and the actions that
// are still applied are rolled back.
func (r Runner) Run(ctx context.Context, experiment Experiment) (Result, error) {
	if err := Validate(experiment); err != nil {
		return Result{}, err
	}

	executor := r.Executor
	if executor == nil {
		executor = DryRunExecutor{}
	}

	started := r.now()
	result := Result{
		Experiment:   experiment.Name,
		Events:       []Event{},
		Affected:     []string{},
		StartedAt:    started.Format(time.RFC3339Nano),
		ActionsTotal: len(experiment.Actions),
	}

	seen := map[string]bool{}
	applied := []Action{}

	rollback := func() {
		for i := len(applied) - 1; i >= 0; i-- {
			_ = executor.Recover(ctx, applied[i])
			result.Events = append(result.Events, Event{
				Action: applied[i].Kind,
				Target: applied[i].Target,
				Phase:  "rolled-back",
				At:     r.now().Format(time.RFC3339Nano),
				Note:   "rolled back after the steady state broke",
			})
		}
	}

	// The steady state has to hold before anything is broken.
	check, ok := r.checkSteadyState(ctx, experiment, "before")
	if experiment.Hypothesis != nil {
		result.SteadyState = append(result.SteadyState, check)
	}
	if !ok {
		result.Aborted = true
		result.AbortReason = "the steady state was already broken: " + check.Note
		result.FinishedAt = r.now().Format(time.RFC3339Nano)
		result.Duration = r.now().Sub(started).String()
		return result, nil
	}

	for _, action := range experiment.Actions {
		if err := ctx.Err(); err != nil {
			rollback()
			result.Aborted = true
			result.AbortReason = "the run was cancelled"
			result.FinishedAt = r.now().Format(time.RFC3339Nano)
			result.Duration = r.now().Sub(started).String()
			return result, err
		}

		if err := executor.Apply(ctx, action); err != nil {
			rollback()
			result.Aborted = true
			result.AbortReason = fmt.Sprintf("could not apply %q to %s: %v", action.Kind, action.Target, err)
			result.FinishedAt = r.now().Format(time.RFC3339Nano)
			result.Duration = r.now().Sub(started).String()
			return result, err
		}
		applied = append(applied, action)

		result.Events = append(result.Events, Event{
			Action: action.Kind,
			Target: action.Target,
			Phase:  "applied",
			At:     r.now().Format(time.RFC3339Nano),
			Note:   r.note(action),
		})

		if action.Duration > 0 {
			r.sleep(action.Duration)
		}

		if err := executor.Recover(ctx, action); err != nil {
			rollback()
			result.Aborted = true
			result.AbortReason = fmt.Sprintf("could not recover %q on %s: %v", action.Kind, action.Target, err)
			result.FinishedAt = r.now().Format(time.RFC3339Nano)
			result.Duration = r.now().Sub(started).String()
			return result, err
		}
		applied = applied[:len(applied)-1]

		result.Events = append(result.Events, Event{
			Action: action.Kind,
			Target: action.Target,
			Phase:  "recovered",
			At:     r.now().Format(time.RFC3339Nano),
			Note:   "fault removed",
		})

		if !seen[action.Target] {
			seen[action.Target] = true
			result.Affected = append(result.Affected, action.Target)
		}
		result.ActionsRun++

		// The steady state has to hold again once the fault is removed.
		check, ok := r.checkSteadyState(ctx, experiment, "after")
		if experiment.Hypothesis != nil {
			result.SteadyState = append(result.SteadyState, check)
		}
		if !ok {
			rollback()
			result.Aborted = true
			result.AbortReason = "the steady state broke: " + check.Note
			break
		}
	}

	finished := r.now()
	result.FinishedAt = finished.Format(time.RFC3339Nano)
	result.Duration = finished.Sub(started).String()

	if r.Store != nil {
		if err := r.Store.Save(result); err != nil {
			return result, err
		}
	}

	return result, nil
}

// ---------------------------------------------------------------------------
// The old, flat entry point
// ---------------------------------------------------------------------------

// Run applies each action and records start and recovery events.
//
// It keeps the original behaviour: a dry run that records what it would do.
// Use Runner.Run for the full behaviour.
func Run(experiment Experiment, now func() time.Time) Result {
	if now == nil {
		now = time.Now
	}

	// The dry run does not really wait, but the timeline still has to show
	// that the fault was in place for the length of the action, so the clock
	// is moved forward instead of waiting.
	current := now()
	runner := Runner{
		Executor: DryRunExecutor{},
		Now:      func() time.Time { return current },
		Sleep:    func(d time.Duration) { current = current.Add(d) },
	}

	result, _ := runner.Run(context.Background(), experiment)
	return result
}

// AffectedServices returns the sorted, unique targets of an experiment.
func AffectedServices(experiment Experiment) []string {
	seen := map[string]bool{}
	out := []string{}
	for _, action := range experiment.Actions {
		if !seen[action.Target] {
			seen[action.Target] = true
			out = append(out, action.Target)
		}
	}
	sort.Strings(out)
	return out
}
