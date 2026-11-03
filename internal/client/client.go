package client

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// UserAgent is sent with every request; main sets it to include the build version.
var UserAgent = "lobectl"

// Client interacts with the lobe-server REST API.
type Client struct {
	BaseURL    string
	Token      string
	HTTPClient *http.Client
}

// New creates a new Client.
func New(serverURL, token string) *Client {
	return &Client{
		BaseURL: strings.TrimRight(serverURL, "/"),
		Token:   token,
		HTTPClient: &http.Client{
			Timeout: 60 * time.Second,
		},
	}
}

func (c *Client) doRequest(ctx context.Context, method, endpoint string, body any, out any) error {
	reqURL := fmt.Sprintf("%s%s", c.BaseURL, endpoint)

	var bodyReader io.Reader
	if body != nil {
		data, err := json.Marshal(body)
		if err != nil {
			return fmt.Errorf("marshal request body: %w", err)
		}
		bodyReader = bytes.NewReader(data)
	}

	req, err := http.NewRequestWithContext(ctx, method, reqURL, bodyReader)
	if err != nil {
		return fmt.Errorf("create request: %w", err)
	}

	req.Header.Set("User-Agent", UserAgent)
	if bodyReader != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if c.Token != "" {
		req.Header.Set("Authorization", "Bearer "+c.Token)
	}

	resp, err := c.HTTPClient.Do(req)
	if err != nil {
		return fmt.Errorf("execute request: %w", err)
	}
	defer resp.Body.Close()

	respBytes, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("read response body: %w", err)
	}

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		var errObj struct {
			Error string `json:"error"`
		}
		msg := string(respBytes)
		if json.Unmarshal(respBytes, &errObj) == nil && errObj.Error != "" {
			msg = errObj.Error
		}
		return &APIError{
			StatusCode: resp.StatusCode,
			Message:    fmt.Sprintf("server returned %d: %s", resp.StatusCode, msg),
		}
	}

	if out != nil && len(respBytes) > 0 {
		if err := json.Unmarshal(respBytes, out); err != nil {
			return fmt.Errorf("unmarshal response JSON: %w (raw response: %s)", err, string(respBytes))
		}
	}

	return nil
}

// GetVerifyStatus fetches the verification status of a wiki page.
func (c *Client) GetVerifyStatus(ctx context.Context, lobe, slug string) (*VerifyStatus, error) {
	endpoint := fmt.Sprintf("/api/lobes/%s/pages/%s/verify", url.PathEscape(lobe), url.PathEscape(slug))
	var st VerifyStatus
	if err := c.doRequest(ctx, http.MethodGet, endpoint, nil, &st); err != nil {
		return nil, err
	}
	return &st, nil
}

// VerifyPage sends a human verification request with the expected content hash.
func (c *Client) VerifyPage(ctx context.Context, lobe, slug, expectedHash, by string) (*VerifyResult, error) {
	endpoint := fmt.Sprintf("/api/lobes/%s/pages/%s/verify", url.PathEscape(lobe), url.PathEscape(slug))
	payload := map[string]string{
		"expected_hash": expectedHash,
	}
	if by != "" {
		payload["by"] = by
	}

	var res VerifyResult
	if err := c.doRequest(ctx, http.MethodPost, endpoint, payload, &res); err != nil {
		return nil, err
	}
	return &res, nil
}

// GetPage fetches page content, metadata, sections, and hash.
func (c *Client) GetPage(ctx context.Context, lobe, slug string) (*PageContent, error) {
	endpoint := fmt.Sprintf("/api/lobes/%s/pages/%s", url.PathEscape(lobe), url.PathEscape(slug))
	var pc PageContent
	if err := c.doRequest(ctx, http.MethodGet, endpoint, nil, &pc); err != nil {
		return nil, err
	}
	return &pc, nil
}

// GetLint runs server-side linting on the lobe; all lifts the server's 20-issues-per-check limit.
func (c *Client) GetLint(ctx context.Context, lobe string, strict, all bool) (*LintResult, error) {
	endpoint := fmt.Sprintf("/api/lobes/%s/lint?strict=%t&all=%t", url.PathEscape(lobe), strict, all)
	var res LintResult
	if err := c.doRequest(ctx, http.MethodGet, endpoint, nil, &res); err != nil {
		return nil, err
	}
	return &res, nil
}

// ListLobes lists all lobes hosted on lobe-server.
func (c *Client) ListLobes(ctx context.Context) (*LobeListResult, error) {
	var res LobeListResult
	if err := c.doRequest(ctx, http.MethodGet, "/api/lobes", nil, &res); err != nil {
		return nil, err
	}
	return &res, nil
}

// SyncLobe triggers remote git fetch/pull/rebase and push on lobe-server.
func (c *Client) SyncLobe(ctx context.Context, lobe string) (*SyncResult, error) {
	endpoint := fmt.Sprintf("/api/lobes/%s/sync", url.PathEscape(lobe))
	var res SyncResult
	if err := c.doRequest(ctx, http.MethodPost, endpoint, map[string]any{}, &res); err != nil {
		return nil, err
	}
	return &res, nil
}

// ExportOKF exports the lobe's pages in Google Open Knowledge Format v0.2.
func (c *Client) ExportOKF(ctx context.Context, lobe string) (*ExportOKFResult, error) {
	endpoint := fmt.Sprintf("/api/lobes/%s/export-okf", url.PathEscape(lobe))
	var res ExportOKFResult
	if err := c.doRequest(ctx, http.MethodGet, endpoint, nil, &res); err != nil {
		return nil, err
	}
	return &res, nil
}
