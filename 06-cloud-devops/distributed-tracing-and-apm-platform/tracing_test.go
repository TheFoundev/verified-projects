package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestTheSpansAreStored(t *testing.T) {
	store := NewStore()
	store.Add(
		Span{TraceID: "t1", SpanID: "s1", Service: "api", Operation: "GET /", Duration: 10 * time.Millisecond},
		Span{TraceID: "t1", SpanID: "s2", ParentID: "s1", Service: "api", Operation: "db", Duration: 4 * time.Millisecond},
	)
	traces := store.Traces()
	if len(traces) != 1 {
		t.Fatalf("unexpected traces %#v", traces)
	}
	if len(traces[0].Spans) != 2 {
		t.Fatalf("unexpected spans %#v", traces[0].Spans)
	}
}

func TestTheStatsAreWorkedOut(t *testing.T) {
	store := NewStore()
	store.Add(
		Span{TraceID: "t1", SpanID: "s1", Service: "api", Duration: 100 * time.Millisecond},
		Span{TraceID: "t2", SpanID: "s2", Service: "api", Duration: 200 * time.Millisecond},
	)
	stats := store.Stats()
	if len(stats) != 1 {
		t.Fatalf("unexpected stats %#v", stats)
	}
	if stats[0].Spans != 2 {
		t.Fatalf("unexpected span count %#v", stats[0])
	}
}

func TestThePercentile(t *testing.T) {
	values := []float64{1, 2, 3, 4, 5, 6, 7, 8, 9, 10}
	if p := Percentile(values, 50); p < 4 || p > 6 {
		t.Fatalf("unexpected percentile %v", p)
	}
	if p := Percentile(values, 95); p < 9 {
		t.Fatalf("unexpected percentile %v", p)
	}
}

func TestTheOpenTelemetryHandlerTakesTheSpans(t *testing.T) {
	store := NewStore()
	server := httptest.NewServer(http.HandlerFunc(store.OTLPHandler))
	defer server.Close()

	body, _ := json.Marshal(map[string]any{
		"resourceSpans": []any{
			map[string]any{
				"scopeSpans": []any{
					map[string]any{
						"spans": []any{
							map[string]any{"trace_id": "t1", "span_id": "s1", "service": "api", "operation": "GET /", "duration": 12},
						},
					},
				},
			},
		},
	})

	response, err := http.Post(server.URL, "application/json", bytes.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		t.Fatalf("unexpected status %d", response.StatusCode)
	}
	if len(store.Traces()) != 1 {
		t.Fatalf("the span was not taken: %#v", store.Traces())
	}
}

func TestTheOpenTelemetryHandlerRefusesABrokenBody(t *testing.T) {
	store := NewStore()
	server := httptest.NewServer(http.HandlerFunc(store.OTLPHandler))
	defer server.Close()

	response, err := http.Post(server.URL, "application/json", bytes.NewReader([]byte("not json")))
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusBadRequest {
		t.Fatalf("unexpected status %d", response.StatusCode)
	}
}
