package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"
)

func main() {
	name := flag.String("campaign", "Q3 awareness", "campaign name")
	template := flag.String("template", "password-reset", "email template")
	targets := flag.String("targets", "ana@example.com,bo@example.com", "comma separated targets")
	flag.Parse()

	campaign := NewCampaign(*name, *template)
	plan := Plan(campaign, strings.Split(*targets, ","))
	// The demo records a plausible funnel: everyone receives, half opens,
	// one clicks and one reports the message.
	if len(plan) > 0 {
		RecordOpen(campaign, plan[0].TrackingID)
	}
	if len(plan) > 1 {
		RecordClick(campaign, plan[1].TrackingID)
		RecordReport(campaign, plan[1].TrackingID)
	}

	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(map[string]any{"plan": plan, "summary": Summary(campaign)}); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
