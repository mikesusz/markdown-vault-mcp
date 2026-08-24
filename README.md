# markdown-vault-mcp

Agent-controlled markdown files with frontmatter-based permissions.

Works with Obsidian vaults, static site generators (Hugo, Jekyll, Astro), note-taking apps (Bear, Typora, iA Writer), or any folder of `.md` files.

_Note:_ if you want a simple, read-only version with no file-write capabilities, see the [1.0 Release](https://github.com/mikesusz/markdown-vault-mcp/releases/tag/1.0).

## Quick Start

```bash
# 1. Clone and install
git clone https://github.com/mikesusz/markdown-vault-mcp
cd markdown-vault-mcp
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .

# 2. Set your markdown folder path
cp .env.example .env
# Edit .env and set VAULT_PATH=/path/to/your/markdown/files

# 3. Add to your MCP client configuration
```

**MCP Client Config** (Claude Desktop, etc.):

```json
{
	"mcpServers": {
		"markdown-vault": {
			"command": "/path/to/markdown-vault-mcp/.venv/bin/python",
			"args": ["-m", "markdown_vault_mcp.server"],
			"env": {
				"VAULT_PATH": "/path/to/your/markdown/files"
			}
		}
	}
}
```

Use the full path to the Python binary inside your virtualenv. The `VAULT_PATH` can be set in `.env` or passed via the client config (client takes precedence).

## Requirements

- Python 3.10+
- A folder containing `.md` files

## Works With

- **Obsidian** — point `VAULT_PATH` at your vault folder
- **Hugo / Jekyll / Astro** — point at your `content/` folder
- **Bear / Typora / iA Writer** — point at your notes folder
- **Plain markdown** — point at any folder of `.md` files

The server doesn't care about your note-taking app — it just needs markdown files with optional YAML frontmatter.

---

## Permission System

Control agent access with a single frontmatter field. **Notes without frontmatter default to `append`** — no configuration files, no hardcoded lists.

### Permission Hierarchy

| Value    | Agent can…                                     |
| -------- | ---------------------------------------------- |
| `hidden` | Nothing — file is completely invisible         |
| `read`   | View but not modify                            |
| `append` | Add content only _(default if no frontmatter)_ |
| `edit`   | Full read + write access                       |

### Usage

Add `agent_access` to any note's frontmatter:

```yaml
---
agent_access: edit # or: append, read, hidden
---
```

### Tool Requirements

| Tools            | Minimum Permission |
| ---------------- | ------------------ |
| Read operations  | `read`             |
| `append_to_note` | `append`           |
| Edit operations  | `edit`             |

**Read operations:** `get_note`, `search_notes`, `list_notes`
**Edit operations:** `update_note`, `replace_in_note`, `update_section`

Notes with insufficient permissions return a clear error:

```
Error: Insufficient permissions. This note has agent_access: 'append',
but this operation requires: 'edit'. Add agent_access: 'edit' to the
note's frontmatter to enable this operation.
```

---

## Available Tools

### `search_notes`

Search markdown notes by title or content (case-insensitive). Returns up to 10 results with a short excerpt around each match.

**Input:**

- `query` (string, required) — term to search for

**Example result:**

```json
{
	"results": [
		{
			"title": "Meeting Notes",
			"path": "work/Meeting Notes.md",
			"snippet": "…discussed the new project architecture…",
			"relevance_score": 7.0
		}
	]
}
```

Plus a [`warnings`](#skipped-files) array if any files had to be skipped.

---

### `get_note`

Retrieve the full content and metadata of a specific note.

**Input:**

- `note_path` (string, required) — path relative to vault root, e.g. `"folder/My Note.md"`

**Example result:**

```json
{
	"title": "My Note",
	"path": "folder/My Note.md",
	"content": "# My Note\n\nBody text here…",
	"frontmatter": { "tags": ["idea"], "created": "2024-01-01" },
	"modified": "2024-06-15T10:30:00",
	"size": 1024
}
```

If the note's frontmatter can't be parsed, `get_note` returns a structured error naming the problem instead of the note:

```json
{
	"error": "Could not parse this note's frontmatter",
	"detail": "YAML parse error: mapping values are not allowed in this context (line 3)",
	"path": "Reference - House.md"
}
```

---

### `list_notes`

List all markdown notes in the vault (or a subfolder).

**Input:**

- `folder` (string, optional) — subfolder path relative to vault root

**Example result:**

```json
{
	"notes": [
		{ "title": "Home", "path": "Home.md", "size": 512, "modified": "2024-06-01T09:00:00" },
		{
			"title": "Daily Note",
			"path": "Daily/2024-01-15.md",
			"size": 256,
			"modified": "2024-01-15T08:00:00"
		}
	]
}
```

Plus a [`warnings`](#skipped-files) array if any files had to be skipped.

---

### `list_writable_notes`

Show all notes that agents can append to — those with `agent_access: append` or `edit`, plus notes without any frontmatter (which default to `append`).

**Input:** none

**Example result:**

```json
{
	"writable_notes": [
		{ "path": "__INBOX.md", "access_level": "append" },
		{ "path": "projects/status.md", "access_level": "edit" }
	]
}
```

Plus a [`warnings`](#skipped-files) array if any files had to be skipped.

---

### `append_to_note`

Append content to the end of a note. Works on any note with `agent_access: append` or `edit`, or any note without frontmatter. Never overwrites existing content. Creates the file if it doesn't exist yet.

**Input:**

- `note_path` (string, required) — path relative to vault root, e.g. `"inbox.md"` or `"folder/notes.md"`
- `content` (string, required) — text to append
- `add_timestamp` (boolean, optional, default: `true`) — if true, inserts a `## YYYY-MM-DD HH:MM` heading before the content

**Example result:**

```json
{
	"success": true,
	"note_path": "inbox.md",
	"appended_content": "\n## 2026-03-24 10:30\nOrder furnace filters\n",
	"message": "Successfully appended to 'inbox.md'."
}
```

**Example usage:**

- `append_to_note("inbox.md", "Order furnace filters")` — timestamped capture
- `append_to_note("books.md", "- The Expanse", false)` — add a list item without a heading
- `append_to_note("new-note.md", "Some content", false)` — creates the file if it doesn't exist

---

### `list_templates`

List all `.md` files in your vault's `templates/` directory.

**Input:** none

**Example result:**

```json
{
	"templates": [
		{
			"name": "PROJECT",
			"path": "templates/PROJECT.md",
			"size": 100,
			"description": "General project template"
		},
		{
			"name": "JOURNAL",
			"path": "templates/JOURNAL.md",
			"size": 96,
			"description": "Daily journal entry"
		}
	]
}
```

Plus a [`warnings`](#skipped-files) array if any files had to be skipped.

---

### Skipped files

Every vault-scanning tool — `list_notes`, `search_notes`, `list_writable_notes`,
and `list_templates` — skips any file it can't read rather than failing the whole
scan. When that happens, it reports the skipped files alongside the normal
results:

```json
{
	"notes": [ "…normal results, unchanged…" ],
	"warnings": [
		{
			"path": "Reference - House.md",
			"error": "YAML parse error: mapping values are not allowed in this context (line 3)"
		}
	]
}
```

- The `warnings` key is **absent entirely** when nothing was skipped — it never
  appears as an empty array.
- Each entry carries only the path and the parse error. A note's title, content,
  frontmatter, and access level are never included, since an unparseable note may
  well have been meant to stay hidden.
- Skipped files are also logged to stderr, visible in your MCP client's logs.
- Hidden notes (`agent_access: hidden`) are excluded silently and never appear
  in `warnings` — that's a deliberate setting, not a problem to report.

---

### `create_note_from_template`

Create a new note from an existing template. Automatically replaces placeholders like `{{TODAY}}` and `{{AGENT_ACCESS}}`, and pre-fills frontmatter or heading-based fields.

**Input:**

- `template_name` (string, required) — name of the template (e.g. `"PROJECT"`, `"New Book"`). Must match a file in `templates/`.
- `note_suffix` (string, optional) — appended to the filename, e.g. `"deck replacement"` → `"PROJECT deck replacement.md"`. Letters, numbers, spaces, hyphens, underscores only.
- `field_values` (object, optional) — pre-fill template fields. Keys match YAML frontmatter field names (e.g. `{"title": "My Book", "authors": "Jane Smith"}`). Keys not found in frontmatter will be matched against `# KEY:` headings in the body instead.
- `agent_access` (string, optional) — permission level for agent access after creation: `"edit"`, `"append"` (default), `"read"`, or `"hidden"`. Overrides any value already in the template's `agent_access` frontmatter field. Infer from the user's phrasing (see Templates section below).

**Example result:**

```json
{
	"success": true,
	"file_path": "PROJECT deck replacement.md",
	"message": "Created new note from template",
	"template_used": "templates/PROJECT.md",
	"fields_applied": { "title": "deck replacement" }
}
```

---

### `update_note`

Replace the entire body of a note with new content. Frontmatter (including `agent_access`) is always preserved. Requires `agent_access: "edit"` in the note.

**Input:**

- `note_path` (string, required) — path relative to vault root, e.g. `"My Note.md"`
- `new_content` (string, required) — full replacement body text

---

### `replace_in_note`

Find and replace a specific piece of text in a note body. Exact match, case-sensitive. Only operates on body content — frontmatter is never touched. Requires `agent_access: "edit"`.

**Input:**

- `note_path` (string, required) — path relative to vault root
- `old_text` (string, required) — exact text to find
- `new_text` (string, required) — replacement text

**Errors:**

- `"Text not found in note: '...'"` — if `old_text` doesn't appear in the body

---

### `update_section`

Replace the content beneath a specific heading, preserving the heading line itself. Content is replaced up to (but not including) the next heading, or end of file. Requires `agent_access: "edit"`.

**Input:**

- `note_path` (string, required) — path relative to vault root
- `heading` (string, required) — exact heading text, e.g. `"## Next Steps"` or `"SYNOPSIS:"`
- `new_content` (string, required) — replacement content for that section

**Errors:**

- `"Heading '...' not found in note"` — if the heading doesn't exist

---

## Templates

### Adding templates

Drop any `.md` file into your vault's `templates/` directory — no configuration needed:

```
templates/
├── PROJECT.md
├── New Book.md
├── JOURNAL.md
└── RECIPE.md
```

Add an optional description comment at the top to make it show up nicely in `list_templates`:

```markdown
%% My recipe template %%

---

date: "{{TODAY}}"
source: ""

---

# TITLE:

# SOURCE:

## Ingredients

## Steps
```

The `templates/examples/` directory in this repo contains ready-to-use templates you can copy into your vault:

- `JOURNAL.md` — Daily journal with date auto-filled (`agent_access: "append"`)
- `RECIPE.md` — Recipe with source and ingredients
- `MEETING_NOTES.md` — Meeting notes with attendees array (`agent_access: "edit"`)
- `REFERENCE_DOC.md` — Reference document with dynamic `agent_access` via `{{AGENT_ACCESS}}`

### Agent access in templates

Templates can include an `agent_access` frontmatter field to declare how freely an agent should edit the note after creation.

**Hardcoded in template** (e.g. JOURNAL.md always appends):

```markdown
---
date: '{{TODAY}}'
agent_access: 'append'
---
```

**Dynamic via placeholder** (e.g. REFERENCE_DOC.md — set at creation time):

```markdown
---
title: '{{TITLE}}'
agent_access: '{{AGENT_ACCESS}}'
---
```

When using the dynamic placeholder, pass `agent_access` to `create_note_from_template` and the value is inferred from the user's phrasing:

| User says…                                               | Inferred value |
| -------------------------------------------------------- | -------------- |
| "you can edit/update/modify" or "fully editable"         | `edit`         |
| "you can add to" or "append-only"                        | `append`       |
| No specific instruction                                  | `append`       |
| "read-only", "I'll edit this myself", "just create it"   | `read`         |
| "this is private", "keep this hidden", "don't show this" | `hidden`       |

If `agent_access` is passed explicitly to `create_note_from_template`, it overrides whatever value the template has (hardcoded or placeholder).

### Template placeholders

The following `{{PLACEHOLDER}}` tokens are automatically replaced when a note is created:

| Placeholder        | Example output                                                              |
| ------------------ | --------------------------------------------------------------------------- |
| `{{TODAY}}`        | `2026-03-24`                                                                |
| `{{DATE}}`         | `2026-03-24` (alias for TODAY)                                              |
| `{{NOW}}`          | `2026-03-24 15:30:45`                                                       |
| `{{TIME}}`         | `15:30:45`                                                                  |
| `{{TIMESTAMP}}`    | `1742820645` (Unix timestamp)                                               |
| `{{YEAR}}`         | `2026`                                                                      |
| `{{MONTH}}`        | `03`                                                                        |
| `{{DAY}}`          | `24`                                                                        |
| `{{AGENT_ACCESS}}` | `"append"` (resolved from the `agent_access` parameter, default `"append"`) |

Placeholders are expanded after `field_values` are applied, so caller-supplied values always take precedence.

### Array fields

If a `field_values` value for an array-typed frontmatter field contains `and`, it's automatically split into a list:

```
"William Gibson and Bruce Sterling" → ["William Gibson", "Bruce Sterling"]
```

---

## Testing

Run the end-to-end test harness (requires a real vault):

```bash
python test_server.py --vault /path/to/your/vault
```

Options:

- `--vault` — path to vault (overrides `VAULT_PATH` env)
- `--note` — specific note path to test `get_note` with
- `--query` — search term for `search_notes` (default: `"the"`)

Run the robustness suite, which builds a throwaway vault full of empty and
malformed notes and asserts that no single bad file breaks vault-wide
operations (no real vault needed):

```bash
python test_vault_robustness.py
```

---

## Troubleshooting

**`VAULT_PATH environment variable is not set`**
Copy `.env.example` to `.env` and set the path, or pass `VAULT_PATH` in your MCP client config.

**`Templates directory not found in vault`**
Create a `templates/` folder in your vault root, or copy templates from `templates/examples/` in this repo.

**`Insufficient permissions`**
The note has `agent_access: read` or `hidden`. Add `agent_access: append` or `agent_access: edit` to its frontmatter.

**A note is missing from `list_notes` or `search_notes`**
A note whose YAML frontmatter can't be parsed is skipped rather than exposed,
so one bad file never breaks results for the rest of the vault. The response
names it in a [`warnings`](#skipped-files) array (and the server logs it to
stderr) — fix the `---` block at the top of that file and it reappears.
`get_note` on the same file returns an explicit error naming the problem.
If a note is missing with no warning, check whether it's `agent_access: hidden`.

**Empty (0-byte) `.md` files**
Handled normally — treated as a note with no frontmatter and no content, which
means the default `agent_access: append`. They appear in `list_notes` and
`list_writable_notes`, never match a search, and don't affect other notes.

**Frontmatter with a non-string `agent_access`**
Values like `agent_access: [append]` or `agent_access: true` resolve to an
unknown level: the note stays visible but is not writable. Quote the value or
use one of `hidden` / `read` / `append` / `edit`.

**Changes to server code aren't taking effect**
Your MCP client keeps the server process alive. Restart the client (e.g. quit and relaunch Claude Desktop) to pick up code changes.

**`field_values` not updating frontmatter**
Check that the key names exactly match the YAML frontmatter keys in the template (case-sensitive). Use `fields_applied` in the response to confirm what was actually written.

---

## Running the server manually

```bash
python -m markdown_vault_mcp.server
# or
markdown-vault-mcp
```

The server communicates over stdio and is normally launched automatically by your MCP client.

---

## Migrating from obsidian-mcp v2

See [MIGRATION.md](https://github.com/mikesusz/markdown-vault-mcp/blob/main/MIGRATION.md) for upgrade steps.

---

## License

GNU Affero General Public License v3.0 — see [LICENSE](https://github.com/mikesusz/markdown-vault-mcp/blob/main/LICENSE) for the full text.

You are free to use, modify, and distribute this software under the AGPL v3 terms. If you run a modified version as a network service, you must make the modified source available to users of that service.

---

## Feedback

This project is a work in progress, and may have bugs. You can submit [a Github Issue](https://github.com/mikesusz/markdown-vault-mcp/issues) if you encounter any problems, and I will probably fix it! Because I don't want to have that problem, either.
