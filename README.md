# HA Dashboard CleanUp

A Home Assistant add-on that audits every Lovelace dashboard for entity
references that no longer exist (deleted or orphaned entities), shows you
exactly what it found, and only removes them once you confirm. Built-in
one-click backup before making changes, and a restore panel if you ever
need to roll back.

![audit-then-confirm](https://img.shields.io/badge/workflow-audit%20%E2%86%92%20review%20%E2%86%92%20apply-blue)

## Why

Delete or rename an entity in Home Assistant and its old dashboard cards
often stick around silently — showing "entity not available" or just
quietly cluttering your dashboards. This add-on finds and (optionally)
removes all of that, across every dashboard, in one pass.

## Features

- **Audit first** — read-only scan of every dashboard, listing each orphaned
  entity and exactly where it lives (dashboard → view → card).
- **Confirm before delete** — nothing is changed until you review the audit
  and click Apply.
- **Automatic backup** — takes a Home Assistant backup before applying
  changes (can be turned off per-run).
- **One-click restore** — browse and restore any backup taken by the add-on
  (or any other HA backup) directly from the add-on UI.
- **Safe removal logic** — only strips the offending entity/card; nested
  stacks and grids are handled recursively.
- Skips YAML-mode dashboards (flagged as read-only, since HA's API can't
  write to those).

## Requirements

- Home Assistant **OS** or **Supervised** (needs the Supervisor — this will
  not work on Container or Core-only installs).

## Installation

This is a native Home Assistant add-on repository — **not** a HACS
integration. Install it via the Add-on Store:

1. In Home Assistant, go to **Settings → Add-ons → Add-on Store**.
2. Click the **⋮** menu (top right) → **Repositories**.
3. Add this URL:
   ```
   https://github.com/jstockley/HA-Dashboard-CleanUp
   ```
4. Close the dialog and refresh — **"HA Dashboard CleanUp"** will appear
   under a new section on the Add-on Store page.
5. Click it → **Install**. The first build takes a few minutes.
6. **Start** the add-on, and optionally turn on "Show in sidebar".

## Usage

1. Open the add-on (sidebar, or Settings → Add-ons → HA Dashboard CleanUp →
   Open Web UI).
2. Click **Run Audit**. This only reads your dashboards — nothing is
   changed. You'll get a per-dashboard list of orphaned entity references.
3. Review the results.
4. Click **Apply Cleanup**. With "Auto-backup before Apply" ticked (default),
   a backup is taken automatically first. The tool then removes only the
   entities/cards flagged in the audit, and shows you a log of exactly what
   was removed from each dashboard.
5. If anything looks wrong afterwards, go to the **Backups & Restore**
   section, find the backup it made, and click **Restore**.

## How "orphaned" is defined

An entity is considered valid if it currently has a live state, **or** it's
in the entity registry and was deliberately disabled (has `disabled_by` set —
e.g. you disabled it yourself, or an integration disabled it). This means
intentionally disabled entities are **not** flagged.

Registry entries with no current state and no disable reason — typically
leftovers from an integration that was removed without a clean uninstall —
**are** flagged, since these are exactly what Home Assistant's frontend
shows as "Entity not found."

## Removal behaviour

- A card with a single `entity:` key that's orphaned → the whole card is
  removed.
- A card with an `entities:` list → only the orphaned rows are stripped;
  if that empties the list, the card is removed.
- Nested cards (`vertical-stack`, `grid`, etc.) are recursed into.
- View-level `badges` are cleaned the same way as `entities` lists.

## Contributing

Issues and PRs welcome. This is a small, single-purpose tool — the entire
add-on lives in [`dashboard_cleanup/`](./dashboard_cleanup).

## Changelog

See [CHANGELOG.md](./CHANGELOG.md) for release history.

## License

MIT — see [LICENSE](./LICENSE).
