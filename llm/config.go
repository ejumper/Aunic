package llm

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// Config is the flattened per-run configuration the runner receives.
type Config struct {
	BaseURL string
	APIKey  string
	Model   string // bare model ID sent to the API (e.g. "qwen9b")

	// Display-only fields, populated from the config file.
	ProviderName string // e.g. "LLaMA.cpp"
	ModelName    string // e.g. "Qwen 3.5 9B"
	ProviderKey  string // e.g. "llamacpp"
	ModelKey     string // e.g. "qwen9b"

	// ProviderKind selects the runner backend. "" or "openai" → OpenAI-compatible
	// HTTP client. "agent_sdk" → Claude Agent SDK via the Node.js bridge.
	ProviderKind string
	// Effort maps to the Agent SDK's effort level when ProviderKind == "agent_sdk".
	// Valid values: "low", "medium", "high", "xhigh", "max". Empty → "medium".
	Effort string

	parseError string // non-empty when the config file was found but malformed
}

// Err returns a non-empty string if the config file was found but could not be
// parsed. The caller can display this in the indicator area on first launch.
func (c Config) Err() string { return c.parseError }

// ── Config file schema ────────────────────────────────────────────────────────

type fileConfig struct {
	Providers map[string]providerConfig `json:"providers"`
	Model     string                    `json:"model"` // "provider/model-id"
}

type providerConfig struct {
	Name    string                 `json:"name"`
	BaseURL string                 `json:"base_url"`
	APIKey  string                 `json:"api_key,omitempty"` // may be "{env:VAR}"
	Models  map[string]modelConfig `json:"models"`
	// Kind selects the backend. "" or "openai" → OpenAI-compatible HTTP client
	// (the default). "agent_sdk" → Claude Agent SDK via the Node.js bridge.
	Kind string `json:"kind,omitempty"`
	// Enabled is honoured for agent_sdk providers: when false, the provider's
	// models are filtered out of AllModels() (so they don't appear in the
	// picker). openai-kind providers default to enabled.
	Enabled *bool `json:"enabled,omitempty"`
}

type modelConfig struct {
	Name string `json:"name"`
	// ID is the canonical model ID sent to the SDK or API. Defaults to the
	// model's key in the parent providerConfig.Models map.
	ID string `json:"id,omitempty"`
	// Effort maps to the Agent SDK's effort level. Only meaningful when the
	// parent provider's Kind == "agent_sdk".
	Effort string `json:"effort,omitempty"`
}

// providerEnabled returns true when the provider is enabled. openai-kind
// providers default to enabled; agent_sdk-kind providers default to disabled
// (must be opted in explicitly).
func providerEnabled(p providerConfig) bool {
	if p.Enabled != nil {
		return *p.Enabled
	}
	return p.Kind != "agent_sdk"
}

// modelID returns the API-facing model ID, falling back to the map key.
func modelID(m modelConfig, key string) string {
	if m.ID != "" {
		return m.ID
	}
	return key
}

// ── Loader ────────────────────────────────────────────────────────────────────

const defaultBaseURL = "https://api.openai.com/v1"

// configFilePath returns the path to aunic.json, honouring XDG_CONFIG_HOME.
func configFilePath() string {
	base := os.Getenv("XDG_CONFIG_HOME")
	if base == "" {
		home, _ := os.UserHomeDir()
		base = filepath.Join(home, ".config")
	}
	return filepath.Join(base, "aunic", "aunic.json")
}

// LoadConfig reads ~/.config/aunic/aunic.json. If the file is missing or
// unreadable it falls back to the AUNIC_LLM_* environment variables.
func LoadConfig() Config {
	path := configFilePath()
	data, err := os.ReadFile(path)
	if err == nil {
		cfg, parseErr := parseFileConfig(data)
		if parseErr == nil {
			return cfg
		}
		// Config file exists but is malformed — surface the error via a
		// sentinel so the app can display it.
		return Config{parseError: parseErr.Error()}
	}
	return loadEnvConfig()
}

// parseFileConfig resolves the active provider/model and returns a flat Config.
func parseFileConfig(data []byte) (Config, error) {
	var fc fileConfig
	if err := json.Unmarshal(data, &fc); err != nil {
		return Config{}, fmt.Errorf("aunic.json: %w", err)
	}

	ref := fc.Model // e.g. "llamacpp/qwen9b"
	if ref == "" {
		return Config{}, fmt.Errorf("aunic.json: \"model\" field is required")
	}

	provKey, modelKey, ok := strings.Cut(ref, "/")
	if !ok {
		return Config{}, fmt.Errorf("aunic.json: model %q must be in \"provider/model\" form", ref)
	}

	prov, exists := fc.Providers[provKey]
	if !exists {
		return Config{}, fmt.Errorf("aunic.json: provider %q not found", provKey)
	}
	mod, exists := prov.Models[modelKey]
	if !exists {
		return Config{}, fmt.Errorf("aunic.json: model %q not found in provider %q", modelKey, provKey)
	}

	baseURL := prov.BaseURL
	if baseURL == "" {
		baseURL = defaultBaseURL
	}

	return Config{
		BaseURL:      baseURL,
		APIKey:       resolveEnvRef(prov.APIKey),
		Model:        modelID(mod, modelKey),
		ProviderName: prov.Name,
		ModelName:    mod.Name,
		ProviderKey:  provKey,
		ModelKey:     modelKey,
		ProviderKind: prov.Kind,
		Effort:       mod.Effort,
	}, nil
}

// loadEnvConfig falls back to AUNIC_LLM_* environment variables.
func loadEnvConfig() Config {
	baseURL := os.Getenv("AUNIC_LLM_BASE_URL")
	if baseURL == "" {
		baseURL = defaultBaseURL
	}
	return Config{
		BaseURL: baseURL,
		APIKey:  os.Getenv("AUNIC_LLM_API_KEY"),
		Model:   os.Getenv("AUNIC_LLM_MODEL"),
	}
}

// resolveEnvRef replaces "{env:VAR_NAME}" with the named environment variable.
// Any other value is returned unchanged.
func resolveEnvRef(s string) string {
	if strings.HasPrefix(s, "{env:") && strings.HasSuffix(s, "}") {
		varName := s[5 : len(s)-1]
		return os.Getenv(varName)
	}
	return s
}

// ── Config file helpers exposed to the UI ─────────────────────────────────────

// AllProviders loads aunic.json and returns the full provider map. Returns nil
// on any error (missing file, parse error). Used by the model-picker UI.
func AllProviders() map[string]providerConfig {
	data, err := os.ReadFile(configFilePath())
	if err != nil {
		return nil
	}
	var fc fileConfig
	if err := json.Unmarshal(data, &fc); err != nil {
		return nil
	}
	return fc.Providers
}

// ProviderConfig and ModelConfig are re-exported for callers that need to
// inspect the full list (e.g. model-picker UI).
type ProviderConfig = providerConfig
type ModelConfig = modelConfig

// ModelEntry is one selectable model from aunic.json, with all fields needed
// to switch to it or display it in the model picker.
type ModelEntry struct {
	ProviderKey  string
	ModelKey     string
	ProviderName string
	ModelName    string
}

// AllModels returns all configured models sorted by provider key then model key.
// Returns nil if aunic.json is missing or unparseable. Disabled providers
// (notably opt-in agent_sdk providers) are skipped.
func AllModels() []ModelEntry {
	providers := AllProviders()
	if providers == nil {
		return nil
	}
	var entries []ModelEntry
	for pk, p := range providers {
		if !providerEnabled(p) {
			continue
		}
		for mk, m := range p.Models {
			entries = append(entries, ModelEntry{
				ProviderKey:  pk,
				ModelKey:     mk,
				ProviderName: p.Name,
				ModelName:    m.Name,
			})
		}
	}
	sort.Slice(entries, func(i, j int) bool {
		if entries[i].ProviderKey != entries[j].ProviderKey {
			return entries[i].ProviderKey < entries[j].ProviderKey
		}
		return entries[i].ModelKey < entries[j].ModelKey
	})
	return entries
}

// ConfigForModel builds a Config for any provider/model pair in aunic.json.
func ConfigForModel(providerKey, modelKey string) (Config, error) {
	data, err := os.ReadFile(configFilePath())
	if err != nil {
		return Config{}, fmt.Errorf("cannot read aunic.json: %w", err)
	}
	var fc fileConfig
	if err := json.Unmarshal(data, &fc); err != nil {
		return Config{}, fmt.Errorf("aunic.json: %w", err)
	}
	prov, exists := fc.Providers[providerKey]
	if !exists {
		return Config{}, fmt.Errorf("provider %q not found", providerKey)
	}
	mod, exists := prov.Models[modelKey]
	if !exists {
		return Config{}, fmt.Errorf("model %q not found in provider %q", modelKey, providerKey)
	}
	baseURL := prov.BaseURL
	if baseURL == "" {
		baseURL = defaultBaseURL
	}
	return Config{
		BaseURL:      baseURL,
		APIKey:       resolveEnvRef(prov.APIKey),
		Model:        modelID(mod, modelKey),
		ProviderName: prov.Name,
		ModelName:    mod.Name,
		ProviderKey:  providerKey,
		ModelKey:     modelKey,
		ProviderKind: prov.Kind,
		Effort:       mod.Effort,
	}, nil
}
