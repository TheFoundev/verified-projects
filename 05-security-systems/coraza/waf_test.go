package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestTheMiddlewareLetsAGoodRequestThrough(t *testing.T) {
	handler := Middleware(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.WriteHeader(http.StatusOK)
		_, _ = writer.Write([]byte("ok"))
	}))

	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/index.html", nil))

	if recorder.Code != http.StatusOK {
		t.Fatalf("unexpected status %d", recorder.Code)
	}
}

func TestTheMiddlewareBlocksASuspiciousRequest(t *testing.T) {
	handler := Middleware(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.WriteHeader(http.StatusOK)
	}))

	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/?q=<script>alert(1)</script>", nil))

	if recorder.Code != http.StatusForbidden {
		t.Fatalf("unexpected status %d", recorder.Code)
	}
}

func TestTheRequestIsDecodedBeforeItIsLookedAt(t *testing.T) {
	// An address that is written as %3Cscript%3E is caught as well.
	handler := Middleware(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.WriteHeader(http.StatusOK)
	}))

	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, httptest.NewRequest(http.MethodGet, "/?q=%3Cscript%3E", nil))

	if recorder.Code != http.StatusForbidden {
		t.Fatalf("unexpected status %d", recorder.Code)
	}
}

func TestTheBodyIsLookedAt(t *testing.T) {
	handler := Middleware(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		writer.WriteHeader(http.StatusOK)
	}))

	request := httptest.NewRequest(http.MethodPost, "/api", http.NoBody)
	request.Body = http.NoBody
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)

	if recorder.Code != http.StatusOK {
		t.Fatalf("unexpected status %d", recorder.Code)
	}
}

func TestTheInspectableRequest(t *testing.T) {
	request := httptest.NewRequest(http.MethodGet, "/path?q=%3Cscript%3E", nil)
	inspect := Inspectable(request)

	if inspect.Method != http.MethodGet {
		t.Fatalf("unexpected method %q", inspect.Method)
	}
	if !strings.Contains(inspect.Query, "<script>") {
		t.Fatalf("the query was not decoded: %q", inspect.Query)
	}
	if strings.Contains(inspect.Query, "%3C") {
		t.Fatalf("the query still has the escapes: %q", inspect.Query)
	}
}
