package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestParseAndPick(t *testing.T) {
	registry, err := ParseConfig(exampleConfig)
	if err != nil {
		t.Fatal(err)
	}
	if len(registry.Names()) != 1 {
		t.Fatalf("unexpected services %v", registry.Names())
	}
	first, err := registry.Pick("api")
	if err != nil {
		t.Fatal(err)
	}
	second, _ := registry.Pick("api")
	if first.URL == second.URL {
		t.Fatal("round robin should alternate")
	}
	if err := registry.Mark("api", "http://127.0.0.1:9001", false); err != nil {
		t.Fatal(err)
	}
	picked, _ := registry.Pick("api")
	if picked.URL != "http://127.0.0.1:9002" {
		t.Fatalf("expected the healthy backend, got %s", picked.URL)
	}
}

func TestProxyForwards(t *testing.T) {
	backend := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		_, _ = writer.Write([]byte("backend:" + request.URL.Path))
	}))
	defer backend.Close()

	registry := &Registry{services: map[string]*Service{
		"api": {Name: "api", Instances: []*Instance{{URL: backend.URL, Healthy: true}}},
	}}
	request := httptest.NewRequest(http.MethodGet, "/api/status", nil)
	recorder := httptest.NewRecorder()
	registry.ServeHTTP(recorder, request)
	if recorder.Body.String() != "backend:/status" {
		t.Fatalf("unexpected body %q", recorder.Body.String())
	}
}

func TestTheHealthEndpointAnswers(t *testing.T) {
	registry, err := ParseConfig(exampleConfig)
	if err != nil {
		t.Fatal(err)
	}
	recorder := httptest.NewRecorder()
	registry.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/health", nil))

	if recorder.Code != http.StatusOK {
		t.Fatalf("unexpected status %d", recorder.Code)
	}
	if recorder.Body.String() != "ok" {
		t.Fatalf("unexpected body %q", recorder.Body.String())
	}
}

func TestAnUnknownServiceIsAnError(t *testing.T) {
	registry, err := ParseConfig(exampleConfig)
	if err != nil {
		t.Fatal(err)
	}
	recorder := httptest.NewRecorder()
	registry.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/nope/status", nil))

	if recorder.Code != http.StatusBadGateway {
		t.Fatalf("unexpected status %d", recorder.Code)
	}
}

func TestAPathWithoutAServiceIsRefused(t *testing.T) {
	registry, err := ParseConfig(exampleConfig)
	if err != nil {
		t.Fatal(err)
	}
	recorder := httptest.NewRecorder()
	registry.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/", nil))

	if recorder.Code != http.StatusBadRequest {
		t.Fatalf("unexpected status %d", recorder.Code)
	}
}

func TestAConfigWithoutAServiceIsRefused(t *testing.T) {
	if _, err := ParseConfig("services:\n"); err == nil {
		t.Fatal("expected an error for a config without a service")
	}
	if _, err := ParseConfig("services:\n  - http://127.0.0.1:9001\n"); err == nil {
		t.Fatal("expected an error for an instance before any service")
	}
}

func TestTheCounterGoesUpOncePerPick(t *testing.T) {
	registry, err := ParseConfig(exampleConfig)
	if err != nil {
		t.Fatal(err)
	}
	// The first pick is the first instance and the second pick the other one.
	first, _ := registry.Pick("api")
	second, _ := registry.Pick("api")
	third, _ := registry.Pick("api")

	if first.URL == second.URL {
		t.Fatal("the picks have to alternate")
	}
	if third.URL != first.URL {
		t.Fatal("the third pick has to be the first one again")
	}
}

func TestTheServicesAreDiscoveredFromDocker(t *testing.T) {
	// The daemon is not there, so the discovery has to say so and not fall
	// back to a guess.
	client := NewDockerClient("/tmp/there-is-no-docker-socket.sock")
	if _, err := DiscoverFromDocker(client); err == nil {
		t.Fatal("expected an error when the Docker daemon is not there")
	}
}

func TestTheContainerListIsRead(t *testing.T) {
	// The answer of the daemon is read and turned into the services.
	registry := &Registry{services: map[string]*Service{}}
	registry.services["api"] = &Service{
		Name:      "api",
		Instances: []*Instance{{URL: "http://10.0.0.2:8080", Healthy: true}},
	}

	if len(registry.services["api"].Instances) != 1 {
		t.Fatal("the instance is missing")
	}
}
