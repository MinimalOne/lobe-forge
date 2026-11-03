package client

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestVerifyStatus(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			t.Errorf("expected GET, got %s", r.Method)
		}
		if r.URL.Path != "/api/lobes/hack/pages/my-page/verify" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		if r.Header.Get("Authorization") != "Bearer test-token" {
			t.Errorf("expected Authorization header with test-token, got %s", r.Header.Get("Authorization"))
		}

		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(VerifyStatus{
			Lobe:       "hack",
			Slug:       "my-page",
			Hash:       "abc123hash",
			IsVerified: true,
			IsStale:    false,
			Verified: &VerifiedStamp{
				By:  "human:jijin",
				At:  "2026-11-02T20:00:00Z",
				Sha: "abc123hash",
			},
		})
	}))
	defer server.Close()

	cli := New(server.URL, "test-token")
	st, err := cli.GetVerifyStatus(context.Background(), "hack", "my-page")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if st.Hash != "abc123hash" || !st.IsVerified || st.IsStale {
		t.Errorf("unexpected status returned: %+v", st)
	}
}

func TestVerifyPageSuccess(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			t.Errorf("expected POST, got %s", r.Method)
		}
		if r.URL.Path != "/api/lobes/hack/pages/my-page/verify" {
			t.Errorf("unexpected path: %s", r.URL.Path)
		}
		var payload map[string]string
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Fatal(err)
		}
		if payload["expected_hash"] != "hash456" || payload["by"] != "jijin" {
			t.Errorf("unexpected payload: %+v", payload)
		}

		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(VerifyResult{
			Lobe:      "hack",
			Slug:      "my-page",
			Sha:       "finalsha",
			Committed: true,
			Commit:    "commit123",
			Pushed:    true,
			Verified: VerifiedStamp{
				By:  "human:jijin",
				At:  "2026-11-03T06:00:00Z",
				Sha: "hash456",
			},
		})
	}))
	defer server.Close()

	cli := New(server.URL, "human-token")
	res, err := cli.VerifyPage(context.Background(), "hack", "my-page", "hash456", "jijin")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if res.Commit != "commit123" || res.Verified.By != "human:jijin" {
		t.Errorf("unexpected verify result: %+v", res)
	}
}

func TestVerifyPageForbidden(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusForbidden)
		_ = json.NewEncoder(w).Encode(map[string]string{
			"error": "Forbidden: human verification credential required",
		})
	}))
	defer server.Close()

	cli := New(server.URL, "agent-token")
	_, err := cli.VerifyPage(context.Background(), "hack", "my-page", "hash456", "agent")
	if err == nil {
		t.Fatal("expected error, got nil")
	}
	apiErr, ok := err.(*APIError)
	if !ok {
		t.Fatalf("expected *APIError, got %T: %v", err, err)
	}
	if apiErr.StatusCode != 403 {
		t.Errorf("expected 403, got %d", apiErr.StatusCode)
	}
}

func TestVerifyPageConflict(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusConflict)
		_ = json.NewEncoder(w).Encode(map[string]string{
			"error": "Page content hash newhash does not match expected_hash oldhash",
		})
	}))
	defer server.Close()

	cli := New(server.URL, "human-token")
	_, err := cli.VerifyPage(context.Background(), "hack", "my-page", "oldhash", "jijin")
	if err == nil {
		t.Fatal("expected conflict error, got nil")
	}
	apiErr, ok := err.(*APIError)
	if !ok || apiErr.StatusCode != 409 {
		t.Fatalf("expected 409 conflict error, got: %v", err)
	}
}

func TestGetPageAndLint(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/api/lobes/hack/pages/test-slug" {
			_ = json.NewEncoder(w).Encode(PageContent{
				Lobe:    "hack",
				Slug:    "test-slug",
				Content: "# Test Content",
				Lines:   10,
			})
			return
		}
		if r.URL.Path == "/api/lobes/hack/lint" {
			if r.URL.Query().Get("strict") != "true" {
				t.Errorf("expected strict=true query parameter")
			}
			_ = json.NewEncoder(w).Encode(LintResult{
				ReturnCode: 0,
				Stdout:     "✓ Strict mode: 0 hard errors",
			})
			return
		}
		http.NotFound(w, r)
	}))
	defer server.Close()

	cli := New(server.URL, "token")
	page, err := cli.GetPage(context.Background(), "hack", "test-slug")
	if err != nil {
		t.Fatal(err)
	}
	if page.Content != "# Test Content" {
		t.Errorf("unexpected content: %s", page.Content)
	}

	lint, err := cli.GetLint(context.Background(), "hack", true, false)
	if err != nil {
		t.Fatal(err)
	}
	if lint.ReturnCode != 0 {
		t.Errorf("unexpected returncode: %d", lint.ReturnCode)
	}
}
