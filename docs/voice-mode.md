# Voice Mode

Koder supports interactive voice dictation in terminal sessions.

Current interaction model:

- Enable voice with `/voice`
- Double-tap `Space` in interactive mode to start recording
- Press `Space` or `Enter` to stop recording
- Koder transcribes the audio and auto-sends the transcript as a user message
- When partial transcripts are available, they are shown directly in the input box while transcribing

## Current Scope

Implemented now:

- Interactive push-to-talk style dictation using the terminal prompt
- Provider-backed speech-to-text for:
  - `openai`
  - `chatgpt`
  - `google`
  - `gemini`
  - `azure`
- Top-level `voice` configuration in `~/.koder/config.yaml`
- `/voice`, `/voice status`, `/voice provider ...`

Not fully finished yet:

- Real-device validation across all platforms
- Windows-specific bring-up and troubleshooting
- Full voice subsystem integration

## Config File

Voice settings live at the top level of `~/.koder/config.yaml`:

```yaml
voice:
  enabled: false
  provider: null
  model: null
  api_key: null
  base_url: null
  api_version: null
```

Fields:

- `voice.enabled`
  - Enables interactive voice dictation
- `voice.provider`
  - Overrides the chat model provider for voice
  - Supported values: `openai`, `chatgpt`, `google`, `gemini`, `azure`
- `voice.model`
  - Optional transcription model override
- `voice.api_key`
  - Optional voice-specific API key override
- `voice.base_url`
  - Optional voice-specific base URL override
- `voice.api_version`
  - Optional API version override
  - Currently only used for Azure

Resolution order:

1. `voice.*`
2. OAuth-backed provider credentials where supported
3. provider-specific env vars
4. `model.*` fallback when provider family matches

## Commands

Voice mode commands:

```bash
/voice
/voice status
/voice provider openai
/voice provider google
/voice provider azure
/voice provider clear
```

`/voice status` reports:

- `voice_enabled`
- `voice_provider`
- `voice_model`
- `voice_base_url`
- `voice_api_version`
- `effective_provider`

## Provider Config

### OpenAI

Use when you want OpenAI-native transcription models.

```yaml
model:
  provider: openai
  name: gpt-5.4

voice:
  enabled: true
  provider: openai
  model: gpt-4o-mini-transcribe
  api_key: sk-...
  base_url: https://api.openai.com/v1
```

Notes:

- If `voice.provider` is omitted, Koder falls back to `model.provider`
- If `voice.model` is omitted, the current default is `gpt-4o-mini-transcribe`

### ChatGPT OAuth

Use when you want to keep chat and voice on OpenAI-family routing but authenticate via `koder auth login chatgpt`.

```yaml
model:
  provider: chatgpt
  name: gpt-5.2

voice:
  enabled: true
  provider: chatgpt
  model: gpt-4o-mini-transcribe
```

Notes:

- `voice.api_key` is optional if you already authenticated with:
  - `koder auth login chatgpt`
- `voice.base_url` can still be set if you need a custom OpenAI-compatible endpoint

### Google

Use when you want Gemini-backed audio transcription.

```yaml
model:
  provider: google
  name: gemini/gemini-2.5-pro

voice:
  enabled: true
  provider: google
  model: gemini-2.5-flash
  api_key: your-google-api-key
```

Notes:

- `voice.base_url` is not normally needed for Google/Gemini
- If `voice.model` is omitted, the current default is `gemini-2.5-flash`

### Gemini

Equivalent to Google-family voice routing, but explicit if your main model naming already uses `gemini`.

```yaml
model:
  provider: gemini
  name: gemini/gemini-2.5-pro

voice:
  enabled: true
  provider: gemini
  model: gemini-2.5-flash
  api_key: your-gemini-api-key
```

### Azure OpenAI

Use when your chat and transcription deployments are hosted on Azure OpenAI.

```yaml
model:
  provider: azure
  name: your-chat-deployment
  api_key: your-azure-api-key
  base_url: https://YOUR_RESOURCE.openai.azure.com/openai/deployments/your-chat-deployment
  azure_api_version: 2025-04-01-preview

voice:
  enabled: true
  provider: azure
  model: your-transcribe-deployment
  api_key: your-azure-api-key
  base_url: https://YOUR_RESOURCE.openai.azure.com/openai/deployments/your-transcribe-deployment
  api_version: 2025-04-01-preview
```

Azure notes:

- `voice.model` should be your transcription deployment name
- `voice.base_url` can be either:
  - the resource root, like `https://YOUR_RESOURCE.openai.azure.com`
  - or a deployment URL containing `/openai/deployments/...`
- Koder extracts the Azure endpoint from `voice.base_url`
- `voice.api_version` overrides `model.azure_api_version`

## Provider Selection Strategy

Recommended setup patterns:

- Same provider for chat and voice
  - Set `model.provider`
  - Omit `voice.provider`
- Different provider for voice
  - Set `voice.provider`
  - Optionally set `voice.model`, `voice.api_key`, `voice.base_url`

Example: chat on OpenAI, voice on Google

```yaml
model:
  provider: openai
  name: gpt-5.4

voice:
  enabled: true
  provider: google
  model: gemini-2.5-flash
  api_key: your-google-api-key
```

## Troubleshooting

If `/voice` says credentials are missing:

- For `chatgpt`:
  - run `koder auth login chatgpt`
  - or set `voice.api_key`
- For `google`:
  - run `koder auth login google`
  - or set `voice.api_key`
- For `openai`:
  - set `voice.api_key`
- For `azure`:
  - set `voice.api_key`
  - set `voice.base_url`
  - set `voice.model`
  - usually set `voice.api_version`

If recording fails:

- Make sure the `sounddevice` dependency is installed via `uv sync`
- Verify your microphone is available to the terminal process
- Try again and read the full `Voice error: ...` text shown in the input box

If the wrong provider is used:

- check `/voice status`
- verify `voice.provider`
- verify `effective_provider`

## Example Full Config

```yaml
model:
  provider: azure
  name: your-chat-deployment
  api_key: your-azure-api-key
  base_url: https://YOUR_RESOURCE.openai.azure.com/openai/deployments/your-chat-deployment
  azure_api_version: 2025-04-01-preview

cli:
  stream: true

voice:
  enabled: true
  provider: azure
  model: your-transcribe-deployment
  api_key: your-azure-api-key
  base_url: https://YOUR_RESOURCE.openai.azure.com/openai/deployments/your-transcribe-deployment
  api_version: 2025-04-01-preview
```
