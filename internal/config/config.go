package config

import (
	"encoding/json"
	"fmt"
	"os"
	"os/user"
	"path/filepath"
	"strings"
)

// Config holds client configuration for lobectl.
type Config struct {
	ServerURL  string `json:"server_url"`
	HumanToken string `json:"human_token"`
	Reviewer   string `json:"reviewer"`
}

const (
	DefaultServerURL = "https://lobes.minimal.one"
)

// Resolve loads configuration with precedence:
// 1. Explicit CLI flag values (if non-empty)
// 2. Environment variables: LOBE_SERVER_URL, LOBE_HUMAN_TOKEN, LOBE_USER
// 3. Configuration files: ~/.config/lobe/config.json, ~/.config/lobe/human.token
// 4. Defaults
func Resolve(flagURL, flagToken, flagReviewer string) (*Config, error) {
	cfg := &Config{
		ServerURL: DefaultServerURL,
	}

	home, err := os.UserHomeDir()
	if err == nil && home != "" {
		cfgDir := filepath.Join(home, ".config", "lobe")

		// Try ~/.config/lobe/config.json
		cfgFile := filepath.Join(cfgDir, "config.json")
		if data, err := os.ReadFile(cfgFile); err == nil {
			var fileCfg Config
			if err := json.Unmarshal(data, &fileCfg); err == nil {
				if fileCfg.ServerURL != "" {
					cfg.ServerURL = fileCfg.ServerURL
				}
				if fileCfg.HumanToken != "" {
					warnIfOpen(cfgFile)
					cfg.HumanToken = fileCfg.HumanToken
				}
				if fileCfg.Reviewer != "" {
					cfg.Reviewer = fileCfg.Reviewer
				}
			}
		}

		// Try ~/.config/lobe/human.token if token not already set
		tokenFile := filepath.Join(cfgDir, "human.token")
		if _, err := os.Stat(tokenFile); err == nil {
			warnIfOpen(tokenFile)
			if cfg.HumanToken == "" {
				if data, err := os.ReadFile(tokenFile); err == nil {
					cfg.HumanToken = strings.TrimSpace(string(data))
				}
			}
		}
	}

	// Environment variable overrides
	if envURL := os.Getenv("LOBE_SERVER_URL"); envURL != "" {
		cfg.ServerURL = envURL
	}
	if envToken := os.Getenv("LOBE_HUMAN_TOKEN"); envToken != "" {
		cfg.HumanToken = envToken
	}
	if envUser := os.Getenv("LOBE_USER"); envUser != "" {
		cfg.Reviewer = envUser
	}

	// CLI flag overrides (highest precedence)
	if flagURL != "" {
		cfg.ServerURL = flagURL
	}
	if flagToken != "" {
		cfg.HumanToken = flagToken
	}
	if flagReviewer != "" {
		cfg.Reviewer = flagReviewer
	}

	// Default reviewer fallback if still empty
	if cfg.Reviewer == "" {
		if u, err := user.Current(); err == nil && u.Username != "" {
			cfg.Reviewer = u.Username
		} else if envUser := os.Getenv("USER"); envUser != "" {
			cfg.Reviewer = envUser
		} else {
			cfg.Reviewer = "human"
		}
	}

	// Trim trailing slashes from server URL
	cfg.ServerURL = strings.TrimRight(cfg.ServerURL, "/")

	return cfg, nil
}

// warnIfOpen warns when a file holding the human token is readable by group or others.
func warnIfOpen(path string) {
	if info, err := os.Stat(path); err == nil && info.Mode().Perm()&0077 != 0 {
		fmt.Fprintf(os.Stderr, "Warning: %s has permissive permissions (%04o). Recommended: chmod 0600 %s\n", path, info.Mode().Perm(), path)
	}
}
