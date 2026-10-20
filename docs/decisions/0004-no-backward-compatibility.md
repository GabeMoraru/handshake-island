# 0004. No backward compatibility during development

- **Status:** Accepted
- **Date:** 2026-10-18

## Context

The earlier project learned that code kept to read old formats grows faster than the code that runs the current one. It has to be read, tested and kept working, and it serves no current game. Recordings in development have no outside users.

## Decision

1. **One version.** Only `CONFIG_VERSION` exists. Code that meets another version refuses it with one clear error. It does not read, list, report or replay it.
2. **No compatibility code.** There are no readers, converters, migrations, fallbacks for old shapes, aliases, version branches, or shims that keep old hashes stable. A format change bumps the version, changes the code to the new shape only and deletes the old path in the same change.
3. **Games are disposable.** Recordings under `data/games/` are deleted when the format changes.
4. **Samples stay current.** `samples/` holds the only recordings the code must read. A format change regenerates them, or updates the tests that use them, in the same change.

## Consequences

- A format change costs the saved games. Results worth keeping go into committed result tables or `samples/`.
- The budget keeps one line (R) to regenerate `samples/` after a format change.
- Revisit this decision once other people hold recordings they want to keep.
