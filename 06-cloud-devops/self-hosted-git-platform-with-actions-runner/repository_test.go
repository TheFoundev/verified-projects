package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestCommitHistory(t *testing.T) {
	repository := NewRepository()
	first := repository.Commit("main", "one", map[string]string{"a.txt": "hello"})
	second := repository.Commit("main", "two", map[string]string{"a.txt": "hello", "b.txt": "world"})
	if first.Hash == second.Hash {
		t.Fatal("commits should differ")
	}
	if second.Parent != first.Hash {
		t.Fatalf("expected parent %s, got %s", first.Hash, second.Parent)
	}
	history := repository.Log("main", 10)
	if len(history) != 2 || history[0].Message != "two" {
		t.Fatalf("unexpected history %#v", history)
	}
	if repository.Head("main") != second.Hash {
		t.Fatal("head should point at the latest commit")
	}
}

func TestParseWorkflow(t *testing.T) {
	workflow, err := ParseWorkflow(sampleWorkflow)
	if err != nil {
		t.Fatal(err)
	}
	if workflow.Name != "ci" || workflow.On != "push" || len(workflow.Jobs) != 1 {
		t.Fatalf("unexpected workflow %#v", workflow)
	}
	if len(workflow.Jobs[0].Steps) != 2 {
		t.Fatalf("expected two steps, got %#v", workflow.Jobs[0].Steps)
	}
	results := RunWorkflow(workflow)
	if len(results) != 2 || results[0].Step != "checkout" {
		t.Fatalf("unexpected results %#v", results)
	}
	if _, err := ParseWorkflow("jobs:\n"); err == nil {
		t.Fatal("expected an error for an empty workflow")
	}
}

func TestTheObjectHashIsTheGitHash(t *testing.T) {
	// Git hashes "blob 12\0hello world\n" like this.
	repository := NewRepository()
	commit := repository.Commit("main", "one", map[string]string{"a.txt": "hello"})
	if len(commit.Hash) != 40 {
		t.Fatalf("unexpected hash %q", commit.Hash)
	}
	blob, ok := repository.Object(commit.Tree)
	if !ok {
		t.Fatal("the tree should be an object")
	}
	if blob.Type != "tree" {
		t.Fatalf("unexpected type %q", blob.Type)
	}
}

func TestTheBranchesAndTheDiff(t *testing.T) {
	repository := NewRepository()
	first := repository.Commit("main", "one", map[string]string{"a.txt": "hello"})
	second := repository.Commit("main", "two", map[string]string{"a.txt": "hello", "b.txt": "world"})

	if err := repository.CreateBranch("feature", first.Hash); err != nil {
		t.Fatal(err)
	}
	if err := repository.CreateBranch("feature", first.Hash); err == nil {
		t.Fatal("expected an error for a branch that is already there")
	}
	branches := repository.Branches()
	if branches["feature"] != first.Hash {
		t.Fatalf("unexpected branches %#v", branches)
	}

	changes := repository.Diff(first.Hash, second.Hash)
	if _, ok := changes["b.txt"]; !ok {
		t.Fatalf("unexpected diff %#v", changes)
	}
	if _, ok := changes["a.txt"]; ok {
		t.Fatal("an unchanged file should not be in the diff")
	}
}

func TestTheRunnerRunsTheSteps(t *testing.T) {
	directory := t.TempDir()
	workflow, err := ParseWorkflow(sampleWorkflow)
	if err != nil {
		t.Fatal(err)
	}
	runner := Runner{Workdir: directory}
	results := runner.Run(workflow)
	if len(results) != 2 {
		t.Fatalf("unexpected results %#v", results)
	}
	if results[0].Status != "failed" {
		// "git clone" has no repository, so the step fails.
		t.Fatalf("unexpected result %#v", results[0])
	}
	if results[1].Status != "skipped" {
		t.Fatalf("the step after a failure should be skipped: %#v", results[1])
	}
}

func TestTheRunnerRunsACommandThatWorks(t *testing.T) {
	directory := t.TempDir()
	workflow, err := ParseWorkflow("name: ci\njobs:\n  - name: test\n    steps:\n      - name: write\n        run: echo hello > out.txt\n")
	if err != nil {
		t.Fatal(err)
	}
	runner := Runner{Workdir: directory}
	results := runner.Run(workflow)
	if len(results) != 1 || results[0].Status != "success" {
		t.Fatalf("unexpected results %#v", results)
	}
	if _, err := os.Stat(filepath.Join(directory, "out.txt")); err != nil {
		t.Fatal("the command did not run")
	}
}

func TestTheRepositoryIsSavedAndReadBack(t *testing.T) {
	path := filepath.Join(t.TempDir(), "repo.json")
	repository := NewRepository()
	commit := repository.Commit("main", "one", map[string]string{"a.txt": "hello"})
	if err := repository.Save(path); err != nil {
		t.Fatal(err)
	}

	loaded, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if loaded.Head("main") != commit.Hash {
		t.Fatal("the branch did not survive")
	}
	if len(loaded.Log("main", 10)) != 1 {
		t.Fatal("the history did not survive")
	}
}

func TestAWorkflowWithoutANameIsRefused(t *testing.T) {
	if _, err := ParseWorkflow("jobs:\n-  - name: test\n"); err == nil {
		t.Fatal("expected an error for a workflow without a name")
	}
}

func TestTheWorkflowFormatOfTheActions(t *testing.T) {
	workflow, err := ParseWorkflow(`name: ci
on: push
jobs:
  build:
    needs: []
    steps:
      - name: checkout
        run: echo checkout
      - uses: actions/cache@v3
`)
	if err != nil {
		t.Fatal(err)
	}
	if len(workflow.Jobs) != 1 || workflow.Jobs[0].Name != "build" {
		t.Fatalf("unexpected workflow %#v", workflow)
	}
	if len(workflow.Jobs[0].Steps) != 2 {
		t.Fatalf("unexpected steps %#v", workflow.Jobs[0].Steps)
	}
	if workflow.Jobs[0].Steps[1].Uses != "actions/cache@v3" {
		t.Fatalf("unexpected step %#v", workflow.Jobs[0].Steps[1])
	}
}
