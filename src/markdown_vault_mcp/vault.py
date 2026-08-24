"""Vault operations for a markdown folder (read + controlled write)."""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from typing import TypedDict

import frontmatter


IGNORED_DIRS = {".obsidian"}


def _resolve_vault(vault_path: str) -> Path:
    """Resolve vault path, handling iCloud-style .nosync or spaces."""
    path = Path(vault_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Vault path does not exist: {path}")
    if not path.is_dir():
        raise NotADirectoryError(f"Vault path is not a directory: {path}")
    return path


def _is_ignored(path: Path, vault_root: Path) -> bool:
    """Return True if any path component is in IGNORED_DIRS."""
    try:
        relative = path.relative_to(vault_root)
    except ValueError:
        return True
    return any(part in IGNORED_DIRS for part in relative.parts)


def _warn(message: str) -> None:
    """Log to stderr so it doesn't interfere with the stdio MCP transport."""
    ts = datetime.now().isoformat(timespec="seconds")
    print(f"[{ts}] warning: {message}", file=sys.stderr)


def _format_modified(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")


class NoteReadError(Exception):
    """A note could not be read, or its frontmatter could not be parsed.

    Carries the vault-relative path and a short, user-facing detail string.
    Never carries any of the note's own content: a note may be unparseable and
    still have meant to be hidden, so only the path and the parse error are
    ever surfaced.
    """

    def __init__(self, note_path: str, detail: str, summary: str) -> None:
        self.note_path = note_path
        self.detail = detail
        self.summary = summary
        super().__init__(f"{summary} ('{note_path}'): {detail}")


def _parse_error_detail(exc: Exception) -> str:
    """Short, single-line description of a frontmatter parse failure."""
    problem = getattr(exc, "problem", None)
    mark = getattr(exc, "problem_mark", None)
    if problem:
        # PyYAML marks are 0-indexed and line up with the file's own lines,
        # since the frontmatter block starts at the top of the file.
        where = f" (line {mark.line + 1})" if mark is not None else ""
        return f"YAML parse error: {problem}{where}"
    first_line = str(exc).splitlines()[0] if str(exc) else ""
    return f"YAML parse error: {first_line or type(exc).__name__}"


def _load_post(full_path: Path, note_path: str) -> frontmatter.Post:
    """Parse a note into a frontmatter Post.

    Raises NoteReadError if the file can't be read or its frontmatter can't be
    parsed, so vault-wide scans can skip a single bad file — and report it —
    instead of failing everywhere. Empty and whitespace-only files are valid:
    they parse to a post with no frontmatter and no content.
    """
    try:
        text = full_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise NoteReadError(
            note_path,
            f"Unreadable file: {exc.strerror or exc}",
            "Could not read this note",
        ) from exc

    # A UTF-8 BOM before the opening '---' hides the frontmatter block from the
    # parser, which would silently downgrade a 'hidden' note to the default.
    text = text.lstrip("﻿")

    if not text.strip():
        return frontmatter.Post("")

    try:
        return frontmatter.loads(text)
    except Exception as exc:  # malformed YAML, unsupported tags, etc.
        raise NoteReadError(
            note_path,
            _parse_error_detail(exc),
            "Could not parse this note's frontmatter",
        ) from exc


def _read_note(full_path: Path, note_path: str) -> tuple[dict, str]:
    """Read a note's frontmatter + body. Raises NoteReadError if unparseable."""
    post = _load_post(full_path, note_path)
    metadata = post.metadata if isinstance(post.metadata, dict) else {}
    return metadata, post.content


def _note_title(path: Path) -> str:
    return path.stem


def _relative_str(path: Path, vault_root: Path) -> str:
    return str(path.relative_to(vault_root))


class NoteInfo(TypedDict):
    title: str
    path: str
    size: int
    modified: str


class NoteContent(TypedDict):
    title: str
    path: str
    content: str
    frontmatter: dict
    modified: str
    size: int


class SearchResult(TypedDict):
    title: str
    path: str
    snippet: str
    relevance_score: float


class SkippedFile(TypedDict):
    """A file a scan couldn't read. Path and error only — never any content."""
    path: str
    error: str


class _WithWarnings(TypedDict, total=False):
    """Mixin: 'warnings' is present only when files were actually skipped."""
    warnings: list[SkippedFile]


class ListNotesResult(_WithWarnings):
    notes: list[NoteInfo]


class SearchNotesResult(_WithWarnings):
    results: list[SearchResult]


class SkipCollector:
    """Collects files skipped during a scan, logging each one to stderr."""

    def __init__(self) -> None:
        self.skipped: list[SkippedFile] = []

    def record(self, exc: NoteReadError) -> None:
        _warn(f"skipping {exc.note_path!r}: {exc.detail}")
        self.skipped.append(SkippedFile(path=exc.note_path, error=exc.detail))

    def attach(self, payload: dict) -> dict:
        """Add 'warnings' to a result, omitting the key entirely when empty."""
        if self.skipped:
            payload["warnings"] = self.skipped
        return payload


def list_notes(vault_path: str, folder: str | None = None) -> ListNotesResult:
    """List all .md notes in the vault, optionally filtered by subfolder."""
    root = _resolve_vault(vault_path)
    search_root = root

    if folder:
        search_root = (root / folder).resolve()
        if not search_root.exists():
            raise FileNotFoundError(f"Folder not found: {folder}")
        if not search_root.is_relative_to(root):
            raise ValueError("Folder path escapes vault root")

    notes: list[NoteInfo] = []
    skips = SkipCollector()

    for md_file in sorted(search_root.rglob("*.md")):
        if _is_ignored(md_file, root):
            continue

        relative = _relative_str(md_file, root)
        try:
            metadata, _ = _read_note(md_file, relative)
            stat = md_file.stat()  # can fail on a broken symlink
        except NoteReadError as exc:
            skips.record(exc)
            continue
        except OSError as exc:
            skips.record(
                NoteReadError(
                    relative,
                    f"Unreadable file: {exc.strerror or exc}",
                    "Could not read this note",
                )
            )
            continue

        # Hidden notes are excluded silently — that's a deliberate setting,
        # not a problem the user needs to be told about.
        if _access_of(metadata) == "hidden":
            continue

        notes.append(
            NoteInfo(
                title=_note_title(md_file),
                path=relative,
                size=stat.st_size,
                modified=_format_modified(md_file),
            )
        )

    return skips.attach(ListNotesResult(notes=notes))


def get_note(vault_path: str, note_path: str) -> NoteContent:
    """Retrieve full content and metadata for a note."""
    root = _resolve_vault(vault_path)
    full_path = (root / note_path).resolve()

    if not full_path.is_relative_to(root):
        raise ValueError("note_path escapes vault root")
    if not full_path.exists():
        raise FileNotFoundError(f"Note not found: {note_path}")
    if not full_path.is_file():
        raise IsADirectoryError(f"Path is a directory, not a note: {note_path}")
    if full_path.suffix.lower() != ".md":
        raise ValueError(f"Not a markdown file: {note_path}")
    if _is_ignored(full_path, root):
        raise PermissionError(f"Note is in an ignored directory: {note_path}")

    metadata, content = _read_note(full_path, _relative_str(full_path, root))
    if _access_of(metadata) == "hidden":
        raise PermissionError(f"Note '{note_path}' is not accessible to agents")

    stat = full_path.stat()

    return NoteContent(
        title=_note_title(full_path),
        path=_relative_str(full_path, root),
        content=content,
        frontmatter=metadata,
        modified=_format_modified(full_path),
        size=stat.st_size,
    )


def _build_snippet(text: str, query: str, context: int = 75) -> str:
    """Return a snippet of ~2*context chars around the first match."""
    lower_text = text.lower()
    lower_query = query.lower()
    idx = lower_text.find(lower_query)
    if idx == -1:
        return text[:context * 2].strip()
    start = max(0, idx - context)
    end = min(len(text), idx + len(query) + context)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet


def _score(title: str, content: str, query: str) -> float:
    """Simple relevance score: title match > content match, weighted by frequency."""
    q = query.lower()
    title_hits = title.lower().count(q)
    content_hits = content.lower().count(q)
    return title_hits * 5.0 + content_hits * 1.0


def search_notes(vault_path: str, query: str, limit: int = 10) -> SearchNotesResult:
    """Search notes by filename and content (case-insensitive), return top results."""
    root = _resolve_vault(vault_path)
    query_lower = query.lower()
    results: list[SearchResult] = []
    skips = SkipCollector()

    for md_file in root.rglob("*.md"):
        if _is_ignored(md_file, root):
            continue

        title = _note_title(md_file)
        try:
            metadata, content = _read_note(md_file, _relative_str(md_file, root))
        except NoteReadError as exc:
            skips.record(exc)
            continue

        if _access_of(metadata) == "hidden":
            continue

        title_match = query_lower in title.lower()
        content_match = query_lower in content.lower()

        if not (title_match or content_match):
            continue

        score = _score(title, content, query)
        snippet_source = content if content_match else title
        snippet = _build_snippet(snippet_source, query)

        results.append(
            SearchResult(
                title=title,
                path=_relative_str(md_file, root),
                snippet=snippet,
                relevance_score=round(score, 2),
            )
        )

    results.sort(key=lambda r: r["relevance_score"], reverse=True)
    return skips.attach(SearchNotesResult(results=results[:limit]))


# ── write operations ──────────────────────────────────────────────────────────

class WritableNoteInfo(TypedDict):
    path: str
    access_level: str


class AppendResult(TypedDict):
    success: bool
    note_path: str
    appended_content: str
    message: str


class WritableNotesResult(_WithWarnings):
    writable_notes: list[WritableNoteInfo]


def list_writable_notes(vault_path: str) -> WritableNotesResult:
    """Return all notes that agents can append to (agent_access: append or edit, or no frontmatter)."""
    root = _resolve_vault(vault_path)
    notes: list[WritableNoteInfo] = []
    skips = SkipCollector()

    for md_file in sorted(root.rglob("*.md")):
        if _is_ignored(md_file, root):
            continue

        relative = _relative_str(md_file, root)
        try:
            metadata, _ = _read_note(md_file, relative)
        except NoteReadError as exc:
            skips.record(exc)
            continue

        access = _access_of(metadata)
        if access not in ("append", "edit"):
            continue

        notes.append(WritableNoteInfo(path=relative, access_level=access))

    return skips.attach(WritableNotesResult(writable_notes=notes))


def append_to_note(
    vault_path: str,
    note_path: str,
    content: str,
    add_timestamp: bool = True,
) -> AppendResult:
    """Append content to any note with agent_access: append or edit. Creates the file if needed."""
    root = _resolve_vault(vault_path)
    full_path = (root / note_path).resolve()

    if not full_path.is_relative_to(root):
        raise ValueError("note_path escapes vault root")
    if full_path.suffix.lower() != ".md":
        raise ValueError(f"Not a markdown file: {note_path}")

    # If the file exists, verify frontmatter permission.
    # If it doesn't exist yet, default access is 'append' — allow creation.
    if full_path.exists():
        allowed, current = _check_agent_access(
            _load_post(full_path, note_path), "append"
        )
        if not allowed:
            raise PermissionError(
                f"Insufficient permissions. This note has agent_access: '{current}', "
                "but this operation requires: 'append' or higher. "
                "Add agent_access: 'append' or 'edit' to the note's frontmatter to enable this."
            )

    if add_timestamp:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        block = f"\n## {ts}\n{content}\n"
    else:
        block = f"\n{content}\n"

    created = not full_path.exists()

    if not created:
        existing = full_path.read_text(encoding="utf-8")
        if existing and not existing.endswith("\n"):
            block = "\n" + block

    with full_path.open("a", encoding="utf-8") as f:
        f.write(block)

    # Log to stderr so it doesn't interfere with the stdio MCP transport
    ts_log = datetime.now().isoformat(timespec="seconds")
    action = "created+appended" if created else "appended"
    print(f"[{ts_log}] write: {action} {note_path!r}", file=sys.stderr)

    msg = (
        f"Created '{note_path}' and appended content."
        if created
        else f"Successfully appended to '{note_path}'."
    )

    return AppendResult(
        success=True,
        note_path=note_path,
        appended_content=block,
        message=msg,
    )


# ── template operations ───────────────────────────────────────────────────────

# Allowed characters in template names and note suffixes.
# Covers the examples in the spec: "PROJECT", "WRITING PROJECT", "New Book", etc.
_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9 _-]+$")
_MAX_FILENAME_LEN = 200


class TemplateInfo(TypedDict):
    name: str
    path: str
    size: int
    description: str


class CreateNoteResult(TypedDict):
    success: bool
    file_path: str
    message: str
    template_used: str
    fields_applied: dict[str, str]


class EditNoteResult(TypedDict):
    success: bool
    note_path: str
    message: str


class TemplatesResult(_WithWarnings):
    templates: list[TemplateInfo]


def _replace_placeholders(content: str) -> str:
    """Replace {{PLACEHOLDER}} tokens with current date/time values."""
    now = datetime.now()
    replacements = {
        "{{TODAY}}":     now.strftime("%Y-%m-%d"),
        "{{DATE}}":      now.strftime("%Y-%m-%d"),
        "{{NOW}}":       now.strftime("%Y-%m-%d %H:%M:%S"),
        "{{TIME}}":      now.strftime("%H:%M:%S"),
        "{{TIMESTAMP}}": str(int(now.timestamp())),
        "{{YEAR}}":      now.strftime("%Y"),
        "{{MONTH}}":     now.strftime("%m"),
        "{{DAY}}":       now.strftime("%d"),
    }
    for placeholder, value in replacements.items():
        content = content.replace(placeholder, value)
    return content


def _extract_template_description(content: str) -> str:
    """Pull a description from the first line if it's a comment, otherwise generic."""
    first_line = content.lstrip().split("\n")[0] if content.strip() else ""
    # Block comment: %% some description %%
    if first_line.startswith("%%"):
        inner = first_line.strip("%").strip()
        if inner:
            return inner
    # HTML comment: <!-- some description -->
    if first_line.startswith("<!--"):
        inner = re.sub(r"^<!--\s*|-->.*$", "", first_line).strip()
        if inner:
            return inner
    return "Template"


def _validate_safe_name(value: str, label: str) -> None:
    """Raise ValueError if value contains characters outside the safe set."""
    if not value:
        raise ValueError(f"{label} must not be empty")
    if not _SAFE_NAME_RE.match(value):
        raise ValueError(
            f"{label} contains invalid characters — "
            "only letters, numbers, spaces, hyphens, and underscores are allowed"
        )


def _templates_dir(root: Path) -> Path:
    td = root / "templates"
    if not td.exists() or not td.is_dir():
        raise FileNotFoundError("Templates directory not found in vault")
    return td


def list_templates(vault_path: str) -> TemplatesResult:
    """List all .md files in the vault's templates/ directory."""
    root = _resolve_vault(vault_path)
    td = _templates_dir(root)

    templates: list[TemplateInfo] = []
    skips = SkipCollector()

    for md_file in sorted(td.glob("*.md")):
        relative = _relative_str(md_file, root)
        try:
            # Parse up front: a template whose frontmatter is unparseable can't
            # be used by create_note_from_template, so listing it as available
            # would just set up a later failure.
            _load_post(md_file, relative)
            content = md_file.read_text(encoding="utf-8", errors="replace")
            stat = md_file.stat()
        except NoteReadError as exc:
            skips.record(exc)
            continue
        except OSError as exc:
            skips.record(
                NoteReadError(
                    relative,
                    f"Unreadable file: {exc.strerror or exc}",
                    "Could not read this template",
                )
            )
            continue

        templates.append(
            TemplateInfo(
                name=md_file.stem,
                path=relative,
                size=stat.st_size,
                description=_extract_template_description(content),
            )
        )

    return skips.attach(TemplatesResult(templates=templates))


def _resolve_note_path(vault_path: str, note_path: str) -> tuple[Path, Path]:
    """Resolve and validate a note path inside the vault. Returns (root, full_path)."""
    root = _resolve_vault(vault_path)
    full_path = (root / note_path).resolve()
    if not full_path.is_relative_to(root):
        raise ValueError("note_path escapes vault root")
    if not full_path.exists():
        raise FileNotFoundError(f"Note not found: {note_path}")
    if not full_path.is_file():
        raise IsADirectoryError(f"Path is a directory, not a note: {note_path}")
    if full_path.suffix.lower() != ".md":
        raise ValueError(f"Not a markdown file: {note_path}")
    if _is_ignored(full_path, root):
        raise PermissionError(f"Note is in an ignored directory: {note_path}")
    return root, full_path


_LEGACY_ACCESS_MAP = {"full": "edit", "none": "read"}
_ACCESS_LEVELS = {"hidden": 0, "read": 1, "append": 2, "edit": 3}


def _normalize_access(raw: object) -> str:
    """Map legacy values and return normalized agent_access string.

    YAML happily produces non-strings here (`agent_access: [append]`,
    `agent_access: true`, a bare `agent_access:`). Anything that isn't a known
    string is stringified rather than raising, so it resolves to an unknown
    level: visible, but not writable.
    """
    if raw is None:
        return "append"  # key present but blank — same as no key at all
    if not isinstance(raw, str):
        return str(raw)
    return _LEGACY_ACCESS_MAP.get(raw, raw)


def _access_of(metadata: dict) -> str:
    """Normalized agent_access level for a note's frontmatter."""
    return _normalize_access(metadata.get("agent_access", "append"))


def _check_agent_access(post: frontmatter.Post, required: str) -> tuple[bool, str]:
    """Check agent_access frontmatter permission. Returns (allowed, current_access)."""
    metadata = post.metadata if isinstance(post.metadata, dict) else {}
    current = _access_of(metadata)
    required_num = _ACCESS_LEVELS.get(required, 0)
    current_num = _ACCESS_LEVELS.get(current, -1)
    return current_num >= required_num, current


def update_note(vault_path: str, note_path: str, new_content: str) -> EditNoteResult:
    """Replace the body of a note, preserving its frontmatter. Requires agent_access: edit."""
    root, full_path = _resolve_note_path(vault_path, note_path)
    post = _load_post(full_path, note_path)
    allowed, current = _check_agent_access(post, "edit")
    if not allowed:
        raise PermissionError(
            f"Insufficient permissions. This note has agent_access: '{current}', "
            "but this operation requires: 'edit'. "
            "Add agent_access: 'edit' to the note's frontmatter to enable this operation."
        )

    post.content = new_content
    full_path.write_text(frontmatter.dumps(post), encoding="utf-8")

    ts_log = datetime.now().isoformat(timespec="seconds")
    print(f"[{ts_log}] update_note: '{note_path}'", file=sys.stderr)

    return EditNoteResult(
        success=True,
        note_path=_relative_str(full_path, root),
        message=f"Note body updated: '{note_path}'",
    )


def replace_in_note(
    vault_path: str, note_path: str, old_text: str, new_text: str
) -> EditNoteResult:
    """Find and replace text in a note body. Requires agent_access: edit."""
    root, full_path = _resolve_note_path(vault_path, note_path)
    post = _load_post(full_path, note_path)
    allowed, current = _check_agent_access(post, "edit")
    if not allowed:
        raise PermissionError(
            f"Insufficient permissions. This note has agent_access: '{current}', "
            "but this operation requires: 'edit'. "
            "Add agent_access: 'edit' to the note's frontmatter to enable this operation."
        )

    if old_text not in post.content:
        raise ValueError(f"Text not found in note: {old_text!r}")

    post.content = post.content.replace(old_text, new_text)
    full_path.write_text(frontmatter.dumps(post), encoding="utf-8")

    ts_log = datetime.now().isoformat(timespec="seconds")
    print(f"[{ts_log}] replace_in_note: '{note_path}'", file=sys.stderr)

    return EditNoteResult(
        success=True,
        note_path=_relative_str(full_path, root),
        message=f"Replacement applied in '{note_path}'",
    )


def update_section(
    vault_path: str, note_path: str, heading: str, new_content: str
) -> EditNoteResult:
    """Replace the content under a heading (preserves the heading). Requires agent_access: edit."""
    root, full_path = _resolve_note_path(vault_path, note_path)
    post = _load_post(full_path, note_path)
    allowed, current = _check_agent_access(post, "edit")
    if not allowed:
        raise PermissionError(
            f"Insufficient permissions. This note has agent_access: '{current}', "
            "but this operation requires: 'edit'. "
            "Add agent_access: 'edit' to the note's frontmatter to enable this operation."
        )

    body = post.content

    # Find the heading line (exact match on the full line after stripping)
    lines = body.splitlines(keepends=True)
    heading_idx = None
    for i, line in enumerate(lines):
        if line.strip() == heading.strip():
            heading_idx = i
            break
    if heading_idx is None:
        raise ValueError(f"Heading '{heading}' not found in note")

    # Find the next heading (any line starting with one or more '#')
    _HEADING_RE = re.compile(r"^#{1,6}\s")
    next_heading_idx = None
    for i in range(heading_idx + 1, len(lines)):
        if _HEADING_RE.match(lines[i]):
            next_heading_idx = i
            break

    replacement = new_content if new_content.endswith("\n") else new_content + "\n"
    if next_heading_idx is not None:
        new_lines = lines[: heading_idx + 1] + [replacement] + lines[next_heading_idx:]
    else:
        new_lines = lines[: heading_idx + 1] + [replacement]

    post.content = "".join(new_lines)
    full_path.write_text(frontmatter.dumps(post), encoding="utf-8")

    ts_log = datetime.now().isoformat(timespec="seconds")
    print(f"[{ts_log}] update_section: '{note_path}' heading={heading!r}", file=sys.stderr)

    return EditNoteResult(
        success=True,
        note_path=_relative_str(full_path, root),
        message=f"Section '{heading}' updated in '{note_path}'",
    )


def create_note_from_template(
    vault_path: str,
    template_name: str,
    note_suffix: str | None = None,
    field_values: dict[str, str] | None = None,
    agent_access: str | None = None,
) -> CreateNoteResult:
    """Create a new note in the vault root from a named template."""
    _validate_safe_name(template_name, "Template name")

    root = _resolve_vault(vault_path)
    td = _templates_dir(root)

    template_path = td / f"{template_name}.md"
    if not template_path.exists():
        available = [f.stem for f in sorted(td.glob("*.md"))]
        available_str = ", ".join(available) if available else "none"
        raise FileNotFoundError(
            f"Template '{template_name}' not found. "
            f"Available templates: {available_str}"
        )

    # Build output filename
    if note_suffix and note_suffix.strip():
        _validate_safe_name(note_suffix.strip(), "Note suffix")
        filename = f"{template_name} {note_suffix.strip()}.md"
    else:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"{template_name} {ts}.md"

    if len(filename) > _MAX_FILENAME_LEN:
        raise ValueError(f"Filename too long (max {_MAX_FILENAME_LEN} characters)")

    output_path = root / filename
    if output_path.exists():
        raise FileExistsError(f"Note already exists: {filename}")

    # Read template and apply field_values, then expand placeholders last.
    # field_values strategy:
    #   - Keys that match existing YAML frontmatter fields → update the frontmatter dict
    #   - Keys with no frontmatter match → fall back to # KEY: heading replacement in body
    # Placeholders ({{TODAY}} etc.) are expanded after all field substitution so they
    # are also resolved inside any freshly-written frontmatter values.
    try:
        _load_post(template_path, _relative_str(template_path, root))
    except NoteReadError as exc:
        raise NoteReadError(
            exc.note_path, exc.detail, "Could not parse this template's frontmatter"
        ) from exc
    raw = template_path.read_text(encoding="utf-8")

    applied: dict[str, str] = {}
    if field_values:
        post = frontmatter.loads(raw)
        body_replacements: dict[str, str] = {}
        for key, value in field_values.items():
            if key in post.metadata:
                if key == "authors" and isinstance(value, str) and " and " in value:
                    post.metadata[key] = [name.strip() for name in value.split(" and ")]
                else:
                    post.metadata[key] = value
                applied[key] = value
            else:
                body_replacements[key] = value
        content = frontmatter.dumps(post)
        for key, value in body_replacements.items():
            if f"# {key}:" in content:
                content = content.replace(f"# {key}:", f"# {key}:\n{value}")
                applied[key] = value
    else:
        content = raw

    content = _replace_placeholders(content)

    # Resolve {{AGENT_ACCESS}} placeholder: explicit param wins, else default to "append".
    _valid_access = ("edit", "append", "read", "hidden", "full", "none")
    if "{{AGENT_ACCESS}}" in content:
        resolved_access = _normalize_access(agent_access) if agent_access in _valid_access else "append"
        content = content.replace("{{AGENT_ACCESS}}", resolved_access)
        applied["agent_access"] = resolved_access
    elif agent_access in _valid_access:
        # Template has a hardcoded value — explicit param overrides it via frontmatter.
        post = frontmatter.loads(content)
        if "agent_access" in post.metadata:
            post.metadata["agent_access"] = _normalize_access(agent_access)
            content = frontmatter.dumps(post)
            applied["agent_access"] = _normalize_access(agent_access)

    output_path.write_text(content, encoding="utf-8")

    ts_log = datetime.now().isoformat(timespec="seconds")
    print(
        f"[{ts_log}] create: '{filename}' from template '{template_name}' "
        f"fields_applied={list(applied)}",
        file=sys.stderr,
    )

    return CreateNoteResult(
        success=True,
        file_path=filename,
        message="Created new note from template",
        template_used=_relative_str(template_path, root),
        fields_applied=applied,
    )
