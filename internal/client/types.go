package client

// VerifiedStamp represents a page's verification frontmatter block.
type VerifiedStamp struct {
	By  string `json:"by"`
	At  string `json:"at"`
	Sha string `json:"sha"`
}

// VerifyStatus represents the response from GET /api/lobes/{lobe}/pages/{slug}/verify.
type VerifyStatus struct {
	Lobe       string         `json:"lobe"`
	Slug       string         `json:"slug"`
	Hash       string         `json:"hash"`
	Verified   *VerifiedStamp `json:"verified"`
	IsVerified bool           `json:"is_verified"`
	IsStale    bool           `json:"is_stale"`
}

// VerifyResult represents the response from POST /api/lobes/{lobe}/pages/{slug}/verify.
type VerifyResult struct {
	Lobe      string        `json:"lobe"`
	Slug      string        `json:"slug"`
	Sha       string        `json:"sha"`
	Verified  VerifiedStamp `json:"verified"`
	Committed bool          `json:"committed"`
	Commit    string        `json:"commit"`
	Pushed    bool          `json:"pushed"`
	PushError *string       `json:"push_error"`
}

// PageContent represents the response from GET /api/lobes/{lobe}/pages/{slug}.
type PageContent struct {
	Lobe     string   `json:"lobe"`
	Slug     string   `json:"slug"`
	Path     string   `json:"path"`
	Sha      string   `json:"sha"`
	Lines    int      `json:"lines"`
	Sections []string `json:"sections"`
	Content  string   `json:"content"`
}

// LintResult represents the response from GET /api/lobes/{lobe}/lint.
type LintResult struct {
	ReturnCode int    `json:"returncode"`
	Stdout     string `json:"stdout"`
	Stderr     string `json:"stderr"`
}

// LobeInfo represents one lobe from GET /api/lobes.
type LobeInfo struct {
	Name     string `json:"name"`
	Path     string `json:"path"`
	Remote   string `json:"remote"`
	Autopush bool   `json:"autopush"`
}

// LobeListResult represents the response from GET /api/lobes.
type LobeListResult struct {
	Lobes []LobeInfo `json:"lobes"`
}

// SyncResult represents the response from POST /api/lobes/{lobe}/sync.
type SyncResult struct {
	Lobe      string  `json:"lobe"`
	Status    string  `json:"status"`
	Commit    string  `json:"commit"`
	Pulled    bool    `json:"pulled"`
	Pushed    bool    `json:"pushed"`
	PushError *string `json:"push_error"`
}

// ExportFile represents an individual file inside an OKF export.
type ExportFile struct {
	Path    string `json:"path"`
	Content string `json:"content"`
}

// ExportOKFResult represents the response from GET /api/lobes/{lobe}/export-okf.
type ExportOKFResult struct {
	Lobe       string       `json:"lobe"`
	OKFVersion string       `json:"okf_version"`
	PageCount  int          `json:"page_count"`
	Files      []ExportFile `json:"files"`
}

// APIError represents an error response from the lobe-server API.
type APIError struct {
	StatusCode int
	Message    string
}

func (e *APIError) Error() string {
	return e.Message
}
