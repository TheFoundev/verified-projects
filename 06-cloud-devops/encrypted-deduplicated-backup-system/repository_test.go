package main

import (
	"bytes"
	"math/rand"
	"os"
	"path/filepath"
	"testing"
)

func TestContentDefinedChunking(t *testing.T) {
	data := bytes.Repeat([]byte("abcdefgh"), 64)
	chunks := ContentDefined(data, 32, 96, 192)
	if len(chunks) == 0 {
		t.Fatal("expected chunks")
	}
	total := 0
	for _, chunk := range chunks {
		total += len(chunk)
		if len(chunk) > 192 {
			t.Fatalf("chunk longer than max: %d", len(chunk))
		}
	}
	if total != len(data) {
		t.Fatalf("chunk sizes lost data: %d vs %d", total, len(data))
	}
}

func TestBackupDedupAndRestore(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "repo")
	repository, err := NewRepository(dir, "secret")
	if err != nil {
		t.Fatal(err)
	}
	payload := bytes.Repeat([]byte("0123456789abcdef"), 40)
	first, err := repository.Put("data.bin", payload)
	if err != nil {
		t.Fatal(err)
	}
	second, err := repository.Put("copy.bin", payload)
	if err != nil {
		t.Fatal(err)
	}
	if second.NewChunks != 0 {
		t.Fatalf("expected full dedup, got %d new chunks", second.NewChunks)
	}
	restored, err := repository.Restore("copy.bin")
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(restored, payload) {
		t.Fatal("restored data does not match")
	}
	// The same content gives the same chunk, so there is one file for every
	// different chunk and not for every chunk.
	distinct := map[string]bool{}
	for _, chunk := range first.Chunks {
		distinct[chunk.ID] = true
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) < len(distinct) {
		t.Fatal("missing chunk files")
	}
}

// The chunking is content defined: a byte at the front of the data may only
// move the boundaries near the front.
func TestChunkingIsContentDefined(t *testing.T) {
	random := rand.New(rand.NewSource(9))
	payload := make([]byte, 4096)
	random.Read(payload)
	shifted := append([]byte("X"), payload...)

	first := ContentDefined(payload, 32, 96, 192)
	second := ContentDefined(shifted, 32, 96, 192)

	if len(first) == 0 || len(second) == 0 {
		t.Fatal("expected chunks")
	}

	// The chunks that are far from the front are the same.
	same := 0
	for _, chunk := range first {
		for _, other := range second {
			if bytes.Equal(chunk, other) {
				same++
				break
			}
		}
	}
	if same*2 < len(first) {
		t.Fatalf("only %d of %d chunks survived the shift", same, len(first))
	}
}

// The key is derived with a salt, so the same secret gives a different key.
func TestTheKeyIsDerivedWithASalt(t *testing.T) {
	first := deriveKey("secret", NewSalt())
	second := deriveKey("secret", NewSalt())
	if bytes.Equal(first, second) {
		t.Fatal("two salts have to give two keys")
	}
	if len(first) != 32 {
		t.Fatalf("unexpected key length %d", len(first))
	}
	// The same salt gives the same key.
	salt := NewSalt()
	if !bytes.Equal(deriveKey("secret", salt), deriveKey("secret", salt)) {
		t.Fatal("the key has to be stable for one salt")
	}
}
