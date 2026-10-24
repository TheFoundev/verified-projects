package main

import (
	"encoding/json"
	"net/http"
)

// Handler serves the cost summary as JSON.
func Handler(summary Summary) http.HandlerFunc {
	return func(writer http.ResponseWriter, request *http.Request) {
		writer.Header().Set("Content-Type", "application/json")
		if err := json.NewEncoder(writer).Encode(summary); err != nil {
			http.Error(writer, err.Error(), http.StatusInternalServerError)
		}
	}
}
