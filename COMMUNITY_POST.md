# Open WebUI Documentation Sync

Give your models an up-to-date Open WebUI documentation knowledge base, maintained automatically by an Event Function.

## What it does

- Creates a private KB from the official `open-webui/docs` Markdown and MDX pages.
- Syncs on startup or enable, then on a configurable schedule; no chat or external scheduler is needed.
- Follows upstream `main`, or a branch, tag or pinned commit you choose.
- Preserves documentation folders and skips unchanged text.
- Verifies new pages are indexed before detaching superseded versions.
- Preserves user-added files and sharing; stops on ownership collisions or edits to managed content.

The documentation helps a model answer platform questions. It does not report your instance's live configuration, and upstream main may describe a newer version than the one you run.

## Setup

1. Import this **Event Function** in **Admin Panel → Functions**.
2. Set `WEBUI_URL` to the trusted backend origin and `ADMIN_TOKEN` to an administrator API key or session token. Default origin: `http://127.0.0.1:8080`. API-key restrictions must allow the identity, knowledge and file APIs; session tokens expire.
3. Leave `SOURCE_PATH=docs` for all documentation, or start with a small source path such as `docs/features/workspace/knowledge.mdx`.
4. Set `ENABLED=true`, then enable the Function. It runs immediately and repeats every 24 hours by default.
5. Verify processed files in **Workspace → Knowledge**, configure the KB's sharing, and let your model discover or attach it.

For an immediate on-demand run, disable and re-enable the Function. Disable it to stop the background task. No model tool attachment is required.

## Configuration

`KNOWLEDGE_NAME` names a newly created KB; existing names and grants are preserved. `SOURCE_REF` selects main or a pin. `INTERVAL_HOURS` controls the schedule. File-count, per-file-size and total-download caps fail closed if exceeded. The default KB name is **Open WebUI Documentation (Auto Sync)**.

## Preservation and limits

The extension uses authenticated Open WebUI APIs. It only detaches files carrying its ownership metadata and keeps their original uploads for historical references; those originals still consume storage. It does not reset knowledge bases or delete user files/directories. All selected source downloads finish before writes, and all replacement pages must index successfully before cleanup.

Credentials stay in administrator Valves and are sent only to the configured WebUI origin. GitHub downloads use a separate client without that token. HTTPS verification remains enabled. No repository code or MDX components are executed.

Requires a working knowledge extraction/embedding setup, Python 3.11+, httpx, Pydantic 2 and a Linux/Unix backend. Tested locally on Open WebUI 0.13.3 with real official-source indexing, an unchanged repeat and an installed Event performing an enable-triggered update, plus synthetic tests for preservation, failure handling and Event lifecycle. Fetching all 461 pages at the tested pinned revision passed; full-corpus indexing throughput and other releases are not claimed as tested.

If processing fails or a managed document was edited, the run stops for review. Check server logs and the affected file before retrying. GitHub rate limits use the normal retry interval. See the standalone README for all Valves and recovery details.

MIT licensed. This is a community extension, not an official Open WebUI release.
