package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
)

// Resource is one desired cloud object.
type Resource struct {
	APIVersion string         `json:"apiVersion"`
	Kind       string         `json:"kind"`
	Name       string         `json:"name"`
	Spec       map[string]any `json:"spec"`
	Status     string         `json:"status"`
	// ResourceVersion goes up every time the resource is written. It is what
	// makes a concurrent write detectable.
	ResourceVersion int `json:"resourceVersion"`
	// Conditions carry the state of the resource, the way a Kubernetes
	// object does.
	Conditions []Condition `json:"conditions,omitempty"`
	// ObservedGeneration is the version of the desired state the controller
	// has seen.
	ObservedGeneration int `json:"observedGeneration,omitempty"`
}

// Condition is one line of the state of a resource.
type Condition struct {
	Type    string `json:"type"`
	Status  string `json:"status"`
	Reason  string `json:"reason,omitempty"`
	Message string `json:"message,omitempty"`
}

// Action is one planned operation.
type Action struct {
	Kind      string         `json:"kind"`
	Name      string         `json:"name"`
	Operation string         `json:"operation"`
	Spec      map[string]any `json:"spec,omitempty"`
}

// Store is an in-memory resource map keyed by kind/name.
type Store struct {
	mu        sync.RWMutex
	resources map[string]Resource
	version   int
}

func key(kind, name string) string {
	return kind + "/" + name
}

func NewStore() *Store {
	return &Store{resources: map[string]Resource{}}
}

func (store *Store) Get(kind, name string) (Resource, bool) {
	store.mu.RLock()
	defer store.mu.RUnlock()
	resource, ok := store.resources[key(kind, name)]
	return resource, ok
}

// Put writes a resource and gives it a new version.
func (store *Store) Put(resource Resource) {
	store.mu.Lock()
	defer store.mu.Unlock()
	store.version++
	resource.ResourceVersion = store.version
	store.resources[key(resource.Kind, resource.Name)] = resource
}

// Update writes a resource only when the version still matches the one the
// caller read. It returns false when somebody else wrote in between.
func (store *Store) Update(resource Resource) bool {
	store.mu.Lock()
	defer store.mu.Unlock()
	existing, ok := store.resources[key(resource.Kind, resource.Name)]
	if !ok {
		return false
	}
	if resource.ResourceVersion != 0 && existing.ResourceVersion != resource.ResourceVersion {
		return false
	}
	store.version++
	resource.ResourceVersion = store.version
	store.resources[key(resource.Kind, resource.Name)] = resource
	return true
}

func (store *Store) Delete(kind, name string) bool {
	store.mu.Lock()
	defer store.mu.Unlock()
	if _, ok := store.resources[key(kind, name)]; !ok {
		return false
	}
	delete(store.resources, key(kind, name))
	return true
}

func (store *Store) List() []Resource {
	store.mu.RLock()
	defer store.mu.RUnlock()
	list := make([]Resource, 0, len(store.resources))
	for _, resource := range store.resources {
		list = append(list, resource)
	}
	sort.Slice(list, func(i, j int) bool {
		if list[i].Kind == list[j].Kind {
			return list[i].Name < list[j].Name
		}
		return list[i].Kind < list[j].Kind
	})
	return list
}

// The condition of a resource that is ready.
func readyCondition() Condition {
	return Condition{Type: "Ready", Status: "True", Reason: "Reconciled"}
}

// The condition of a resource that could not be reconciled.
func failedCondition(reason, message string) Condition {
	return Condition{Type: "Ready", Status: "False", Reason: reason, Message: message}
}

func sameSpec(left, right map[string]any) bool {
	leftJSON, _ := json.Marshal(left)
	rightJSON, _ := json.Marshal(right)
	return string(leftJSON) == string(rightJSON)
}

// ---------------------------------------------------------------------------
// Schemas
// ---------------------------------------------------------------------------

// Schema says what a kind needs.
type Schema struct {
	Kind     string
	Required []string
	// Allowed lists the values a field may have. An empty list allows
	// everything.
	Allowed map[string][]string
}

// SchemaRegistry holds the schemas of the kinds the control plane knows.
type SchemaRegistry struct {
	mu      sync.RWMutex
	schemas map[string]Schema
}

func NewSchemaRegistry() *SchemaRegistry {
	return &SchemaRegistry{schemas: map[string]Schema{}}
}

// Register adds or replaces the schema of a kind.
func (registry *SchemaRegistry) Register(schema Schema) {
	registry.mu.Lock()
	defer registry.mu.Unlock()
	registry.schemas[schema.Kind] = schema
}

// Schema returns the schema of a kind, if there is one.
func (registry *SchemaRegistry) Schema(kind string) (Schema, bool) {
	registry.mu.RLock()
	defer registry.mu.RUnlock()
	schema, ok := registry.schemas[kind]
	return schema, ok
}

// Validate checks a resource against the schema of its kind.
//
// A kind without a schema is accepted: the control plane does not know every
// kind of the world.
func (registry *SchemaRegistry) Validate(resource Resource) error {
	schema, ok := registry.Schema(resource.Kind)
	if !ok {
		return nil
	}
	if strings.TrimSpace(resource.Name) == "" {
		return fmt.Errorf("a %s needs a name", resource.Kind)
	}
	for _, field := range schema.Required {
		value, ok := resource.Spec[field]
		if !ok || value == nil || value == "" {
			return fmt.Errorf("a %s needs the field %q", resource.Kind, field)
		}
	}
	for field, allowed := range schema.Allowed {
		value, ok := resource.Spec[field]
		if !ok {
			continue
		}
		text := fmt.Sprintf("%v", value)
		for _, option := range allowed {
			if option == text {
				goto allowed
			}
		}
		return fmt.Errorf("the field %q of a %s can only be %s", field, resource.Kind, strings.Join(allowed, ", "))
	allowed:
	}
	return nil
}

// ---------------------------------------------------------------------------
// Planning
// ---------------------------------------------------------------------------

// Plan diffs current against desired.
func Plan(current, desired []Resource) []Action {
	currentByKey := map[string]Resource{}
	for _, resource := range current {
		currentByKey[key(resource.Kind, resource.Name)] = resource
	}
	desiredByKey := map[string]Resource{}
	for _, resource := range desired {
		desiredByKey[key(resource.Kind, resource.Name)] = resource
	}
	actions := []Action{}
	for _, resource := range desired {
		existing, ok := currentByKey[key(resource.Kind, resource.Name)]
		if !ok {
			actions = append(actions, Action{Kind: resource.Kind, Name: resource.Name, Operation: "create", Spec: resource.Spec})
			continue
		}
		if !sameSpec(existing.Spec, resource.Spec) {
			actions = append(actions, Action{Kind: resource.Kind, Name: resource.Name, Operation: "update", Spec: resource.Spec})
		}
	}
	for _, resource := range current {
		if _, ok := desiredByKey[key(resource.Kind, resource.Name)]; !ok {
			actions = append(actions, Action{Kind: resource.Kind, Name: resource.Name, Operation: "delete"})
		}
	}
	sort.Slice(actions, func(i, j int) bool {
		if actions[i].Kind == actions[j].Kind {
			return actions[i].Name < actions[j].Name
		}
		return actions[i].Kind < actions[j].Kind
	})
	return actions
}

// ---------------------------------------------------------------------------
// Reconciling
// ---------------------------------------------------------------------------

// Result is what a reconciliation did.
type Result struct {
	Applied []Action        `json:"applied"`
	Failed  []FailedAction  `json:"failed"`
	Skipped []Action        `json:"skipped,omitempty"`
	Errors  []string        `json:"errors,omitempty"`
	Store   *Store          `json:"-"`
	schemas *SchemaRegistry `json:"-"`
}

// FailedAction is an action that could not be applied.
type FailedAction struct {
	Action  Action `json:"action"`
	Message string `json:"message"`
}

// Reconcile applies a plan to the store.
//
// The desired spec of the action is what is written, so an update really
// changes the resource. A resource that cannot be written is reported and
// the other actions still go through.
func Reconcile(store *Store, plan []Action) []Action {
	result := ReconcileWithSchemas(store, plan, nil)
	return result.Applied
}

// ReconcileWithSchemas applies a plan and reports what happened.
func ReconcileWithSchemas(store *Store, plan []Action, registry *SchemaRegistry) Result {
	result := Result{Applied: []Action{}, Failed: []FailedAction{}}

	for _, action := range plan {
		switch action.Operation {
		case "delete":
			store.Delete(action.Kind, action.Name)
		case "create", "update":
			existing, ok := store.Get(action.Kind, action.Name)
			if !ok {
				store.Put(Resource{
					APIVersion: "cloud/v1",
					Kind:       action.Kind,
					Name:       action.Name,
					Spec:       action.Spec,
					Status:     "Ready",
					Conditions: []Condition{readyCondition()},
				})
				break
			}
			// The desired spec is what is written, not the old one.
			existing.Spec = action.Spec
			existing.Status = "Ready"
			existing.Conditions = []Condition{readyCondition()}
			existing.ObservedGeneration = existing.ResourceVersion + 1
			if !store.Update(existing) {
				result.Failed = append(result.Failed, FailedAction{
					Action:  action,
					Message: "the resource was changed by somebody else",
				})
				continue
			}
		default:
			result.Skipped = append(result.Skipped, action)
			continue
		}
		result.Applied = append(result.Applied, action)
	}

	return result
}

// ---------------------------------------------------------------------------
// The controller
// ---------------------------------------------------------------------------

// Controller keeps the desired state and reconciles the store with it.
type Controller struct {
	store    *Store
	registry *SchemaRegistry

	mu      sync.Mutex
	desired []Resource
	// generation goes up every time the desired state changes.
	generation int
}

// NewController creates a controller with the schemas of the kinds it knows.
func NewController(store *Store, registry *SchemaRegistry) *Controller {
	return &Controller{store: store, registry: registry, desired: []Resource{}}
}

// SetDesired replaces the desired state and returns the new generation.
func (controller *Controller) SetDesired(resources []Resource) (int, error) {
	for _, resource := range resources {
		if err := controller.registry.Validate(resource); err != nil {
			return controller.generation, err
		}
	}

	controller.mu.Lock()
	defer controller.mu.Unlock()
	controller.desired = append([]Resource{}, resources...)
	controller.generation++
	return controller.generation, nil
}

// Desired returns the desired state.
func (controller *Controller) Desired() []Resource {
	controller.mu.Lock()
	defer controller.mu.Unlock()
	return append([]Resource{}, controller.desired...)
}

// Generation returns the version of the desired state.
func (controller *Controller) Generation() int {
	controller.mu.Lock()
	defer controller.mu.Unlock()
	return controller.generation
}

// Reconcile brings the store in line with the desired state.
func (controller *Controller) Reconcile() Result {
	plan := Plan(controller.store.List(), controller.Desired())
	result := ReconcileWithSchemas(controller.store, plan, controller.registry)
	for _, failure := range result.Failed {
		result.Errors = append(result.Errors, failure.Message)
	}
	return result
}

// ---------------------------------------------------------------------------
// The API
// ---------------------------------------------------------------------------

// Server answers the HTTP requests of the control plane.
type Server struct {
	controller *Controller
	store      *Store
	mux        *http.ServeMux
}

// NewServer creates the API.
func NewServer(controller *Controller, store *Store) *Server {
	server := &Server{controller: controller, store: store, mux: http.NewServeMux()}
	server.mux.HandleFunc("/api/resources", server.handleResources)
	server.mux.HandleFunc("/api/desired", server.handleDesired)
	server.mux.HandleFunc("/api/plan", server.handlePlan)
	server.mux.HandleFunc("/api/reconcile", server.handleReconcile)
	server.mux.HandleFunc("/healthz", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, http.StatusOK, map[string]any{"ok": true})
	})
	return server
}

func (server *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	server.mux.ServeHTTP(w, r)
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	encoder := json.NewEncoder(w)
	encoder.SetIndent("", "  ")
	_ = encoder.Encode(payload)
}

func (server *Server) handleResources(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"resources":  server.store.List(),
		"desired":    server.controller.Desired(),
		"generation": server.controller.Generation(),
	})
}

func (server *Server) handleDesired(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "use POST"})
		return
	}
	var payload struct {
		Resources []Resource `json:"resources"`
	}
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": "the body is not valid JSON"})
		return
	}
	generation, err := server.controller.SetDesired(payload.Resources)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]any{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"generation": generation})
}

func (server *Server) handlePlan(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"plan": Plan(server.store.List(), server.controller.Desired()),
	})
}

func (server *Server) handleReconcile(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		writeJSON(w, http.StatusMethodNotAllowed, map[string]any{"error": "use POST"})
		return
	}
	result := server.controller.Reconcile()
	status := http.StatusOK
	if len(result.Failed) > 0 {
		status = http.StatusConflict
	}
	writeJSON(w, status, result)
}

// ---------------------------------------------------------------------------
// Persistence
// ---------------------------------------------------------------------------

// FileStore keeps the resources in a JSON file, so a restart does not lose
// them.
type FileStore struct {
	path string
	mu   sync.Mutex
}

// NewFileStore creates a file store.
func NewFileStore(path string) *FileStore {
	return &FileStore{path: path}
}

// Load reads the resources from the file. A file that is not there yet gives
// an empty list.
func (file *FileStore) Load() ([]Resource, error) {
	file.mu.Lock()
	defer file.mu.Unlock()

	data, err := os.ReadFile(file.path)
	if os.IsNotExist(err) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var resources []Resource
	if err := json.Unmarshal(data, &resources); err != nil {
		return nil, err
	}
	return resources, nil
}

// Save writes the resources to the file.
func (file *FileStore) Save(resources []Resource) error {
	file.mu.Lock()
	defer file.mu.Unlock()

	data, err := json.MarshalIndent(resources, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(file.path, data, 0o644)
}

// ---------------------------------------------------------------------------
// A little helper for the command line
// ---------------------------------------------------------------------------

// ParseResource reads a resource from a JSON document.
func ParseResource(data []byte) (Resource, error) {
	var resource Resource
	if err := json.Unmarshal(data, &resource); err != nil {
		return Resource{}, err
	}
	if resource.Kind == "" || resource.Name == "" {
		return Resource{}, fmt.Errorf("a resource needs a kind and a name")
	}
	return resource, nil
}

// FormatResource renders a resource as JSON.
func FormatResource(resource Resource) string {
	data, _ := json.MarshalIndent(resource, "", "  ")
	return string(data)
}

// ParsePort reads a port number from a string.
func ParsePort(value string) (int, error) {
	port, err := strconv.Atoi(value)
	if err != nil || port <= 0 || port > 65535 {
		return 0, fmt.Errorf("%q is not a port number", value)
	}
	return port, nil
}
