# markdown-vault-mcp — Project Context

A Python-based MCP (Model Context Protocol) server that gives LLMs read and limited write access to any folder of markdown files over stdio. Designed for use with Claude Desktop and other MCP-compatible clients.

## What It Does

- **Full read access** to all markdown notes (search, retrieve, list)
- **Frontmatter-controlled write access** — notes opt in to LLM writes via `agent_access` frontmatter field
- **Template-based note creation** with field substitution and date/time placeholders
- All configured via environment variable (`VAULT_PATH`)

## Tech Stack

- Python 3.10+, async/await throughout
- `mcp` — MCP SDK (stdio transport)
- `python-frontmatter` — YAML frontmatter parsing
- `python-dotenv` — env var management
- `hatchling` — build backend

## Architecture

```
src/markdown_vault_mcp/
  server.py     — MCP server entry point; registers and routes all tools
  vault.py      — All vault I/O: reading, searching, writing, template expansion

templates/
  examples/     — JOURNAL.md, RECIPE.md, MEETING_NOTES.md starter templates

test_server.py  — End-to-end test harness; spawns server as subprocess over stdio
```

## The MCP Tools

### Read

| Tool           | Description                                                                                  |
| -------------- | -------------------------------------------------------------------------------------------- |
| `list_notes`   | List all `.md` files; optional subfolder filter                                              |
| `get_note`     | Full content + frontmatter + metadata for a specific note                                    |
| `search_notes` | Case-insensitive search of titles and content; returns up to 10 scored results with snippets |

### Write

| Tool                  | Description                                                                                              |
| --------------------- | -------------------------------------------------------------------------------------------------------- |
| `list_writable_notes` | Show which notes allow LLM writes (via frontmatter)                                                      |
| `append_to_note`      | Append content to a writable note; optionally prefixes with a timestamp heading; creates file if missing |

### Edit

| Tool               | Description                                                             |
| ------------------ | ----------------------------------------------------------------------- |
| `update_note`      | Replace entire note body; frontmatter (including `agent_access`) preserved |
| `replace_in_note`  | Exact case-sensitive find-and-replace in note body                      |
| `update_section`   | Replace content beneath a specific heading, preserving the heading line |

### Templates

| Tool                        | Description                                                                                                             |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `list_templates`            | Discover all `.md` files under vault's `templates/` dir; extracts description from first-line comment                   |
| `create_note_from_template` | Create a new note from a template; substitutes frontmatter fields, `# KEY:` body patterns, and `{{PLACEHOLDER}}` tokens |

## Frontmatter-Based Permissions

Write access is controlled by an `agent_access` field in each note's frontmatter:

```yaml
---
agent_access: append # or: hidden, read, append, edit
---
```

- `hidden` — file is completely invisible to the agent
- `read` — view only, no modifications
- `append` — add content only _(default when no frontmatter present)_
- `edit` — full read + write access

## Template Placeholders

`{{TODAY}}`, `{{DATE}}`, `{{NOW}}`, `{{TIME}}`, `{{TIMESTAMP}}`, `{{YEAR}}`, `{{MONTH}}`, `{{DAY}}`

## Configuration

**`.env`** (gitignored):

```
VAULT_PATH=/path/to/your/vault
```

No external config file needed — permissions live in note frontmatter.

## Safety Model

- **Reads:** All `.md` files accessible; path traversal rejected
- **Writes:** Frontmatter opt-in only; `append` permission adds only, `edit` permission allows full replacement; path traversal structurally impossible
- **Template creation:** Safe filename validation (regex + 200 char max); duplicate protection
- All write/create operations logged to stderr (doesn't interfere with stdio transport)

## How It Evolved

| Phase | What Was Built                                                                                               |
| ----- | ------------------------------------------------------------------------------------------------------------ |
| 1     | Read-only: `search_notes`, `get_note`, `list_notes`                                                          |
| 2     | Append-only writes: `append_to_note`, `list_writable_notes`                                                  |
| 2.5   | Template creation: `list_templates`, `create_note_from_template`, field substitution                         |
| 2.6   | Smart placeholders: `{{TODAY}}`, `{{NOW}}`, etc.                                                             |
| 3     | Config file abstraction, example templates, README, general-purpose release                                  |
| 4     | Rebrand from `obsidian-mcp` → `markdown-vault-mcp`; frontmatter-based permissions replacing config whitelist |

## Running

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env  # set VAULT_PATH

# Test against a real vault:
python test_server.py --vault /path/to/your/vault
```

## MCP Client Setup (Claude Desktop example)

```json
{
	"mcpServers": {
		"markdown-vault": {
			"command": "/path/to/markdown-vault-mcp/.venv/bin/python",
			"args": ["-m", "markdown_vault_mcp.server"],
			"env": { "VAULT_PATH": "/path/to/your/vault" }
		}
	}
}
```
