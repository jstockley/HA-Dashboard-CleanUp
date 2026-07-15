import os
import json
from datetime import datetime, timezone
from flask import Flask, request, jsonify, render_template
import requests
import websocket

app = Flask(__name__)

SUPERVISOR_TOKEN = os.environ.get("SUPERVISOR_TOKEN")
REST_BASE = "http://supervisor/core/api"
SUPERVISOR_BASE = "http://supervisor"
WS_URL = "ws://supervisor/core/websocket"
DATA_DIR = "/data"
AUDIT_FILE = os.path.join(DATA_DIR, "last_audit.json")
BACKUP_PREFIX = "dashboard_cleanup_"


def supervisor_headers():
    if not SUPERVISOR_TOKEN:
        raise RuntimeError(
            "SUPERVISOR_TOKEN environment variable is not set inside the app "
            "container. This usually means the app needs a full uninstall + "
            "reinstall for permission grants to take effect."
        )
    return {"Authorization": f"Bearer {SUPERVISOR_TOKEN}", "Content-Type": "application/json"}


def _raise_with_body(r):
    """Like raise_for_status(), but includes the response body, which is where
    Supervisor puts the actual reason (raise_for_status() alone discards it)."""
    if not r.ok:
        raise RuntimeError(f"{r.status_code} {r.reason} for {r.url} — body: {r.text[:500]}")


def create_backup(name):
    """Partial backup of the Home Assistant core config (.storage etc, where
    Lovelace dashboards live). Returns the backup slug."""
    r = requests.post(
        f"{SUPERVISOR_BASE}/backups/new/partial",
        headers=supervisor_headers(),
        json={"name": name, "homeassistant": True},
        timeout=180,
    )
    _raise_with_body(r)
    data = r.json()
    return data.get("data", {}).get("slug")


def list_backups():
    r = requests.get(f"{SUPERVISOR_BASE}/backups", headers=supervisor_headers(), timeout=30)
    _raise_with_body(r)
    backups = r.json().get("data", {}).get("backups", [])
    backups.sort(key=lambda b: b.get("date", ""), reverse=True)
    return backups


def restore_backup(slug):
    """Restores just the Home Assistant core config from the given backup.
    This will briefly restart Home Assistant core."""
    r = requests.post(
        f"{SUPERVISOR_BASE}/backups/{slug}/restore/partial",
        headers=supervisor_headers(),
        json={"homeassistant": True},
        timeout=300,
    )
    _raise_with_body(r)
    return r.json()


# ---------------------------------------------------------------------------
# Home Assistant API helpers
# ---------------------------------------------------------------------------

def ws_call(messages):
    """Open a WS connection, authenticate, send each message in order, return results."""
    ws = websocket.create_connection(WS_URL, timeout=30)
    try:
        ws.recv()  # auth_required
        ws.send(json.dumps({"type": "auth", "access_token": SUPERVISOR_TOKEN}))
        auth_result = json.loads(ws.recv())
        if auth_result.get("type") != "auth_ok":
            raise RuntimeError(f"WebSocket auth failed: {auth_result}")

        results = []
        for i, msg in enumerate(messages, start=1):
            msg = dict(msg)
            msg["id"] = i
            ws.send(json.dumps(msg))
            while True:
                resp = json.loads(ws.recv())
                if resp.get("id") == i:
                    results.append(resp)
                    break
        return results
    finally:
        ws.close()


def get_valid_entity_ids():
    """An entity_id counts as valid if either:
    - it currently has a live state, or
    - it's in the entity registry AND was deliberately disabled (disabled_by set).

    A registry entry with no current state and disabled_by == None is a dead
    stub — usually left behind by an integration that was torn out without a
    clean removal — and is exactly what Home Assistant's frontend shows as
    "Entity not found". Those should NOT count as valid."""
    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}
    r = requests.get(f"{REST_BASE}/states", headers=headers, timeout=30)
    _raise_with_body(r)
    state_ids = {s["entity_id"] for s in r.json()}

    reg_result = ws_call([{"type": "config/entity_registry/list"}])[0]
    disabled_registered_ids = set()
    if reg_result.get("success"):
        disabled_registered_ids = {
            e["entity_id"] for e in reg_result["result"] if e.get("disabled_by")
        }

    return state_ids | disabled_registered_ids


def get_dashboards():
    result = ws_call([{"type": "lovelace/dashboards/list"}])[0]
    dashboards = result.get("result", []) if result.get("success") else []
    # Default dashboard isn't included in the list and has url_path None
    return [{"url_path": None, "title": "Default (Overview)", "mode": "storage"}] + dashboards


def get_dashboard_config(url_path):
    result = ws_call([{"type": "lovelace/config", "url_path": url_path}])[0]
    if not result.get("success"):
        return None, result.get("error", {}).get("message", "unknown error")
    return result.get("result", {}), None


def save_dashboard_config(url_path, config):
    result = ws_call([{"type": "lovelace/config/save", "url_path": url_path, "config": config}])[0]
    if not result.get("success"):
        return False, result.get("error", {}).get("message", "unknown error")
    return True, None


# ---------------------------------------------------------------------------
# Dashboard scanning (read-only audit)
# ---------------------------------------------------------------------------

def iter_entity_refs(node, path="root"):
    """Yield (entity_id, path) for every entity reference found in a card/view tree."""
    if isinstance(node, dict):
        if "entity" in node and isinstance(node["entity"], str):
            yield (node["entity"], f"{path}.entity")
        if "entities" in node and isinstance(node["entities"], list):
            for idx, item in enumerate(node["entities"]):
                if isinstance(item, str):
                    yield (item, f"{path}.entities[{idx}]")
                elif isinstance(item, dict) and isinstance(item.get("entity"), str):
                    yield (item["entity"], f"{path}.entities[{idx}].entity")
        for k, v in node.items():
            if isinstance(v, (dict, list)):
                yield from iter_entity_refs(v, f"{path}.{k}")
    elif isinstance(node, list):
        for idx, item in enumerate(node):
            if isinstance(item, (dict, list)):
                yield from iter_entity_refs(item, f"{path}[{idx}]")


def audit_dashboard_config(config, valid_ids):
    orphans = []
    seen = set()
    for entity_id, path in iter_entity_refs(config):
        if entity_id not in valid_ids:
            orphans.append({"entity_id": entity_id, "path": path})
            seen.add(entity_id)
    return orphans


# ---------------------------------------------------------------------------
# Dashboard cleaning (destructive, only run after confirmation)
# ---------------------------------------------------------------------------

def clean_entities_list(entities, valid_ids, removed, path):
    kept = []
    for idx, item in enumerate(entities):
        if isinstance(item, str):
            if item in valid_ids:
                kept.append(item)
            else:
                removed.append({"entity_id": item, "path": f"{path}.entities[{idx}]"})
        elif isinstance(item, dict) and isinstance(item.get("entity"), str):
            if item["entity"] in valid_ids:
                kept.append(item)
            else:
                removed.append({"entity_id": item["entity"], "path": f"{path}.entities[{idx}].entity"})
        else:
            kept.append(item)
    return kept


def clean_card(card, valid_ids, removed, path):
    """Returns the cleaned card, or None if the whole card should be dropped."""
    if not isinstance(card, dict):
        return card

    if "entity" in card and isinstance(card["entity"], str):
        if card["entity"] not in valid_ids:
            removed.append({"entity_id": card["entity"], "path": f"{path}.entity",
                             "card_type": card.get("type"), "note": "card removed"})
            return None

    if "entities" in card and isinstance(card["entities"], list):
        before = len(card["entities"])
        cleaned = clean_entities_list(card["entities"], valid_ids, removed, path)
        if not cleaned and before > 0:
            removed.append({"entity_id": None, "path": path, "card_type": card.get("type"),
                             "note": "card removed: all entities were orphaned"})
            return None
        card["entities"] = cleaned

    if "cards" in card and isinstance(card["cards"], list):
        cleaned_cards = []
        for i, sub in enumerate(card["cards"]):
            c = clean_card(sub, valid_ids, removed, f"{path}.cards[{i}]")
            if c is not None:
                cleaned_cards.append(c)
        if card["cards"] and not cleaned_cards:
            return None
        card["cards"] = cleaned_cards

    return card


def clean_config(config, valid_ids):
    removed = []
    views = config.get("views", [])
    for vi, view in enumerate(views):
        if "badges" in view and isinstance(view["badges"], list):
            view["badges"] = clean_entities_list(view["badges"], valid_ids, removed, f"views[{vi}]")

        # Legacy / masonry views: cards live directly under view.cards
        if "cards" in view and isinstance(view["cards"], list):
            cleaned_cards = []
            for ci, card in enumerate(view["cards"]):
                c = clean_card(card, valid_ids, removed, f"views[{vi}].cards[{ci}]")
                if c is not None:
                    cleaned_cards.append(c)
            view["cards"] = cleaned_cards

        # Modern "sections" views: cards live under view.sections[].cards.
        # Each section is cleaned like a card container — clean_card already
        # knows how to recurse into a "cards" list, so reuse it directly.
        if "sections" in view and isinstance(view["sections"], list):
            cleaned_sections = []
            for si, section in enumerate(view["sections"]):
                s = clean_card(section, valid_ids, removed, f"views[{vi}].sections[{si}]")
                if s is not None:
                    cleaned_sections.append(s)
            view["sections"] = cleaned_sections

    config["views"] = views
    return config, removed


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/audit", methods=["POST"])
def api_audit():
    try:
        valid_ids = get_valid_entity_ids()
        dashboards = get_dashboards()
        report = []
        for d in dashboards:
            config, err = get_dashboard_config(d["url_path"])
            name = d.get("title") or d.get("url_path") or "Default"
            if err:
                report.append({"dashboard": name, "url_path": d["url_path"],
                                "error": err, "orphans": [], "orphan_count": 0})
                continue
            orphans = audit_dashboard_config(config, valid_ids)
            report.append({
                "dashboard": name,
                "url_path": d["url_path"],
                "orphans": orphans,
                "orphan_count": len(orphans),
            })

        os.makedirs(DATA_DIR, exist_ok=True)
        with open(AUDIT_FILE, "w") as f:
            json.dump({"dashboards": dashboards, "report": report}, f)

        return jsonify({"success": True, "report": report})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/debug", methods=["GET"])
def api_debug():
    info = {
        "supervisor_token_present": bool(SUPERVISOR_TOKEN),
        "supervisor_token_length": len(SUPERVISOR_TOKEN) if SUPERVISOR_TOKEN else 0,
        "env_var_names": sorted(os.environ.keys()),
    }
    # Try a minimal, harmless call against each API surface and report the raw result.
    try:
        r = requests.get(f"{REST_BASE}/config", headers=supervisor_headers(), timeout=15)
        info["core_api_status"] = r.status_code
        info["core_api_body"] = r.text[:300]
    except Exception as e:
        info["core_api_error"] = str(e)

    try:
        r = requests.get(f"{SUPERVISOR_BASE}/info", headers=supervisor_headers(), timeout=15)
        info["supervisor_api_status"] = r.status_code
        info["supervisor_api_body"] = r.text[:300]
    except Exception as e:
        info["supervisor_api_error"] = str(e)

    try:
        r = requests.get(f"{SUPERVISOR_BASE}/backups", headers=supervisor_headers(), timeout=15)
        info["backups_api_status"] = r.status_code
        info["backups_api_body"] = r.text[:300]
    except Exception as e:
        info["backups_api_error"] = str(e)

    return jsonify(info)


@app.route("/api/backup", methods=["POST"])
def api_backup():
    try:
        name = f"{BACKUP_PREFIX}{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        slug = create_backup(name)
        return jsonify({"success": True, "slug": slug, "name": name})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/backups", methods=["GET"])
def api_backups():
    try:
        backups = list_backups()
        return jsonify({"success": True, "backups": backups})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/restore", methods=["POST"])
def api_restore():
    try:
        slug = request.get_json(force=True).get("slug")
        if not slug:
            return jsonify({"success": False, "error": "No backup slug provided."}), 400
        result = restore_backup(slug)
        return jsonify({"success": True, "result": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/apply", methods=["POST"])
def api_apply():
    try:
        if not os.path.exists(AUDIT_FILE):
            return jsonify({"success": False, "error": "Run an audit first."}), 400

        body = request.get_json(silent=True) or {}
        auto_backup = body.get("auto_backup", True)
        backup_slug = None
        if auto_backup:
            backup_name = f"{BACKUP_PREFIX}pre_apply_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
            backup_slug = create_backup(backup_name)

        with open(AUDIT_FILE) as f:
            audit_data = json.load(f)

        valid_ids = get_valid_entity_ids()
        results = []
        for d in audit_data["dashboards"]:
            config, err = get_dashboard_config(d["url_path"])
            if err or config is None:
                continue
            cleaned, removed = clean_config(config, valid_ids)
            if removed:
                ok, save_err = save_dashboard_config(d["url_path"], cleaned)
                results.append({
                    "dashboard": d.get("title") or d.get("url_path") or "Default",
                    "url_path": d["url_path"],
                    "removed": removed,
                    "saved": ok,
                    "error": save_err,
                })

        return jsonify({"success": True, "results": results, "backup_slug": backup_slug})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8099)
