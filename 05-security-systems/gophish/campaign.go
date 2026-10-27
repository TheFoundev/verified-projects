package main

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"strings"
	"time"
)

// Target is one recipient in an awareness campaign.
type Target struct {
	Email string `json:"email"`
	Name  string `json:"name"`
}

// Send records one simulated delivery.
type Send struct {
	TrackingID string    `json:"tracking_id"`
	Email      string    `json:"email"`
	SentAt     time.Time `json:"sent_at"`
	Opened     bool      `json:"opened"`
	Clicked    bool      `json:"clicked"`
	Reported   bool      `json:"reported"`
}

// Campaign groups sends under one name and template.
type Campaign struct {
	Name     string           `json:"name"`
	Template string           `json:"template"`
	Sends    map[string]*Send `json:"sends"`
}

// Stats holds the funnel rates.
type Stats struct {
	Campaign   string  `json:"campaign"`
	Sent       int     `json:"sent"`
	Opened     int     `json:"opened"`
	Clicked    int     `json:"clicked"`
	Reported   int     `json:"reported"`
	OpenRate   float64 `json:"open_rate"`
	ClickRate  float64 `json:"click_rate"`
	ReportRate float64 `json:"report_rate"`
}

func NewCampaign(name, template string) *Campaign {
	return &Campaign{Name: name, Template: template, Sends: map[string]*Send{}}
}

// counter makes the fallback tokens unique.
var counter int64

// token returns the tracking id of a send.
//
// When the random source does not work the token still has to be unique, or
// two sends would share one tracking id and the first one would overwrite
// the second one in the map.
func token() string {
	buffer := make([]byte, 8)
	if _, err := rand.Read(buffer); err != nil {
		counter++
		return fmt.Sprintf("fallback-%d-%d", time.Now().UnixNano(), counter)
	}
	return hex.EncodeToString(buffer)
}

// Plan creates a send record for every non-empty target.
func Plan(campaign *Campaign, targets []string) []Send {
	plan := []Send{}
	for _, raw := range targets {
		email := strings.TrimSpace(raw)
		if email == "" {
			continue
		}
		send := Send{TrackingID: token(), Email: email, SentAt: time.Now().UTC()}
		campaign.Sends[send.TrackingID] = &send
		plan = append(plan, send)
	}
	return plan
}

func mark(campaign *Campaign, id string, field func(*Send)) bool {
	send, ok := campaign.Sends[id]
	if !ok {
		return false
	}
	field(send)
	return true
}

func RecordOpen(campaign *Campaign, id string) bool {
	return mark(campaign, id, func(send *Send) { send.Opened = true })
}

func RecordClick(campaign *Campaign, id string) bool {
	return mark(campaign, id, func(send *Send) { send.Opened = true; send.Clicked = true })
}

func RecordReport(campaign *Campaign, id string) bool {
	return mark(campaign, id, func(send *Send) { send.Reported = true })
}

// Summary computes the campaign funnel.
//
// The rates are the share of the sends that did the thing: the open rate and
// the click rate over the sends, and the report rate over the opens.
func Summary(campaign *Campaign) Stats {
	summary := Stats{Campaign: campaign.Name, Sent: len(campaign.Sends)}
	for _, send := range campaign.Sends {
		if send.Opened {
			summary.Opened++
		}
		if send.Clicked {
			summary.Clicked++
		}
		if send.Reported {
			summary.Reported++
		}
	}
	if summary.Sent > 0 {
		summary.OpenRate = float64(summary.Opened) / float64(summary.Sent)
		summary.ClickRate = float64(summary.Clicked) / float64(summary.Sent)
	}
	if summary.Opened > 0 {
		summary.ReportRate = float64(summary.Reported) / float64(summary.Opened)
	}
	return summary
}
