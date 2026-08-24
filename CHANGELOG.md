# v4.0.0 — Robustness & Fail-Closed Error Handling

## ⚠️ Breaking Change

**`search_notes` and `list_notes` no longer return a bare array.**

```diff
- search_notes("query") → [ {...}, {...} ]
+ search_notes("query") → { "results": [ {...}, {...} ] }

- list_notes() → [ {...}, {...} ]
+ list_notes() → { "notes": [ {...}, {...} ] }
```

**Why:** these tools can now report skipped files (see below) via a
`warnings` array alongside the results. There's nowhere to attach that to a
bare array, so both tools now return an object.

**Migration:** anywhere you're reading the return value of `search_notes`
or `list_notes`, unwrap `.results` / `.notes` respectively. `list_writable_notes`
and `list_templates` were already objects and are unchanged in the common
case (no `warnings` key when nothing was skipped).

---

## Fixed: One bad note could take down your entire vault

Previously, a single problematic `.md` file — malformed YAML, or a valid-YAML
value that crashed downstream code — could cause `search_notes`,
`list_writable_notes`, and `list_notes` to fail **across the whole vault**,
not just for that file.

Confirmed triggers included:

- Broken YAML syntax in frontmatter (unclosed quotes, bad mapping syntax, etc.)
- A non-string `agent_access` value (e.g. `agent_access: [append]`) — this is
  _valid_ YAML and would pass any syntax check, but crashed the permission
  logic downstream
- A broken symlink, or a file removed mid-scan

All note reads now go through a single defensive path that:

- Treats empty (0-byte) files as valid, content-free notes — not an error
- Strips a leading BOM before parsing (see below)
- Catches parse and read failures per-file and **skips that file only** —
  every other note in your vault is unaffected

As a final backstop, no unexpected exception can escape a tool call any more —
the server catches it, logs the traceback to stderr, and returns an error for
that call instead of failing the whole request.

## Fixed: a UTF-8 BOM could silently expose a `hidden` note

A byte-order mark before the opening `---` hid the frontmatter block from the
parser. The note didn't fail — it parsed cleanly as a note with *no*
frontmatter, which meant `agent_access: hidden` was never seen and the note was
returned as a visible, default-access note.

Editors that write a BOM do so invisibly, so there was nothing to see in the
file. A leading BOM is now stripped before parsing, so `hidden` stays hidden.

If you have any notes you marked `hidden` and want to confirm they're actually
hidden, `list_notes` and `search_notes` are now the authoritative check —
neither will return them.

## Fixed: `get_note` failed on notes with dated frontmatter

A frontmatter value that YAML parses as a date or time — `date: 2024-01-01`,
which Obsidian templates and daily notes produce constantly — could not be
serialized to JSON, so `get_note` failed with
`TypeError: Object of type date is not JSON serializable`.

This affected completely healthy, correctly-formatted notes and was unrelated
to any of the malformed-file handling above. Dates and times are now serialized
as strings.

## Changed: `agent_access` edge cases now have defined behavior

Previously these either crashed or were undefined. Now:

- **Non-string values** (`agent_access: [append]`, `agent_access: true`) resolve
  to an unknown level: the note stays **visible but not writable**. Quote the
  value or use one of `hidden` / `read` / `append` / `edit`.
- **A blank value** (`agent_access:` with nothing after it) is treated the same
  as no key at all — the default `append`.

### Design: unparseable notes fail closed

If a note's frontmatter can't be parsed, it's **skipped, not defaulted to
`append`**. A YAML typo in a note you marked `hidden` or `read` will never
silently promote that note to visible or writable. Skipped notes are refused
for writes too, rather than risking a rewrite of a file the server didn't
fully understand.

## New: skipped files are now visible, not just logged

Every scanning tool (`list_notes`, `search_notes`, `list_writable_notes`,
`list_templates`) now includes a `warnings` array when files were skipped:

```json
{
	"notes": [
		/* your healthy notes */
	],
	"warnings": [
		{
			"path": "Reference - House.md",
			"error": "YAML parse error: mapping values are not allowed in this context (line 3)"
		}
	]
}
```

`warnings` is omitted entirely when nothing was skipped — no shape change
for the common case beyond the array→object change above.

Warning entries contain only a path and a short error string — never the
note's title, content, or attempted access level. A skipped note stays
exactly as protected as it was before it broke.

Direct calls to a broken file — `get_note`, and any write tool
(`append_to_note`, `update_note`, `replace_in_note`, `update_section`,
`create_note_from_template`) — now return a clear structured error instead
of a raw exception:

```json
{
	"error": "Could not parse this note's frontmatter",
	"detail": "YAML parse error: mapping values are not allowed in this context (line 3)",
	"path": "Reference - House.md"
}
```

`list_templates` also now skips (and warns on) templates with unparseable
frontmatter, rather than listing a template that `create_note_from_template`
would then refuse to use.

## Testing

`test_vault_robustness.py` (new) builds a vault covering every degenerate
case above — empty files, broken YAML, non-string `agent_access`, BOM-prefixed
hidden notes, dated frontmatter — and asserts healthy notes are unaffected.
41 checks, all passing, alongside the existing end-to-end suite.

If you run `test_server.py` yourself, note that it had two stale checks that
are now fixed: it crashed partway through on an outdated field name (silently
skipping every check after `list_writable_notes`), and it asserted that
appending to a nonexistent note errors, which contradicts the documented
create-on-append behavior. It now runs end to end and reports any `warnings`
the tools return.

## Upgrade Notes

1. Update any code calling `search_notes` or `list_notes` to unwrap
   `.results` / `.notes`
2. If you have automation watching server logs for parse errors, you can
   now get the same information from tool output directly via `warnings`
3. No vault changes are required — this is a server-side fix. Two cases are
   worth a look after upgrading, though, and both now tell you about
   themselves:
   - Run `list_notes` once and check for a `warnings` array. Anything listed
     there was previously either crashing your whole vault or silently
     missing; fix the `---` block and it comes back.
   - If a note stops being writable, check its `agent_access` for a
     non-string value like `[append]` — quote it or use a plain
     `hidden` / `read` / `append` / `edit`.
