package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"net/http"
	"os"
	"time"
)

func sampleSpans() []Span {
	start := time.Unix(1700000000, 0)
	return []Span{
		{TraceID: "t1", SpanID: "s1", Service: "gateway", Operation: "GET /checkout", Start: start, Duration: 120 * time.Millisecond, Status: "ok"},
		{TraceID: "t1", SpanID: "s2", ParentID: "s1", Service: "orders", Operation: "create", Start: start.Add(10 * time.Millisecond), Duration: 90 * time.Millisecond, Status: "ok"},
		{TraceID: "t1", SpanID: "s3", ParentID: "s2", Service: "payments", Operation: "charge", Start: start.Add(30 * time.Millisecond), Duration: 40 * time.Millisecond, Status: "error"},
		{TraceID: "t2", SpanID: "s4", Service: "gateway", Operation: "GET /catalog", Start: start.Add(time.Minute), Duration: 45 * time.Millisecond, Status: "ok"},
	}
}

func main() {
	listen := flag.String("listen", "", "optional HTTP listen address")
	flag.Parse()

	store := NewStore()
	store.Add(sampleSpans()...)
	if *listen != "" {
		mux := http.NewServeMux()
		mux.HandleFunc("/spans", store.IngestHandler)
		mux.HandleFunc("/stats", store.StatsHandler)
		mux.HandleFunc("/traces/", store.TraceHandler)
		fmt.Println("serving trace API on", *listen)
		if err := http.ListenAndServe(*listen, mux); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	_ = encoder.Encode(map[string]any{"stats": store.Stats(), "traces": len(store.Traces())})
}
