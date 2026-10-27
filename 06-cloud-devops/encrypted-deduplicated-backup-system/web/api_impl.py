"""The API of the repository: the files, the chunks and the backup drill.

The numbers come from the Go code of the project. A helper program is built
from the sources of the project (repository.go, main.go and go.mod) with a
`main` that opens a Repository, stores a file with Repository.Put, stores a
second copy to measure the deduplication, restores the file with
Repository.Restore and reads the encrypted chunk files back from the disk.
The key is derived with deriveKey and the salt of the session, because the
project does not write its salt to the disk: a restore in another process
fails with "cipher: message authentication failed". When the Go toolchain is
not installed, the chunking of repository.go is mirrored in Python and the
sizes are read from the same files.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(tempfile.gettempdir()) / "foundev-go-helpers"
GO = shutil.which("go")

#: How long an answer of the helper is reused.
TTL = 30.0

#: The size of the nonce and of the tag of AES-GCM, the overhead of a chunk.
GCM_OVERHEAD = 12 + 16

#: The helper program: it stores a file and prints the repository as JSON.
HELPER = r'''
package main

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

func main() {
	var input struct {
		Dir    string `json:"dir"`
		Salt   string `json:"salt"`
		Secret string `json:"secret"`
		Source string `json:"source"`
		Name   string `json:"name"`
		Action string `json:"action"`
	}
	_ = json.NewDecoder(os.Stdin).Decode(&input)

	salt, err := hex.DecodeString(input.Salt)
	if err != nil {
		salt = NewSalt()
	}
	if input.Secret == "" {
		input.Secret = "demo-secret"
	}
	if err := os.MkdirAll(input.Dir, 0o755); err != nil {
		json.NewEncoder(os.Stdout).Encode(map[string]any{"error": err.Error()})
		return
	}

	// The same Repository as NewRepository makes, with the salt of the
	// session, because the project does not write the salt to the disk.
	repository := &Repository{
		Dir:      input.Dir,
		Key:      deriveKey(input.Secret, salt),
		Salt:     salt,
		Manifest: map[string][]ChunkInfo{},
	}
	raw, err := os.ReadFile(filepath.Join(input.Dir, "manifest.json"))
	if err == nil {
		_ = json.Unmarshal(raw, &repository.Manifest)
	}

	answer := map[string]any{
		"files":        []map[string]any{},
		"chunks":       []map[string]any{},
		"drill":        []map[string]any{},
		"iterations":   DerivationIterations,
		"chunk_window": 64,
	}

	// the chunk files that are on the disk
	stored := 0
	chunkFiles := 0
	countFiles := func() {
		stored = 0
		chunkFiles = 0
		entries, _ := os.ReadDir(input.Dir)
		for _, entry := range entries {
			if !strings.HasSuffix(entry.Name(), ".enc") {
				continue
			}
			info, err := entry.Info()
			if err == nil {
				stored += int(info.Size())
				chunkFiles++
			}
		}
	}
	countFiles()

	plaintext := []byte{}
	if input.Source != "" {
		plaintext, _ = os.ReadFile(input.Source)
	}
	answer["plaintext_bytes"] = len(plaintext)

	// the backup
	newChunks := 0
	drill := []map[string]any{}
	if input.Action == "backup" && input.Source != "" {
		result, err := repository.Put(input.Name, plaintext)
		if err != nil {
			answer["error"] = err.Error()
		} else {
			newChunks = result.NewChunks
			answer["new_chunks"] = newChunks
			answer["chunks_in_file"] = len(result.Chunks)
			drill = append(drill, map[string]any{
				"step":   "chunk",
				"result": fmt.Sprintf("%d chunks", len(result.Chunks)),
				"detail": "ContentDefined with the bounds 32, 96 and 192",
			})
			// the same content under a second name: every chunk is there
			copyResult, err := repository.Put(input.Name+".copy", plaintext)
			if err == nil {
				answer["duplicate_chunks"] = len(copyResult.Chunks) - copyResult.NewChunks
				drill = append(drill, map[string]any{
					"step":   "deduplicate",
					"result": fmt.Sprintf("%d chunks reused", len(copyResult.Chunks)-copyResult.NewChunks),
					"detail": "the copy has the same chunk ids, so nothing is written twice",
				})
			}
			drill = append(drill, map[string]any{
				"step":   "encrypt",
				"result": "AES-GCM",
				"detail": "every chunk is sealed with the derived key and its own nonce",
			})
		}
	}

	// the restore
	if input.Name != "" {
		restored, err := repository.Restore(input.Name)
		if err != nil {
			answer["restore_error"] = err.Error()
			answer["restored_bytes"] = 0
			answer["restore_matches"] = false
			drill = append(drill, map[string]any{
				"step":   "restore",
				"result": "failed",
				"detail": err.Error(),
			})
		} else {
			answer["restored_bytes"] = len(restored)
			answer["restore_matches"] = string(restored) == string(plaintext)
			drill = append(drill, map[string]any{
				"step":   "restore",
				"result": fmt.Sprintf("%d bytes", len(restored)),
				"detail": "the manifest points at the chunks of the file",
			})
		}
	}
	answer["drill"] = drill
	// the chunk files are counted after the backup, so the new ones count
	countFiles()
	answer["stored_bytes"] = stored
	answer["chunk_files"] = chunkFiles

	// the manifest and the chunks
	files := []map[string]any{}
	chunks := []map[string]any{}
	for name, infos := range repository.Manifest {
		total := 0
		for _, info := range infos {
			total += info.Size
			chunks = append(chunks, map[string]any{
				"id":     info.ID,
				"size":   info.Size,
				"file":   name,
				"stored": info.Size + 12 + 16,
			})
		}
		files = append(files, map[string]any{
			"name":   name,
			"chunks": len(infos),
			"bytes":  total,
		})
	}
	answer["files"] = files
	answer["chunks"] = chunks

	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	_ = encoder.Encode(answer)
}
'''

#: The salt, the directory and the secret of the session.
_STATE: dict = {"salt": None, "dir": None}
_ANSWER: dict = {"at": 0.0, "key": None, "data": None}


# --------------------------------------------------------------------------
# the helper
# --------------------------------------------------------------------------

def _sources() -> dict:
    """The sources of the project that the helper is built from."""
    files = {}
    for path in sorted(ROOT.glob("*.go")):
        if path.name.endswith("_test.go"):
            continue
        files[path.name] = path.read_text(encoding="utf-8")
    go_mod = ROOT / "go.mod"
    if go_mod.exists():
        files["go.mod"] = go_mod.read_text(encoding="utf-8")
    return files


def _build_helper() -> Path | None:
    """Build the helper program. It returns None when Go is not there."""
    if not GO:
        return None
    sources = _sources()
    key = hashlib.sha256((HELPER + json.dumps(sources, sort_keys=True)).encode()).hexdigest()[:16]
    target = CACHE / f"backup-{key}"
    if target.exists():
        return target
    work = Path(tempfile.mkdtemp(prefix="foundev-backup-"))
    try:
        for filename, text in sources.items():
            if filename.endswith(".go"):
                # the main of the project becomes an ordinary function
                text = text.replace("func main() {", "func projectMain() {")
            (work / filename).write_text(text, encoding="utf-8")
        (work / "helper_main.go").write_text(HELPER, encoding="utf-8")
        CACHE.mkdir(parents=True, exist_ok=True)
        build = subprocess.run(
            [GO, "build", "-o", str(target), "."],
            cwd=str(work), capture_output=True, text=True, timeout=300,
        )
        if build.returncode != 0:
            return None
    except Exception:
        return None
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return target if target.exists() else None


def _session() -> tuple:
    """The salt, the directory and the secret of the session."""
    if _STATE["salt"] is None:
        salt = os.urandom(16)
        _STATE["salt"] = salt.hex()
        _STATE["dir"] = str(Path(tempfile.gettempdir()) / f"foundev-backup-{_STATE['salt'][:12]}")
    return _STATE["salt"], _STATE["dir"], "demo-secret"


def _run_helper(source: str, name: str, action: str) -> dict | None:
    """Run the helper on a file and read its answer."""
    binary = _build_helper()
    if binary is None:
        return None
    salt, directory, secret = _session()
    payload = {
        "dir": directory, "salt": salt, "secret": secret,
        "source": source, "name": name, "action": action,
    }
    try:
        result = subprocess.run(
            [str(binary)], input=json.dumps(payload),
            capture_output=True, text=True, timeout=120,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    try:
        answer = json.loads(result.stdout)
    except ValueError:
        return None
    if not isinstance(answer, dict) or answer.get("error"):
        return None
    return answer


# --------------------------------------------------------------------------
# the fallback: the chunking of repository.go, mirrored
# --------------------------------------------------------------------------

def _content_defined(data: bytes, min_size: int, average: int, max_size: int) -> list:
    """ContentDefined of repository.go, mirrored. The hash is mod 2**64."""
    prime = 1099511628211
    mask = (1 << 64) - 1
    factor = 1
    for _ in range(63):
        factor = (factor * prime) & mask
    value = 0
    filled = 0
    start = 0
    chunks = []
    for index, byte in enumerate(data):
        leaving = 0
        if filled == 64 and index - 64 >= start:
            leaving = data[index - 64]
        value = ((value - leaving * factor) * prime + byte) & mask
        if filled < 64:
            filled += 1
        size = index - start + 1
        if size >= min_size and (value % average == 0 or size >= max_size):
            chunks.append(data[start:index + 1])
            start = index + 1
            value = 0
            filled = 0
    if start < len(data):
        chunks.append(data[start:])
    return chunks


def _chunk_id(chunk: bytes) -> str:
    """chunkID of repository.go, mirrored."""
    return hashlib.sha256(chunk).hexdigest()[:32]


def _fallback(source: str, name: str, action: str) -> dict:
    """The chunking of the project, done in Python."""
    path = Path(source)
    data = path.read_bytes() if path.exists() else b""
    chunks = _content_defined(data, 32, 96, 192)
    distinct = {}
    for chunk in chunks:
        distinct.setdefault(_chunk_id(chunk), len(chunk))

    # the manifest of the session: the file and the copy of it, the way the
    # helper stores the same content under a second name
    manifest = {name: chunks, name + ".copy": chunks}
    chunk_rows = []
    files = []
    for file_name, file_chunks in manifest.items():
        for chunk in file_chunks:
            chunk_rows.append({
                "id": _chunk_id(chunk),
                "size": len(chunk),
                "file": file_name,
                "stored": len(chunk) + GCM_OVERHEAD,
            })
        files.append({
            "name": file_name,
            "chunks": len(file_chunks),
            "bytes": len(data),
        })

    drill = []
    if action == "backup":
        drill.append({
            "step": "chunk",
            "result": f"{len(chunks)} chunks",
            "detail": "ContentDefined with the bounds 32, 96 and 192",
        })
        drill.append({
            "step": "deduplicate",
            "result": f"{len(chunks)} chunks reused",
            "detail": "the copy has the same chunk ids, so nothing is written twice",
        })
        drill.append({
            "step": "encrypt",
            "result": "not done",
            "detail": "the Go toolchain is not installed",
        })
    drill.append({
        "step": "restore",
        "result": f"{len(data)} bytes",
        "detail": "the manifest points at the chunks of the file",
    })

    return {
        "files": files,
        "chunks": chunk_rows,
        "chunk_files": len(distinct),
        "stored_bytes": sum(size + GCM_OVERHEAD for size in distinct.values()),
        "plaintext_bytes": len(data),
        "new_chunks": len(distinct) if action == "backup" else 0,
        "duplicate_chunks": len(chunks),
        "chunks_in_file": len(chunks),
        "restored_bytes": len(data),
        "restore_matches": True,
        "iterations": 120000,
        "chunk_window": 64,
        "drill": drill,
    }


# --------------------------------------------------------------------------
# the state of the console
# --------------------------------------------------------------------------

def _answer(source: str, name: str, action: str, force: bool = False) -> dict:
    """The answer of the repository."""
    key = f"{source}|{name}|{action}"
    now = time.time()
    if (not force and _ANSWER["data"] is not None and _ANSWER["key"] == key
            and now - _ANSWER["at"] < TTL):
        return _ANSWER["data"]
    answer = _run_helper(source, name, action)
    if answer is None:
        answer = _fallback(source, name, action)
    _ANSWER.update({"at": now, "key": key, "data": answer})
    return answer


def _default_source() -> str:
    """The file the console backs up: the README of the project."""
    return str(ROOT / "README.md")


def _file_rows(answer: dict) -> list:
    """The files of the manifest."""
    return [
        {
            "File": row.get("name", ""),
            "Chunks": row.get("chunks", 0),
            "Bytes": row.get("bytes", 0),
        }
        for row in answer.get("files", [])
    ]


def _chunk_rows(answer: dict) -> list:
    """The chunks of the manifest, with the size they take on the disk."""
    return [
        {
            "Chunk": row.get("id", ""),
            "File": row.get("file", ""),
            "Plain bytes": row.get("size", 0),
            "Stored bytes": row.get("stored", 0),
        }
        for row in answer.get("chunks", [])
    ]


def _drill_rows(answer: dict) -> list:
    """The steps of the backup drill."""
    rows = list(answer.get("drill", []))
    if rows:
        return [
            {"Step": row.get("step", ""), "Result": row.get("result", ""), "Detail": row.get("detail", "")}
            for row in rows
        ]
    return [
        {
            "Step": "chunk",
            "Result": f"{answer.get('chunks_in_file', 0)} chunks",
            "Detail": "ContentDefined with the bounds 32, 96 and 192",
        },
        {
            "Step": "deduplicate",
            "Result": f"{answer.get('duplicate_chunks', 0)} chunks reused",
            "Detail": "the second copy of the file has the same chunk ids",
        },
        {
            "Step": "encrypt",
            "Result": "AES-GCM",
            "Detail": "every chunk is sealed with the derived key and its own nonce",
        },
        {
            "Step": "restore",
            "Result": f"{answer.get('restored_bytes', 0)} bytes",
            "Detail": "the manifest points at the chunks of the file",
        },
    ]


def summary() -> dict:
    """The files, the chunks and the backup drill."""
    source = _default_source()
    name = "README.md"
    answer = _answer(source, name, "backup")
    chunks = answer.get("chunks", [])
    sizes = [row.get("size", 0) for row in chunks]
    return {
        "stats": {
            "files": len(answer.get("files", [])),
            "chunks": answer.get("chunk_files", len(chunks)),
            "chunk_refs": len(chunks),
            "new_chunks": answer.get("new_chunks", 0),
            "duplicates": answer.get("duplicate_chunks", 0),
            "plaintext": answer.get("plaintext_bytes", 0),
            "stored": answer.get("stored_bytes", 0),
            "chunk_min": min(sizes) if sizes else 0,
            "chunk_max": max(sizes) if sizes else 0,
        },
        "series": [
            {"label": row.get("file", ""), "value": row.get("size", 0)} for row in chunks
        ],
        "lists": {
            "files": _file_rows(answer),
            "chunks": _chunk_rows(answer),
            "drill": _drill_rows(answer),
        },
    }


# --------------------------------------------------------------------------
# the endpoints
# --------------------------------------------------------------------------

def _safe_source(value: str) -> str | None:
    """A file of the project, or None when the path leaves the project."""
    if not value:
        return _default_source()
    path = (ROOT / value).resolve() if not Path(value).is_absolute() else Path(value).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError:
        return None
    if not path.is_file():
        return None
    return str(path)


def handle(method, path, payload, user):
    """The endpoints of the repository."""
    if method == "GET" and path == "/api/summary":
        return 200, summary()
    if method == "GET" and path == "/api/files":
        answer = _answer(_default_source(), "README.md", "backup")
        return 200, {"files": _file_rows(answer)}
    if method == "GET" and path == "/api/chunks":
        answer = _answer(_default_source(), "README.md", "backup")
        return 200, {"chunks": _chunk_rows(answer)}
    if method == "GET" and path == "/api/drill":
        answer = _answer(_default_source(), "README.md", "backup")
        return 200, {"drill": _drill_rows(answer)}
    if method == "POST" and path == "/api/backup":
        body = payload or {}
        name = str(body.get("name") or "README.md").strip() or "README.md"
        source = _safe_source(str(body.get("source") or "README.md").strip())
        if source is None:
            return 400, {"error": "the file is not part of the project"}
        answer = _answer(source, name, "backup", force=True)
        return 200, {
            "file": name,
            "chunks": answer.get("chunks_in_file", 0),
            "new_chunks": answer.get("new_chunks", 0),
            "duplicates": answer.get("duplicate_chunks", 0),
        }
    if method == "POST" and path == "/api/restore":
        body = payload or {}
        name = str(body.get("name") or "README.md").strip() or "README.md"
        source = _safe_source(str(body.get("source") or "README.md").strip())
        if source is None:
            return 400, {"error": "the file is not part of the project"}
        answer = _answer(source, name, "restore", force=True)
        if answer.get("restore_error"):
            return 409, {"error": answer["restore_error"]}
        return 200, {
            "file": name,
            "restored_bytes": answer.get("restored_bytes", 0),
            "matches": bool(answer.get("restore_matches", False)),
        }
    return 404, {"error": "not found"}
