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
    return {"Authorization": f"Bearer {SUPERVISOR_TOKEN}", "Content-Type": "application/json"}


def create_backup(name):
    """Partial backup of the Home Assistant core config (.storage etc, where
    Lovelace dashboards live). Returns the backup slug."""
    r = requests.post(
        f"{SUPERVISOR_BASE}/backups/new/partial",
        headers=supervisor_headers(),
        json={"name": name, "homeassistant": True},
        timeout=180,
    )
    r.raise_for_status()
    data = r.json()
    return data.get("data", {}).get("slug")


def list_backups():
    r = requests.get(f"{SUPERVISOR_BASE}/backups", headers=supervisor_headers(), timeout=30)
    r.raise_for_status()
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
    r.raise_for_status()
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
    """Union of every entity_id currently in the state machine + entity registry
    (covers disabled entities too), used as the 'this entity really exists' set."""
    headers = {"Authorization": f"Bearer {SUPERVISOR_TOKEN}"}
    r = requests.get(f"{REST_BASE}/states", headers=headers, timeout=30)
    r.raise_for_status()
    state_ids = {s["entity_id"] for s in r.json()}

    reg_result = ws_call([{"type": "config/entity_registry/list"}])[0]
    reg_ids = set()
    if reg_result.get("success"):
        reg_ids = {e["entity_id"] for e in reg_result["result"]}

    return state_ids | reg_ids


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
        cards = view.get("cards", [])
        cleaned_cards = []
        for ci, card in enumerate(cards):
            c = clean_card(card, valid_ids, removed, f"views[{vi}].cards[{ci}]")
            if c is not None:
                cleaned_cards.append(c)
        view["cards"] = cleaned_cards
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
