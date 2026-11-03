package commands

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/MinimalOne/lobe-forge/internal/client"
)

func TestParseStaleV(t *testing.T) {
	out := `
B. Orphan pages (no inbound links) (1)
  1. orphan.md — no inbound links

V. Stale verification (content changed since human review) (2)  [warning — not blocking]
  2. alpha.md — content changed since review by human:jo on 2026-11-02T20:55:01Z (sha 1111 != 2222)
  3. beta-two.md — content changed since review by human:al on 2026-11-01T10:00:00Z (sha aaaa != bbbb)

T. Page types (missing or invalid type) (1)
  4. gamma.md — content changed since review by human:x on y (sha 1 != 2)
`
	items := parseStaleV(out)
	if len(items) != 2 || items[0].Slug != "alpha" || items[1].Slug != "beta-two" ||
		items[0].OldSha != "1111" || items[1].CurrentSha != "bbbb" || items[1].ReviewedBy != "human:al" {
		t.Fatalf("unexpected items: %+v", items)
	}
}

func TestRunExportRejectsUnsafePaths(t *testing.T) {
	for _, bad := range []string{"../escaped.txt", "/abs.txt", "wiki/../../x.md"} {
		srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			_ = json.NewEncoder(w).Encode(client.ExportOKFResult{Files: []client.ExportFile{
				{Path: "wiki/ok.md", Content: "ok"}, {Path: bad, Content: "pwn"},
			}})
		}))
		dir := t.TempDir()
		out := filepath.Join(dir, "out")
		err := RunExport(context.Background(), client.New(srv.URL, ""), "hack", out, false)
		srv.Close()
		if err == nil {
			t.Fatalf("%q: expected error", bad)
		}
		if _, err := os.Stat(filepath.Join(out, "wiki", "ok.md")); err == nil {
			t.Fatalf("%q: wrote files before rejecting the bundle", bad)
		}
	}
}
