package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// A clock the test drives by hand.
type fakeClock struct {
	now time.Time
}

func (c *fakeClock) Now() time.Time { return c.now }

func (c *fakeClock) advance(d time.Duration) { c.now = c.now.Add(d) }

// A sleep function that moves the fake clock instead of waiting.
func (c *fakeClock) Sleep(d time.Duration) { c.advance(d) }

// An executor that records what it was asked to do.
type recordingExecutor struct {
	applied     []Action
	recovered   []Action
	failApply   bool
	failRecover bool
}

func (e *recordingExecutor) Name() string { return "recording" }

func (e *recordingExecutor) Apply(_ context.Context, action Action) error {
	if e.failApply {
		return errors.New("apply failed")
	}
	e.applied = append(e.applied, action)
	return nil
}

func (e *recordingExecutor) Recover(_ context.Context, action Action) error {
	if e.failRecover {
		return errors.New("recover failed")
	}
	e.recovered = append(e.recovered, action)
	return nil
}

func TestValidate(t *testing.T) {
	if err := Validate(Experiment{Name: "x"}); err == nil {
		t.Fatal("expected an error for no actions")
	}
	bad := Experiment{Name: "x", Actions: []Action{{Kind: "explode", Target: "api"}}}
	if err := Validate(bad); err == nil {
		t.Fatal("expected an unsupported action error")
	}
	good := Experiment{Name: "x", Actions: []Action{{Kind: "delay", Target: "api", Magnitude: 100}}}
	if err := Validate(good); err != nil {
		t.Fatal(err)
	}
}

func TestValidateHypothesis(t *testing.T) {
	experiment := Experiment{
		Name:       "x",
		Actions:    []Action{{Kind: "delay", Target: "api"}},
		Hypothesis: &Hypothesis{Service: "api", Metric: "error_rate", Op: "lt", Value: 0.01},
	}
	if err := Validate(experiment); err != nil {
		t.Fatal(err)
	}

	broken := experiment
	broken.Hypothesis = &Hypothesis{Service: "api", Metric: "error_rate", Op: "wat", Value: 1}
	if err := Validate(broken); err == nil {
		t.Fatal("expected an error for an unsupported operator")
	}
}

func TestCompare(t *testing.T) {
	if !Compare("lt", 0.5, 1) {
		t.Fatal("expected 0.5 < 1")
	}
	if Compare("lt", 1.5, 1) {
		t.Fatal("did not expect 1.5 < 1")
	}
	if !Compare("gt", 2, 1) {
		t.Fatal("expected 2 > 1")
	}
	if !Compare("eq", 1, 1) {
		t.Fatal("expected 1 == 1")
	}
	if Compare("wat", 1, 1) {
		t.Fatal("did not expect an unknown operator to match")
	}
}

func TestRunRecordsTimeline(t *testing.T) {
	experiment := Experiment{Name: "x", Actions: []Action{
		{Kind: "delay", Target: "api", Duration: time.Second, Magnitude: 100},
		{Kind: "kill", Target: "worker", Duration: 2 * time.Second},
	}}
	result := Run(experiment, time.Now)
	if len(result.Events) != 4 {
		t.Fatalf("expected four events, got %d", len(result.Events))
	}
	if len(result.Affected) != 2 {
		t.Fatalf("expected two affected services, got %v", result.Affected)
	}
	// The duration is the whole experiment, not the longest action.
	if result.Duration != "3s" {
		t.Fatalf("unexpected duration %s", result.Duration)
	}
}

func TestTimelineHasRealElapsedTime(t *testing.T) {
	clock := &fakeClock{now: time.Date(2026, 4, 23, 8, 0, 0, 0, time.UTC)}
	executor := &recordingExecutor{}

	runner := Runner{Executor: executor, Now: clock.Now, Sleep: clock.Sleep}
	experiment := Experiment{Name: "x", Actions: []Action{
		{Kind: "delay", Target: "api", Duration: 3 * time.Second},
	}}

	result, err := runner.Run(context.Background(), experiment)
	if err != nil {
		t.Fatal(err)
	}

	if len(result.Events) != 2 {
		t.Fatalf("expected two events, got %d", len(result.Events))
	}
	applied := result.Events[0]
	recovered := result.Events[1]
	if applied.At == recovered.At {
		t.Fatal("expected the recovered event to be later than the applied one")
	}
	if result.Duration != "3s" {
		t.Fatalf("expected the duration to be the elapsed time, got %s", result.Duration)
	}
}

func TestDurationIsTheTotalNotTheLongest(t *testing.T) {
	clock := &fakeClock{now: time.Date(2026, 4, 23, 8, 0, 0, 0, time.UTC)}
	runner := Runner{Executor: &recordingExecutor{}, Now: clock.Now, Sleep: clock.Sleep}

	experiment := Experiment{Name: "x", Actions: []Action{
		{Kind: "delay", Target: "api", Duration: 2 * time.Second},
		{Kind: "delay", Target: "api", Duration: 3 * time.Second},
	}}

	result, _ := runner.Run(context.Background(), experiment)
	if result.Duration != "5s" {
		t.Fatalf("expected the total duration 5s, got %s", result.Duration)
	}
}

func TestActionsAreAppliedAndRecoveredInOrder(t *testing.T) {
	executor := &recordingExecutor{}
	runner := Runner{Executor: executor, Sleep: func(time.Duration) {}}

	experiment := Experiment{Name: "x", Actions: []Action{
		{Kind: "delay", Target: "api", Duration: time.Second},
		{Kind: "kill", Target: "worker", Duration: time.Second},
	}}

	result, _ := runner.Run(context.Background(), experiment)

	if len(executor.applied) != 2 || len(executor.recovered) != 2 {
		t.Fatalf("expected two applies and two recoveries, got %d and %d", len(executor.applied), len(executor.recovered))
	}
	if executor.applied[0].Target != "api" || executor.applied[1].Target != "worker" {
		t.Fatal("the actions were not applied in order")
	}
	if result.ActionsRun != 2 || result.ActionsTotal != 2 {
		t.Fatalf("expected two of two actions to run, got %d of %d", result.ActionsRun, result.ActionsTotal)
	}
}

func TestSteadyStateIsCheckedBeforeAndAfter(t *testing.T) {
	clock := &fakeClock{now: time.Date(2026, 4, 23, 8, 0, 0, 0, time.UTC)}
	executor := &recordingExecutor{}
	metrics := StaticMetricReader{Values: map[string]float64{"api/error_rate": 0.001}}

	runner := Runner{Executor: executor, Metrics: metrics, Now: clock.Now, Sleep: clock.Sleep}
	experiment := Experiment{
		Name:       "x",
		Actions:    []Action{{Kind: "delay", Target: "api", Duration: time.Second}},
		Hypothesis: &Hypothesis{Service: "api", Metric: "error_rate", Op: "lt", Value: 0.01},
	}

	result, _ := runner.Run(context.Background(), experiment)

	if len(result.SteadyState) != 2 {
		t.Fatalf("expected two steady state checks, got %d", len(result.SteadyState))
	}
	if result.SteadyState[0].Phase != "before" || result.SteadyState[1].Phase != "after" {
		t.Fatalf("unexpected phases: %v", result.SteadyState)
	}
	if result.Aborted {
		t.Fatal("the experiment should not have been aborted")
	}
}

func TestABrokenSteadyStateAbortsBeforeAnythingIsApplied(t *testing.T) {
	executor := &recordingExecutor{}
	metrics := StaticMetricReader{Values: map[string]float64{"api/error_rate": 0.5}}

	runner := Runner{Executor: executor, Metrics: metrics, Sleep: func(time.Duration) {}}
	experiment := Experiment{
		Name:       "x",
		Actions:    []Action{{Kind: "delay", Target: "api", Duration: time.Second}},
		Hypothesis: &Hypothesis{Service: "api", Metric: "error_rate", Op: "lt", Value: 0.01},
	}

	result, err := runner.Run(context.Background(), experiment)
	if err != nil {
		t.Fatal(err)
	}

	if !result.Aborted {
		t.Fatal("expected the experiment to be aborted")
	}
	if len(executor.applied) != 0 {
		t.Fatal("no action should have been applied")
	}
	if result.ActionsRun != 0 {
		t.Fatal("no action should have run")
	}
}

func TestAFailedApplyRollsBack(t *testing.T) {
	executor := &recordingExecutor{failApply: true}
	runner := Runner{Executor: executor, Sleep: func(time.Duration) {}}

	experiment := Experiment{Name: "x", Actions: []Action{{Kind: "delay", Target: "api"}}}
	result, err := runner.Run(context.Background(), experiment)

	if err == nil {
		t.Fatal("expected an error")
	}
	if !result.Aborted {
		t.Fatal("expected the experiment to be aborted")
	}
}

func TestAFailedRecoveryRollsBack(t *testing.T) {
	executor := &recordingExecutor{failRecover: true}
	runner := Runner{Executor: executor, Sleep: func(time.Duration) {}}

	experiment := Experiment{Name: "x", Actions: []Action{{Kind: "delay", Target: "api"}}}
	result, err := runner.Run(context.Background(), experiment)

	if err == nil {
		t.Fatal("expected an error")
	}
	if !result.Aborted {
		t.Fatal("expected the experiment to be aborted")
	}
}

func TestTheResultsAreStored(t *testing.T) {
	store := &MemoryStore{}
	runner := Runner{Executor: &recordingExecutor{}, Store: store, Sleep: func(time.Duration) {}}

	experiment := Experiment{Name: "stored", Actions: []Action{{Kind: "delay", Target: "api"}}}
	if _, err := runner.Run(context.Background(), experiment); err != nil {
		t.Fatal(err)
	}

	results, err := store.List()
	if err != nil {
		t.Fatal(err)
	}
	if len(results) != 1 || results[0].Experiment != "stored" {
		t.Fatalf("expected the result to be stored, got %v", results)
	}
}

func TestTheFileStoreKeepsTheResults(t *testing.T) {
	path := filepath.Join(t.TempDir(), "results.json")
	store := &FileStore{Path: path}
	runner := Runner{Executor: &recordingExecutor{}, Store: store, Sleep: func(time.Duration) {}}

	experiment := Experiment{Name: "stored", Actions: []Action{{Kind: "delay", Target: "api"}}}
	if _, err := runner.Run(context.Background(), experiment); err != nil {
		t.Fatal(err)
	}

	results, err := store.List()
	if err != nil {
		t.Fatal(err)
	}
	if len(results) != 1 {
		t.Fatalf("expected one result, got %d", len(results))
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("expected the results file to exist: %v", err)
	}
}

func TestTheFileStoreStartsEmpty(t *testing.T) {
	store := &FileStore{Path: filepath.Join(t.TempDir(), "missing.json")}
	results, err := store.List()
	if err != nil {
		t.Fatal(err)
	}
	if len(results) != 0 {
		t.Fatalf("expected no results, got %d", len(results))
	}
}

func TestTheDryRunExecutorChangesNothing(t *testing.T) {
	executor := DryRunExecutor{}
	action := Action{Kind: "delay", Target: "api", Duration: time.Hour}

	started := time.Now()
	if err := executor.Apply(context.Background(), action); err != nil {
		t.Fatal(err)
	}
	if elapsed := time.Since(started); elapsed > time.Second {
		t.Fatal("the dry run executor should not wait")
	}
	if err := executor.Recover(context.Background(), action); err != nil {
		t.Fatal(err)
	}
}

func TestTheLocalExecutorReallyWaits(t *testing.T) {
	slept := time.Duration(0)
	executor := LocalExecutor{Sleep: func(d time.Duration) { slept += d }}

	if err := executor.Apply(context.Background(), Action{Kind: "delay", Target: "api", Duration: 5 * time.Second}); err != nil {
		t.Fatal(err)
	}
	if slept != 5*time.Second {
		t.Fatalf("expected the executor to wait five seconds, got %s", slept)
	}
}

func TestTheLocalExecutorRefusesANonNumericKillTarget(t *testing.T) {
	executor := LocalExecutor{}
	err := executor.Apply(context.Background(), Action{Kind: "kill", Target: "api"})
	if err == nil {
		t.Fatal("expected an error for a target that is not a process id")
	}
	if !strings.Contains(fmt.Sprint(err), "process id") {
		t.Fatalf("unexpected error %v", err)
	}
}

func TestTheMetricReaderReportsAMissingMetric(t *testing.T) {
	reader := StaticMetricReader{Values: map[string]float64{}}
	if _, err := reader.Read(context.Background(), "api", "error_rate"); err == nil {
		t.Fatal("expected an error for a missing metric")
	}
}

func TestAffectedServices(t *testing.T) {
	experiment := Experiment{Name: "x", Actions: []Action{
		{Target: "worker"}, {Target: "api"}, {Target: "worker"},
	}}
	services := AffectedServices(experiment)
	if len(services) != 2 || services[0] != "api" || services[1] != "worker" {
		t.Fatalf("unexpected services %v", services)
	}
}
