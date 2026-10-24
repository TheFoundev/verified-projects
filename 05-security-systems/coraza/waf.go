package main

import (
	"fmt"
	"io"
	"net/http"
	"net/url"
	"regexp"
)

// Request is the subset of an HTTP request the firewall inspects.
type Request struct {
	Method string
	Path   string
	Query  string
	Body   string
}

// Rule matches one class of attack.
type Rule struct {
	ID       string
	Category string
	Severity int
	Pattern  *regexp.Regexp
}

// Finding is a rule match for one request.
type Finding struct {
	Rule     string `json:"rule"`
	Category string `json:"category"`
	Severity int    `json:"severity"`
	Evidence string `json:"evidence"`
}

var rules = []Rule{
	{"CRS-001", "sqli", 3, regexp.MustCompile(`(?i)(union\s+select|or\s+1\s*=\s*1|'\s*--|sleep\s*\()`)},
	{"CRS-002", "xss", 3, regexp.MustCompile(`(?i)(<script|javascript:|onerror\s*=|onload\s*=)`)},
	{"CRS-003", "traversal", 2, regexp.MustCompile(`(\.\./|\.\.\\|%2e%2e%2f)`)},
	{"CRS-004", "scanner", 1, regexp.MustCompile(`(?i)(sqlmap|nikto|nmap|acunetix)`)},
}

// Inspect returns the rule matches for a request.
// Middleware puts the WAF in front of a handler.
//
// The request is decoded before it is looked at, so an address that is
// written as %2e%2e%2f is caught as well.
func Middleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		findings := Inspect(Inspectable(request))
		if Blocked(findings) {
			writer.Header().Set("Content-Type", "text/plain; charset=utf-8")
			writer.WriteHeader(http.StatusForbidden)
			fmt.Fprintf(writer, "blocked by the WAF: %d finding(s)\n", len(findings))
			return
		}
		next.ServeHTTP(writer, request)
	})
}

// Inspectable turns an HTTP request into the request the firewall looks at.
//
// The escapes are decoded first, so an address that is written as
// %2e%2e%2f is caught as well.
func Inspectable(request *http.Request) Request {
	inspect := Request{Method: request.Method, Path: request.URL.Path}

	if request.URL.RawQuery != "" {
		inspect.Query = decodeOnce(request.URL.RawQuery)
	}
	if request.URL.RawPath != "" {
		inspect.Path = decodeOnce(request.URL.RawPath)
	}

	body := ""
	if request.Body != nil {
		raw, err := io.ReadAll(io.LimitReader(request.Body, 1<<20))
		if err == nil {
			body = string(raw)
		}
	}
	inspect.Body = decodeOnce(body)

	return inspect
}

// decodeOnce decodes the escapes of a text one time.
func decodeOnce(text string) string {
	decoded, err := url.PathUnescape(text)
	if err != nil {
		return text
	}
	return decoded
}

func Inspect(request Request) []Finding {
	targets := []string{request.Path, request.Query, request.Body}
	findings := []Finding{}
	for _, rule := range rules {
		for _, target := range targets {
			if match := rule.Pattern.FindString(target); match != "" && target != "" {
				findings = append(findings, Finding{
					Rule: rule.ID, Category: rule.Category, Severity: rule.Severity, Evidence: match,
				})
				break
			}
		}
	}
	return findings
}

// Score sums the severity of each finding.
func Score(findings []Finding) int {
	total := 0
	for _, finding := range findings {
		total += finding.Severity
	}
	return total
}

// Blocked reports whether the request should be rejected.
func Blocked(findings []Finding) bool {
	return Score(findings) >= 2
}

// Evidence returns the matched text for each finding.
func Evidence(findings []Finding) []string {
	values := make([]string, 0, len(findings))
	for _, finding := range findings {
		values = append(values, finding.Evidence)
	}
	return values
}
