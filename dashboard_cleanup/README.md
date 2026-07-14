# Dashboard Cleanup (add-on)

See the [repository README](../README.md) for install instructions and full
documentation. This folder contains the add-on itself — you shouldn't need
to touch these files directly unless you're developing/forking it.

## Local structure

- `config.yaml` — add-on manifest (Supervisor reads this)
- `Dockerfile` — builds the add-on's Python/Flask environment
- `app/app.py` — audit, cleanup, backup and restore logic
- `app/templates/index.html` — the add-on's web UI
