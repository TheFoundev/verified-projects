"""The API of the broker: the topics, the messages and the consumers.

The numbers come from the Go code of the project. A helper program is built
from the sources of the project (queue.go, main.go and go.mod) with a `main`
that makes a Broker with NewBroker, publishes the messages of the topics with
Broker.Publish, reads them back with Broker.Fetch, polls them with
Consumer.Poll, acknowledges the last one with Consumer.Ack and gives the
partition of every key with Broker.PartitionOf. The messages of the "orders"
topic are the two of the demo of main.go. When the Go toolchain is not
installed, the same code is mirrored in Python.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(tempfile.gettempdir()) / "foundev-go-helpers"
GO = shutil.which("go")

#: How long an answer of the helper is reused.
TTL = 20.0

#: The helper program: it publishes the messages and prints the broker.
HELPER = r'''
package main

import (
	"encoding/json"
	"os"
	"sort"
)

func main() {
	var input struct {
		Topics []struct {
			Name     string `json:"name"`
			Messages []struct {
				Key     string `json:"key"`
				Payload string `json:"payload"`
			} `json:"messages"`
		} `json:"topics"`
		Retention int `json:"retention"`
		Action    string `json:"action"`
		Message   struct {
			Topic   string `json:"topic"`
			Key     string `json:"key"`
			Payload string `json:"payload"`
		} `json:"message"`
	}
	_ = json.NewDecoder(os.Stdin).Decode(&input)
	if input.Retention <= 0 {
		input.Retention = 100
	}

	broker := NewBroker(input.Retention)
	names := []string{}
	for _, topic := range input.Topics {
		names = append(names, topic.Name)
		for _, message := range topic.Messages {
			broker.Publish(topic.Name, message.Key, message.Payload)
		}
	}

	// a message that is published through the API
	published := map[string]any{}
	if input.Action == "publish" && input.Message.Topic != "" {
		message := broker.Publish(input.Message.Topic, input.Message.Key, input.Message.Payload)
		published = map[string]any{
			"topic":     message.Topic,
			"offset":    message.Offset,
			"key":       message.Key,
			"payload":   message.Payload,
			"timestamp": message.Timestamp,
			"partition": broker.PartitionOf(message.Key),
		}
		if !contains(names, input.Message.Topic) {
			names = append(names, input.Message.Topic)
		}
	}
	sort.Strings(names)

	messages := []map[string]any{}
	topics := []map[string]any{}
	consumers := []map[string]any{}
	partitions := []map[string]any{}
	keys := map[string]bool{}

	for _, name := range names {
		stored := broker.Fetch(name, 0, 0)
		for _, message := range stored {
			messages = append(messages, map[string]any{
				"topic":     message.Topic,
				"offset":    message.Offset,
				"key":       message.Key,
				"payload":   message.Payload,
				"timestamp": message.Timestamp,
				"partition": broker.PartitionOf(message.Key),
			})
			if !keys[message.Key] {
				keys[message.Key] = true
				partitions = append(partitions, map[string]any{
					"key":       message.Key,
					"partition": broker.PartitionOf(message.Key),
				})
			}
		}
		last := uint64(0)
		if len(stored) > 0 {
			last = stored[len(stored)-1].Offset + 1
		}
		topics = append(topics, map[string]any{
			"name":       name,
			"messages":   broker.Length(name),
			"last":       last,
			"partitions": broker.PartitionsOf(name),
		})
		// a consumer that polls the topic and acknowledges the last message
		consumer := broker.Consumer(name)
		polled := consumer.Poll(10)
		if len(polled) > 0 {
			consumer.Ack(polled[len(polled)-1].Offset)
		}
		consumers = append(consumers, map[string]any{
			"topic":   name,
			"offset":  consumer.Offset,
			"pending": consumer.Pending(),
			"polled":  len(polled),
		})
	}
	sort.Slice(partitions, func(i, j int) bool {
		return partitions[i]["key"].(string) < partitions[j]["key"].(string)
	})

	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	_ = encoder.Encode(map[string]any{
		"topics":     topics,
		"messages":   messages,
		"consumers":  consumers,
		"partitions": partitions,
		"published":  published,
		"retention":  broker.Retention,
		"partitions_per_topic": Partitions,
	})
}

func contains(names []string, name string) bool {
	for _, item := range names {
		if item == name {
			return true
		}
	}
	return false
}
'''

#: The topics of the console. The two messages of "orders" are the ones of
#: the demo of main.go.
TOPICS = [
    {
        "name": "orders",
        "messages": [
            {"key": "o-1", "payload": "{\"total\": 20}"},
            {"key": "o-2", "payload": "{\"total\": 45}"},
            {"key": "o-3", "payload": "{\"total\": 12}"},
        ],
    },
    {
        "name": "events",
        "messages": [
            {"key": "e-1", "payload": "{\"type\": \"signup\"}"},
            {"key": "e-2", "payload": "{\"type\": \"login\"}"},
        ],
    },
]

#: The number of partitions of a topic, the constant of queue.go.
PARTITIONS = 4

#: The state of the broker and the cache.
_STATE: dict = {"topics": None}
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
    target = CACHE / f"queue-{key}"
    if target.exists():
        return target
    work = Path(tempfile.mkdtemp(prefix="foundev-queue-"))
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


def _run_helper(topics: list, action: str = "summary", message: dict | None = None) -> dict | None:
    """Run the helper and read its answer."""
    binary = _build_helper()
    if binary is None:
        return None
    payload = {"topics": topics, "retention": 100, "action": action, "message": message or {}}
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
    return answer if isinstance(answer, dict) else None


# --------------------------------------------------------------------------
# the fallback: the broker of queue.go, mirrored
# --------------------------------------------------------------------------

def _partition_of(key: str) -> int:
    """partitionOf of queue.go, mirrored."""
    if not key:
        return 0
    total = 0
    for character in key.encode():
        total = (total * 31 + character) % PARTITIONS
    return total


def _fallback(topics: list) -> dict:
    """Publish, Fetch, Poll, Ack and PartitionOf of queue.go, mirrored."""
    stored = {}
    offsets = {}
    for topic in topics:
        name = topic.get("name", "")
        stored.setdefault(name, [])
        offsets.setdefault(name, 0)
        for message in topic.get("messages", []):
            stored[name].append({
                "topic": name,
                "offset": offsets[name],
                "key": message.get("key", ""),
                "payload": message.get("payload", ""),
                "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                "partition": _partition_of(message.get("key", "")),
            })
            offsets[name] += 1

    messages = []
    topic_rows = []
    consumer_rows = []
    partitions = {}
    for name in sorted(stored):
        messages.extend(stored[name])
        topic_rows.append({
            "name": name,
            "messages": len(stored[name]),
            "last": offsets[name],
            "partitions": PARTITIONS,
        })
        # a consumer that polls the topic and acknowledges the last message
        polled = stored[name][:10]
        consumer_rows.append({
            "topic": name,
            "offset": polled[-1]["offset"] + 1 if polled else 0,
            "pending": max(len(polled) - 1, 0),
            "polled": len(polled),
        })
        for message in stored[name]:
            partitions.setdefault(message["key"], _partition_of(message["key"]))
    return {
        "topics": topic_rows,
        "messages": messages,
        "consumers": consumer_rows,
        "partitions": [{"key": key, "partition": value} for key, value in sorted(partitions.items())],
        "published": {},
        "retention": 100,
        "partitions_per_topic": PARTITIONS,
    }


# --------------------------------------------------------------------------
# the state of the console
# --------------------------------------------------------------------------

def _topics() -> list:
    """The topics of the console."""
    if _STATE["topics"] is None:
        _STATE["topics"] = [dict(topic) for topic in TOPICS]
    return _STATE["topics"]


def _answer(force: bool = False) -> dict:
    """The state of the broker."""
    topics = _topics()
    key = json.dumps(topics, sort_keys=True)
    now = time.time()
    if (not force and _ANSWER["data"] is not None and _ANSWER["key"] == key
            and now - _ANSWER["at"] < TTL):
        return _ANSWER["data"]
    answer = _run_helper(topics) or _fallback(topics)
    _ANSWER.update({"at": now, "key": key, "data": answer})
    return answer


def _message_rows(answer: dict) -> list:
    """The messages of every topic."""
    return [
        {
            "Topic": message.get("topic", ""),
            "Offset": message.get("offset", 0),
            "Key": message.get("key", ""),
            "Partition": message.get("partition", 0),
            "Payload": message.get("payload", ""),
        }
        for message in answer.get("messages", [])
    ]


def _topic_rows(answer: dict) -> list:
    """The topics with their length."""
    return [
        {
            "Topic": topic.get("name", ""),
            "Messages": topic.get("messages", 0),
            "Last offset": topic.get("last", 0),
            "Partitions": topic.get("partitions", PARTITIONS),
        }
        for topic in answer.get("topics", [])
    ]


def _partition_rows(answer: dict) -> list:
    """The partition of every key."""
    return [
        {"Key": row.get("key", ""), "Partition": row.get("partition", 0)}
        for row in answer.get("partitions", [])
    ]


def _consumer_rows(answer: dict) -> list:
    """The consumers with their offset and their pending messages."""
    return [
        {
            "Topic": consumer.get("topic", ""),
            "Offset": consumer.get("offset", 0),
            "Polled": consumer.get("polled", 0),
            "Pending": consumer.get("pending", 0),
        }
        for consumer in answer.get("consumers", [])
    ]


def summary() -> dict:
    """The topics, the messages and the consumers."""
    answer = _answer()
    messages = answer.get("messages", [])
    consumers = answer.get("consumers", [])
    return {
        "stats": {
            "topics": len(answer.get("topics", [])),
            "messages": len(messages),
            "partitions": answer.get("partitions_per_topic", PARTITIONS),
            "pending": sum(int(consumer.get("pending", 0)) for consumer in consumers),
            "consumers": len(consumers),
            "retention": answer.get("retention", 100),
        },
        "series": [
            {"label": row["Topic"], "value": row["Messages"]} for row in _topic_rows(answer)
        ],
        "lists": {
            "messages": _message_rows(answer),
            "topics": _topic_rows(answer),
            "partitions": _partition_rows(answer),
            "consumers": _consumer_rows(answer),
        },
    }


# --------------------------------------------------------------------------
# the endpoints
# --------------------------------------------------------------------------

def handle(method, path, payload, user):
    """The endpoints of the broker."""
    if method == "GET" and path == "/api/summary":
        return 200, summary()
    if method == "GET" and path == "/api/messages":
        return 200, {"messages": _message_rows(_answer())}
    if method == "GET" and path == "/api/topics":
        return 200, {"topics": _topic_rows(_answer())}
    if method == "GET" and path == "/api/partitions":
        return 200, {"partitions": _partition_rows(_answer())}
    if method == "GET" and path == "/api/consumers":
        return 200, {"consumers": _consumer_rows(_answer())}
    if method == "POST" and path == "/api/publish":
        body = payload or {}
        topic = str(body.get("topic") or "orders").strip() or "orders"
        key = str(body.get("key") or "").strip()
        payload_value = str(body.get("payload") or "").strip()
        topics = [dict(item) for item in _topics()]
        found = False
        for item in topics:
            if item.get("name") == topic:
                item.setdefault("messages", []).append({"key": key, "payload": payload_value})
                found = True
        if not found:
            topics.append({"name": topic, "messages": [{"key": key, "payload": payload_value}]})
        message = {"topic": topic, "key": key, "payload": payload_value}
        answer = _run_helper(topics, "publish", message)
        if answer is None:
            answer = _fallback(topics)
            offset = len(answer.get("messages", []))
            answer["published"] = dict(message, offset=offset, partition=_partition_of(key))
        _STATE["topics"] = topics
        _ANSWER["data"] = None
        published = answer.get("published", {}) or {}
        return 200, {
            "topic": published.get("topic", topic),
            "offset": published.get("offset", 0),
            "key": published.get("key", key),
            "partition": published.get("partition", _partition_of(key)),
        }
    return 404, {"error": "not found"}
