package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"strings"
	"sync"
	"sync/atomic"
	"time"
)

// Instance is one backend for a service.
type Instance struct {
	URL     string
	Healthy bool
	// proxies holds the reverse proxy of the instance, so it is built once
	// and not on every request.
	proxies sync.Map
}

// Service groups instances under a name.
type Service struct {
	Name      string
	Instances []*Instance
	next      uint64
}

// Registry resolves service names to backends and proxies requests.
type Registry struct {
	mu       sync.RWMutex
	services map[string]*Service
	order    []string
}

// ParseConfig reads the small "services:" example format.
func ParseConfig(data string) (*Registry, error) {
	registry := &Registry{services: map[string]*Service{}}
	var current *Service
	for _, line := range strings.Split(data, "\n") {
		trimmed := strings.TrimSpace(line)
		switch {
		case trimmed == "" || strings.HasPrefix(trimmed, "#") || trimmed == "services:":
			continue
		case strings.HasPrefix(trimmed, "- name:"):
			name := strings.TrimSpace(strings.TrimPrefix(trimmed, "- name:"))
			current = &Service{Name: name}
			registry.services[name] = current
			registry.order = append(registry.order, name)
		case strings.HasPrefix(trimmed, "- http") || strings.HasPrefix(trimmed, "- https"):
			if current == nil {
				return nil, errors.New("instance before any service")
			}
			current.Instances = append(current.Instances, &Instance{URL: strings.TrimPrefix(trimmed, "- "), Healthy: true})
		}
	}
	if len(registry.services) == 0 {
		return nil, errors.New("no services found")
	}
	return registry, nil
}

func (registry *Registry) Names() []string {
	return registry.order
}

// Mark updates the health of one instance.
func (registry *Registry) Mark(service, instance string, healthy bool) error {
	registry.mu.Lock()
	defer registry.mu.Unlock()
	target, ok := registry.services[service]
	if !ok {
		return fmt.Errorf("unknown service %q", service)
	}
	for _, candidate := range target.Instances {
		if candidate.URL == instance {
			candidate.Healthy = healthy
			return nil
		}
	}
	return fmt.Errorf("unknown instance %q", instance)
}

// Pick chooses the next healthy instance in round-robin order.
//
// The counter goes up once per pick, so the instances take turns even when
// some of them are unhealthy.
func (registry *Registry) Pick(service string) (*Instance, error) {
	registry.mu.Lock()
	defer registry.mu.Unlock()
	target, ok := registry.services[service]
	if !ok {
		return nil, fmt.Errorf("unknown service %q", service)
	}
	if len(target.Instances) == 0 {
		return nil, fmt.Errorf("service %q has no instances", service)
	}

	start := int(atomic.AddUint64(&target.next, 1)-1) % len(target.Instances)
	for attempt := 0; attempt < len(target.Instances); attempt++ {
		candidate := target.Instances[(start+attempt)%len(target.Instances)]
		if candidate.Healthy {
			return candidate, nil
		}
	}
	return nil, fmt.Errorf("no healthy instance for %q", service)
}

// proxyFor returns the reverse proxy of an instance, building it the first
// time it is needed.
func (instance *Instance) proxyFor() (*httputil.ReverseProxy, error) {
	if cached, ok := instance.proxies.Load(instance.URL); ok {
		return cached.(*httputil.ReverseProxy), nil
	}
	target, err := url.Parse(instance.URL)
	if err != nil {
		return nil, err
	}
	proxy := httputil.NewSingleHostReverseProxy(target)
	actual, _ := instance.proxies.LoadOrStore(instance.URL, proxy)
	return actual.(*httputil.ReverseProxy), nil
}

// ServeHTTP routes /service/... to a healthy backend.
func (registry *Registry) ServeHTTP(writer http.ResponseWriter, request *http.Request) {
	if request.URL.Path == "/health" {
		writer.WriteHeader(http.StatusOK)
		_, _ = writer.Write([]byte("ok"))
		return
	}
	parts := strings.SplitN(strings.TrimPrefix(request.URL.Path, "/"), "/", 2)
	if len(parts) == 0 || parts[0] == "" {
		http.Error(writer, "expected /service/path", http.StatusBadRequest)
		return
	}
	instance, err := registry.Pick(parts[0])
	if err != nil {
		http.Error(writer, err.Error(), http.StatusBadGateway)
		return
	}
	proxy, err := instance.proxyFor()
	if err != nil {
		http.Error(writer, err.Error(), http.StatusBadGateway)
		return
	}
	request.URL.Path = "/" + strings.Join(parts[1:], "/")
	proxy.ServeHTTP(writer, request)
}

// ---------------------------------------------------------------------------
// Docker
// ---------------------------------------------------------------------------

// DockerClient talks to the Docker daemon over its unix socket.
type DockerClient struct {
	socket string
	http   *http.Client
}

// NewDockerClient creates a client for the Docker socket.
func NewDockerClient(socket string) *DockerClient {
	if socket == "" {
		socket = "/var/run/docker.sock"
	}
	return &DockerClient{
		socket: socket,
		http: &http.Client{
			Timeout: 5 * time.Second,
			Transport: &http.Transport{
				DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
					return (&net.Dialer{}).DialContext(ctx, "unix", socket)
				},
			},
		},
	}
}

// Container is what the Docker daemon says about a container.
type Container struct {
	ID     string            `json:"Id"`
	Names  []string          `json:"Names"`
	Image  string            `json:"Image"`
	State  string            `json:"State"`
	Labels map[string]string `json:"Labels"`
}

// ListContainers asks the daemon for the running containers.
func (client *DockerClient) ListContainers() ([]Container, error) {
	response, err := client.http.Get("http://docker/containers/json")
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("docker answered with %d", response.StatusCode)
	}
	var containers []Container
	if err := json.NewDecoder(bufio.NewReader(response.Body)).Decode(&containers); err != nil {
		return nil, err
	}
	return containers, nil
}

// DiscoverFromDocker builds the registry from the containers of the daemon.
//
// Every container with the label "foundev.service" is a service, and the
// label "foundev.port" says which port of the container the traffic goes to.
func DiscoverFromDocker(client *DockerClient) (*Registry, error) {
	containers, err := client.ListContainers()
	if err != nil {
		return nil, err
	}

	registry := &Registry{services: map[string]*Service{}}
	for _, container := range containers {
		name := container.Labels["foundev.service"]
		if name == "" {
			continue
		}
		if container.State != "" && container.State != "running" {
			continue
		}
		port := container.Labels["foundev.port"]
		if port == "" {
			port = "80"
		}
		host := container.Labels["foundev.host"]
		if host == "" {
			host = container.ID[:12]
		}
		url := fmt.Sprintf("http://%s:%s", host, port)

		service, ok := registry.services[name]
		if !ok {
			service = &Service{Name: name}
			registry.services[name] = service
			registry.order = append(registry.order, name)
		}
		service.Instances = append(service.Instances, &Instance{URL: url, Healthy: true})
	}

	if len(registry.services) == 0 {
		return nil, errors.New("no container carries the foundev.service label")
	}
	return registry, nil
}

// ---------------------------------------------------------------------------
// The command line
// ---------------------------------------------------------------------------

// exampleConfig is the small format the config file uses.
const exampleConfig = `services:
  - name: api
    instances:
      - http://127.0.0.1:9001
      - http://127.0.0.1:9002
`

func main() {
	configPath := flag.String("config", "config.yaml", "the file with the services")
	port := flag.Int("port", 8080, "the port to listen on")
	docker := flag.String("docker", "", "discover the services from the Docker daemon on this socket")
	tlsCert := flag.String("tls-cert", "", "the certificate of the server, for https")
	tlsKey := flag.String("tls-key", "", "the key of the certificate")
	flag.Parse()

	var registry *Registry
	var err error
	if *docker != "" {
		registry, err = DiscoverFromDocker(NewDockerClient(*docker))
	} else {
		data := []byte(exampleConfig)
		if *configPath != "" {
			loaded, readError := os.ReadFile(*configPath)
			if readError != nil {
				fmt.Fprintln(os.Stderr, "could not read the config:", readError)
				os.Exit(1)
			}
			data = loaded
		}
		registry, err = ParseConfig(string(data))
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}

	server := &http.Server{Addr: fmt.Sprintf(":%d", *port), Handler: registry}
	if *tlsCert != "" && *tlsKey != "" {
		fmt.Printf("the proxy is listening on https://127.0.0.1:%d\n", *port)
		if err := server.ListenAndServeTLS(*tlsCert, *tlsKey); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	fmt.Printf("the proxy is listening on http://127.0.0.1:%d\n", *port)
	if err := server.ListenAndServe(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
