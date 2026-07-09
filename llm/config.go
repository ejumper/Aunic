package llm

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// Config is the flattened per-run configuration a harness run receives.
type Config struct {
	// Harness selects the Aunic-side dispatch backend (e.g. "pi"). This is
	// the key of the aunic.json "harnesses" block the active model came
	// from. There is no more built-in (harness-less) execution path — every
	// model must resolve to a harness Aunic knows how to run.
	Harness string
	// HarnessName is the display name for Harness (from the harness block's
	// "name" field, or a capitalized fallback derived from the key).
	HarnessName string

	// Model is the canonical model ID passed to the harness (e.g. Pi's
	// --model value, "addie/local" or "openrouter/z-ai/glm-5.2").
	Model string
	// ModelKey is the aunic.json alias key (for an explicit "models" entry)
	// or the raw discovered ID (when the harness's model list wasn't
	// whitelisted). Combined with Harness as "harness/modelKey" for the
	// "model" field, the model picker, and persisted per-file state.
	ModelKey string
	// ModelName is the display name shown in the model picker and indicator.
	ModelName string

	// Voice TTS settings, read from the "voice" block in aunic.json.
	VoiceTTSEndpoint string // default "http://localhost:8880"
	VoiceTTSVoice    string // default "natalie_p3"

	parseError string // non-empty when the config file was found but malformed
}

// Err returns a non-empty string if the config file was found but could not be
// parsed. The caller can display this in the indicator area on first launch.
func (c Config) Err() string { return c.parseError }

// ── Config file schema ────────────────────────────────────────────────────────
//
// aunic.json is keyed by harness, not by raw HTTP provider — Aunic has no
// built-in model execution of its own, so every entry under "harnesses" must
// correspond to a harness Aunic knows how to launch (see harnessDiscoverers
// below). Omitting a harness's "models" block auto-discovers every model the
// harness is itself configured to offer (for "pi", that's the enabledModels
// list in ~/.pi/agent/settings.json). Adding an explicit "models" block
// restricts the picker to just those entries and lets you assign short
// aliases and display names. See _notes/configs/adding-models.md.

type fileConfig struct {
	Harnesses map[string]harnessConfig `json:"harnesses"`
	Model     string                   `json:"model"` // "harness/model"
	Voice     voiceFileConfig          `json:"voice,omitempty"`
}

type voiceFileConfig struct {
	TTSEndpoint string `json:"tts_endpoint"`
	TTSVoice    string `json:"tts_voice"`
	STTEndpoint string `json:"stt_endpoint"`
}

// harnessConfig is one entry under the top-level "harnesses" map.
type harnessConfig struct {
	// Name is the display name for this harness. Defaults to a capitalized
	// version of the map key when omitted.
	Name string `json:"name,omitempty"`
	// Models is an explicit whitelist of models to expose from this harness.
	// When omitted (or empty), every model the harness is itself configured
	// to offer is used instead — see harnessDiscoverers.
	Models map[string]modelConfig `json:"models,omitempty"`
}

// modelConfig is one entry under a harness's "models" map.
type modelConfig struct {
	// Name is the display name shown in the picker. Defaults to ID, or the
	// map key if ID is also empty.
	Name string `json:"name,omitempty"`
	// ID is the canonical model ID passed to the harness. Defaults to the
	// model's key in the parent harnessConfig.Models map.
	ID string `json:"id,omitempty"`
}

// modelID returns the harness-facing model ID, falling back to the map key.
func modelID(m modelConfig, key string) string {
	if m.ID != "" {
		return m.ID
	}
	return key
}

// modelDisplayName returns the picker display name, falling back to the
// last "/"-separated segment of the ID, then of the map key. Harness model
// IDs are often fully-qualified (e.g. "addie/local",
// "openrouter/minimax/minimax-m3") — the last segment is the part a human
// actually recognizes as "the model name".
func modelDisplayName(m modelConfig, key string) string {
	if m.Name != "" {
		return m.Name
	}
	if m.ID != "" {
		return lastSegment(m.ID)
	}
	return lastSegment(key)
}

// lastSegment returns the substring after the final "/", or the whole
// string if it contains no "/".
func lastSegment(s string) string {
	if i := strings.LastIndex(s, "/"); i >= 0 {
		return s[i+1:]
	}
	return s
}

// defaultHarnessName derives a display name from a harness key when the
// config doesn't set one explicitly (e.g. "pi" -> "Pi").
func defaultHarnessName(key string) string {
	key = strings.ReplaceAll(key, "-", " ")
	if key == "" {
		return key
	}
	return strings.ToUpper(key[:1]) + key[1:]
}

// ── Harness model discovery ───────────────────────────────────────────────────

// harnessDiscoverers maps a harness key to a function returning the models
// that harness is itself configured to offer, used when aunic.json doesn't
// list an explicit "models" whitelist for that harness. A harness with no
// discoverer here (or one whose discovery fails) simply contributes no
// default models — its aunic.json block must list "models" explicitly.
var harnessDiscoverers = map[string]func() map[string]modelConfig{
	"pi":     discoverPiModels,
	"claude": discoverClaudeModels,
}

// discoverPiModels reads Pi's own curated model list — enabledModels in
// ~/.pi/agent/settings.json — rather than Pi's full catalog (which spans
// hundreds of models across every provider Pi supports). Each entry is
// already a fully-qualified "provider/model" string, which doubles as both
// the map key and the ID passed to `pi --model`.
func discoverPiModels() map[string]modelConfig {
	home, err := os.UserHomeDir()
	if err != nil {
		return nil
	}
	data, err := os.ReadFile(filepath.Join(home, ".pi", "agent", "settings.json"))
	if err != nil {
		return nil
	}
	var s struct {
		EnabledModels []string `json:"enabledModels"`
	}
	if err := json.Unmarshal(data, &s); err != nil {
		return nil
	}
	models := make(map[string]modelConfig, len(s.EnabledModels))
	for _, id := range s.EnabledModels {
		if id == "" {
			continue
		}
		models[id] = modelConfig{ID: id}
	}
	return models
}

// discoverClaudeModels returns Claude Code's documented model aliases as the
// default catalog. Unlike Pi, there's no external curated-model config file
// to read — `claude --help` documents a small, effectively static alias set
// ("Provide an alias for the latest model (e.g. 'fable', 'opus', or
// 'sonnet') or a model's full name"). Keep this list minimal; update only if
// the documented aliases change.
func discoverClaudeModels() map[string]modelConfig {
	return map[string]modelConfig{
		"sonnet": {ID: "sonnet", Name: "Sonnet"},
		"opus":   {ID: "opus", Name: "Opus"},
		"haiku":  {ID: "haiku", Name: "Haiku"},
		"fable":  {ID: "fable", Name: "Fable"},
	}
}

// harnessModels resolves the models to expose for one harness block: its
// explicit whitelist if set, otherwise the harness's own discovered catalog.
func harnessModels(key string, h harnessConfig) map[string]modelConfig {
	if len(h.Models) > 0 {
		return h.Models
	}
	if discover, ok := harnessDiscoverers[key]; ok {
		return discover()
	}
	return nil
}

// ── Loader ────────────────────────────────────────────────────────────────────

// configFilePath returns the path to aunic.json, honouring XDG_CONFIG_HOME.
func configFilePath() string {
	base := os.Getenv("XDG_CONFIG_HOME")
	if base == "" {
		home, _ := os.UserHomeDir()
		base = filepath.Join(home, ".config")
	}
	return filepath.Join(base, "aunic", "aunic.json")
}

// loadFileConfig reads and parses aunic.json. ok is false if the file is
// missing or malformed.
func loadFileConfig() (fileConfig, bool) {
	data, err := os.ReadFile(configFilePath())
	if err != nil {
		return fileConfig{}, false
	}
	var fc fileConfig
	if err := json.Unmarshal(data, &fc); err != nil {
		return fileConfig{}, false
	}
	return fc, true
}

// LoadConfig reads ~/.config/aunic/aunic.json. If the file is missing or
// unreadable it falls back to the AUNIC_LLM_MODEL environment variable.
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

// parseFileConfig resolves the active harness/model and returns a flat Config.
func parseFileConfig(data []byte) (Config, error) {
	var fc fileConfig
	if err := json.Unmarshal(data, &fc); err != nil {
		return Config{}, fmt.Errorf("aunic.json: %w", err)
	}

	ref := fc.Model // e.g. "pi/addie/local"
	if ref == "" {
		return Config{}, fmt.Errorf("aunic.json: \"model\" field is required")
	}

	harnessKey, modelKey, ok := strings.Cut(ref, "/")
	if !ok {
		return Config{}, fmt.Errorf("aunic.json: model %q must be in \"harness/model\" form", ref)
	}

	h, exists := fc.Harnesses[harnessKey]
	if !exists {
		return Config{}, fmt.Errorf("aunic.json: harness %q not found", harnessKey)
	}
	mod, exists := harnessModels(harnessKey, h)[modelKey]
	if !exists {
		return Config{}, fmt.Errorf("aunic.json: model %q not found in harness %q", modelKey, harnessKey)
	}

	return buildConfig(harnessKey, harnessDisplayName(harnessKey, h), modelKey, mod, fc.Voice), nil
}

// harnessDisplayName returns the harness block's configured name, or the
// derived default when it's unset.
func harnessDisplayName(key string, h harnessConfig) string {
	if h.Name != "" {
		return h.Name
	}
	return defaultHarnessName(key)
}

// buildConfig assembles the flat Config for one resolved harness/model pair.
func buildConfig(harnessKey, harnessName, modelKey string, mod modelConfig, voice voiceFileConfig) Config {
	ttsEndpoint := voice.TTSEndpoint
	if ttsEndpoint == "" {
		ttsEndpoint = "http://localhost:8880"
	}
	ttsVoice := voice.TTSVoice
	if ttsVoice == "" {
		ttsVoice = "natalie_p3"
	}
	return Config{
		Harness:          harnessKey,
		HarnessName:      harnessName,
		Model:            modelID(mod, modelKey),
		ModelKey:         modelKey,
		ModelName:        modelDisplayName(mod, modelKey),
		VoiceTTSEndpoint: ttsEndpoint,
		VoiceTTSVoice:    ttsVoice,
	}
}

// loadEnvConfig falls back to the AUNIC_LLM_MODEL environment variable
// ("harness/model") when aunic.json is missing entirely.
func loadEnvConfig() Config {
	ref := os.Getenv("AUNIC_LLM_MODEL")
	harnessKey, modelKey, _ := strings.Cut(ref, "/")
	return Config{
		Harness:     harnessKey,
		HarnessName: defaultHarnessName(harnessKey),
		Model:       modelKey,
		ModelKey:    modelKey,
		ModelName:   modelKey,
	}
}

// ── Config file helpers exposed to the UI ─────────────────────────────────────

// ModelEntry is one selectable model from aunic.json, with all fields needed
// to switch to it or display it in the model picker.
type ModelEntry struct {
	HarnessKey  string `json:"harnessKey"`
	ModelKey    string `json:"modelKey"`
	HarnessName string `json:"harnessName"`
	ModelName   string `json:"modelName"`
}

// AllModels returns every model across every configured harness, sorted by
// harness key then model key. Returns nil if aunic.json is missing or
// unparseable. Harnesses without an explicit "models" whitelist contribute
// their auto-discovered catalog (see harnessDiscoverers); a harness with no
// discoverer and no whitelist contributes nothing.
func AllModels() []ModelEntry {
	fc, ok := loadFileConfig()
	if !ok {
		return nil
	}
	var entries []ModelEntry
	for hk, h := range fc.Harnesses {
		name := harnessDisplayName(hk, h)
		for mk, m := range harnessModels(hk, h) {
			entries = append(entries, ModelEntry{
				HarnessKey:  hk,
				ModelKey:    mk,
				HarnessName: name,
				ModelName:   modelDisplayName(m, mk),
			})
		}
	}
	sort.Slice(entries, func(i, j int) bool {
		if entries[i].HarnessKey != entries[j].HarnessKey {
			return entries[i].HarnessKey < entries[j].HarnessKey
		}
		return entries[i].ModelKey < entries[j].ModelKey
	})
	return entries
}

// ConfigForModel builds a Config for any harness/model pair in aunic.json.
func ConfigForModel(harnessKey, modelKey string) (Config, error) {
	fc, ok := loadFileConfig()
	if !ok {
		return Config{}, fmt.Errorf("cannot read aunic.json")
	}
	h, exists := fc.Harnesses[harnessKey]
	if !exists {
		return Config{}, fmt.Errorf("harness %q not found", harnessKey)
	}
	mod, exists := harnessModels(harnessKey, h)[modelKey]
	if !exists {
		return Config{}, fmt.Errorf("model %q not found in harness %q", modelKey, harnessKey)
	}
	return buildConfig(harnessKey, harnessDisplayName(harnessKey, h), modelKey, mod, fc.Voice), nil
}
