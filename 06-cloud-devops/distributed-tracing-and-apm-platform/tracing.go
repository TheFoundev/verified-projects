package main

import (
	"encoding/json"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"
)

// Span is one unit of work in a trace.
type Span struct {
	TraceID   string        `json:"trace_id"`
	SpanID    string        `json:"span_id"`
	ParentID  string        `json:"parent_id,omitempty"`
	Service   string        `json:"service"`
	Operation string        `json:"operation"`
	Start     time.Time     `json:"start"`
	Duration  time.Duration `json:"duration"`
	Status    string        `json:"status"`
}

// Trace groups spans with the same trace id.
type Trace struct {
	TraceID  string        `json:"trace_id"`
	Spans    []Span        `json:"spans"`
	Duration time.Duration `json:"duration"`
	Errors   int           `json:"errors"`
	Root     string        `json:"root"`
}

// ServiceStats is the latency summary for one service.
type ServiceStats struct {
	Service string  `json:"service"`
	Spans   int     `json:"spans"`
	P50     float64 `json:"p50_ms"`
	P95     float64 `json:"p95_ms"`
	Errors  int     `json:"errors"`
}

// Store keeps spans in memory.
type Store struct {
	mu    sync.RWMutex
	spans []Span
}

func NewStore() *Store {
	return &Store{}
}

func (store *Store) Add(spans ...Span) {
	store.mu.Lock()
	defer store.mu.Unlock()
	store.spans = append(store.spans, spans...)
}

func (store *Store) all() []Span {
	store.mu.RLock()
	defer store.mu.RUnlock()
	copied := make([]Span, len(store.spans))
	copy(copied, store.spans)
	return copied
}

// Traces assembles spans into traces sorted by trace id.
func (store *Store) Traces() []Trace {
	grouped := map[string][]Span{}
	for _, span := range store.all() {
		grouped[span.TraceID] = append(grouped[span.TraceID], span)
	}
	traces := []Trace{}
	for id, spans := range grouped {
		trace := Trace{TraceID: id, Spans: spans}
		earliest := spans[0].Start
		latest := spans[0].Start.Add(spans[0].Duration)
		for _, span := range spans {
			if span.Start.Before(earliest) {
				earliest = span.Start
			}
			if end := span.Start.Add(span.Duration); end.After(latest) {
				latest = end
			}
			if span.Status == "error" {
				trace.Errors++
			}
			if span.ParentID == "" {
				trace.Root = span.SpanID
			}
		}
		trace.Duration = latest.Sub(earliest)
		traces = append(traces, trace)
	}
	sort.Slice(traces, func(i, j int) bool { return traces[i].TraceID < traces[j].TraceID })
	return traces
}

// Percentile returns the p-th percentile of a slice of milliseconds.
func Percentile(values []float64, p float64) float64 {
	if len(values) == 0 {
		return 0
	}
	sorted := append([]float64{}, values...)
	sort.Float64s(sorted)
	index := int(p / 100 * float64(len(sorted)-1))
	return sorted[index]
}

// Stats summarises latency per service.
func (store *Store) Stats() []ServiceStats {
	spans := store.all()
	grouped := map[string][]Span{}
	for _, span := range spans {
		grouped[span.Service] = append(grouped[span.Service], span)
	}
	stats := []ServiceStats{}
	for service, items := range grouped {
		values := make([]float64, 0, len(items))
		errors := 0
		for _, span := range items {
			values = append(values, float64(span.Duration.Milliseconds()))
			if span.Status == "error" {
				errors++
			}
		}
		stats = append(stats, ServiceStats{
			Service: service,
			Spans:   len(items),
			P50:     Percentile(values, 50),
			P95:     Percentile(values, 95),
			Errors:  errors,
		})
	}
	sort.Slice(stats, func(i, j int) bool { return stats[i].Service < stats[j].Service })
	return stats
}

// IngestHandler accepts a JSON array of spans.
// OTLPHandler takes the spans of the OpenTelemetry protocol.
//
// The answer of the protocol is protobuf, so the spans are read as JSON,
// which is what the JSON of the protocol carries.
func (store *Store) OTLPHandler(writer http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodPost {
		http.Error(writer, "only POST is allowed", http.StatusMethodNotAllowed)
		return
	}
	var payload struct {
		ResourceSpans []struct {
			ScopeSpans []struct {
				Spans []Span `json:"spans"`
			} `json:"scopeSpans"`
		} `json:"resourceSpans"`
	}
	if err := json.NewDecoder(request.Body).Decode(&payload); err != nil {
		http.Error(writer, "the body is not valid JSON", http.StatusBadRequest)
		return
	}

	spans := []Span{}
	for _, resource := range payload.ResourceSpans {
		for _, scope := range resource.ScopeSpans {
			spans = append(spans, scope.Spans...)
		}
	}
	store.Add(spans...)
	writeJSON(writer, http.StatusOK, map[string]any{"accepted": len(spans)})
}

// writeJSON answers with JSON.
func writeJSON(writer http.ResponseWriter, status int, payload any) {
	writer.Header().Set("Content-Type", "application/json")
	writer.WriteHeader(status)
	_ = json.NewEncoder(writer).Encode(payload)
}

func (store *Store) IngestHandler(writer http.ResponseWriter, request *http.Request) {
	var spans []Span
	if err := json.NewDecoder(request.Body).Decode(&spans); err != nil {
		http.Error(writer, err.Error(), http.StatusBadRequest)
		return
	}
	store.Add(spans...)
	writer.WriteHeader(http.StatusAccepted)
	_ = json.NewEncoder(writer).Encode(map[string]int{"accepted": len(spans)})
}

// StatsHandler returns per-service latency.
func (store *Store) StatsHandler(writer http.ResponseWriter, request *http.Request) {
	_ = json.NewEncoder(writer).Encode(store.Stats())
}

// TraceHandler returns one trace by id from a /traces/{id} path.
func (store *Store) TraceHandler(writer http.ResponseWriter, request *http.Request) {
	id := strings.TrimPrefix(request.URL.Path, "/traces/")
	for _, trace := range store.Traces() {
		if trace.TraceID == id {
			_ = json.NewEncoder(writer).Encode(trace)
			return
		}
	}
	http.Error(writer, "trace not found", http.StatusNotFound)
}
