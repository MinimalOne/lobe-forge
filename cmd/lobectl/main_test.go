package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/MinimalOne/lobe-forge/internal/client"
	"github.com/MinimalOne/lobe-forge/internal/commands"
	"github.com/MinimalOne/lobe-forge/internal/config"
)

func TestLobectlCommandsWithMockServer(t *testing.T) {
	mockServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")

		switch {
		case r.URL.Path == "/api/lobes" && r.Method == http.MethodGet:
			_ = json.NewEncoder(w).Encode(client.LobeListResult{
				Lobes: []client.LobeInfo{
					{Name: "hack", Autopush: true, Remote: "https://github.com/jijinp/hack.git"},
					{Name: "ftf", Autopush: false, Remote: ""},
				},
			})

		case r.URL.Path == "/api/lobes/hack/pages/test-slug/verify" && r.Method == http.MethodGet:
			_ = json.NewEncoder(w).Encode(client.VerifyStatus{
				Lobe:       "hack",
				Slug:       "test-slug",
				Hash:       "hash999",
				IsVerified: true,
				IsStale:    false,
				Verified: &client.VerifiedStamp{
					By:  "human:jijin",
					At:  "2026-11-03T06:00:00Z",
					Sha: "hash999",
				},
			})

		case r.URL.Path == "/api/lobes/hack/pages/test-slug/verify" && r.Method == http.MethodPost:
			var payload map[string]string
			_ = json.NewDecoder(r.Body).Decode(&payload)
			_ = json.NewEncoder(w).Encode(client.VerifyResult{
				Lobe:      "hack",
				Slug:      "test-slug",
				Sha:       "finalsha",
				Committed: true,
				Commit:    "commitabc",
				Pushed:    true,
				Verified: client.VerifiedStamp{
					By:  "human:" + payload["by"],
					At:  "2026-11-03T06:01:00Z",
					Sha: payload["expected_hash"],
				},
			})

		case r.URL.Path == "/api/lobes/hack/lint":
			_ = json.NewEncoder(w).Encode(client.LintResult{
				ReturnCode: 0,
				Stdout: `V. Stale verification (content changed since human review) (1)  [warning — not blocking]
  1. test-slug.md — content changed since review by human:jijin on 2026-11-02T20:55:01Z (sha 11111111 != 22222222)

✓ Strict mode: 0 hard errors (commit allowed). 1 soft warning(s) above.
`,
			})

		case r.URL.Path == "/api/lobes/hack/sync" && r.Method == http.MethodPost:
			_ = json.NewEncoder(w).Encode(client.SyncResult{
				Lobe:   "hack",
				Status: "up to date",
				Commit: "commitabc",
				Pulled: true,
				Pushed: true,
			})

		case r.URL.Path == "/api/lobes/hack/export-okf":
			_ = json.NewEncoder(w).Encode(client.ExportOKFResult{
				Lobe:       "hack",
				OKFVersion: "0.2",
				PageCount:  1,
				Files: []client.ExportFile{
					{Path: "wiki/index.md", Content: "# OKF Index"},
				},
			})

		default:
			http.NotFound(w, r)
		}
	}))
	defer mockServer.Close()

	cli := client.New(mockServer.URL, "mock-token")
	cfg := &config.Config{
		ServerURL:  mockServer.URL,
		HumanToken: "mock-token",
		Reviewer:   "jijin",
	}
	ctx := context.Background()

	// 1. Test List
	if err := commands.RunList(ctx, cli, false); err != nil {
		t.Fatalf("RunList failed: %v", err)
	}

	// 2. Test Verify Status
	if err := commands.RunVerifyStatus(ctx, cli, "hack", "test-slug", false); err != nil {
		t.Fatalf("RunVerifyStatus failed: %v", err)
	}

	// 3. Test Verify Stamp
	if err := commands.RunVerifyStamp(ctx, cli, cfg, "hack", []string{"test-slug"}, true, false); err != nil {
		t.Fatalf("RunVerifyStamp failed: %v", err)
	}

	// 4. Test Verify List (parses Check V)
	if err := commands.RunVerifyList(ctx, cli, "hack", false); err != nil {
		t.Fatalf("RunVerifyList failed: %v", err)
	}

	// 5. Test Lint
	if err := commands.RunLint(ctx, cli, "hack", true, false); err != nil {
		t.Fatalf("RunLint failed: %v", err)
	}

	// 6. Test Sync
	if err := commands.RunSync(ctx, cli, "hack", false); err != nil {
		t.Fatalf("RunSync failed: %v", err)
	}

	// 7. Test Export
	tmpExport := filepath.Join(t.TempDir(), "export-out")
	if err := commands.RunExport(ctx, cli, "hack", tmpExport, false); err != nil {
		t.Fatalf("RunExport failed: %v", err)
	}
	exportedFile := filepath.Join(tmpExport, "wiki", "index.md")
	content, err := os.ReadFile(exportedFile)
	if err != nil {
		t.Fatalf("reading exported file: %v", err)
	}
	if string(content) != "# OKF Index" {
		t.Fatalf("unexpected export content: %s", string(content))
	}
}
