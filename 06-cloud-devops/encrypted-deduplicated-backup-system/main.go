package main

import (
	"flag"
	"fmt"
	"os"
)

func main() {
	source := flag.String("source", "", "file to back up")
	restore := flag.String("restore", "", "restore a file by name from the repository")
	repoDir := flag.String("repo", "artifacts/repository", "repository directory")
	flag.Parse()

	if *restore != "" {
		repository, err := OpenRepository(*repoDir, "demo-secret")
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		data, err := repository.Restore(*restore)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		fmt.Printf("restored %s (%d bytes)\n", *restore, len(data))
		return
	}
	if *source == "" {
		fmt.Println("pass -source to back up a file or -restore to restore one")
		return
	}
	data, err := os.ReadFile(*source)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	repository, err := NewRepository(*repoDir, "demo-secret")
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	stored, err := repository.Put(*source, data)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	fmt.Printf("stored %d chunks (%d new)\n", len(stored.Chunks), stored.NewChunks)
}
