package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
)

func TestPlanAndReconcile(t *testing.T) {
	store := NewStore()
	desired := []Resource{
		{Kind: "Bucket", Name: "assets", Spec: map[string]any{"region": "eu-west-1"}},
		{Kind: "Database", Name: "orders", Spec: map[string]any{"size": "small"}},
	}
	plan := Plan(store.List(), desired)
	if len(plan) != 2 || plan[0].Operation != "create" {
		t.Fatalf("unexpected plan %#v", plan)
	}
	Reconcile(store, plan)
	if _, ok := store.Get("Bucket", "assets"); !ok {
		t.Fatal("bucket not created")
	}

	updated := []Resource{{Kind: "Bucket", Name: "assets", Spec: map[string]any{"region": "us-east-1"}}}
	plan = Plan(store.List(), updated)
	if len(plan) != 2 {
		t.Fatalf("unexpected plan %#v", plan)
	}
	if plan[0].Operation != "update" || plan[1].Operation != "delete" {
		t.Fatalf("unexpected plan order %#v", plan)
	}
	Reconcile(store, plan)
	if _, ok := store.Get("Database", "orders"); ok {
		t.Fatal("database should have been deleted")
	}
}

// The bug this project had: a reconciliation wrote the old spec back, so an
// update never changed anything.
func TestAnUpdateWritesTheDesiredSpec(t *testing.T) {
	store := NewStore()
	store.Put(Resource{Kind: "Bucket", Name: "assets", Spec: map[string]any{"region": "eu-west-1"}})

	desired := []Resource{{Kind: "Bucket", Name: "assets", Spec: map[string]any{"region": "us-east-1"}}}
	plan := Plan(store.List(), desired)
	if len(plan) != 1 || plan[0].Operation != "update" {
		t.Fatalf("unexpected plan %#v", plan)
	}

	Reconcile(store, plan)

	stored, ok := store.Get("Bucket", "assets")
	if !ok {
		t.Fatal("the bucket is gone")
	}
	if stored.Spec["region"] != "us-east-1" {
		t.Fatalf("the spec was not written, got %#v", stored.Spec)
	}
}

// A create has to write the spec as well.
func TestACreateWritesTheSpec(t *testing.T) {
	store := NewStore()
	plan := Plan(store.List(), []Resource{
		{Kind: "Bucket", Name: "assets", Spec: map[string]any{"region": "eu-west-1"}},
	})
	Reconcile(store, plan)

	stored, _ := store.Get("Bucket", "assets")
	if stored.Spec["region"] != "eu-west-1" {
		t.Fatalf("the spec was not written, got %#v", stored.Spec)
	}
	if stored.Status != "Ready" {
		t.Fatalf("the resource should be ready, got %q", stored.Status)
	}
	if len(stored.Conditions) != 1 || stored.Conditions[0].Status != "True" {
		t.Fatalf("the resource should have a ready condition, got %#v", stored.Conditions)
	}
}

// Every write goes up a version, so a concurrent write can be spotted.
func TestEveryWriteGoesUpTheVersion(t *testing.T) {
	store := NewStore()
	store.Put(Resource{Kind: "Bucket", Name: "assets"})
	first, _ := store.Get("Bucket", "assets")
	store.Put(Resource{Kind: "Bucket", Name: "assets"})
	second, _ := store.Get("Bucket", "assets")

	if second.ResourceVersion <= first.ResourceVersion {
		t.Fatalf("the version did not go up: %d then %d", first.ResourceVersion, second.ResourceVersion)
	}
}

// A write with an old version is refused.
func TestAStaleWriteIsRefused(t *testing.T) {
	store := NewStore()
	store.Put(Resource{Kind: "Bucket", Name: "assets", Spec: map[string]any{"region": "eu-west-1"}})
	stale, _ := store.Get("Bucket", "assets")

	store.Put(Resource{Kind: "Bucket", Name: "assets", Spec: map[string]any{"region": "us-east-1"}})
	stale.Spec = map[string]any{"region": "ap-south-1"}

	if store.Update(stale) {
		t.Fatal("a write with an old version should be refused")
	}
	current, _ := store.Get("Bucket", "assets")
	if current.Spec["region"] != "us-east-1" {
		t.Fatalf("the stale write went through, got %#v", current.Spec)
	}
}

// The schemas say what a kind needs.
func TestTheSchemaIsChecked(t *testing.T) {
	registry := NewSchemaRegistry()
	registry.Register(Schema{
		Kind:     "Bucket",
		Required: []string{"region"},
		Allowed:  map[string][]string{"versioning": {"true", "false"}},
	})

	if err := registry.Validate(Resource{Kind: "Bucket", Name: "a", Spec: map[string]any{"region": "eu"}}); err != nil {
		t.Fatalf("a good resource was refused: %v", err)
	}
	if err := registry.Validate(Resource{Kind: "Bucket", Name: "a", Spec: map[string]any{}}); err == nil {
		t.Fatal("a resource without the required field was accepted")
	}
	if err := registry.Validate(Resource{Kind: "Bucket", Name: "a", Spec: map[string]any{"region": "eu", "versioning": "maybe"}}); err == nil {
		t.Fatal("a resource with a value that is not allowed was accepted")
	}
	if err := registry.Validate(Resource{Kind: "Unknown", Name: "a"}); err != nil {
		t.Fatalf("a kind without a schema should be accepted: %v", err)
	}
}

// The controller keeps the desired state and reconciles the store with it.
func TestTheControllerReconcilesTheStore(t *testing.T) {
	store := NewStore()
	registry := NewSchemaRegistry()
	registry.Register(Schema{Kind: "Bucket", Required: []string{"region"}})
	controller := NewController(store, registry)

	if _, err := controller.SetDesired([]Resource{
		{Kind: "Bucket", Name: "assets", Spec: map[string]any{"region": "eu-west-1"}},
	}); err != nil {
		t.Fatal(err)
	}

	result := controller.Reconcile()
	if len(result.Applied) != 1 {
		t.Fatalf("unexpected result %#v", result)
	}
	stored, ok := store.Get("Bucket", "assets")
	if !ok {
		t.Fatal("the bucket was not created")
	}
	if stored.Spec["region"] != "eu-west-1" {
		t.Fatalf("the spec was not written, got %#v", stored.Spec)
	}

	// The second reconciliation has nothing to do.
	result = controller.Reconcile()
	if len(result.Applied) != 0 {
		t.Fatalf("the second reconciliation should be empty, got %#v", result.Applied)
	}
}

// A desired state that does not pass the schema is refused.
func TestTheControllerRefusesABadDesiredState(t *testing.T) {
	store := NewStore()
	registry := NewSchemaRegistry()
	registry.Register(Schema{Kind: "Bucket", Required: []string{"region"}})
	controller := NewController(store, registry)

	if _, err := controller.SetDesired([]Resource{{Kind: "Bucket", Name: "assets"}}); err == nil {
		t.Fatal("a desired state that does not pass the schema should be refused")
	}
	if len(controller.Desired()) != 0 {
		t.Fatal("the desired state should not have changed")
	}
}

// The API answers the requests.
func TestTheAPI(t *testing.T) {
	store := NewStore()
	registry := NewSchemaRegistry()
	registry.Register(Schema{Kind: "Bucket", Required: []string{"region"}})
	controller := NewController(store, registry)
	server := NewServer(controller, store)
	httpServer := httptest.NewServer(server)
	defer httpServer.Close()

	body := `{"resources":[{"kind":"Bucket","name":"assets","spec":{"region":"eu-west-1"}}]}`
	response, err := http.Post(httpServer.URL+"/api/desired", "application/json", strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != http.StatusOK {
		t.Fatalf("unexpected status %d", response.StatusCode)
	}
	response.Body.Close()

	response, err = http.Post(httpServer.URL+"/api/reconcile", "application/json", nil)
	if err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != http.StatusOK {
		t.Fatalf("unexpected status %d", response.StatusCode)
	}
	var result Result
	json.NewDecoder(response.Body).Decode(&result)
	response.Body.Close()
	if len(result.Applied) != 1 {
		t.Fatalf("unexpected result %#v", result)
	}

	response, err = http.Get(httpServer.URL + "/api/resources")
	if err != nil {
		t.Fatal(err)
	}
	var listing struct {
		Resources []Resource `json:"resources"`
	}
	json.NewDecoder(response.Body).Decode(&listing)
	response.Body.Close()
	if len(listing.Resources) != 1 {
		t.Fatalf("unexpected listing %#v", listing)
	}
}

// The API refuses a desired state that does not pass the schema.
func TestTheAPIRefusesABadDesiredState(t *testing.T) {
	store := NewStore()
	registry := NewSchemaRegistry()
	registry.Register(Schema{Kind: "Bucket", Required: []string{"region"}})
	controller := NewController(store, registry)
	server := NewServer(controller, store)
	httpServer := httptest.NewServer(server)
	defer httpServer.Close()

	body := `{"resources":[{"kind":"Bucket","name":"assets","spec":{}}]}`
	response, err := http.Post(httpServer.URL+"/api/desired", "application/json", strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusBadRequest {
		t.Fatalf("unexpected status %d", response.StatusCode)
	}
}

// The resources survive a restart.
func TestTheFileStore(t *testing.T) {
	path := filepath.Join(t.TempDir(), "resources.json")
	store := NewFileStore(path)

	if err := store.Save([]Resource{{Kind: "Bucket", Name: "assets", Spec: map[string]any{"region": "eu"}}}); err != nil {
		t.Fatal(err)
	}

	loaded, err := store.Load()
	if err != nil {
		t.Fatal(err)
	}
	if len(loaded) != 1 || loaded[0].Name != "assets" {
		t.Fatalf("unexpected resources %#v", loaded)
	}
}

// A file that is not there yet gives an empty list.
func TestTheFileStoreStartsEmpty(t *testing.T) {
	store := NewFileStore(filepath.Join(t.TempDir(), "missing.json"))
	loaded, err := store.Load()
	if err != nil {
		t.Fatal(err)
	}
	if len(loaded) != 0 {
		t.Fatalf("unexpected resources %#v", loaded)
	}
}

// A resource is read from JSON.
func TestParseResource(t *testing.T) {
	resource, err := ParseResource([]byte(`{"kind":"Bucket","name":"assets","spec":{"region":"eu"}}`))
	if err != nil {
		t.Fatal(err)
	}
	if resource.Kind != "Bucket" || resource.Name != "assets" {
		t.Fatalf("unexpected resource %#v", resource)
	}

	if _, err := ParseResource([]byte(`{"kind":"Bucket"}`)); err == nil {
		t.Fatal("a resource without a name should be refused")
	}
}

// A port number is read from a string.
func TestParsePort(t *testing.T) {
	port, err := ParsePort("8080")
	if err != nil || port != 8080 {
		t.Fatalf("unexpected port %d (%v)", port, err)
	}
	if _, err := ParsePort("abc"); err == nil {
		t.Fatal("a port that is not a number should be refused")
	}
}

// The health check answers.
func TestTheHealthCheck(t *testing.T) {
	store := NewStore()
	controller := NewController(store, NewSchemaRegistry())
	server := NewServer(controller, store)
	httpServer := httptest.NewServer(server)
	defer httpServer.Close()

	response, err := http.Get(httpServer.URL + "/healthz")
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		t.Fatalf("unexpected status %d", response.StatusCode)
	}
}
