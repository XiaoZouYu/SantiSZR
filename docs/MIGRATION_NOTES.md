# Migration Notes

## Keep First

- source parsing and download logic
- transcript and copy extraction logic
- rewrite request composition
- TTS request composition
- subtitle generation and FFmpeg post-processing
- TuiliONNX avatar request flow
- typed schemas, worker protocol, and GUI background task architecture

## Defer

- bundled publishing automation
- browser-driven account workflows
- true TuiliONNX runtime execution
- any UI shell logic coupled to `Gradio + dist`

## Remove From The New Architecture

- business logic inside button callbacks
- hidden contracts between page parameters and backend function signatures
- implicit working-directory assumptions
- hard-coded accounts, ports, or machine paths
- default fallbacks into external legacy projects
- duplicate backup modules for the same feature

## Recommended Migration Order

1. stabilize typed contracts
2. migrate content extraction and downloader logic
3. connect rewrite, TTS, subtitle, and avatar integrations
4. add workflow orchestration and task queueing
5. add publishing after the local pipeline is stable

## Current Migration Status

- implemented:
  - content extraction service
  - rewrite service with unified client plus heuristic fallback
  - TTS service with CosyVoice adapter plus builtin WAV fallback
  - subtitle generation and FFmpeg burn-in
  - TuiliONNX-oriented asset rendering path
  - end-to-end workflow orchestration
  - GUI execution entry points for dashboard, copywriting, voice, subtitle, and
    avatar
  - removal of default legacy paths and legacy project fallbacks from
    `src/santiszr`
- explicitly disconnected:
  - legacy avatar engine is no longer exposed in the main workflow
  - publishing no longer shells out to external legacy scripts by default
- still TODO:
  - bundled publisher implementations
  - direct external TuiliONNX runtime execution instead of asset-based rendering
  - browser automation publishing
  - full speech-to-text transcription from arbitrary input audio
