package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"
)

// Sample is one metric point.
type Sample struct {
	Metric string            `json:"metric"`
	Labels map[string]string `json:"labels"`
	Value  float64           `json:"value"`
	At     time.Time         `json:"at"`
}

// Store keeps samples in memory.
type Store struct {
	mu      sync.RWMutex
	samples []Sample
}

func NewStore() *Store {
	return &Store{}
}

func (store *Store) Add(samples ...Sample) {
	store.mu.Lock()
	defer store.mu.Unlock()
	for _, sample := range samples {
		if sample.At.IsZero() {
			sample.At = time.Now().UTC()
		}
		store.samples = append(store.samples, sample)
	}
}

// windowLocked is the window of a metric, with the lock already held.
func (store *Store) windowLocked(metric string, window time.Duration, now time.Time) []Sample {
	matches := []Sample{}
	for _, sample := range store.samples {
		if sample.Metric != metric {
			continue
		}
		if now.Sub(sample.At) > window {
			continue
		}
		matches = append(matches, sample)
	}
	sort.Slice(matches, func(i, j int) bool { return matches[i].At.Before(matches[j].At) })
	return matches
}

func (store *Store) window(metric string, window time.Duration, now time.Time) []Sample {
	store.mu.RLock()
	defer store.mu.RUnlock()
	cutoff := now.Add(-window)
	selected := []Sample{}
	for _, sample := range store.samples {
		if sample.Metric == metric && !sample.At.Before(cutoff) && !sample.At.After(now) {
			selected = append(selected, sample)
		}
	}
	sort.Slice(selected, func(i, j int) bool { return selected[i].At.Before(selected[j].At) })
	return selected
}

// Rate estimates the per-second increase from first to last sample.
// rateLocked is the rate of a metric, with the lock already held.
func (store *Store) rateLocked(metric string, window time.Duration, now time.Time) float64 {
	samples := store.windowLocked(metric, window, now)
	if len(samples) < 2 {
		return 0
	}
	first := samples[0]
	last := samples[len(samples)-1]
	elapsed := last.At.Sub(first.At).Seconds()
	if elapsed <= 0 {
		return 0
	}
	return float64(len(samples)-1) / elapsed
}

func (store *Store) Rate(metric string, window time.Duration, now time.Time) float64 {
	samples := store.window(metric, window, now)
	if len(samples) < 2 {
		return 0
	}
	first := samples[0]
	last := samples[len(samples)-1]
	seconds := last.At.Sub(first.At).Seconds()
	if seconds <= 0 {
		return 0
	}
	return (last.Value - first.Value) / seconds
}

// Sum adds values in a window.
func (store *Store) Sum(metric string, window time.Duration, now time.Time) float64 {
	total := 0.0
	for _, sample := range store.window(metric, window, now) {
		total += sample.Value
	}
	return total
}

// Average returns the mean value in a window.
func (store *Store) Average(metric string, window time.Duration, now time.Time) float64 {
	samples := store.window(metric, window, now)
	if len(samples) == 0 {
		return 0
	}
	return store.Sum(metric, window, now) / float64(len(samples))
}

// Rule fires when a windowed value crosses a threshold for long enough.
type Rule struct {
	Name      string        `json:"name"`
	Metric    string        `json:"metric"`
	Threshold float64       `json:"threshold"`
	For       time.Duration `json:"for"`
	Mode      string        `json:"mode"`
}

// Alert is a firing rule.
type Alert struct {
	Rule      string  `json:"rule"`
	Metric    string  `json:"metric"`
	Value     float64 `json:"value"`
	Threshold float64 `json:"threshold"`
	Since     string  `json:"since"`
}

// Evaluate checks each rule for its for-duration.
func Evaluate(store *Store, rules []Rule, now time.Time) []Alert {
	alerts := []Alert{}
	for _, rule := range rules {
		value := store.Average(rule.Metric, rule.For, now)
		if rule.Mode == "rate" {
			value = store.Rate(rule.Metric, rule.For, now)
		}
		if value <= rule.Threshold {
			continue
		}
		samples := store.window(rule.Metric, rule.For, now)
		if len(samples) < 2 {
			continue
		}
		alerts = append(alerts, Alert{
			Rule: rule.Name, Metric: rule.Metric, Value: value, Threshold: rule.Threshold,
			Since: samples[0].At.Format(time.RFC3339),
		})
	}
	return alerts
}

// Prometheus writes the counters of the store in the text format
// Prometheus reads.
func (store *Store) Prometheus(now time.Time) string {
	store.mu.RLock()
	defer store.mu.RUnlock()

	lines := []string{
		"# HELP metrics_samples_total The samples that were taken in.",
		"# TYPE metrics_samples_total counter",
		fmt.Sprintf("metrics_samples_total %d", len(store.samples)),
	}
	windows := map[string]time.Duration{}
	for _, sample := range store.samples {
		if _, ok := windows[sample.Metric]; !ok {
			windows[sample.Metric] = 5 * time.Minute
		}
	}
	for metric, window := range windows {
		lines = append(lines,
			fmt.Sprintf("# HELP metrics_rate_%s The rate of the metric over the window.", metric),
			fmt.Sprintf("# TYPE metrics_rate_%s gauge", metric),
			fmt.Sprintf("metrics_rate_%s %.6f", metric, store.rateLocked(metric, window, now)),
		)
	}
	return strings.Join(lines, "\n") + "\n"
}

// PrometheusHandler answers a request for the metrics.
func (store *Store) PrometheusHandler(writer http.ResponseWriter, request *http.Request) {
	writer.Header().Set("Content-Type", "text/plain; version=0.0.4")
	_, _ = writer.Write([]byte(store.Prometheus(time.Now())))
}

func (store *Store) IngestHandler(writer http.ResponseWriter, request *http.Request) {
	var samples []Sample
	if err := json.NewDecoder(request.Body).Decode(&samples); err != nil {
		http.Error(writer, err.Error(), http.StatusBadRequest)
		return
	}
	store.Add(samples...)
	_ = json.NewEncoder(writer).Encode(map[string]int{"accepted": len(samples)})
}

func (store *Store) QueryHandler(writer http.ResponseWriter, request *http.Request) {
	metric := request.URL.Query().Get("metric")
	now := time.Now().UTC()
	_ = json.NewEncoder(writer).Encode(map[string]any{
		"metric":  metric,
		"rate":    store.Rate(metric, time.Minute, now),
		"average": store.Average(metric, time.Minute, now),
	})
}
