# Changelog

All notable changes to this project are documented here.

## [1.6.0] - 2026-07-15

### Added
- `/api/inspect` diagnostic endpoint + "Inspect Entity" UI — enter any
  entity_id and see its raw current state, its raw registry entry (if
  any), and whether the tool currently considers it orphaned and why.
  Added to pin down cases where an entity shows "Entity not found" on a
  dashboard but the audit doesn't flag it.

## [1.5.0] - 2026-07-15

### Fixed
- Orphan detection was missing entities with a leftover entity registry
  entry but no live state and no `disabled_by` reason (dead stubs, typically
  left behind when an integration is removed without a clean uninstall).
  These are exactly what Home Assistant's frontend shows as "Entity not
  found," but were previously treated as valid just because a registry
  entry existed. Deliberately disabled entities (`disabled_by` set) are
  still correctly left alone.

## [1.4.0] - 2026-07-15

### Fixed
- Apply Cleanup wasn't touching modern "sections" view type dashboards
  (the current default grid-based dashboard editor) — only the legacy
  `view.cards` structure was handled. Audit correctly found orphans in
  these dashboards, but Apply silently skipped them. Both `view.cards`
  and `view.sections[].cards` are now cleaned.

## [1.3.0] - 2026-07-15

### Fixed
- `SUPERVISOR_TOKEN` wasn't reaching the app process, causing every
  Supervisor/Core API call to fail with 401/403 regardless of correct
  `config.yaml` permissions. Root cause: s6-overlay v3 starts services
  with a stripped-down environment by default and only re-imports the
  full container environment when a script explicitly requests it via
  `with-contenv`. Added a `run.sh` entrypoint using
  `#!/usr/bin/with-contenv bashio` instead of calling the app directly
  from `CMD`.

## [1.2.1] - 2026-07-14

### Added
- Debug endpoint now also dumps environment variable *names* (not values)
  present in the container, to help diagnose the missing-token issue
  above.

## [1.2.0] - 2026-07-14

### Added
- `/api/debug` diagnostics endpoint and a "Check Permissions / Token"
  button in the UI, hitting Core API, Supervisor API, and Backups API
  directly and reporting raw status/response bodies.
- Error messages now include the response body (`requests`' default
  `raise_for_status()` discards it, hiding the actual reason for a
  failure).

### Fixed
- Dockerfile updated for Supervisor 2026.04.0's new app builder: explicit
  `FROM ghcr.io/home-assistant/base:latest` and required `io.hass.*`
  labels, since `BUILD_FROM` is no longer auto-provided and `build.yaml`
  is no longer read.
- Trimmed `arch:` list to `aarch64`/`amd64` per current documentation
  (older `armhf`/`armv7`/`i386` entries are no longer listed as
  supported).

## [1.1.1] - 2026-07-14

### Fixed
- Docker build failing with `base name ($BUILD_FROM) should not be
  blank` — added `build.yaml` specifying a base image per architecture.
  (Superseded by the 1.2.0 Dockerfile rewrite once `build.yaml` itself
  was found to be deprecated.)

## [1.1.0] - 2026-07-14

### Added
- Automatic backup before Apply Cleanup (toggle, on by default).
- "Create Backup Now" manual backup button.
- Backups & Restore panel — list and one-click restore of any backup.

## [1.0.0] - 2026-07-14

### Added
- Initial release: audit every Lovelace dashboard for orphaned/deleted
  entity references, review the list, then apply cleanup only on
  confirmation. Handles single-entity cards, `entities:` lists, and
  nested stack/grid cards.
