package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"
)

func main() {
	demo := flag.Bool("demo", false, "run a small demo reconciliation and print the result")
	serve := flag.Bool("serve", false, "serve the control plane over HTTP")
	port := flag.String("port", "8080", "the port to serve on")
	state := flag.String("state", "", "write the resources to this file so they survive a restart")
	interval := flag.Duration("interval", 0, "reconcile every this often (0 turns it off)")
	flag.Parse()

	store := NewStore()
	registry := NewSchemaRegistry()
	registry.Register(Schema{
		Kind:     "Bucket",
		Required: []string{"region"},
		Allowed:  map[string][]string{"versioning": {"true", "false"}},
	})
	registry.Register(Schema{
		Kind:     "Database",
		Required: []string{"size"},
		Allowed:  map[string][]string{"size": {"small", "medium", "large"}, "multi_az": {"true", "false"}},
	})

	controller := NewController(store, registry)

	// The resources of a previous run are read back, so a restart does not
	// lose them.
	if *state != "" {
		file := NewFileStore(*state)
		loaded, err := file.Load()
		if err != nil {
			fmt.Fprintln(os.Stderr, "could not read the state:", err)
			os.Exit(1)
		}
		for _, resource := range loaded {
			store.Put(resource)
		}
	}

	if *demo {
		desired := []Resource{
			{Kind: "Bucket", Name: "assets", Spec: map[string]any{"region": "eu-west-1", "versioning": true}},
			{Kind: "Database", Name: "orders", Spec: map[string]any{"size": "small", "multi_az": false}},
		}
		if _, err := controller.SetDesired(desired); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		result := controller.Reconcile()
		if *state != "" {
			if err := NewFileStore(*state).Save(store.List()); err != nil {
				fmt.Fprintln(os.Stderr, "could not write the state:", err)
				os.Exit(1)
			}
		}
		encoder := json.NewEncoder(os.Stdout)
		encoder.SetIndent("", "  ")
		if err := encoder.Encode(map[string]any{"actions": result.Applied, "resources": store.List()}); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}

	if *serve {
		number, err := ParsePort(*port)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}

		if *interval > 0 {
			go func() {
				ticker := time.NewTicker(*interval)
				defer ticker.Stop()
				for range ticker.C {
					controller.Reconcile()
					if *state != "" {
						NewFileStore(*state).Save(store.List())
					}
				}
			}()
		}

		server := &http.Server{Addr: fmt.Sprintf(":%d", number), Handler: NewServer(controller, store)}
		stop := make(chan os.Signal, 1)
		signal.Notify(stop, os.Interrupt, syscall.SIGTERM)
		go func() {
			<-stop
			_ = server.Close()
		}()

		fmt.Printf("the control plane is listening on http://127.0.0.1:%d\n", number)
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}

	fmt.Println("use -demo for a short run, -serve to start the API.")
}
