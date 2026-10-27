package main

import (
	"bytes"
	"crypto/aes"
	"crypto/cipher"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
)

// ChunkInfo records one stored chunk.
type ChunkInfo struct {
	ID   string `json:"id"`
	Size int    `json:"size"`
}

// PutResult summarises one backup.
type PutResult struct {
	Chunks    []ChunkInfo `json:"chunks"`
	NewChunks int         `json:"new_chunks"`
}

// Repository stores encrypted chunks in a directory.
type Repository struct {
	Dir string
	Key []byte
	// Salt is kept with the repository, so the same secret gives a
	// different key on every machine.
	Salt     []byte
	Manifest map[string][]ChunkInfo
}

// The parameters of the key derivation.
const (
	// SaltSize is the length of the salt.
	SaltSize = 16
	// DerivationIterations is how often the secret is hashed.
	DerivationIterations = 120000
)

// NewSalt returns a new random salt.
func NewSalt() []byte {
	salt := make([]byte, SaltSize)
	if _, err := rand.Read(salt); err != nil {
		return make([]byte, SaltSize)
	}
	return salt
}

// deriveKey turns a secret and a salt into a key.
//
// The key is derived with PBKDF2, so the same secret gives a different key
// with every salt and a dictionary of secrets does not help against a stored
// key.
func deriveKey(secret string, salt []byte) []byte {
	return pbkdf2Key([]byte(secret), salt, DerivationIterations, 32)
}

// pbkdf2Key is PBKDF2 with HMAC-SHA256.
func pbkdf2Key(password, salt []byte, iterations, length int) []byte {
	blocks := (length + 31) / 32
	result := make([]byte, 0, blocks*32)
	for block := 1; block <= blocks; block++ {
		mac := hmac.New(sha256.New, password)
		mac.Write(salt)
		mac.Write([]byte{byte(block >> 24), byte(block >> 16), byte(block >> 8), byte(block)})
		current := mac.Sum(nil)
		result = append(result, current...)
		for round := 1; round < iterations; round++ {
			mac.Reset()
			mac.Write(current)
			current = mac.Sum(nil)
			for index := range current {
				result[(block-1)*32+index] ^= current[index]
			}
		}
	}
	return result[:length]
}

// ContentDefined splits data on rolling hash boundaries.
//
// The hash is a rolling one over the last bytes of the chunk. It is started
// again from the same basis at every boundary, so the same bytes always land
// in the same chunk, whatever is around them. A hash that was seeded with
// the place of the chunk would move every boundary of the file when a byte
// was inserted at the front.

// The window of the rolling hash.
const chunkWindow = 64

// ContentDefined splits data on rolling hash boundaries.
//
// The hash is a polynomial hash over the last bytes of the chunk, so the
// boundary of a chunk only depends on the bytes around it and not on the
// place of the chunk in the file. That is what makes the deduplication work:
// a byte that is inserted at the front only moves the boundaries near the
// front.
func ContentDefined(data []byte, min, average, max int) [][]byte {
	var chunks [][]byte
	const prime = uint64(1099511628211)

	// prime^(window-1), the weight of the byte that leaves the window.
	var factor uint64 = 1
	for i := 0; i < chunkWindow-1; i++ {
		factor *= prime
	}

	hash := uint64(0)
	filled := 0
	start := 0

	for index, value := range data {
		// The byte that leaves the window.
		var leaving uint64
		if filled == chunkWindow && index-chunkWindow >= start {
			leaving = uint64(data[index-chunkWindow])
		}
		hash = (hash-leaving*factor)*prime + uint64(value)
		if filled < chunkWindow {
			filled++
		}

		size := index - start + 1
		if size >= min && (hash%uint64(average) == 0 || size >= max) {
			chunks = append(chunks, data[start:index+1])
			start = index + 1
			hash = 0
			filled = 0
		}
	}

	if start < len(data) {
		chunks = append(chunks, data[start:])
	}
	return chunks
}

func chunkID(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:16])
}

func seal(key, plaintext []byte) ([]byte, error) {
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	nonce := make([]byte, aead.NonceSize())
	if _, err := rand.Read(nonce); err != nil {
		return nil, err
	}
	return aead.Seal(nonce, nonce, plaintext, nil), nil
}

func openSealed(key, data []byte) ([]byte, error) {
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	aead, err := cipher.NewGCM(block)
	if err != nil {
		return nil, err
	}
	if len(data) < aead.NonceSize() {
		return nil, errors.New("chunk too short")
	}
	return aead.Open(nil, data[:aead.NonceSize()], data[aead.NonceSize():], nil)
}

// NewRepository creates a repository directory.
func NewRepository(dir, secret string) (*Repository, error) {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	salt := NewSalt()
	return &Repository{Dir: dir, Key: deriveKey(secret, salt), Salt: salt, Manifest: map[string][]ChunkInfo{}}, nil
}

// OpenRepository loads an existing manifest.
func OpenRepository(dir, secret string) (*Repository, error) {
	repository, err := NewRepository(dir, secret)
	if err != nil {
		return nil, err
	}
	raw, err := os.ReadFile(filepath.Join(dir, "manifest.json"))
	if err != nil {
		return repository, err
	}
	if err := json.Unmarshal(raw, &repository.Manifest); err != nil {
		return nil, err
	}
	return repository, nil
}

// Put chunks, deduplicates and encrypts a file.
func (repository *Repository) Put(name string, data []byte) (PutResult, error) {
	result := PutResult{Chunks: []ChunkInfo{}}
	for _, chunk := range ContentDefined(data, 32, 96, 192) {
		id := chunkID(chunk)
		path := filepath.Join(repository.Dir, id+".enc")
		if _, err := os.Stat(path); errors.Is(err, os.ErrNotExist) {
			sealed, err := seal(repository.Key, chunk)
			if err != nil {
				return result, err
			}
			if err := os.WriteFile(path, sealed, 0o600); err != nil {
				return result, err
			}
			result.NewChunks++
		}
		result.Chunks = append(result.Chunks, ChunkInfo{ID: id, Size: len(chunk)})
	}
	repository.Manifest[name] = result.Chunks
	if err := repository.Save(); err != nil {
		return result, err
	}
	return result, nil
}

// Restore reassembles a file from its chunks.
func (repository *Repository) Restore(name string) ([]byte, error) {
	infos, ok := repository.Manifest[name]
	if !ok {
		return nil, fmt.Errorf("unknown file %q", name)
	}
	var output bytes.Buffer
	for _, info := range infos {
		raw, err := os.ReadFile(filepath.Join(repository.Dir, info.ID+".enc"))
		if err != nil {
			return nil, err
		}
		plaintext, err := openSealed(repository.Key, raw)
		if err != nil {
			return nil, err
		}
		output.Write(plaintext)
	}
	return output.Bytes(), nil
}

// Save writes the manifest next to the chunks.
func (repository *Repository) Save() error {
	raw, err := json.MarshalIndent(repository.Manifest, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(repository.Dir, "manifest.json"), raw, 0o644)
}
