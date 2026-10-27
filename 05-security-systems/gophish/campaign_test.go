package main

import (
	"strings"
	"testing"
)

func TestEveryTargetGetsItsOwnTrackingId(t *testing.T) {
	campaign := NewCampaign("demo", "hello")
	plan := Plan(campaign, []string{"ann@example.test", "bob@example.test"})

	if len(plan) != 2 {
		t.Fatalf("expected two sends, got %d", len(plan))
	}
	if plan[0].TrackingID == plan[1].TrackingID {
		t.Fatal("two sends may not share a tracking id")
	}
	if len(campaign.Sends) != 2 {
		t.Fatalf("expected two sends in the campaign, got %d", len(campaign.Sends))
	}
}

func TestAnEmptyTargetIsSkipped(t *testing.T) {
	campaign := NewCampaign("demo", "hello")
	plan := Plan(campaign, []string{"", "   ", "ann@example.test"})

	if len(plan) != 1 {
		t.Fatalf("expected one send, got %d", len(plan))
	}
}

func TestTheFunnelIsCounted(t *testing.T) {
	campaign := NewCampaign("demo", "hello")
	plan := Plan(campaign, []string{"ann@example.test", "bob@example.test", "cat@example.test"})

	RecordOpen(campaign, plan[0].TrackingID)
	RecordClick(campaign, plan[1].TrackingID)
	RecordReport(campaign, plan[2].TrackingID)

	summary := Summary(campaign)
	if summary.Sent != 3 {
		t.Fatalf("unexpected number of sends %d", summary.Sent)
	}
	// A click means the mail was opened as well.
	if summary.Opened != 2 {
		t.Fatalf("unexpected number of opens %d", summary.Opened)
	}
	if summary.Clicked != 1 {
		t.Fatalf("unexpected number of clicks %d", summary.Clicked)
	}
	if summary.Reported != 1 {
		t.Fatalf("unexpected number of reports %d", summary.Reported)
	}
}

func TestTheRatesAreShares(t *testing.T) {
	campaign := NewCampaign("demo", "hello")
	plan := Plan(campaign, []string{"ann@example.test", "bob@example.test"})

	RecordOpen(campaign, plan[0].TrackingID)
	RecordClick(campaign, plan[1].TrackingID)

	summary := Summary(campaign)
	// The click means the second mail was opened as well.
	if summary.OpenRate != 1 {
		t.Fatalf("unexpected open rate %v", summary.OpenRate)
	}
	if summary.ClickRate != 0.5 {
		t.Fatalf("unexpected click rate %v", summary.ClickRate)
	}
	if summary.ReportRate != 0 {
		t.Fatalf("unexpected report rate %v", summary.ReportRate)
	}
}

func TestAnUnknownTrackingIdIsRefused(t *testing.T) {
	campaign := NewCampaign("demo", "hello")
	if RecordOpen(campaign, "nope") {
		t.Fatal("an unknown tracking id should be refused")
	}
	if !strings.HasPrefix(NewCampaign("demo", "x").Name, "demo") {
		t.Fatal("the campaign keeps its name")
	}
}
