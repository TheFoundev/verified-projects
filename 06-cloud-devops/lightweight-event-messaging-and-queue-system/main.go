package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"net/http"
	"os"
)

func main() {
	listen := flag.String("listen", "", "optional HTTP listen address")
	flag.Parse()

	broker := NewBroker(100)
	broker.Publish("orders", "o-1", `{"total": 20}`)
	broker.Publish("orders", "o-2", `{"total": 45}`)

	if *listen != "" {
		mux := http.NewServeMux()
		mux.HandleFunc("/publish", broker.PublishHandler)
		mux.HandleFunc("/fetch", broker.FetchHandler)
		fmt.Println("serving queue API on", *listen)
		if err := http.ListenAndServe(*listen, mux); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}

	consumer := broker.Consumer("orders")
	messages := consumer.Poll(10)
	consumer.Ack(messages[len(messages)-1].Offset)
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	_ = encoder.Encode(map[string]any{"messages": messages, "pending": consumer.Pending()})
}
