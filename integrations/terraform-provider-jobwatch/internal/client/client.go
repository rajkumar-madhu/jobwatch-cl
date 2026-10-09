// Package client is a small JobWatch API client for the Terraform provider (R64).
package client

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

// ErrNotFound means the object no longer exists (Terraform removes it from state).
var ErrNotFound = errors.New("not found")

type Client struct {
	BaseURL string
	APIKey  string
	HTTP    *http.Client
	UA      string
}

func New(baseURL, apiKey, version string) *Client {
	return &Client{BaseURL: strings.TrimRight(baseURL, "/"), APIKey: apiKey, HTTP: &http.Client{Timeout: 30 * time.Second},
		UA: "terraform-provider-jobwatch/" + version}
}

// APIError carries the API's own message (e.g. a 422 explaining an invalid schedule).
type APIError struct {
	Status int
	Body   string
}

func (e *APIError) Error() string {
	return fmt.Sprintf("JobWatch API answered HTTP %d: %s", e.Status, e.Body)
}

func (c *Client) do(ctx context.Context, method, path string, in, out any) error {
	var body io.Reader
	if in != nil {
		b, err := json.Marshal(in)
		if err != nil {
			return err
		}
		body = bytes.NewReader(b)
	}
	req, err := http.NewRequestWithContext(ctx, method, c.BaseURL+path, body)
	if err != nil {
		return err
	}
	req.Header.Set("X-API-Key", c.APIKey)
	req.Header.Set("User-Agent", c.UA)
	if in != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(io.LimitReader(resp.Body, 1<<20))
	if resp.StatusCode == http.StatusNotFound {
		return ErrNotFound
	}
	if resp.StatusCode >= 300 {
		return &APIError{Status: resp.StatusCode, Body: strings.TrimSpace(string(raw))}
	}
	if out != nil && len(raw) > 0 {
		return json.Unmarshal(raw, out)
	}
	return nil
}

// ---- jobs ----

type Job struct {
	ID                 string   `json:"id,omitempty"`
	WorkspaceID        string   `json:"workspace_id,omitempty"`
	Name               string   `json:"name"`
	Description        *string  `json:"description"`
	ScheduleExpr       *string  `json:"schedule_expr"`
	TZ                 string   `json:"tz,omitempty"`
	GraceS             *int64   `json:"grace_s,omitempty"`
	ExpectedRuntimeS   *int64   `json:"expected_runtime_s"`
	Tags               []string `json:"tags"`
	Assertions         []string `json:"assertions"`
	Paused             *bool    `json:"paused,omitempty"`
	HeartbeatToken     *string  `json:"heartbeat_token,omitempty"`
	HeartbeatTokenHint string   `json:"heartbeat_token_hint,omitempty"`
	PingURL            *string  `json:"ping_url,omitempty"`
}

func (c *Client) CreateJob(ctx context.Context, j Job) (*Job, error) {
	var out Job
	return &out, c.do(ctx, "POST", "/api/v1/jobs", j, &out)
}

func (c *Client) GetJob(ctx context.Context, id string) (*Job, error) {
	var out Job
	return &out, c.do(ctx, "GET", "/api/v1/jobs/"+id, nil, &out)
}

// UpdateJob sends every managed field; null clears optional ones (the API's PATCH semantics).
func (c *Client) UpdateJob(ctx context.Context, id string, patch map[string]any) (*Job, error) {
	var out Job
	return &out, c.do(ctx, "PATCH", "/api/v1/jobs/"+id, patch, &out)
}

func (c *Client) DeleteJob(ctx context.Context, id string) error {
	return c.do(ctx, "DELETE", "/api/v1/jobs/"+id, nil, nil)
}

type Workspace struct {
	ID   string `json:"id"`
	Name string `json:"name"`
}

// DefaultWorkspace is the organization's first workspace by name, used when none is configured.
func (c *Client) DefaultWorkspace(ctx context.Context) (string, error) {
	var ws []Workspace
	if err := c.do(ctx, "GET", "/api/v1/workspaces", nil, &ws); err != nil {
		return "", err
	}
	if len(ws) == 0 {
		return "", errors.New("the organization has no workspace; set workspace_id")
	}
	return ws[0].ID, nil
}

// ---- maintenance windows ----

type MaintenanceScope struct {
	JobIDs         []string `json:"job_ids,omitempty"`
	Tags           []string `json:"tags,omitempty"`
	EnvironmentIDs []string `json:"environment_ids,omitempty"`
}

type Maintenance struct {
	ID       string           `json:"id,omitempty"`
	Name     string           `json:"name"`
	Scope    MaintenanceScope `json:"scope"`
	StartsAt string           `json:"starts_at"`
	EndsAt   string           `json:"ends_at"`
	TZ       string           `json:"tz"`
	Repeat   string           `json:"repeat,omitempty"`
	Weekdays []string         `json:"weekdays,omitempty"`
	Until    *string          `json:"until,omitempty"`
	RRule    *string          `json:"rrule,omitempty"`
}

func (c *Client) CreateMaintenance(ctx context.Context, m Maintenance) (*Maintenance, error) {
	var out Maintenance
	return &out, c.do(ctx, "POST", "/api/v1/alerts/maintenance", m, &out)
}

func (c *Client) GetMaintenance(ctx context.Context, id string) (*Maintenance, error) {
	var out Maintenance
	return &out, c.do(ctx, "GET", "/api/v1/alerts/maintenance/"+id, nil, &out)
}

func (c *Client) DeleteMaintenance(ctx context.Context, id string) error {
	return c.do(ctx, "DELETE", "/api/v1/alerts/maintenance/"+id, nil, nil)
}
