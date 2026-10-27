package main

import "testing"

func TestTheMessagesOfAKeyKeepTheirOrder(t *testing.T) {
	broker := NewBroker(10)
	broker.Publish("orders", "customer-1", "first")
	broker.Publish("orders", "customer-1", "second")

	messages := broker.Fetch("orders", 0, 10)
	if len(messages) != 2 {
		t.Fatalf("unexpected messages %#v", messages)
	}
	if messages[0].Payload != "first" || messages[1].Payload != "second" {
		t.Fatalf("the order is wrong: %#v", messages)
	}
}

func TestAKeyAlwaysGoesIntoTheSamePartition(t *testing.T) {
	broker := NewBroker(10)
	first := broker.PartitionOf("customer-1")
	second := broker.PartitionOf("customer-1")
	other := broker.PartitionOf("customer-2")

	if first != second {
		t.Fatal("the same key has to go into the same partition")
	}
	if first < 0 || first >= Partitions {
		t.Fatalf("unexpected partition %d", first)
	}
	_ = other
}

func TestTheTopicHasPartitions(t *testing.T) {
	broker := NewBroker(10)
	if broker.PartitionsOf("orders") != Partitions {
		t.Fatal("the topic has to have partitions")
	}
}

func TestTheMessagesAreFetchedFromTheOffset(t *testing.T) {
	broker := NewBroker(10)
	broker.Publish("orders", "key", "first")
	broker.Publish("orders", "key", "second")

	messages := broker.Fetch("orders", 1, 10)
	if len(messages) != 1 {
		t.Fatalf("unexpected messages %#v", messages)
	}
	if messages[0].Payload != "second" {
		t.Fatalf("unexpected message %#v", messages[0])
	}
}
