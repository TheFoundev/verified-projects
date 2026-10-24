package main

import (
	"encoding/json"
	"testing"
)

func planWith(resources ...map[string]any) []byte {
	list := []any{}
	for _, resource := range resources {
		list = append(list, resource)
	}
	document := map[string]any{
		"planned_values": map[string]any{
			"root_module": map[string]any{"resources": list},
		},
	}
	data, _ := json.Marshal(document)
	return data
}

func TestTheResourcesOfThePlanAreRead(t *testing.T) {
	resources, err := ParsePlan(planWith(
		map[string]any{"type": "aws_instance", "name": "web", "values": map[string]any{"instance_type": "t3.micro"}},
	))
	if err != nil {
		t.Fatal(err)
	}
	if len(resources) != 1 {
		t.Fatalf("unexpected resources %v", resources)
	}
	if resources[0].Type != "aws_instance" {
		t.Fatalf("unexpected resource %v", resources[0])
	}
}

func TestTheResourcesOfAModuleAreRead(t *testing.T) {
	// A plan with a module keeps its resources under child_modules.
	document := map[string]any{
		"planned_values": map[string]any{
			"root_module": map[string]any{
				"resources": []any{},
				"child_modules": []any{
					map[string]any{
						"resources": []any{
							map[string]any{"type": "aws_instance", "name": "inside", "values": map[string]any{"instance_type": "t3.micro"}},
						},
					},
				},
			},
		},
	}
	data, _ := json.Marshal(document)
	resources, err := ParsePlan(data)
	if err != nil {
		t.Fatal(err)
	}
	if len(resources) != 1 {
		t.Fatalf("the resources of the module are missing: %v", resources)
	}
	if resources[0].Name != "inside" {
		t.Fatalf("unexpected resource %v", resources[0])
	}
}

func TestTheSummaryHasATotalPerTypeAndPerProvider(t *testing.T) {
	resources, err := ParsePlan(planWith(
		map[string]any{"type": "aws_instance", "name": "web", "values": map[string]any{"instance_type": "t3.micro"}},
		map[string]any{"type": "aws_instance", "name": "worker", "values": map[string]any{"instance_type": "t3.small"}},
		map[string]any{"type": "google_compute_instance", "name": "gcp", "values": map[string]any{"machine_type": "n1-standard-1"}},
	))
	if err != nil {
		t.Fatal(err)
	}
	summary := EstimateAll(resources)

	if summary.Total <= 0 {
		t.Fatalf("unexpected total %v", summary.Total)
	}
	if summary.ByType["aws_instance"] <= 0 {
		t.Fatalf("unexpected total per type %v", summary.ByType)
	}
	if summary.ByProvider["aws"] <= 0 {
		t.Fatalf("unexpected total per provider %v", summary.ByProvider)
	}
	if summary.ByProvider["google"] <= 0 {
		t.Fatalf("unexpected total per provider %v", summary.ByProvider)
	}
}

func TestTheResourcesWithoutAPriceAreNamed(t *testing.T) {
	resources, err := ParsePlan(planWith(
		map[string]any{"type": "aws_eks_cluster", "name": "cluster", "values": map[string]any{}},
	))
	if err != nil {
		t.Fatal(err)
	}
	summary := EstimateAll(resources)

	if len(summary.Unpriced) == 0 {
		t.Fatal("a resource without a price has to be named")
	}
}

func TestTheProviderIsTakenFromTheType(t *testing.T) {
	if providerOf("aws_instance") != "aws" {
		t.Fatal("the provider of an aws resource is aws")
	}
	if providerOf("google_compute_instance") != "google" {
		t.Fatal("the provider of a google resource is google")
	}
}
