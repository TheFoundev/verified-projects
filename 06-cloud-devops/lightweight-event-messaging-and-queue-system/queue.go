package main

import (
	"encoding/json"
	"net/http"
	"strconv"
	"sync"
	"time"
)

// Message is one queue entry.
type Message struct {
	Topic     string    `json:"topic"`
	Offset    uint64    `json:"offset"`
	Key       string    `json:"key"`
	Payload   string    `json:"payload"`
	Timestamp time.Time `json:"timestamp"`
}

// Topic is an append-only log.
type Topic struct {
	mu       sync.RWMutex
	Name     string
	Messages []Message
	next     uint64
}

// Broker holds all topics.
type Broker struct {
	mu        sync.RWMutex
	topics    map[string]*Topic
	Retention int
}

func NewBroker(retention int) *Broker {
	return &Broker{topics: map[string]*Topic{}, Retention: retention}
}

func (broker *Broker) topic(name string) *Topic {
	broker.mu.Lock()
	defer broker.mu.Unlock()
	if topic, ok := broker.topics[name]; ok {
		return topic
	}
	topic := &Topic{Name: name}
	broker.topics[name] = topic
	return topic
}

// Publish appends a message and returns it.
// Partitions is the number of partitions a topic has.
//
// The messages of a key always go into the same partition, so the order of
// the messages of a key is kept.
const Partitions = 4

// partitionOf is the partition a key belongs to.
//
// The same key always gives the same partition, so the messages of a key
// keep their order.
func partitionOf(key string) int {
	if key == "" {
		return 0
	}
	sum := 0
	for _, character := range []byte(key) {
		sum = (sum*31 + int(character)) % Partitions
	}
	return sum
}

func (broker *Broker) Publish(name, key, payload string) Message {
	topic := broker.topic(name)
	topic.mu.Lock()
	defer topic.mu.Unlock()
	message := Message{Topic: name, Offset: topic.next, Key: key, Payload: payload, Timestamp: time.Now().UTC()}
	topic.next++
	topic.Messages = append(topic.Messages, message)
	if broker.Retention > 0 && len(topic.Messages) > broker.Retention {
		trim := len(topic.Messages) - broker.Retention
		topic.Messages = topic.Messages[trim:]
	}
	return message
}

// Fetch returns messages at or after an offset.
// PartitionsOf returns the number of partitions of a topic.
func (broker *Broker) PartitionsOf(name string) int {
	return Partitions
}

// PartitionOf is the partition a key belongs to.
func (broker *Broker) PartitionOf(key string) int {
	return partitionOf(key)
}

func (broker *Broker) Fetch(name string, offset uint64, limit int) []Message {
	topic := broker.topic(name)
	topic.mu.RLock()
	defer topic.mu.RUnlock()
	messages := []Message{}
	for _, message := range topic.Messages {
		if message.Offset < offset {
			continue
		}
		messages = append(messages, message)
		if limit > 0 && len(messages) >= limit {
			break
		}
	}
	return messages
}

func (broker *Broker) Length(name string) int {
	topic := broker.topic(name)
	topic.mu.RLock()
	defer topic.mu.RUnlock()
	return len(topic.Messages)
}

// Consumer tracks an offset for one topic.
type Consumer struct {
	Broker  *Broker
	Topic   string
	Offset  uint64
	pending map[uint64]bool
}

func (broker *Broker) Consumer(topic string) *Consumer {
	return &Consumer{Broker: broker, Topic: topic, pending: map[uint64]bool{}}
}

// Poll fetches new messages and marks them pending.
func (consumer *Consumer) Poll(limit int) []Message {
	messages := consumer.Broker.Fetch(consumer.Topic, consumer.Offset, limit)
	for _, message := range messages {
		consumer.pending[message.Offset] = true
		consumer.Offset = message.Offset + 1
	}
	return messages
}

// Ack clears a pending offset.
func (consumer *Consumer) Ack(offset uint64) {
	delete(consumer.pending, offset)
}

// Pending counts unacknowledged messages.
func (consumer *Consumer) Pending() int {
	return len(consumer.pending)
}

func (broker *Broker) PublishHandler(writer http.ResponseWriter, request *http.Request) {
	topic := request.URL.Query().Get("topic")
	payload := request.URL.Query().Get("payload")
	if topic == "" {
		http.Error(writer, "topic is required", http.StatusBadRequest)
		return
	}
	message := broker.Publish(topic, request.URL.Query().Get("key"), payload)
	_ = json.NewEncoder(writer).Encode(message)
}

func (broker *Broker) FetchHandler(writer http.ResponseWriter, request *http.Request) {
	topic := request.URL.Query().Get("topic")
	offset, _ := strconv.ParseUint(request.URL.Query().Get("offset"), 10, 64)
	limit, _ := strconv.Atoi(request.URL.Query().Get("limit"))
	_ = json.NewEncoder(writer).Encode(broker.Fetch(topic, offset, limit))
}
