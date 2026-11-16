# Open WebUI Documentation Sync

[Open WebUI community post](https://openwebui.com/posts/9049e895-9a7c-40a9-835f-a14129d6d1fa) · [Installable source](function.py)

An optional Event Function that maintains a knowledge base from the official `open-webui/docs` repository. It runs automatically when Open WebUI starts or the Function is enabled, then repeats on a configurable interval. It does not need a model prompt, chat session, external scheduler or Git executable.

This is a standalone community extension. It is not part of the deployment bootstrap's documentation synchronizer and does not adopt its knowledge bases. The two synchronizers use different ownership markers and should manage separate bases.

## How it works

1. Verify the configured token belongs to a current administrator.
2. Resolve the configured official documentation branch, tag or commit to a full immutable commit SHA.
3. Fetch Markdown and MDX below the selected `docs/` path. Download all selected text within configured limits before changing the KB. MDX components remain source text; nothing from the repository is executed.
4. Find the extension-owned KB, or create a private one. A conflicting name or duplicate ownership marker stops the run.
5. Upload new or changed pages through Open WebUI's Files API, preserving source folders. Verify completed processing and extracted content before replacing old attachments.
6. Detach superseded or removed extension-owned pages only after every replacement is indexed and linked. Keep original Files records for historical references. Preserve user-added files, directories, sharing and KB renames.

Unchanged text is not uploaded again when an unrelated upstream commit changes. Source metadata records the commit and immutable GitHub URL used when that document version was uploaded. Existing managed text is checked for edits before writes; conflicts require operator review. Empty/truncated source discovery, size-limit failures and incomplete indexing do not trigger cleanup.

The extension uses the supported knowledge and file APIs. It deliberately avoids broad directory cleanup or whole-KB resets. Original files retained after replacement consume storage; retention is an operator decision rather than an automatic deletion policy.

## Installation

1. Create an Event Function in **Admin Panel → Functions** using the complete source (deployment checkout: `Michael/functions/openwebui_docs_sync.py`), or import the community Function listing.
2. Open its Valves. Set `WEBUI_URL` to the trusted WebUI origin reachable from the backend. For the usual container setup, `http://127.0.0.1:8080` calls the same backend.
3. Set `ADMIN_TOKEN` to an administrator API key or session token. API-key access and endpoint restrictions must permit the identity (`/api/v1/auths/`), knowledge and file APIs. Session tokens expire; expired or non-admin credentials stop writes. The token is stored in administrator configuration, never a prompt.
4. Choose `SOURCE_REF` and `SOURCE_PATH`, and set `ENABLED=true`.
5. Enable the Function. It runs immediately, then every `INTERVAL_HOURS`. To request an immediate run later, disable and re-enable it after saving Valves.
6. Open **Workspace → Knowledge** and verify the new base and processed pages. It starts private. Configure sharing there, then let your model discover it with native knowledge tools or attach it to a preset.

No model attachment is required for the Event itself. Disabling the Function cancels its task; startup resumes enabled scheduling. Shutdown cancellation cannot undo an API write already accepted by the server. On failure, inspect state and the server log before retrying.

## Valves

| Valve | Default | Purpose |
| --- | --- | --- |
| `ENABLED` | `false` | Explicit opt-in to sync; the Function must also be active |
| `WEBUI_URL` | `http://127.0.0.1:8080` | Trusted backend origin; credentials go only here |
| `ADMIN_TOKEN` | empty | Administrator API key/session token; password-style UI field |
| `KNOWLEDGE_NAME` | `Open WebUI Documentation (Auto Sync)` | Name for a newly created base; existing name/access are preserved |
| `SOURCE_REF` | `main` | Follow upstream automatically, or pin a full commit SHA |
| `SOURCE_PATH` | `docs` | All Markdown/MDX, one subdirectory, or one exact source file |
| `INTERVAL_HOURS` | `24` | Delay between runs, minimum one hour |
| `MAX_FILES` | `2000` | Fail closed if source selection exceeds the cap |
| `MAX_FILE_BYTES` | `2000000` | Maximum bytes per source page |
| `MAX_TOTAL_BYTES` | `20000000` | Maximum total downloaded Markdown/MDX bytes |

Changing the source selection can detach previously managed pages outside that selection. Start with a small path if you want to assess embedding cost and indexing behavior. `SOURCE_PATH` uses the upstream filename: for example, `docs/features/workspace/knowledge.mdx`; the KB copy is normalized to `.md` for extraction.

## Access, networking and limits

HTTPS verification stays enabled. Configure runtime CA trust as needed. Downloads use a separate unauthenticated client restricted by the implementation to the official GitHub API and raw-content hosts; the WebUI bearer token is not forwarded to them. HTTP redirects are not followed. GitHub API rate limits or unavailable source pages fail the run and retry on the normal interval.

The Function requires Python 3.11+, httpx, Pydantic 2 and a Linux/Unix backend providing `fcntl`. A file lock in the WebUI data directory serializes cooperating workers using that shared directory. Separate deployments without shared storage must not run competing sync writers against the same target. The lock does not prevent manual edits in the UI; readback checks reduce conflicts without promising transactional updates.

The KB is a documentation mirror, not a live inventory. Upstream `main` may document features newer than your installed Open WebUI version. Pin `SOURCE_REF` when version alignment matters. It does not fetch screenshots, render MDX components, import non-document assets, change retrieval settings, modify presets or provision model providers. Knowledge processing must already work in the target instance.

A failed or interrupted upload may leave a partial managed file. The next run refuses to overwrite uncertain content. Review the file's processing status, remove only the failed extension-owned attachment if needed, and rerun. User-edited managed pages likewise require review; move personal notes to a separate file instead of editing mirror content.

## Verification

Offline tests in test_sync.py (deployment checkout: `Michael/documentation_sync/test_sync.py`) cover idempotent replacement, user-content preservation, ownership collisions, edited-content conflicts, indexing failure, duplicate-hash protection, removed-source handling, empty/truncated discovery, lifecycle cancellation and the shared lock. They use synthetic data and no external service calls.

Local acceptance uses an isolated Open WebUI 0.13.3 instance with an existing working extraction/embedding configuration. It verifies real official-source fetching, indexing/readback, an unchanged repeat, and installation as an Event Function, including an enable-triggered source-path update and disable cancellation. Full pinned-source fetching was validated for 461 pages (about 4.6 MB). Full-corpus indexing performance and compatibility with other Open WebUI releases require separate evaluation. Preparing or publishing this extension does not install it in production.

## Distribution and license

package.py (deployment checkout: `Michael/documentation_sync/package.py`) prepares an independent local Git repository containing `function.py`, this README, `COMMUNITY_POST.md`, tests, pinned test dependencies and the MIT license. It does not publish automatically.

MIT. Copyright (c) 2026 Michael Lei. Open WebUI and its documentation retain their respective licenses and notices. This community extension is not an official Open WebUI release.

## Running the standalone tests

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m unittest discover -p 'test_*.py'
```

These tests use synthetic fixtures and do not require production credentials.
