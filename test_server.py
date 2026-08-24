"""
Quick test harness for the markdown-vault-mcp server.

Spawns the server as a subprocess over stdio and exercises each tool,
printing results so you can verify the MCP communication layer end-to-end.

Usage:
    python test_server.py [--vault /path/to/vault] [--note "relative/path.md"] [--query "search term"]

Defaults: reads VAULT_PATH from .env / environment, uses first note found for get_note.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

load_dotenv()

# ── helpers ──────────────────────────────────────────────────────────────────

RESET  = "\033[0m"
BOLD   = "\033[1m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
CYAN   = "\033[36m"


def header(msg: str) -> None:
    print(f"\n{BOLD}{CYAN}{'─' * 60}{RESET}")
    print(f"{BOLD}{CYAN}{msg}{RESET}")
    print(f"{BOLD}{CYAN}{'─' * 60}{RESET}")


def ok(msg: str) -> None:
    print(f"{GREEN}✓ {msg}{RESET}")


def warn(msg: str) -> None:
    print(f"{YELLOW}⚠ {msg}{RESET}")


def err(msg: str) -> None:
    print(f"{RED}✗ {msg}{RESET}")


def report_warnings(payload: dict) -> None:
    """Print the 'warnings' array a scanning tool returns, if it has one."""
    skipped = payload.get("warnings") if isinstance(payload, dict) else None
    if not skipped:
        return
    warn(f"{len(skipped)} file(s) skipped:")
    for entry in skipped[:5]:
        print(f"    {entry['path']} — {entry['error']}")


def pretty(data: str) -> None:
    try:
        parsed = json.loads(data)
        print(json.dumps(parsed, indent=2))
    except json.JSONDecodeError:
        print(data)


# ── core ─────────────────────────────────────────────────────────────────────

async def run_tests(vault_path: str, note_path: str | None, query: str) -> None:
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "markdown_vault_mcp.server"],
        env={**os.environ, "VAULT_PATH": vault_path},
    )

    all_note_paths: set[str] = set()
    writable_paths: set[str] = set()

    print(f"{BOLD}Vault:{RESET} {vault_path}")
    print(f"{BOLD}Server:{RESET} {sys.executable} -m markdown_vault_mcp.server")

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:

            # ── initialize ────────────────────────────────────────────────
            header("initialize")
            result = await session.initialize()
            ok(f"Connected — server: {result.serverInfo.name} {result.serverInfo.version or ''}")

            # ── list_tools ────────────────────────────────────────────────
            header("list_tools")
            tools_result = await session.list_tools()
            tools = tools_result.tools
            tool_names = [t.name for t in tools]
            ok(f"Tools advertised: {tool_names}")

            expected = {"search_notes", "get_note", "list_notes", "list_writable_notes", "append_to_note", "list_templates", "create_note_from_template"}
            missing = expected - set(tool_names)
            if missing:
                err(f"Missing expected tools: {missing}")
            else:
                ok("All expected tools present")

            # ── list_notes (no folder) ────────────────────────────────────
            header("list_notes  (no folder)")
            r = await session.call_tool("list_notes", {})
            text = r.content[0].text if r.content else ""
            if r.isError:
                err(f"Tool returned error:\n{text}")
            else:
                try:
                    payload = json.loads(text)
                    notes = payload["notes"]
                    all_note_paths = {n["path"] for n in notes}
                    ok(f"Found {len(notes)} note(s)")
                    report_warnings(payload)
                    if notes:
                        print(f"  First 3: {[n['path'] for n in notes[:3]]}")
                        if note_path is None:
                            note_path = notes[0]["path"]
                            warn(f"No --note given; will use first note: {note_path}")
                except json.JSONDecodeError:
                    warn(f"Response is not JSON:\n{text}")

            # ── list_notes (with folder) ──────────────────────────────────
            # Try the parent folder of the first note, or "." equivalent
            if note_path:
                folder = str(Path(note_path).parent) if Path(note_path).parent != Path(".") else None
                if folder:
                    header(f"list_notes  (folder={folder!r})")
                    r = await session.call_tool("list_notes", {"folder": folder})
                    text = r.content[0].text if r.content else ""
                    if r.isError:
                        err(f"Tool returned error:\n{text}")
                    else:
                        try:
                            payload = json.loads(text)
                            ok(f"Found {len(payload['notes'])} note(s) in '{folder}'")
                            report_warnings(payload)
                        except json.JSONDecodeError:
                            warn(f"Response is not JSON:\n{text}")

            # ── list_notes (bad folder) ───────────────────────────────────
            header("list_notes  (non-existent folder — expect error)")
            r = await session.call_tool("list_notes", {"folder": "__no_such_folder__"})
            text = r.content[0].text if r.content else ""
            if text.startswith("Error:"):
                ok(f"Returned expected error: {text}")
            else:
                warn(f"Expected an error message, got:\n{text}")

            # ── get_note ──────────────────────────────────────────────────
            if note_path:
                header(f"get_note  (path={note_path!r})")
                r = await session.call_tool("get_note", {"note_path": note_path})
                text = r.content[0].text if r.content else ""
                if r.isError:
                    err(f"Tool returned error:\n{text}")
                else:
                    try:
                        note = json.loads(text)
                        ok(f"Got note: {note.get('title')!r}  ({note.get('size')} bytes)")
                        print(f"  Frontmatter keys: {list(note.get('frontmatter', {}).keys())}")
                        preview = note.get("content", "")[:120].replace("\n", " ")
                        print(f"  Content preview: {preview!r}")
                    except json.JSONDecodeError:
                        warn(f"Response is not JSON:\n{text}")

            # ── get_note (missing file) ───────────────────────────────────
            header("get_note  (missing file — expect error)")
            r = await session.call_tool("get_note", {"note_path": "__nonexistent__.md"})
            text = r.content[0].text if r.content else ""
            if text.startswith("Error:"):
                ok(f"Returned expected error: {text}")
            else:
                warn(f"Expected an error message, got:\n{text}")

            # ── search_notes ──────────────────────────────────────────────
            header(f"search_notes  (query={query!r})")
            r = await session.call_tool("search_notes", {"query": query})
            text = r.content[0].text if r.content else ""
            if r.isError:
                err(f"Tool returned error:\n{text}")
            else:
                try:
                    payload = json.loads(text)
                    results = payload["results"]
                    ok(f"Search returned {len(results)} result(s)")
                    report_warnings(payload)
                    for res in results[:3]:
                        print(f"  [{res['relevance_score']}] {res['path']}")
                        print(f"    {res['snippet'][:80]}")
                except json.JSONDecodeError:
                    warn(f"Response is not JSON:\n{text}")

            # ── search_notes (no results) ─────────────────────────────────
            header("search_notes  (query unlikely to match anything)")
            r = await session.call_tool("search_notes", {"query": "zzzzzzzzzzzzzzzzzzzzzz"})
            text = r.content[0].text if r.content else ""
            if r.isError:
                err(f"Tool returned error:\n{text}")
            else:
                try:
                    payload = json.loads(text)
                    ok(f"Returned {len(payload['results'])} result(s) (expected 0)")
                    report_warnings(payload)
                except json.JSONDecodeError:
                    warn(f"Response is not JSON:\n{text}")

            # ── list_writable_notes ───────────────────────────────────────
            header("list_writable_notes")
            r = await session.call_tool("list_writable_notes", {})
            text = r.content[0].text if r.content else ""
            if r.isError:
                err(f"Tool returned error:\n{text}")
            else:
                try:
                    data = json.loads(text)
                    writable = data.get("writable_notes", [])
                    writable_paths = {w["path"] for w in writable}
                    ok(f"Whitelist has {len(writable)} note(s)")
                    for w in writable[:10]:
                        print(f"  {w['path']:40s}  [{w['access_level']}]")
                    report_warnings(data)
                except json.JSONDecodeError:
                    warn(f"Response is not JSON:\n{text}")

            # ── append_to_note ────────────────────────────────────────────
            header("append_to_note  (__scratch.md, with timestamp)")
            r = await session.call_tool(
                "append_to_note",
                {"note_path": "__scratch.md", "content": "test harness append — phase 2", "add_timestamp": True},
            )
            text = r.content[0].text if r.content else ""
            if r.isError:
                err(f"Tool returned error:\n{text}")
            else:
                try:
                    result = json.loads(text)
                    if result.get("success"):
                        ok(result.get("message", "ok"))
                        print(f"  Appended: {result.get('appended_content', '')!r}")
                    else:
                        warn(f"success=false: {result}")
                except json.JSONDecodeError:
                    warn(f"Response is not JSON:\n{text}")

            # ── append_to_note (no timestamp) ─────────────────────────────
            header("append_to_note  (books.md, no timestamp)")
            r = await session.call_tool(
                "append_to_note",
                {"note_path": "books.md", "content": "- test entry (safe to delete)", "add_timestamp": False},
            )
            text = r.content[0].text if r.content else ""
            if r.isError:
                err(f"Tool returned error:\n{text}")
            else:
                try:
                    result = json.loads(text)
                    if result.get("success"):
                        ok(result.get("message", "ok"))
                    else:
                        warn(f"success=false: {result}")
                except json.JSONDecodeError:
                    warn(f"Response is not JSON:\n{text}")

            # ── append_to_note (read-only note — expect error) ────────────
            # A note that list_notes sees but list_writable_notes doesn't is
            # read-only, so appending to it must be refused. Notes that don't
            # exist yet are NOT a permission error — they're created on append,
            # which is the documented default-access behaviour.
            header("append_to_note  (read-only note — expect error)")
            read_only = next(iter(all_note_paths - writable_paths), None)
            if read_only is None:
                warn("No read-only note in this vault; skipping permission check")
            else:
                r = await session.call_tool(
                    "append_to_note",
                    {"note_path": read_only, "content": "should be blocked"},
                )
                text = r.content[0].text if r.content else ""
                if text.startswith("Error:"):
                    ok(f"Blocked as expected ({read_only}): {text}")
                else:
                    err(f"Expected a permission error for {read_only!r}, got:\n{text}")

            # ── append_to_note (path traversal — expect error) ────────────
            header("append_to_note  (path traversal attempt — expect error)")
            r = await session.call_tool(
                "append_to_note",
                {"note_path": "../etc/passwd", "content": "should be blocked"},
            )
            text = r.content[0].text if r.content else ""
            if text.startswith("Error:"):
                ok(f"Blocked as expected: {text}")
            else:
                err(f"Expected a whitelist error, got:\n{text}")

            # ── list_templates ────────────────────────────────────────────
            header("list_templates")
            r = await session.call_tool("list_templates", {})
            text = r.content[0].text if r.content else ""
            template_names: list[str] = []
            if r.isError or text.startswith("Error:"):
                warn(f"list_templates returned error (templates/ may not exist): {text}")
            else:
                try:
                    data = json.loads(text)
                    templates = data.get("templates", [])
                    template_names = [t["name"] for t in templates]
                    ok(f"Found {len(templates)} template(s): {template_names}")
                    for t in templates:
                        print(f"  {t['name']:25s}  {t['description']}")
                    report_warnings(data)
                except json.JSONDecodeError:
                    warn(f"Response is not JSON:\n{text}")

            # ── list_templates (no templates dir) ─────────────────────────
            # Already covered above gracefully — if templates/ exists we test creation below

            # ── create_note_from_template ─────────────────────────────────
            if template_names:
                first_template = template_names[0]
                test_suffix = "test-harness-delete-me"
                header(f"create_note_from_template  (template={first_template!r}, suffix={test_suffix!r})")
                r = await session.call_tool(
                    "create_note_from_template",
                    {"template_name": first_template, "note_suffix": test_suffix},
                )
                text = r.content[0].text if r.content else ""
                if r.isError:
                    err(f"Tool returned error:\n{text}")
                else:
                    try:
                        result = json.loads(text)
                        if result.get("success"):
                            ok(result.get("message", "ok"))
                            print(f"  Created: {result.get('file_path')}")
                            print(f"  From:    {result.get('template_used')}")
                        else:
                            warn(f"success=false: {result}")
                    except json.JSONDecodeError:
                        warn(f"Response is not JSON:\n{text}")

                # ── duplicate creation — expect error ─────────────────────
                header(f"create_note_from_template  (duplicate — expect error)")
                r = await session.call_tool(
                    "create_note_from_template",
                    {"template_name": first_template, "note_suffix": test_suffix},
                )
                text = r.content[0].text if r.content else ""
                if text.startswith("Error:"):
                    ok(f"Duplicate blocked as expected: {text}")
                else:
                    err(f"Expected duplicate error, got:\n{text}")

            # ── invalid template name — expect error ──────────────────────
            header("create_note_from_template  (invalid template — expect error)")
            r = await session.call_tool(
                "create_note_from_template",
                {"template_name": "DOES_NOT_EXIST_TEMPLATE"},
            )
            text = r.content[0].text if r.content else ""
            if text.startswith("Error:"):
                ok(f"Missing template caught: {text}")
            else:
                err(f"Expected not-found error, got:\n{text}")

            # ── invalid suffix characters — expect error ───────────────────
            header("create_note_from_template  (invalid suffix chars — expect error)")
            r = await session.call_tool(
                "create_note_from_template",
                {"template_name": "PROJECT", "note_suffix": "bad/name/../here"},
            )
            text = r.content[0].text if r.content else ""
            if text.startswith("Error:"):
                ok(f"Invalid chars blocked: {text}")
            else:
                err(f"Expected validation error, got:\n{text}")

            # ── path traversal in template_name — expect error ────────────
            header("create_note_from_template  (path traversal — expect error)")
            r = await session.call_tool(
                "create_note_from_template",
                {"template_name": "../etc/passwd"},
            )
            text = r.content[0].text if r.content else ""
            if text.startswith("Error:"):
                ok(f"Traversal blocked: {text}")
            else:
                err(f"Expected validation error, got:\n{text}")

    header("done")
    ok("Server exited cleanly")


def main() -> None:
    parser = argparse.ArgumentParser(description="Test harness for markdown-vault-mcp")
    parser.add_argument("--vault", help="Path to Obsidian vault (overrides VAULT_PATH env)")
    parser.add_argument("--note", help="Relative path to a note to test get_note with")
    parser.add_argument("--query", default="the", help="Search term for search_notes (default: 'the')")
    args = parser.parse_args()

    vault = args.vault or os.environ.get("VAULT_PATH", "")
    if not vault:
        err("No vault path. Set VAULT_PATH in .env or pass --vault /path/to/vault")
        sys.exit(1)

    asyncio.run(run_tests(vault, args.note, args.query))


if __name__ == "__main__":
    main()
