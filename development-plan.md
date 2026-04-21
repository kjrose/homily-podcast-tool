# Development Plan

## Purpose

This plan is intended to move the current application from a working single-installation script into a maintainable, testable, and secure product that can support multiple churches without breaking existing behavior. The immediate goal is not a rewrite. The goal is to preserve the current workflow, add regression protection around it, and then refactor in controlled phases.

## Current System Summary

The application currently performs one end-to-end workflow:

1. Poll S3 for recent `Mass-*.mp3` files.
2. Download matching files locally.
3. Normalize audio and transcribe via a batch file that shells out to Whisper.
4. Read transcript text and use OpenAI to generate liturgical metadata, title, description, and special context.
5. Parse VTT to extract the homily audio segment.
6. Generate cover art and upload audio, image, and post content to WordPress.
7. Store metadata in SQLite and send alert emails on failures.

That workflow is valid, but the implementation is tightly coupled, highly side-effectful, and difficult to test safely.

## Review Findings

### High-severity issues

| Area | Finding | Impact | Evidence |
| --- | --- | --- | --- |
| S3 ingestion | S3 key filtering only matches keys that start with `Mass-`, but listed objects include the configured prefix. | A non-empty `s3.folder` can cause valid MP3s to be skipped entirely. | `homily_monitor/s3_utils.py:100-110` |
| Pipeline control flow | The main loop continues into transcription and transcript processing even if download fails. | Downstream work can run against missing or partial files, causing noisy failures and inconsistent state. | `main.py:53-58`, `homily_monitor/s3_utils.py:142-160` |
| Portability | FFmpeg is hardcoded to a specific user profile path. | The application is not portable to another PC, service account, or church deployment. | `homily_monitor/audio_utils.py:15-16` |
| Data integrity | Homily analyses are inserted without a uniqueness rule or update path, and later reads use `fetchone()` with no ordering. | Reprocessing can create duplicate records and WordPress uploads can use stale metadata unpredictably. | `homily_monitor/database.py:26-35`, `homily_monitor/database.py:69-79`, `homily_monitor/wordpress_utils.py:27-33` |
| WordPress scheduling | Local mass time is labeled as UTC instead of being converted from the church’s timezone. | Draft publish timestamps will be wrong for most real deployments. | `homily_monitor/wordpress_utils.py:53-57`, `homily_monitor/wordpress_utils.py:105-112` |

### Medium-severity issues

| Area | Finding | Impact | Evidence |
| --- | --- | --- | --- |
| Alerting | S3 alert wrappers call `send_email_alert(subject, body)`, but `send_email_alert` always uses the configured transcript subject/body format. | Operational alerts are misleading and harder to act on. | `homily_monitor/s3_utils.py:89-92`, `homily_monitor/email_utils.py:21-35` |
| Networking | WordPress requests do not set timeouts or retry policy. | The service can hang indefinitely and become hard to recover. | `homily_monitor/wordpress_utils.py:77`, `homily_monitor/wordpress_utils.py:94`, `homily_monitor/wordpress_utils.py:121` |
| Coupling | Extraction immediately uploads to WordPress as a side effect. | It is difficult to test extraction independently and to support dry-run or approval workflows. | `homily_monitor/audio_utils.py:481-492` |
| Testability | Configuration, clients, and database connection are created at import time. | Unit tests are brittle and require real config or monkeypatching imports. | `homily_monitor/config_loader.py:37-42`, `homily_monitor/gpt_utils.py:19`, `homily_monitor/database.py:16-20` |
| Incomplete feature path | Weekend deviation detection is partially implemented and currently disabled/commented out. | The feature cannot be trusted and creates dead code paths. | `main.py:60-62`, `homily_monitor/helpers.py:155-211` |

### Lower-severity but important productization issues

| Area | Finding | Impact | Evidence |
| --- | --- | --- | --- |
| Tenant-specific behavior | The homilist is hardcoded as `**HOMILIST**`, and there is no church profile abstraction. | The app is not ready for reuse across churches. | `homily_monitor/wordpress_utils.py:60-61` |
| External dependency management | Whisper is invoked through batch files with no formal contract, version pin, or health validation. | Reproducibility is weak across environments. | `TranscribeHomilies.bat`, `TranscribeWildcard.bat` |
| AI implementation | Model calls are scattered across modules and mixed between older API patterns. | Upgrades are hard, behavior is inconsistent, and regression testing is harder than necessary. | `homily_monitor/gpt_utils.py`, `homily_monitor/helpers.py:197-203`, `homily_monitor/audio_utils.py:413-423` |

## Planning Principles

1. Preserve current behavior first.
2. Add characterization tests before deep refactors.
3. Separate orchestration from side effects.
4. Make every external dependency mockable.
5. Keep church-specific settings out of code.
6. Upgrade AI in a centralized and configurable way.
7. Add observability and rollback paths before major deployment changes.

## Target Outcome

At the end of this plan, the application should:

- Continue to process homilies end to end as it does now.
- Have automated regression tests for the current workflow.
- Have unit tests for parsing, validation, database, S3 filtering, WordPress payload generation, and AI response handling.
- Support dry-run and non-destructive validation modes.
- Be configurable for multiple churches through profiles or tenant config.
- Use centralized, configurable AI providers/models for metadata and image generation.
- Be able to upgrade models on a regular cadence with low-risk configuration changes.
- Be able to switch AI providers behind a stable internal interface when cost, policy, quality, or availability requires it.
- Use safe retries, timeouts, idempotency, and better error handling around external services.

## Testing Strategy

### 1. Characterization and regression tests

Before meaningful refactoring, capture the current behavior with fixtures and golden outputs.

Create fixtures for:

- Representative transcript `.txt` files.
- Representative `.vtt` files.
- A few short sample `.mp3` files or mocked FFmpeg/Whisper calls.
- Sample OpenAI responses for title/description/liturgical metadata.
- Sample WordPress API responses.
- Sample S3 object listings, including prefixed keys.

Regression scenarios to lock down:

- S3 object filtering with and without folder prefixes.
- Transcript validation for blank, repetitive, and valid transcripts.
- Group-key assignment for Saturday vigil vs Sunday.
- Homily extraction start/end detection from VTT.
- WordPress draft payload construction.
- Idempotent reprocessing of the same Mass file.
- Failure paths for S3, OpenAI, FFmpeg, WordPress, and SMTP.

### 2. Unit tests

Use `pytest` with small deterministic tests around pure logic first.

Initial unit test targets:

- `config_loader.load_config`
- `s3_utils.is_file_within_last_48_hours`
- S3 key filtering logic
- `helpers.validate_and_get_transcript`
- `audio_utils.parse_timestamp`
- Homily start/end marker logic
- SQLite repository functions
- WordPress title/date payload builders
- AI response parsing and schema validation

Recommended tooling:

- `pytest`
- `pytest-mock`
- `requests-mock` or `responses`
- `freezegun`
- temp directories and fixture files

### 3. Integration tests

Add integration tests with mocked boundaries rather than live services.

Integration scope:

- Main orchestration using fake S3, fake OpenAI, fake WordPress, fake SMTP.
- Database migrations on a temp SQLite database.
- File-processing workflow on fixture inputs.

### 4. Safe regression gate for future AI changes

AI behavior will evolve, so the test plan must separate:

- Schema correctness
- Prompt contract correctness
- Fallback behavior
- Human-reviewed output quality checks

Add:

- Prompt snapshots with version tags.
- JSON schema validation for AI outputs.
- Recorded mock responses for CI.
- A manual review checklist for prompt/model changes.
- Side-by-side evaluation runs for candidate model/provider replacements.
- A small acceptance dataset for title, description, liturgical metadata, and image-prompt quality scoring.

### 5. AI portability and upgrade governance

The application should treat AI as a replaceable dependency, not as embedded application logic.

Add governance around:

- a model registry that maps each AI task to the currently approved provider, model, prompt version, and fallback
- a capability matrix that defines which providers can handle:
  - transcript metadata extraction
  - title generation
  - description generation
  - VTT fallback reasoning
  - image prompt generation
  - image generation
- a scheduled review cadence for evaluating newer model releases
- a documented rollback path if a new model regresses output quality or cost

Recommended operational policy:

- Review model recommendations on a fixed cadence, such as monthly or quarterly.
- Test candidate upgrades against the acceptance dataset before promotion.
- Promote new models through environments in stages: local review, staging, production.
- Keep the previous approved model/provider configuration available for immediate rollback.
- Record provider, model, prompt version, and evaluation result for each promotion decision.

## Refactoring and Upgrade Roadmap

### Phase 0: Stabilize and protect current behavior

Goal: stop the most dangerous failures and create a baseline for later refactors.

Tasks:

- Fix S3 key filtering to account for configured prefixes.
- Make `download_file` return success/failure and stop the pipeline on failed downloads.
- Ensure local directories exist before writing files.
- Remove the hardcoded FFmpeg path and make resolution configurable with validation.
- Fix WordPress timezone handling by introducing a configurable church timezone.
- Split generic operational alerts from transcript-specific alerts.
- Add request timeouts and retry wrappers for WordPress and SMTP interactions.
- Add a backup step for the SQLite database before schema changes.

Deliverable:

- A stabilized current workflow with minimal code movement.

### Phase 1: Build the test harness

Goal: make the existing workflow safe to refactor.

Tasks:

- Create `tests/unit`, `tests/integration`, and `tests/regression`.
- Add a fixture library for transcripts, VTTs, S3 responses, OpenAI responses, and WordPress responses.
- Add a smoke test for the main orchestration path using fakes.
- Add regression tests around the currently expected WordPress title/content output.
- Add CI to run tests on every change.

Deliverable:

- Automated test suite that protects the current behavior.

### Phase 2: Isolate side effects behind service boundaries

Goal: make the code understandable and mockable.

Refactor direction:

- Move orchestration out of module-level logic into explicit services.
- Replace global singleton initialization with dependency injection.
- Introduce clear boundaries for:
  - configuration
  - storage/repository
  - AI provider
  - transcription runner
  - audio processor
  - S3 client
  - WordPress client
  - email notifier

Suggested module layout:

```text
homily_monitor/
  app.py
  cli.py
  config/
  domain/
  services/
  adapters/
  repositories/
  workflows/
```

Concrete refactors:

- Convert DB access into repository functions or classes.
- Extract WordPress payload building into pure functions.
- Extract transcript analysis prompt construction into an AI service module.
- Make homily extraction return a result object instead of uploading as a side effect.
- Make CLI commands call explicit workflows rather than directly chaining modules.

Deliverable:

- Cleaner architecture without changing the end-user workflow.

### Phase 3: Database hardening and idempotency

Goal: prevent duplicate data and make reprocessing safe.

Tasks:

- Add uniqueness and indexes around the canonical homily identity.
- Introduce an explicit processing state model.
- Replace raw insert-only behavior with upsert or versioned records.
- Ensure WordPress upload logic always retrieves the latest intended metadata.
- Track upload status, WordPress post ID, media IDs, and last successful sync time.
- Add migration scripts instead of ad hoc `ALTER TABLE` logic.

Suggested data improvements:

- `church_id`
- `source_key`
- `mass_datetime_local`
- `mass_timezone`
- `transcript_status`
- `analysis_status`
- `upload_status`
- `wp_post_id`
- `wp_audio_media_id`
- `wp_image_media_id`
- `prompt_version`
- `ai_model_text`
- `ai_model_image`

Deliverable:

- Reliable reprocessing and better operational visibility.

### Phase 4: AI modernization and centralization

Goal: improve maintainability and upgradeability of all AI behavior.

Tasks:

- Centralize all OpenAI access in one adapter/service.
- Introduce a provider-agnostic AI interface so OpenAI is one adapter, not the application architecture.
- Make models configurable by purpose:
  - metadata generation
  - title generation
  - description generation
  - image prompt generation
  - image generation
  - optional VTT fallback detection
- Replace free-form JSON prompting with structured outputs and schema validation.
- Version prompts and keep them under source control.
- Add output validation and fallback behavior before persisting data.
- Add a dry-run mode that stores AI outputs locally without publishing.
- Add model/provider selection through configuration rather than hardcoded imports or model IDs.
- Add candidate-model evaluation tooling so newer models can be assessed without changing production behavior.
- Add cost, latency, and quality logging per AI task.

Implementation guidance:

- Do not hardcode model names across multiple files.
- Keep model selection in a central registry/configuration layer with sane defaults.
- Separate application intent from provider implementation.
- Define task contracts in terms of required outputs, not vendor-specific APIs.
- Normalize provider responses into shared internal result objects.
- Keep provider-specific prompt formatting inside adapters where necessary.
- Support per-task primary and fallback providers/models.
- Keep the currently approved model configurable independently for each task.
- Track upgrade cadence explicitly and document the last review date.
- Log prompt version and model used for each artifact.
- Preserve the current user-facing style until tests and content review confirm acceptable changes.

Deliverable:

- One AI integration layer that can be upgraded or replaced without invasive code changes.

### Phase 4A: Provider abstraction and portability

Goal: make future provider switches manageable.

Tasks:

- Define an internal `AIProvider` contract for text and image capabilities.
- Create task-oriented service interfaces such as:
  - `HomilyMetadataGenerator`
  - `HomilyTitleGenerator`
  - `HomilyDescriptionGenerator`
  - `HomilyImagePromptGenerator`
  - `HomilyImageGenerator`
- Implement an OpenAI adapter first.
- Design a second adapter slot for future providers even if it is initially unimplemented.
- Add provider capability checks so unsupported tasks fail clearly.
- Keep prompts, schemas, and output validation outside the transport client where possible.

Suggested adapter responsibilities:

- authentication and client initialization
- request formatting
- retries and timeout policy
- provider-specific response parsing
- provider-specific limits and fallback handling

Deliverable:

- A provider-neutral AI boundary with OpenAI as the first implementation.

### Phase 4B: Upgrade workflow for latest models

Goal: make regular model upgrades routine instead of risky.

Tasks:

- Create a single AI registry file that maps each task to:
  - provider
  - model
  - fallback model
  - prompt version
  - expected schema
  - rollout status
- Add a lightweight evaluation command that runs the acceptance dataset against a candidate model.
- Add a scorecard for:
  - schema compliance
  - factual/liturgical correctness
  - title quality
  - description quality
  - image prompt quality
  - latency
  - cost
- Require regression and acceptance results before changing the approved model in production.
- Store evaluation artifacts so changes are reviewable over time.
- Add an operational runbook for “upgrade latest model” and “rollback to previous model.”

Deliverable:

- A repeatable, low-risk model-upgrade process.

### Phase 5: Multi-church productization

Goal: support multiple churches effectively without branching the codebase per client.

Tasks:

- Introduce a church profile or tenant configuration model.
- Move hardcoded values into per-church config:
  - timezone
  - homilist display rules
  - S3 bucket/folder
  - WordPress site and post type
  - branding and image style
  - title template
  - email recipients
  - liturgy preferences or locale overrides
- Support multiple configured churches in one deployment or in isolated deployments using the same codebase.
- Add a validation command to confirm a church profile is complete.
- Add onboarding documentation for a new church.

Deliverable:

- A reusable application that can be configured rather than forked.

### Phase 6: Security and operations hardening

Goal: make the system safer to run in production and easier to support.

Tasks:

- Replace plaintext secret handling with environment variables or a secret manager.
- Validate config on startup and fail fast with actionable errors.
- Use least-privilege credentials for S3 and WordPress.
- Add structured logging with correlation IDs per processed file.
- Add health-check and status commands.
- Add rate limiting and exponential backoff for network calls.
- Add audit logging for upload and publication actions.
- Add backup/restore and retention policy documentation for DB and media metadata.

Deliverable:

- Safer deployment and better supportability.

## Recommended Near-Term Backlog

These items should be done before any major functional expansion:

1. Fix S3 prefix filtering.
2. Stop processing after failed downloads.
3. Remove hardcoded FFmpeg path.
4. Correct WordPress timezone conversion.
5. Add WordPress request timeouts.
6. Split alert email types cleanly.
7. Add DB uniqueness/idempotency around homily records.
8. Build the first regression fixtures and smoke tests.
9. Centralize AI calls behind one service interface.
10. Introduce an AI registry and provider abstraction.
11. Introduce church profile configuration.

## Recommended Test Matrix

### Unit

- parsing and validation logic
- DB repository behavior
- title construction
- timezone conversion
- config validation
- prompt/response parsing

### Integration

- one-file full processing with mocked dependencies
- repeated processing of the same file
- WordPress upload failure and retry behavior
- AI invalid JSON / malformed output handling

### Regression

- known transcript to expected title/description schema
- known VTT to expected homily boundaries
- known S3 listing to expected download set
- known metadata row to expected WordPress payload
- current approved AI provider/model outputs vs candidate replacement outputs

### Manual acceptance

- compare old vs refactored output on sample weekend data
- review AI-generated titles/descriptions for tone continuity
- verify upload draft appearance in WordPress
- verify image generation matches church branding expectations
- review candidate provider/model substitutions before promotion

## Definition of Done

This project should be considered ready for broader church rollout only when:

- Core workflow behavior is covered by regression tests.
- The application can run without code edits for a new church.
- Secrets are not stored in repo-tracked config files.
- Processing the same file twice is safe and predictable.
- External service failures do not silently corrupt downstream state.
- AI models and prompts are centrally managed and auditable.
- AI providers can be swapped at the adapter layer without rewriting workflow code.
- Model upgrades follow a documented evaluation and rollback process.
- CI is enforcing unit, integration, and regression tests.
- Operational documentation exists for install, configure, run, recover, and upgrade.

## Proposed Execution Order

1. Stabilization hotfixes.
2. Test harness and fixtures.
3. Service boundary refactor.
4. Database and idempotency work.
5. AI centralization and upgrade path.
6. Multi-church configuration model.
7. Security and deployment hardening.

## Final Recommendation

Do not start with a full rewrite. Start with characterization tests and the highest-risk hotfixes, then refactor behind those tests. That approach gives the best chance of preserving current production behavior while making the application substantially cleaner, safer, and ready for wider reuse.
