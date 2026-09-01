"""
Robustness tests for vault scanning against malformed and degenerate notes.

These cover the class of bug where a single bad file made vault-wide
operations (search_notes, list_writable_notes, list_notes) fail for every
note. Pure unit tests against vault.py — no server subprocess, no real vault.

Usage:
    python test_vault_robustness.py
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from markdown_vault_mcp import vault  # noqa: E402

RESET, GREEN, RED = "\033[0m", "\033[32m", "\033[31m"

_failures: list[str] = []


def check(condition: bool, label: str) -> None:
    if condition:
        print(f"{GREEN}✓ {label}{RESET}")
    else:
        print(f"{RED}✗ {label}{RESET}")
        _failures.append(label)


# ── fixture ───────────────────────────────────────────────────────────────────

# One healthy note per access level, plus every degenerate file we know of.
NOTES: dict[str, str] = {
    "Good.md": "---\nagent_access: edit\n---\nhello alpha world\n",
    "Plain.md": "no frontmatter at all, alpha\n",
    "ReadOnly.md": "---\nagent_access: read\n---\nalpha read-only\n",
    "Hidden.md": "---\nagent_access: hidden\n---\nalpha secret\n",
    # degenerate but valid
    "Empty.md": "",
    "Whitespace.md": "   \n\n\t\n",
    "OnlyDashes.md": "---\n---\n",
    "BlankAccess.md": "---\nagent_access:\n---\nalpha blank\n",
    # malformed / hostile
    "BadYaml.md": "---\ntitle: a: b\n---\nalpha body\n",
    "UnclosedQuote.md": '---\ntitle: "oops\n---\nalpha body\n',
    "BadToken.md": "---\nfoo: @bar\n---\nalpha body\n",
    "PyTag.md": "---\nfoo: !!python/object:os.system\n---\nalpha\n",
    "ListAccess.md": "---\nagent_access: [append]\n---\nalpha body\n",
    "DictAccess.md": "---\nagent_access: {a: 1}\n---\nalpha body\n",
    "BoolAccess.md": "---\nagent_access: true\n---\nalpha body\n",
    "DateFrontmatter.md": "---\ndate: 2024-01-01\nagent_access: edit\n---\nalpha dated\n",
    "BomHidden.md": "﻿---\nagent_access: hidden\n---\nalpha bom secret\n",
}

HEALTHY_VISIBLE = {"Good.md", "Plain.md", "ReadOnly.md", "Empty.md",
                   "Whitespace.md", "OnlyDashes.md", "BlankAccess.md",
                   "DateFrontmatter.md"}


def build_vault(root: Path) -> None:
    (root / "templates").mkdir(parents=True)
    for name, body in NOTES.items():
        (root / name).write_text(body, encoding="utf-8")
    (root / "templates" / "EmptyTemplate.md").write_text("", encoding="utf-8")
    (root / "templates" / "BadTemplate.md").write_text(
        "---\ntitle: a: b\n---\nbody\n", encoding="utf-8"
    )
    (root / "templates" / "Good.md").write_text(
        "%% a good template %%\n---\nagent_access: {{AGENT_ACCESS}}\n---\n# Notes\n",
        encoding="utf-8",
    )


def run(root: Path) -> None:
    v = str(root)

    # ── the original symptom: one bad file must not break the whole vault ──
    listing = vault.list_notes(v)
    paths = {n["path"] for n in listing["notes"]}
    check(HEALTHY_VISIBLE <= paths, "list_notes returns every healthy note")
    check("Hidden.md" not in paths, "list_notes excludes agent_access: hidden")
    check("BomHidden.md" not in paths, "list_notes excludes hidden note with a UTF-8 BOM")
    check("BadYaml.md" not in paths, "list_notes skips the unparseable note")

    search = vault.search_notes(v, "alpha", limit=100)
    hits = {r["path"] for r in search["results"]}
    check("Good.md" in hits and "Plain.md" in hits, "search_notes finds healthy notes")
    check("Hidden.md" not in hits, "search_notes excludes hidden notes")
    check("BomHidden.md" not in hits, "search_notes excludes BOM-hidden notes")
    check("Empty.md" not in hits, "search_notes never matches an empty note")

    writable = {n["path"]: n["access_level"] for n in vault.list_writable_notes(v)["writable_notes"]}
    check("Good.md" in writable and writable["Good.md"] == "edit",
          "list_writable_notes reports edit access")
    check(writable.get("Empty.md") == "append",
          "list_writable_notes lists an empty note as append")
    check(writable.get("BlankAccess.md") == "append",
          "a blank agent_access value falls back to append")
    check("ReadOnly.md" not in writable, "list_writable_notes excludes read-only notes")
    check("ListAccess.md" not in writable,
          "a non-string agent_access is not writable (fails closed)")
    check("BadYaml.md" not in writable, "list_writable_notes skips unparseable notes")

    # ── get_note ──────────────────────────────────────────────────────────
    empty = vault.get_note(v, "Empty.md")
    check(empty["content"] == "" and empty["frontmatter"] == {},
          "get_note on an empty note returns empty content and frontmatter")

    dated = vault.get_note(v, "DateFrontmatter.md")
    check(json.dumps(dated, default=str).startswith("{"),
          "get_note output with a YAML date is JSON-serializable")

    for bad, label in [("BadYaml.md", "malformed YAML"), ("BadToken.md", "an unscannable token")]:
        try:
            vault.get_note(v, bad)
            check(False, f"get_note on {label} raises NoteReadError")
        except vault.NoteReadError as exc:
            check(exc.note_path == bad and exc.detail.startswith("YAML parse error:"),
                  f"get_note on {label} raises NoteReadError with path and detail")
        except Exception as exc:  # noqa: BLE001
            check(False, f"get_note on {label} raised {type(exc).__name__} instead")

    try:
        vault.get_note(v, "Hidden.md")
        check(False, "get_note on a hidden note raises PermissionError")
    except PermissionError:
        check(True, "get_note on a hidden note raises PermissionError")

    # ── writes ────────────────────────────────────────────────────────────
    vault.append_to_note(v, "Empty.md", "first line", add_timestamp=False)
    check("first line" in (root / "Empty.md").read_text(encoding="utf-8"),
          "append_to_note works on a previously empty note")

    for bad in ("BadYaml.md", "ListAccess.md"):
        try:
            vault.append_to_note(v, bad, "nope", add_timestamp=False)
            check(False, f"append_to_note refuses {bad}")
        except (vault.NoteReadError, ValueError, PermissionError):
            check(True, f"append_to_note refuses {bad}")

    try:
        vault.update_note(v, "BadYaml.md", "clobbered")
        check(False, "update_note refuses to rewrite an unparseable note")
    except vault.NoteReadError:
        check(True, "update_note refuses to rewrite an unparseable note")
    check("alpha body" in (root / "BadYaml.md").read_text(encoding="utf-8"),
          "the unparseable note was left untouched on disk")

    # ── templates ─────────────────────────────────────────────────────────
    templates = vault.list_templates(v)
    names = {t["name"] for t in templates["templates"]}
    check({"EmptyTemplate", "Good"} <= names,
          "list_templates lists empty and healthy templates")
    check("BadTemplate" not in names,
          "list_templates skips a template with malformed frontmatter")

    created = vault.create_note_from_template(v, "Good", "From Test", agent_access="edit")
    check((root / created["file_path"]).exists(), "create_note_from_template still works")

    try:
        vault.create_note_from_template(v, "BadTemplate", "Nope")
        check(False, "create_note_from_template rejects a malformed template clearly")
    except vault.NoteReadError as exc:
        check(exc.note_path == "templates/BadTemplate.md",
              "create_note_from_template rejects a malformed template clearly")

    # ── skipped files are visible in the output, not just stderr ──────────
    run_warning_checks(root, listing, search, templates)


# The note scans recurse into templates/ too, so the broken template is
# reported alongside the broken notes.
BROKEN = {"BadYaml.md", "UnclosedQuote.md", "BadToken.md", "PyTag.md",
          "templates/BadTemplate.md"}


def run_warning_checks(root: Path, listing, search, templates) -> None:
    v = str(root)

    for label, payload in [("list_notes", listing), ("search_notes", search)]:
        warned = {w["path"] for w in payload.get("warnings", [])}
        check(warned == BROKEN, f"{label} warns about exactly the broken notes")

    writable_payload = vault.list_writable_notes(v)
    check({w["path"] for w in writable_payload.get("warnings", [])} == BROKEN,
          "list_writable_notes warns about exactly the broken notes")
    check([w["path"] for w in templates.get("warnings", [])] == ["templates/BadTemplate.md"],
          "list_templates warns about the broken template")

    every_warning = [
        w
        for payload in (listing, search, writable_payload, templates)
        for w in payload.get("warnings", [])
    ]
    check(all(set(w) == {"path", "error"} for w in every_warning),
          "warning entries carry only 'path' and 'error' — no title, content, or access level")
    check(all(w["error"].startswith("YAML parse error:") for w in every_warning),
          "warning errors are the YAML parse detail")
    check(any("(line 2)" in w["error"] for w in every_warning),
          "warning errors include the offending line number")

    # Empty files are valid notes, not skipped files.
    warned_paths = {w["path"] for w in every_warning}
    check(not ({"Empty.md", "Whitespace.md", "OnlyDashes.md"} & warned_paths),
          "empty and whitespace-only notes never appear in warnings")
    check("Hidden.md" not in warned_paths and "BomHidden.md" not in warned_paths,
          "hidden notes are excluded silently, never named in warnings")

    # A clean vault must not gain a 'warnings' key at all.
    clean = root / "clean"
    (clean / "templates").mkdir(parents=True)
    (clean / "Note.md").write_text("---\nagent_access: edit\n---\nalpha\n", encoding="utf-8")
    (clean / "Blank.md").write_text("", encoding="utf-8")
    (clean / "templates" / "T.md").write_text("# T\n", encoding="utf-8")
    c = str(clean)
    for label, payload in [
        ("list_notes", vault.list_notes(c)),
        ("search_notes", vault.search_notes(c, "alpha")),
        ("list_writable_notes", vault.list_writable_notes(c)),
        ("list_templates", vault.list_templates(c)),
    ]:
        check("warnings" not in payload,
              f"{label} omits 'warnings' entirely on a clean vault")


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="vault-robustness-"))
    try:
        build_vault(root)
        run(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    print()
    if _failures:
        print(f"{RED}{len(_failures)} check(s) failed:{RESET}")
        for f in _failures:
            print(f"  - {f}")
        sys.exit(1)
    print(f"{GREEN}All checks passed.{RESET}")


if __name__ == "__main__":
    main()
