# Configurable Ollama Models

## What changed

The Ollama model names are now stored in `config/model_settings.json` instead of being hard-coded in the pipeline configuration.

The file currently contains:

```json
{
  "window_verifier_model": "qwen3:4b",
  "answer_model": "qwen3:8b"
}
```

The command-line flags `--window-model` and `--answer-model` still override the configured values for a single run.

## Why

The answer model is the component with the largest effect on local resource usage. Keeping the model choice in a dedicated configuration file makes it possible to change models without editing Python source code.

The default answer model was changed from `qwen3:14b` to `qwen3:8b` to better match the current local-machine workload.
