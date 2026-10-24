package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
)

// Resource is one planned infrastructure object.
type Resource struct {
	Type   string         `json:"type"`
	Name   string         `json:"name"`
	Values map[string]any `json:"values"`
}

// Estimate is the monthly cost for one resource.
type Estimate struct {
	Type    string  `json:"type"`
	Name    string  `json:"name"`
	Monthly float64 `json:"monthly"`
}

// Summary aggregates estimates.
type Summary struct {
	Resources []Estimate         `json:"resources"`
	Total     float64            `json:"total"`
	ByType    map[string]float64 `json:"by_type"`
	// ByProvider holds the total of every provider, so a plan that uses two
	// of them can be read at a glance.
	ByProvider map[string]float64 `json:"by_provider"`
	// Unpriced holds the resources whose price is not known. A cost tool
	// that says nothing about them is dangerous: a resource that is not
	// priced may still cost money.
	Unpriced []string `json:"unpriced"`
}

// childModules returns the modules of a module of a plan.
func childModules(module map[string]any) []map[string]any {
	children := []map[string]any{}
	list, _ := module["child_modules"].([]any)
	for _, item := range list {
		child, _ := item.(map[string]any)
		if child != nil {
			children = append(children, child)
		}
	}
	return children
}

// providerOf says which provider a resource belongs to.
//
// The type of the resource starts with the name of the provider, the way
// Terraform writes it.
func providerOf(resourceType string) string {
	parts := strings.SplitN(resourceType, "_", 2)
	if len(parts) < 2 {
		return "other"
	}
	return parts[0]
}

var instancePrices = map[string]float64{
	"t3.micro": 7.5, "t3.small": 15.0, "t3.medium": 30.0, "m5.large": 70.0,
}

// The prices of the machines of Google, so a plan that uses two providers
// can be read at a glance.
var googleComputePrices = map[string]float64{
	"e2-micro": 6.8, "e2-small": 13.6, "e2-medium": 27.0, "n1-standard-1": 29.5,
	"n2-standard-2": 58.0,
}

var databasePrices = map[string]float64{
	"db.t3.small": 25.0, "db.t3.medium": 50.0, "db.m5.large": 120.0,
}

// ParsePlan extracts resources from a Terraform plan document.
func ParsePlan(data []byte) ([]Resource, error) {
	var document map[string]any
	if err := json.Unmarshal(data, &document); err != nil {
		return nil, fmt.Errorf("parse plan: %w", err)
	}
	planned, _ := document["planned_values"].(map[string]any)
	root, _ := planned["root_module"].(map[string]any)
	raw, _ := root["resources"].([]any)
	if raw == nil {
		return nil, errors.New("no planned resources found")
	}

	// A plan with modules keeps the resources of a module under
	// child_modules, and they are part of the plan as well.
	for _, child := range childModules(root) {
		more, _ := child["resources"].([]any)
		raw = append(raw, more...)
	}

	resources := []Resource{}
	for _, item := range raw {
		entry, _ := item.(map[string]any)
		resource := Resource{
			Type:   stringValue(entry["type"]),
			Name:   stringValue(entry["name"]),
			Values: map[string]any{},
		}
		if values, ok := entry["values"].(map[string]any); ok {
			resource.Values = values
		}
		resources = append(resources, resource)
	}
	return resources, nil
}

func stringValue(value any) string {
	text, _ := value.(string)
	return text
}

func numberValue(value any) float64 {
	switch typed := value.(type) {
	case float64:
		return typed
	case int:
		return float64(typed)
	case json.Number:
		parsed, _ := typed.Float64()
		return parsed
	default:
		return 0
	}
}

// EstimateResource returns the monthly cost for one resource.
func EstimateResource(resource Resource) Estimate {
	monthly := 0.0
	count := numberValue(resource.Values["count"])
	if count == 0 {
		count = 1
	}
	switch resource.Type {
	case "aws_instance":
		monthly = instancePrices[stringValue(resource.Values["instance_type"])] * count
	case "aws_db_instance":
		monthly = databasePrices[stringValue(resource.Values["instance_class"])]
	case "aws_s3_bucket":
		storage := numberValue(resource.Values["storage_gb"])
		if storage == 0 {
			storage = 10
		}
		monthly = storage * 0.023
	case "aws_ebs_volume":
		monthly = numberValue(resource.Values["size"]) * 0.08
	case "google_compute_instance":
		monthly = googleComputePrices[stringValue(resource.Values["machine_type"])] * count
	case "google_sql_database_instance":
		monthly = 30.0 * count
	}
	return Estimate{Type: resource.Type, Name: resource.Name, Monthly: monthly}
}

// EstimateAll aggregates per-resource estimates.
func EstimateAll(resources []Resource) Summary {
	summary := Summary{
		Resources:  []Estimate{},
		ByType:     map[string]float64{},
		ByProvider: map[string]float64{},
		Unpriced:   []string{},
	}
	for _, resource := range resources {
		estimate := EstimateResource(resource)
		summary.Resources = append(summary.Resources, estimate)
		summary.Total += estimate.Monthly
		summary.ByType[resource.Type] += estimate.Monthly
		provider := providerOf(resource.Type)
		summary.ByProvider[provider] += estimate.Monthly
		if estimate.Monthly == 0 {
			// The price of the resource is not known, so it is named: a
			// resource that costs nothing may still cost money.
			summary.Unpriced = append(summary.Unpriced, resource.Type+"/"+resource.Name)
		}
	}
	sort.Strings(summary.Unpriced)
	return summary
}
