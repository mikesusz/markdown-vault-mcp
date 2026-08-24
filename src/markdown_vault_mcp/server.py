"""markdown-vault-mcp server - markdown folder access via stdio transport."""

from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any

import mcp.server.stdio
import mcp.types as types
from dotenv import load_dotenv
from mcp.server import Server

from .vault import (
    NoteReadError,
    append_to_note,
    create_note_from_template,
    get_note,
    list_notes,
    list_templates,
    list_writable_notes,
    replace_in_note,
    search_notes,
    update_note,
    update_section,
)

load_dotenv()

app = Server("markdown-vault")

# Shared tail for every tool that scans the vault and may skip files.
WARNINGS_NOTE = (
    "If any files had to be skipped because they are unreadable or their YAML "
    "frontmatter is malformed, a 'warnings' array lists each skipped path and its "
    "error — surface those to the user, since the notes are missing from the "
    "results. The key is absent entirely when nothing was skipped."
)


def _reply(payload: Any) -> list[types.TextContent]:
    """Serialize a tool result. default=str keeps YAML dates/times serializable."""
    text = json.dumps(payload, indent=2, default=str)
    return [types.TextContent(type="text", text=text)]


def _vault_path() -> str:
    path = os.environ.get("VAULT_PATH", "")
    if not path:
        raise RuntimeError(
            "VAULT_PATH environment variable is not set. "
            "Copy .env.example to .env and set your markdown folder path."
        )
    return path


@app.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search_notes",
            description=(
                "Search markdown notes by content or title (case-insensitive). "
                "Returns {'results': [...]} with up to 10 matches, each with a short "
                "excerpt around the match. " + WARNINGS_NOTE
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search term to look for in note titles and content",
                    }
                },
                "required": ["query"],
            },
        ),
        types.Tool(
            name="get_note",
            description=(
                "Retrieve the full content and metadata of a specific note by its path "
                "relative to the vault root. If the note's frontmatter cannot be parsed, "
                "returns {'error', 'detail', 'path'} describing the problem instead."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "note_path": {
                        "type": "string",
                        "description": "Path to the note relative to the vault root (e.g. 'folder/My Note.md')",
                    }
                },
                "required": ["note_path"],
            },
        ),
        types.Tool(
            name="list_notes",
            description=(
                "List all markdown notes in the vault, optionally filtered to a specific "
                "subfolder. Returns {'notes': [...]}. " + WARNINGS_NOTE
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "folder": {
                        "type": "string",
                        "description": "Optional subfolder path relative to vault root to filter results",
                    }
                },
                "required": [],
            },
        ),
        types.Tool(
            name="list_writable_notes",
            description=(
                "Show all notes that agents can append to — those with agent_access: 'append' or 'edit' "
                "in their frontmatter, plus notes with no frontmatter (default access is 'append'). "
                "Use this before append_to_note to confirm a note is writable. "
                "Returns {'writable_notes': [...]}. " + WARNINGS_NOTE
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="append_to_note",
            description=(
                "Append content to the end of a note. "
                "Works on any note with agent_access: 'append' or 'edit' in its frontmatter, "
                "or any note without frontmatter (default access). "
                "Use list_writable_notes first if unsure which notes are available."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "note_path": {
                        "type": "string",
                        "description": "Path to the note relative to vault root (e.g. 'inbox.md' or 'folder/notes.md'). Note must have agent_access: append or edit.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Text to append to the note.",
                    },
                    "add_timestamp": {
                        "type": "boolean",
                        "description": "If true (default), prepend a '## YYYY-MM-DD HH:MM' heading before the content.",
                    },
                },
                "required": ["note_path", "content"],
            },
        ),
        types.Tool(
            name="list_templates",
            description=(
                "List all available templates in the vault's templates/ directory. "
                "Use this before create_note_from_template to see what templates exist. "
                "Returns {'templates': [...]}. " + WARNINGS_NOTE
            ),
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        types.Tool(
            name="create_note_from_template",
            description=(
                "Create a new note at the vault root from an existing template. "
                "The note is named '{template_name} {note_suffix}.md'. "
                "Use list_templates first to see available templates. "
                "Infer agent_access from the user's phrasing: "
                "'you can edit/update/modify' or 'fully editable' → 'edit'; "
                "'read-only', 'I'll edit this myself', or 'just create it' → 'read'; "
                "'this is private', 'keep this hidden', or 'don't show this' → 'hidden'; "
                "no instruction or 'you can add to'/'append-only' → 'append' (default)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "template_name": {
                        "type": "string",
                        "description": "Name of the template to use (e.g. 'PROJECT', 'New Book'). Must match a file in templates/.",
                    },
                    "note_suffix": {
                        "type": "string",
                        "description": "Custom name to append after the template name (e.g. 'deck replacement'). Letters, numbers, spaces, hyphens, underscores only. If omitted, a timestamp is used.",
                    },
                    "field_values": {
                        "type": "object",
                        "description": "Optional key-value pairs to pre-fill the note. Keys should match YAML frontmatter field names in the template (e.g. {'title': 'My Book', 'authors': 'Jane Smith'}). Keys not found in frontmatter will be matched against '# KEY:' headings in the body instead.",
                        "additionalProperties": {"type": "string"},
                    },
                    "agent_access": {
                        "type": "string",
                        "enum": ["edit", "append", "read", "hidden"],
                        "description": "Permission level for agent access to this note. 'edit' = agent can edit freely; 'append' = agent can only add content (default); 'read' = agent can view but not modify; 'hidden' = agent cannot see, search, or access this note at all.",
                    },
                },
                "required": ["template_name"],
            },
        ),
        types.Tool(
            name="update_note",
            description=(
                "Replace the entire body of a note with new content, preserving frontmatter. "
                "Requires agent_access: 'edit' in the note's frontmatter."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "note_path": {
                        "type": "string",
                        "description": "Path to the note relative to vault root (e.g. 'My Note.md').",
                    },
                    "new_content": {
                        "type": "string",
                        "description": "New body content for the note (frontmatter is preserved).",
                    },
                },
                "required": ["note_path", "new_content"],
            },
        ),
        types.Tool(
            name="replace_in_note",
            description=(
                "Find and replace a specific piece of text in a note body (exact match, case-sensitive). "
                "Only operates on body content — never touches frontmatter. "
                "Requires agent_access: 'edit' in the note's frontmatter."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "note_path": {
                        "type": "string",
                        "description": "Path to the note relative to vault root.",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "Exact text to find (case-sensitive).",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "Text to replace it with.",
                    },
                },
                "required": ["note_path", "old_text", "new_text"],
            },
        ),
        types.Tool(
            name="update_section",
            description=(
                "Replace the content under a specific heading in a note. "
                "The heading line itself is preserved; only the content below it (until the next heading) is replaced. "
                "Requires agent_access: 'edit' in the note's frontmatter."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "note_path": {
                        "type": "string",
                        "description": "Path to the note relative to vault root.",
                    },
                    "heading": {
                        "type": "string",
                        "description": "Exact heading text to find (e.g. '## Next Steps' or 'SYNOPSIS:').",
                    },
                    "new_content": {
                        "type": "string",
                        "description": "New content to place under the heading.",
                    },
                },
                "required": ["note_path", "heading", "new_content"],
            },
        ),
    ]


@app.call_tool()
async def handle_call_tool(
    name: str, arguments: dict[str, Any]
) -> list[types.TextContent]:
    try:
        vault = _vault_path()

        if name == "search_notes":
            query = arguments.get("query", "")
            if not query:
                raise ValueError("'query' argument is required")
            results = search_notes(vault, query)
            return _reply(results)

        elif name == "get_note":
            note_path = arguments.get("note_path", "")
            if not note_path:
                raise ValueError("'note_path' argument is required")
            result = get_note(vault, note_path)
            return _reply(result)

        elif name == "list_notes":
            folder = arguments.get("folder")
            results = list_notes(vault, folder)
            return _reply(results)

        elif name == "list_writable_notes":
            result = list_writable_notes(vault)
            return _reply(result)

        elif name == "append_to_note":
            note_path = arguments.get("note_path", "")
            content = arguments.get("content", "")
            if not note_path:
                raise ValueError("'note_path' argument is required")
            if not content:
                raise ValueError("'content' argument is required")
            add_timestamp = arguments.get("add_timestamp", True)
            result = append_to_note(vault, note_path, content, add_timestamp)
            return _reply(result)

        elif name == "list_templates":
            result = list_templates(vault)
            return _reply(result)

        elif name == "create_note_from_template":
            template_name = arguments.get("template_name", "")
            if not template_name:
                raise ValueError("'template_name' argument is required")
            note_suffix = arguments.get("note_suffix") or None
            field_values = arguments.get("field_values") or None
            agent_access = arguments.get("agent_access") or None
            print(
                f"[create_note_from_template] template={template_name!r} "
                f"suffix={note_suffix!r} field_values={field_values!r} "
                f"agent_access={agent_access!r}",
                file=sys.stderr,
            )
            result = create_note_from_template(vault, template_name, note_suffix, field_values, agent_access)
            return _reply(result)

        elif name == "update_note":
            note_path = arguments.get("note_path", "")
            new_content = arguments.get("new_content", "")
            if not note_path:
                raise ValueError("'note_path' argument is required")
            if not new_content:
                raise ValueError("'new_content' argument is required")
            result = update_note(vault, note_path, new_content)
            return _reply(result)

        elif name == "replace_in_note":
            note_path = arguments.get("note_path", "")
            old_text = arguments.get("old_text", "")
            new_text = arguments.get("new_text", "")
            if not note_path:
                raise ValueError("'note_path' argument is required")
            if not old_text:
                raise ValueError("'old_text' argument is required")
            result = replace_in_note(vault, note_path, old_text, new_text)
            return _reply(result)

        elif name == "update_section":
            note_path = arguments.get("note_path", "")
            heading = arguments.get("heading", "")
            new_content = arguments.get("new_content", "")
            if not note_path:
                raise ValueError("'note_path' argument is required")
            if not heading:
                raise ValueError("'heading' argument is required")
            if not new_content:
                raise ValueError("'new_content' argument is required")
            result = update_section(vault, note_path, heading, new_content)
            return _reply(result)

        else:
            raise ValueError(f"Unknown tool: {name}")

    except (FileNotFoundError, IsADirectoryError, NotADirectoryError, FileExistsError) as e:
        return [types.TextContent(type="text", text=f"Error: {e}")]
    except NoteReadError as e:
        # Structured so the caller can tell "this file is broken" apart from
        # "this file doesn't exist". Path + parse error only — never content.
        return _reply({"error": e.summary, "detail": e.detail, "path": e.note_path})
    except (ValueError, PermissionError, RuntimeError) as e:
        return [types.TextContent(type="text", text=f"Error: {e}")]
    except Exception as e:
        # Backstop: a single malformed note must never take down the tool call.
        traceback.print_exc(file=sys.stderr)
        return [
            types.TextContent(
                type="text", text=f"Error: unexpected {type(e).__name__}: {e}"
            )
        ]


async def _run() -> None:
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options(),
        )


def main() -> None:
    import asyncio
    asyncio.run(_run())


if __name__ == "__main__":
    main()
