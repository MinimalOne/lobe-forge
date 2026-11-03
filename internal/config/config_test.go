package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestConfigResolvePrecedence(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("HOME", tmpDir)
	t.Setenv("LOBE_SERVER_URL", "")
	t.Setenv("LOBE_HUMAN_TOKEN", "")
	t.Setenv("LOBE_USER", "")

	// 1. Defaults
	cfg, err := Resolve("", "", "")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if cfg.ServerURL != DefaultServerURL {
		t.Errorf("expected %s, got %s", DefaultServerURL, cfg.ServerURL)
	}

	// 2. Config file ~/.config/lobe/config.json
	cfgDir := filepath.Join(tmpDir, ".config", "lobe")
	if err := os.MkdirAll(cfgDir, 0700); err != nil {
		t.Fatal(err)
	}
	cfgJSON := `{"server_url": "https://config.example.com", "human_token": "token-from-file", "reviewer": "file-user"}`
	if err := os.WriteFile(filepath.Join(cfgDir, "config.json"), []byte(cfgJSON), 0600); err != nil {
		t.Fatal(err)
	}

	cfg, err = Resolve("", "", "")
	if err != nil {
		t.Fatal(err)
	}
	if cfg.ServerURL != "https://config.example.com" {
		t.Errorf("expected config file URL, got %s", cfg.ServerURL)
	}
	if cfg.HumanToken != "token-from-file" {
		t.Errorf("expected config file token, got %s", cfg.HumanToken)
	}
	if cfg.Reviewer != "file-user" {
		t.Errorf("expected config file user, got %s", cfg.Reviewer)
	}

	// 3. Env var overrides
	t.Setenv("LOBE_SERVER_URL", "https://env.example.com")
	t.Setenv("LOBE_HUMAN_TOKEN", "token-from-env")
	t.Setenv("LOBE_USER", "env-user")

	cfg, err = Resolve("", "", "")
	if err != nil {
		t.Fatal(err)
	}
	if cfg.ServerURL != "https://env.example.com" {
		t.Errorf("expected env URL, got %s", cfg.ServerURL)
	}
	if cfg.HumanToken != "token-from-env" {
		t.Errorf("expected env token, got %s", cfg.HumanToken)
	}
	if cfg.Reviewer != "env-user" {
		t.Errorf("expected env user, got %s", cfg.Reviewer)
	}

	// 4. CLI flags override everything
	cfg, err = Resolve("https://flag.example.com/", "token-from-flag", "flag-user")
	if err != nil {
		t.Fatal(err)
	}
	if cfg.ServerURL != "https://flag.example.com" { // trailing slash stripped
		t.Errorf("expected flag URL, got %s", cfg.ServerURL)
	}
	if cfg.HumanToken != "token-from-flag" {
		t.Errorf("expected flag token, got %s", cfg.HumanToken)
	}
	if cfg.Reviewer != "flag-user" {
		t.Errorf("expected flag user, got %s", cfg.Reviewer)
	}
}
