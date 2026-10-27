package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func sample(metric string, value float64, at time.Time) Sample {
	return Sample{Metric: metric, Labels: map[string]string{}, Value: value, At: at}
}

func TestTheRateIsCounted(t *testing.T) {
	store := NewStore()
	now := time.Now()
	store.Add(sample("http.requests", 10, now.Add(-2*time.Second)))
	store.Add(sample("http.requests", 20, now.Add(-1*time.Second)))

	rate := store.Rate("http.requests", time.Minute, now)
	if rate != 10 {
		t.Fatalf("unexpected rate %v", rate)
	}
}

func TestTheSumAndTheAverage(t *testing.T) {
	store := NewStore()
	now := time.Now()
	store.Add(sample("m", 10, now.Add(-time.Second)))
	store.Add(sample("m", 20, now))

	if sum := store.Sum("m", time.Minute, now); sum != 30 {
		t.Fatalf("unexpected sum %v", sum)
	}
	if average := store.Average("m", time.Minute, now); average != 15 {
		t.Fatalf("unexpected average %v", average)
	}
}

func TestAWindowThatIsTooOldIsNotCounted(t *testing.T) {
	store := NewStore()
	now := time.Now()
	store.Add(sample("m", 5, now.Add(-2*time.Hour)))

	if sum := store.Sum("m", time.Minute, now); sum != 0 {
		t.Fatalf("unexpected sum %v", sum)
	}
}

func TestThePrometheusOutputHasTheCounters(t *testing.T) {
	store := NewStore()
	now := time.Now()
	store.Add(sample("http.requests", 1, now))

	output := store.Prometheus(now)
	for _, want := range []string{"metrics_samples_total", "metrics_rate_http.requests"} {
		if !strings.Contains(output, want) {
			t.Fatalf("the output has no %q:\n%s", want, output)
		}
	}
}

func TestThePrometheusEndpointAnswers(t *testing.T) {
	store := NewStore()
	store.Add(sample("m", 1, time.Now()))

	recorder := httptest.NewRecorder()
	store.PrometheusHandler(recorder, httptest.NewRequest(http.MethodGet, "/metrics", nil))

	if recorder.Code != http.StatusOK {
		t.Fatalf("unexpected status %d", recorder.Code)
	}
	if !strings.Contains(recorder.Header().Get("Content-Type"), "text/plain") {
		t.Fatalf("unexpected content type %q", recorder.Header().Get("Content-Type"))
	}
	if !strings.Contains(recorder.Body.String(), "metrics_samples_total") {
		t.Fatalf("unexpected body %q", recorder.Body.String())
	}
}
