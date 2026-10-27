package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"net/http"
	"os"
	"time"
)

func sampleSeries() []Sample {
	now := time.Unix(1700000000, 0)
	samples := []Sample{}
	for index := 0; index < 10; index++ {
		samples = append(samples,
			Sample{Metric: "http_requests_total", Labels: map[string]string{"route": "/api"}, Value: float64(index * 20), At: now.Add(time.Duration(index) * 15 * time.Second)},
			Sample{Metric: "queue_depth", Labels: map[string]string{"topic": "orders"}, Value: float64(index + 1), At: now.Add(time.Duration(index) * time.Minute)},
		)
	}
	return samples
}

func main() {
	listen := flag.String("listen", "", "optional HTTP listen address")
	flag.Parse()

	store := NewStore()
	store.Add(sampleSeries()...)
	rules := []Rule{{Name: "queue-growing", Metric: "queue_depth", Threshold: 5, For: 3 * time.Minute, Mode: "average"}}
	now := time.Unix(1700000000, 0).Add(9 * time.Minute)

	if *listen != "" {
		mux := http.NewServeMux()
		mux.HandleFunc("/ingest", store.IngestHandler)
		mux.HandleFunc("/query", store.QueryHandler)
		fmt.Println("serving metrics API on", *listen)
		if err := http.ListenAndServe(*listen, mux); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	alerts := Evaluate(store, rules, now)
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	_ = encoder.Encode(map[string]any{
		"rate_http": store.Rate("http_requests_total", 2*time.Minute, now),
		"avg_queue": store.Average("queue_depth", 10*time.Minute, now),
		"alerts":    alerts,
	})
}
