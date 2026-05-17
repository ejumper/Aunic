package llm

import (
	"github.com/charmbracelet/openai-go"
	"github.com/charmbracelet/openai-go/option"
)

func NewClient(cfg Config) openai.Client {
	opts := []option.RequestOption{option.WithBaseURL(cfg.BaseURL)}
	if cfg.APIKey != "" {
		opts = append(opts, option.WithAPIKey(cfg.APIKey))
	}
	return openai.NewClient(opts...)
}
