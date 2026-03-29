#!/usr/bin/env python3
"""
Tom Wood Workshop — REST API Server
Python HTTP server + SQLite + Google staff authentication.
"""
import json
import hashlib
import os
import re
import secrets
import sqlite3
import sys
import traceback
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from http.cookies import SimpleCookie
from urllib.parse import urlparse, parse_qs
from datetime import datetime, date, timedelta, timezone

from env_config import load_env

load_env()

try:
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2 import id_token as google_id_token
except ModuleNotFoundError:
    GoogleRequest = None
    google_id_token = None

# Ensure local modules are importable
sys.path.insert(0, os.path.dirname(__file__))
try:
    from db.schema import DEFAULT_SETTINGS, DB_PATH, get_db, get_settings, init_db, save_settings
except ModuleNotFoundError:
    from db_schema import DEFAULT_SETTINGS, DB_PATH, get_db, get_settings, init_db, save_settings

PORT = int(os.getenv("PORT", "8484"))
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if not os.path.isdir(STATIC_DIR):
    STATIC_DIR = os.path.dirname(__file__)

ALLOWED_SORTS = {"due_date", "priority", "client", "value", "created"}
ALLOWED_PRIORITIES = {"high", "med", "low"}
ALLOWED_ACCESS_LEVELS = {"admin", "staff"}
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_HOSTED_DOMAIN = os.getenv("GOOGLE_HOSTED_DOMAIN", "").strip().lower()
SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "tw_session")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0").strip().lower() in {"1", "true", "yes", "on"}
try:
    SESSION_TTL_HOURS = int(os.getenv("SESSION_TTL_HOURS", "12"))
except ValueError:
    SESSION_TTL_HOURS = 12

# ── HELPERS ──

def json_response(handler, data, status=200, headers=None):
    body = json.dumps(data, default=str).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    if headers:
        for key, value in headers.items():
            handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body)

def error_response(handler, msg, status=400):
    json_response(handler, {"error": msg}, status)

def read_body(handler):
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode())
    except Exception:
        return {}

def row_to_dict(row):
    if row is None:
        return None
    return dict(row)

def rows_to_list(rows):
    return [dict(r) for r in rows]

def utc_now():
    return datetime.now(timezone.utc)

def utc_iso(dt):
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()

def parse_iso_dt(value):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

def next_order_number(conn):
    rows = conn.execute("SELECT order_number FROM orders").fetchall()
    if not rows:
        return "TW-1001"
    nums = []
    for r in rows:
        mm = re.search(r'(\d+)$', r["order_number"])
        if mm:
            nums.append(int(mm.group(1)))
    if not nums:
        return "TW-1001"
    return f"TW-{max(nums) + 1}"


def google_auth_ready():
    return bool(GOOGLE_CLIENT_ID and GoogleRequest and google_id_token)


def get_request_cookies(handler):
    cookies = SimpleCookie()
    cookies.load(handler.headers.get("Cookie", ""))
    return cookies


def build_session_cookie(value, max_age):
    cookie = SimpleCookie()
    cookie[SESSION_COOKIE_NAME] = value
    morsel = cookie[SESSION_COOKIE_NAME]
    morsel["path"] = "/"
    morsel["httponly"] = True
    morsel["samesite"] = "Lax"
    morsel["max-age"] = str(max_age)
    if COOKIE_SECURE:
        morsel["secure"] = True
    return cookie.output(header="").strip()


def clear_session_cookie():
    return build_session_cookie("", 0)


def hash_session_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def delete_session_by_hash(db, session_hash):
    db.execute("DELETE FROM staff_sessions WHERE session_token_hash = ?", (session_hash,))
    db.commit()


def session_payload(session):
    return {
        "goldsmith_id": session["goldsmith_id"],
        "email": session["email"],
        "name": session["goldsmith_name"],
        "role": session["goldsmith_role"],
    }


def get_session(handler, db):
    cookies = get_request_cookies(handler)
    morsel = cookies.get(SESSION_COOKIE_NAME)
    if not morsel or not morsel.value:
        return None

    session_hash = hash_session_token(morsel.value)
    row = db.execute("""
        SELECT
            s.id,
            s.goldsmith_id,
            s.email,
            s.google_sub,
            s.expires_at,
            g.name AS goldsmith_name,
            g.role AS goldsmith_role,
            g.active AS goldsmith_active
        FROM staff_sessions s
        JOIN goldsmiths g ON g.id = s.goldsmith_id
        WHERE s.session_token_hash = ?
    """, (session_hash,)).fetchone()

    if not row:
        return None

    expires_at = parse_iso_dt(row["expires_at"])
    if not row["goldsmith_active"] or not expires_at or expires_at <= utc_now():
        delete_session_by_hash(db, session_hash)
        return None

    db.execute(
        "UPDATE staff_sessions SET last_seen_at = ? WHERE id = ?",
        (utc_iso(utc_now()), row["id"])
    )
    db.commit()
    return row_to_dict(row)


def create_session(db, goldsmith_id, email, google_sub):
    token = secrets.token_urlsafe(32)
    expires_at = utc_now() + timedelta(hours=max(1, SESSION_TTL_HOURS))
    db.execute(
        """
        INSERT INTO staff_sessions (session_token_hash, goldsmith_id, email, google_sub, expires_at)
        VALUES (?,?,?,?,?)
        """,
        (hash_session_token(token), goldsmith_id, email, google_sub, utc_iso(expires_at))
    )
    db.commit()
    return token, int((expires_at - utc_now()).total_seconds())


def destroy_session(handler, db):
    cookies = get_request_cookies(handler)
    morsel = cookies.get(SESSION_COOKIE_NAME)
    if morsel and morsel.value:
        delete_session_by_hash(db, hash_session_token(morsel.value))


def verify_google_credential(credential):
    if not google_auth_ready():
        raise ValueError("Google SSO is not configured on the server")
    if not credential:
        raise ValueError("Google credential is required")

    token_info = google_id_token.verify_oauth2_token(
        credential,
        GoogleRequest(),
        GOOGLE_CLIENT_ID,
    )

    if not token_info.get("email_verified"):
        raise ValueError("Google account email must be verified")

    hosted_domain = (token_info.get("hd") or "").lower()
    if GOOGLE_HOSTED_DOMAIN and hosted_domain != GOOGLE_HOSTED_DOMAIN:
        raise ValueError("Google account is not in the allowed hosted domain")

    return token_info


def normalize_text_list(value, *, lower=False):
    if not isinstance(value, list):
        raise ValueError("Must be a list")

    cleaned = []
    seen = set()
    for item in value:
        text = str(item).strip()
        if lower:
            text = text.lower()
        if not text:
            continue
        marker = text.lower()
        if marker in seen:
            continue
        seen.add(marker)
        cleaned.append(text)

    if not cleaned:
        raise ValueError("At least one value is required")
    return cleaned


def coerce_int(value, field, minimum=None, maximum=None):
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be a whole number")

    if minimum is not None and value < minimum:
        raise ValueError(f"{field} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{field} must be at most {maximum}")
    return value


def validate_settings_payload(body, db):
    if not isinstance(body, dict) or not body:
        raise ValueError("Settings payload is required")

    allowed_keys = set(DEFAULT_SETTINGS.keys())
    unknown = sorted(set(body.keys()) - allowed_keys)
    if unknown:
        raise ValueError(f"Unknown settings: {', '.join(unknown)}")

    cleaned = {}

    for key in ("workshop_name", "workshop_subtitle", "currency_locale"):
        if key not in body:
            continue
        value = str(body.get(key, "")).strip()
        if not value:
            raise ValueError(f"{key.replace('_', ' ')} is required")
        cleaned[key] = value

    if "currency_code" in body:
        value = str(body.get("currency_code", "")).strip().upper()
        if not value:
            raise ValueError("currency code is required")
        cleaned["currency_code"] = value

    if "dashboard_refresh_seconds" in body:
        cleaned["dashboard_refresh_seconds"] = coerce_int(
            body.get("dashboard_refresh_seconds"),
            "dashboard refresh seconds",
            minimum=10,
            maximum=3600,
        )

    if "default_sort" in body:
        value = str(body.get("default_sort", "")).strip()
        if value not in ALLOWED_SORTS:
            raise ValueError(f"default sort must be one of: {', '.join(sorted(ALLOWED_SORTS))}")
        cleaned["default_sort"] = value

    if "default_priority" in body:
        value = str(body.get("default_priority", "")).strip()
        if value not in ALLOWED_PRIORITIES:
            raise ValueError("default priority must be one of: high, med, low")
        cleaned["default_priority"] = value

    if "current_goldsmith_id" in body:
        raw = body.get("current_goldsmith_id")
        if raw in (None, ""):
            cleaned["current_goldsmith_id"] = None
        else:
            goldsmith_id = coerce_int(raw, "current goldsmith id", minimum=1)
            row = db.execute("SELECT id FROM goldsmiths WHERE id = ?", (goldsmith_id,)).fetchone()
            if not row:
                raise ValueError("current goldsmith id is invalid")
            cleaned["current_goldsmith_id"] = goldsmith_id

    if "default_due_days" in body:
        due_days = body.get("default_due_days")
        if not isinstance(due_days, dict):
            raise ValueError("default due days must be an object")
        cleaned["default_due_days"] = {
            priority: coerce_int(due_days.get(priority), f"default due days ({priority})", minimum=0, maximum=365)
            for priority in ("high", "med", "low")
        }

    list_fields = {
        "item_types": False,
        "item_materials": False,
        "service_types": False,
        "contact_methods": True,
    }
    for key, lower in list_fields.items():
        if key not in body:
            continue
        cleaned[key] = normalize_text_list(body.get(key), lower=lower)

    return cleaned


def normalize_goldsmith_payload(body, *, partial=False):
    if not isinstance(body, dict):
        raise ValueError("Goldsmith payload is required")

    cleaned = {}

    if not partial or "name" in body:
        name = str(body.get("name", "")).strip()
        if not name:
            raise ValueError("Goldsmith name is required")
        cleaned["name"] = name

    if not partial or "role" in body:
        role = str(body.get("role", "Goldsmith")).strip() or "Goldsmith"
        cleaned["role"] = role

    if not partial or "access_level" in body:
        access_level = str(body.get("access_level", "staff")).strip().lower() or "staff"
        if access_level not in ALLOWED_ACCESS_LEVELS:
            raise ValueError("User level must be either admin or staff")
        cleaned["access_level"] = access_level

    if not partial or "email" in body:
        email = str(body.get("email", "") or "").strip()
        cleaned["email"] = email or None

    if not partial or "active" in body:
        active = body.get("active", 1)
        if isinstance(active, str):
            active = active.strip().lower() in ("1", "true", "yes", "on")
        cleaned["active"] = 1 if active else 0

    return cleaned


def require_admin_actor(body, db):
    raw_actor_id = body.get("acting_goldsmith_id")
    try:
        actor_id = int(raw_actor_id)
    except (TypeError, ValueError):
        raise PermissionError("Only Admin users can manage team members")

    actor = db.execute(
        """
        SELECT id, access_level, active
        FROM goldsmiths
        WHERE id = ?
        """,
        (actor_id,),
    ).fetchone()

    if not actor or not actor["active"] or str(actor["access_level"] or "staff").lower() != "admin":
        raise PermissionError("Only Admin users can manage team members")

    return actor_id


def require_admin_settings_access(db):
    current_operator_id = get_settings(db).get("current_goldsmith_id")
    try:
        actor_id = int(current_operator_id)
    except (TypeError, ValueError):
        raise PermissionError("Only Admin users can access settings")

    actor = db.execute(
        """
        SELECT id, access_level, active
        FROM goldsmiths
        WHERE id = ?
        """,
        (actor_id,),
    ).fetchone()

    if not actor or not actor["active"] or str(actor["access_level"] or "staff").lower() != "admin":
        raise PermissionError("Only Admin users can access settings")

    return actor_id


def count_goldsmith_references(db, goldsmith_id):
    counts = {
        "orders": db.execute(
            "SELECT COUNT(*) FROM orders WHERE goldsmith_id = ?",
            (goldsmith_id,),
        ).fetchone()[0],
        "timeline": db.execute(
            "SELECT COUNT(*) FROM order_timeline WHERE goldsmith_id = ?",
            (goldsmith_id,),
        ).fetchone()[0],
        "status_log": db.execute(
            "SELECT COUNT(*) FROM order_status_log WHERE changed_by = ?",
            (goldsmith_id,),
        ).fetchone()[0],
        "sessions": db.execute(
            "SELECT COUNT(*) FROM staff_sessions WHERE goldsmith_id = ?",
            (goldsmith_id,),
        ).fetchone()[0],
    }
    return counts

# ── ROUTE TABLE ──
# (method, regex_pattern) -> handler_fn(handler, matches, qs, body, db)

ROUTES = []

def route(method, pattern):
    def decorator(fn):
        ROUTES.append((method, re.compile(pattern), fn))
        return fn
    return decorator


# ──────────────────────────────────────────────
# AUTH
# ──────────────────────────────────────────────

@route("GET", r"^/api/auth/me$")
def auth_me(h, m, qs, body, db):
    session = getattr(h, "current_session", None)
    json_response(h, {
        "configured": bool(GOOGLE_CLIENT_ID),
        "auth_ready": google_auth_ready(),
        "client_id": GOOGLE_CLIENT_ID or None,
        "hosted_domain": GOOGLE_HOSTED_DOMAIN or None,
        "authenticated": bool(session),
        "user": session_payload(session) if session else None,
        "message": None if google_auth_ready() else "Google SSO is not configured on the server",
    })


@route("POST", r"^/api/auth/google$")
def auth_google(h, m, qs, body, db):
    if not google_auth_ready():
        return error_response(h, "Google SSO is not configured on the server", 503)

    try:
        token_info = verify_google_credential(body.get("credential"))
    except Exception as e:
        return error_response(h, str(e), 401)

    email = (token_info.get("email") or "").strip().lower()
    google_sub = (token_info.get("sub") or "").strip()
    if not email or not google_sub:
        return error_response(h, "Google response did not include the required identity fields", 401)

    goldsmith = db.execute("""
        SELECT id, name, role, email
        FROM goldsmiths
        WHERE active = 1 AND LOWER(email) = ?
        LIMIT 1
    """, (email,)).fetchone()

    if not goldsmith:
        return error_response(h, "This Google account is not authorised for Tom Wood staff access", 403)

    destroy_session(h, db)
    token, max_age = create_session(db, goldsmith["id"], email, google_sub)
    json_response(
        h,
        {
            "authenticated": True,
            "user": {
                "goldsmith_id": goldsmith["id"],
                "email": goldsmith["email"],
                "name": goldsmith["name"],
                "role": goldsmith["role"],
            }
        },
        headers={"Set-Cookie": build_session_cookie(token, max_age)},
    )


@route("POST", r"^/api/auth/logout$")
def auth_logout(h, m, qs, body, db):
    destroy_session(h, db)
    json_response(h, {"success": True}, headers={"Set-Cookie": clear_session_cookie()})


# ──────────────────────────────────────────────
# ORDERS
# ──────────────────────────────────────────────

@route("GET", r"^/api/orders$")
def list_orders(h, m, qs, body, db):
    status_filter = qs.get("status", ["all"])[0]
    search = qs.get("q", [""])[0].strip().lower()
    sort = qs.get("sort", ["due_date"])[0]

    query = """
        SELECT
            o.*,
            c.name  AS client_name,
            c.email AS client_email,
            c.phone AS client_phone,
            g.name  AS goldsmith_name
        FROM orders o
        JOIN clients c ON c.id = o.client_id
        LEFT JOIN goldsmiths g ON g.id = o.goldsmith_id
    """
    conditions = []
    params = []

    if status_filter == "intake":
        conditions.append("o.status IN ('intake','assessment')")
    elif status_filter == "progress":
        conditions.append("o.status IN ('approved','progress','qc')")
    elif status_filter == "ready":
        conditions.append("o.status = 'ready'")
    elif status_filter == "complete":
        conditions.append("o.status IN ('complete','cancelled')")
    elif status_filter == "hold":
        conditions.append("o.status = 'hold'")
    elif status_filter != "all":
        conditions.append("o.status = ?")
        params.append(status_filter)

    if search:
        conditions.append(
            "(LOWER(c.name) LIKE ? OR LOWER(o.order_number) LIKE ? "
            "OR LOWER(o.item_name) LIKE ? OR LOWER(o.service_type) LIKE ?)"
        )
        like = f"%{search}%"
        params += [like, like, like, like]

    if conditions:
        query += " WHERE " + " AND ".join(conditions)

    sort_map = {
        "due_date": "o.due_date ASC NULLS LAST",
        "priority": "CASE o.priority WHEN 'high' THEN 0 WHEN 'med' THEN 1 ELSE 2 END",
        "client": "c.name ASC",
        "value": "o.price_estimate DESC",
        "created": "o.created_at DESC",
    }
    query += f" ORDER BY {sort_map.get(sort, 'o.due_date ASC NULLS LAST')}"

    rows = db.execute(query, params).fetchall()

    # Attach timeline summary (last event)
    result = []
    for r in rows:
        d = row_to_dict(r)
        last = db.execute(
            "SELECT event, created_at FROM order_timeline "
            "WHERE order_id = ? ORDER BY id DESC LIMIT 1", (d["id"],)
        ).fetchone()
        d["last_event"] = row_to_dict(last)
        result.append(d)

    # Stats
    stats = db.execute("""
        SELECT
            COUNT(*) FILTER (WHERE status NOT IN ('complete','cancelled')) AS active,
            COUNT(*) FILTER (WHERE status = 'ready') AS ready,
            COUNT(*) FILTER (WHERE status NOT IN ('complete','cancelled','hold')
                             AND due_date < date('now')) AS overdue,
            COUNT(*) FILTER (WHERE status = 'intake') AS intake,
            SUM(price_estimate) FILTER (WHERE strftime('%Y-%m', received_at) = strftime('%Y-%m', 'now'))
                AS revenue_mtd
        FROM orders
    """).fetchone()

    json_response(h, {
        "orders": result,
        "stats": row_to_dict(stats),
        "total": len(result)
    })


@route("GET", r"^/api/orders/(\d+)$")
def get_order(h, m, qs, body, db):
    oid = int(m.group(1))
    row = db.execute("""
        SELECT o.*, c.name AS client_name, c.email AS client_email,
               c.phone AS client_phone, c.preferred_contact,
               g.name AS goldsmith_name, g.role AS goldsmith_role
        FROM orders o
        JOIN clients c ON c.id = o.client_id
        LEFT JOIN goldsmiths g ON g.id = o.goldsmith_id
        WHERE o.id = ?
    """, (oid,)).fetchone()

    if not row:
        return error_response(h, "Order not found", 404)

    d = row_to_dict(row)

    d["timeline"] = rows_to_list(db.execute("""
        SELECT t.*, g.name AS goldsmith_name
        FROM order_timeline t
        LEFT JOIN goldsmiths g ON g.id = t.goldsmith_id
        WHERE t.order_id = ?
        ORDER BY t.id ASC
    """, (oid,)).fetchall())

    d["photos"] = rows_to_list(db.execute(
        "SELECT * FROM order_photos WHERE order_id = ? ORDER BY id", (oid,)
    ).fetchall())

    json_response(h, d)


@route("POST", r"^/api/orders$")
def create_order(h, m, qs, body, db):
    required = ["client_id", "item_name", "item_type", "service_type"]
    for f in required:
        if not body.get(f):
            return error_response(h, f"Missing required field: {f}", 422)

    session = getattr(h, "current_session", None)
    settings = get_settings(db)
    order_number = next_order_number(db)

    db.execute("""
        INSERT INTO orders
          (order_number, client_id, goldsmith_id, item_name, item_type, item_material,
           item_condition, item_description, service_type, service_notes, internal_notes,
           status, priority, price_estimate, currency, due_date, received_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        order_number,
        body["client_id"],
        body.get("goldsmith_id"),
        body["item_name"],
        body["item_type"],
        body.get("item_material"),
        body.get("item_condition"),
        body.get("item_description"),
        body["service_type"],
        body.get("service_notes"),
        body.get("internal_notes"),
        "intake",
        body.get("priority", "med"),
        body.get("price_estimate"),
        body.get("currency") or settings.get("currency_code", "NOK"),
        body.get("due_date"),
        datetime.now().isoformat(),
    ))

    new_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.execute(
        "INSERT INTO order_timeline (order_id, event, note, goldsmith_id) VALUES (?,?,?,?)",
        (new_id, "Order created", body.get("internal_notes"), session["goldsmith_id"] if session else None)
    )
    db.commit()

    row = db.execute("SELECT * FROM orders WHERE id = ?", (new_id,)).fetchone()
    json_response(h, row_to_dict(row), 201)


@route("PUT", r"^/api/orders/(\d+)$")
def update_order(h, m, qs, body, db):
    oid = int(m.group(1))
    row = db.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone()
    if not row:
        return error_response(h, "Order not found", 404)

    fields = [
        "goldsmith_id", "item_name", "item_type", "item_material", "item_condition",
        "item_description", "service_type", "service_notes", "internal_notes",
        "priority", "price_estimate", "price_final", "deposit_paid", "due_date"
    ]
    updates = {f: body[f] for f in fields if f in body}
    updates["updated_at"] = datetime.now().isoformat()

    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        db.execute(
            f"UPDATE orders SET {set_clause} WHERE id = ?",
            list(updates.values()) + [oid]
        )
        db.commit()

    row = db.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone()
    json_response(h, row_to_dict(row))


@route("PATCH", r"^/api/orders/(\d+)/status$")
def update_status(h, m, qs, body, db):
    oid = int(m.group(1))
    session = getattr(h, "current_session", None)
    acting_goldsmith_id = session["goldsmith_id"] if session else None
    new_status = body.get("status")
    valid = ["intake", "assessment", "approved", "progress", "qc", "ready", "complete", "hold", "cancelled"]
    if new_status not in valid:
        return error_response(h, f"Invalid status. Must be one of: {', '.join(valid)}", 422)

    row = db.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone()
    if not row:
        return error_response(h, "Order not found", 404)

    old_status = row["status"]
    now = datetime.now().isoformat()

    db.execute(
        "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
        (new_status, now, oid)
    )

    if new_status == "complete":
        db.execute("UPDATE orders SET completed_at = ? WHERE id = ?", (now, oid))
    if new_status == "ready":
        db.execute(
            "INSERT INTO order_timeline (order_id, event, note, goldsmith_id) VALUES (?,?,?,?)",
            (oid, "Ready for collection", body.get("note", "Client notification sent."), acting_goldsmith_id)
        )
    elif new_status == "complete":
        db.execute(
            "INSERT INTO order_timeline (order_id, event, note, goldsmith_id) VALUES (?,?,?,?)",
            (oid, "Order completed & collected", body.get("note"), acting_goldsmith_id)
        )
    else:
        label_map = {
            "assessment": "Sent for assessment",
            "approved": "Quote approved — work authorised",
            "progress": "Work in progress",
            "qc": "QC inspection started",
            "hold": "Order placed on hold",
            "cancelled": "Order cancelled",
        }
        event = label_map.get(new_status, f"Status updated to {new_status}")
        db.execute(
            "INSERT INTO order_timeline (order_id, event, note, goldsmith_id) VALUES (?,?,?,?)",
            (oid, event, body.get("note"), acting_goldsmith_id)
        )

    db.execute(
        "INSERT INTO order_status_log (order_id, from_status, to_status, changed_by) VALUES (?,?,?,?)",
        (oid, old_status, new_status, acting_goldsmith_id)
    )
    db.commit()

    json_response(h, {"success": True, "order_id": oid, "status": new_status})


@route("POST", r"^/api/orders/(\d+)/timeline$")
def add_timeline_event(h, m, qs, body, db):
    oid = int(m.group(1))
    session = getattr(h, "current_session", None)
    event = body.get("event", "").strip()
    if not event:
        return error_response(h, "Event text required", 422)

    db.execute(
        "INSERT INTO order_timeline (order_id, event, note, goldsmith_id) VALUES (?,?,?,?)",
        (oid, event, body.get("note"), body.get("goldsmith_id") or (session["goldsmith_id"] if session else None))
    )
    db.commit()
    json_response(h, {"success": True})


@route("DELETE", r"^/api/orders/(\d+)$")
def delete_order(h, m, qs, body, db):
    oid = int(m.group(1))
    row = db.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone()
    if not row:
        return error_response(h, "Order not found", 404)
    db.execute("DELETE FROM orders WHERE id = ?", (oid,))
    db.commit()
    json_response(h, {"success": True})


# ──────────────────────────────────────────────
# CLIENTS
# ──────────────────────────────────────────────

@route("GET", r"^/api/clients$")
def list_clients(h, m, qs, body, db):
    search = qs.get("q", [""])[0].lower()
    query = "SELECT * FROM clients"
    params = []
    if search:
        query += " WHERE LOWER(name) LIKE ? OR LOWER(email) LIKE ?"
        params = [f"%{search}%", f"%{search}%"]
    query += " ORDER BY name"
    rows = db.execute(query, params).fetchall()
    json_response(h, rows_to_list(rows))


@route("POST", r"^/api/clients$")
def create_client(h, m, qs, body, db):
    name = body.get("name", "").strip()
    email = body.get("email", "").strip()
    if not name or not email:
        return error_response(h, "Name and email required", 422)
    contact_methods = get_settings(db).get("contact_methods", DEFAULT_SETTINGS["contact_methods"])
    preferred_contact = (body.get("preferred_contact") or contact_methods[0]).strip().lower()
    if preferred_contact not in contact_methods:
        return error_response(h, "Invalid preferred contact method", 422)
    try:
        db.execute(
            "INSERT INTO clients (name, email, phone, preferred_contact, notes) VALUES (?,?,?,?,?)",
            (name, email, body.get("phone"), preferred_contact, body.get("notes"))
        )
        db.commit()
        row = db.execute("SELECT * FROM clients WHERE email = ?", (email,)).fetchone()
        json_response(h, row_to_dict(row), 201)
    except sqlite3.IntegrityError:
        error_response(h, "Email already exists", 409)


@route("GET", r"^/api/clients/(\d+)/orders$")
def client_orders(h, m, qs, body, db):
    cid = int(m.group(1))
    rows = db.execute(
        "SELECT * FROM orders WHERE client_id = ? ORDER BY created_at DESC", (cid,)
    ).fetchall()
    json_response(h, rows_to_list(rows))


# ──────────────────────────────────────────────
# SETTINGS
# ──────────────────────────────────────────────

@route("GET", r"^/api/settings$")
def list_settings(h, m, qs, body, db):
    json_response(h, get_settings(db))


@route("PUT", r"^/api/settings$")
def update_settings(h, m, qs, body, db):
    try:
        require_admin_settings_access(db)
    except PermissionError as e:
        return error_response(h, str(e), 403)

    try:
        cleaned = validate_settings_payload(body, db)
    except ValueError as e:
        return error_response(h, str(e), 422)

    settings = save_settings(db, cleaned)
    json_response(h, settings)


# ──────────────────────────────────────────────
# GOLDSMITHS
# ──────────────────────────────────────────────

@route("GET", r"^/api/goldsmiths$")
def list_goldsmiths(h, m, qs, body, db):
    include_all = qs.get("all", ["0"])[0] == "1"
    query = "SELECT * FROM goldsmiths"
    if not include_all:
        query += " WHERE active = 1"
    query += " ORDER BY name"
    rows = db.execute(query).fetchall()
    json_response(h, rows_to_list(rows))


@route("POST", r"^/api/goldsmiths$")
def create_goldsmith(h, m, qs, body, db):
    try:
        require_admin_actor(body, db)
    except PermissionError as e:
        return error_response(h, str(e), 403)

    try:
        cleaned = normalize_goldsmith_payload(body, partial=False)
    except ValueError as e:
        return error_response(h, str(e), 422)

    db.execute(
        "INSERT INTO goldsmiths (name, role, access_level, email, active) VALUES (?,?,?,?,?)",
        (cleaned["name"], cleaned["role"], cleaned["access_level"], cleaned["email"], cleaned["active"])
    )
    new_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    db.commit()
    row = db.execute("SELECT * FROM goldsmiths WHERE id = ?", (new_id,)).fetchone()
    json_response(h, row_to_dict(row), 201)


@route("PUT", r"^/api/goldsmiths/(\d+)$")
def update_goldsmith(h, m, qs, body, db):
    gid = int(m.group(1))

    try:
        require_admin_actor(body, db)
    except PermissionError as e:
        return error_response(h, str(e), 403)

    row = db.execute("SELECT * FROM goldsmiths WHERE id = ?", (gid,)).fetchone()
    if not row:
        return error_response(h, "Goldsmith not found", 404)

    try:
        cleaned = normalize_goldsmith_payload(body, partial=True)
    except ValueError as e:
        return error_response(h, str(e), 422)

    if not cleaned:
        return error_response(h, "No fields to update", 422)

    set_clause = ", ".join(f"{key} = ?" for key in cleaned)
    db.execute(
        f"UPDATE goldsmiths SET {set_clause} WHERE id = ?",
        list(cleaned.values()) + [gid]
    )
    db.commit()
    row = db.execute("SELECT * FROM goldsmiths WHERE id = ?", (gid,)).fetchone()
    json_response(h, row_to_dict(row))


@route("DELETE", r"^/api/goldsmiths/(\d+)$")
def delete_goldsmith(h, m, qs, body, db):
    gid = int(m.group(1))

    try:
        actor_id = require_admin_actor(body, db)
    except PermissionError as e:
        return error_response(h, str(e), 403)

    row = db.execute("SELECT * FROM goldsmiths WHERE id = ?", (gid,)).fetchone()
    if not row:
        return error_response(h, "Goldsmith not found", 404)

    if gid == actor_id:
        return error_response(h, "You cannot delete the current Admin user", 422)

    references = count_goldsmith_references(db, gid)
    if references["orders"] or references["timeline"] or references["status_log"] or references["sessions"]:
        return error_response(
            h,
            "This user has order history or active sessions. Set them inactive instead of deleting.",
            409,
        )

    current_operator_id = get_settings(db).get("current_goldsmith_id")
    if str(current_operator_id or "") == str(gid):
        return error_response(h, "Change the current operator before deleting this user", 422)

    db.execute("DELETE FROM goldsmiths WHERE id = ?", (gid,))
    db.commit()
    json_response(h, {"success": True, "deleted_id": gid})


# ──────────────────────────────────────────────
# STATS / DASHBOARD
# ──────────────────────────────────────────────

@route("GET", r"^/api/stats$")
def get_stats(h, m, qs, body, db):
    stats = row_to_dict(db.execute("""
        SELECT
            COUNT(*) FILTER (WHERE status NOT IN ('complete','cancelled')) AS active,
            COUNT(*) FILTER (WHERE status = 'ready') AS ready_for_collection,
            COUNT(*) FILTER (WHERE status IN ('intake','assessment')) AS intake,
            COUNT(*) FILTER (WHERE status NOT IN ('complete','cancelled','hold')
                             AND due_date < date('now')) AS overdue,
            COUNT(*) FILTER (WHERE status = 'hold') AS on_hold,
            COUNT(*) FILTER (WHERE status IN ('complete','cancelled')
                             AND strftime('%Y-%m', completed_at) = strftime('%Y-%m', 'now')) AS completed_mtd,
            COALESCE(SUM(price_estimate) FILTER (
                WHERE strftime('%Y-%m', received_at) = strftime('%Y-%m', 'now')
            ), 0) AS revenue_mtd,
            COALESCE(SUM(price_estimate) FILTER (
                WHERE strftime('%Y-%m', received_at) = strftime('%Y-%m', date('now', '-1 month'))
            ), 0) AS revenue_last_month
        FROM orders
    """).fetchone())

    # Revenue by goldsmith
    by_gs = db.execute("""
        SELECT g.name, COUNT(*) AS order_count,
               COALESCE(SUM(o.price_estimate), 0) AS total_value
        FROM orders o
        JOIN goldsmiths g ON g.id = o.goldsmith_id
        WHERE o.status NOT IN ('cancelled')
        GROUP BY g.id
        ORDER BY total_value DESC
    """).fetchall()

    # Orders by status
    by_status = db.execute("""
        SELECT status, COUNT(*) AS count FROM orders GROUP BY status ORDER BY status
    """).fetchall()

    json_response(h, {
        "overview": stats,
        "by_goldsmith": rows_to_list(by_gs),
        "by_status": rows_to_list(by_status),
    })


@route("GET", r"^/api/health$")
def health(h, m, qs, body, db):
    json_response(h, {
        "ok": True,
        "port": PORT,
        "db_path": DB_PATH,
        "auth_configured": google_auth_ready(),
    })


# ──────────────────────────────────────────────
# HTTP HANDLER
# ──────────────────────────────────────────────

class TomWoodHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Custom compact logging
        print(f"  {self.command:6} {self.path.split('?')[0][:50]}  →  {args[1]}")

    def _dispatch(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)
        body = {}

        if self.command in ("POST", "PUT", "PATCH", "DELETE"):
            body = read_body(self)

        # Serve static files
        if self.command == "GET" and not path.startswith("/api"):
            self._serve_static(path)
            return

        # API routing
        for (method, pattern, fn) in ROUTES:
            if method == self.command:
                mm = pattern.match(path)
                if mm:
                    db = get_db()
                    try:
                        session = get_session(self, db)
                        self.current_session = session
                        fn(self, mm, qs, body, db)
                    except Exception as e:
                        traceback.print_exc()
                        error_response(self, str(e), 500)
                    finally:
                        self.current_session = None
                        db.close()
                    return

        error_response(self, "Not found", 404)

    def _serve_static(self, path, head_only=False):
        if path == "/" or path == "":
            path = "/index.html"

        file_path = os.path.join(STATIC_DIR, path.lstrip("/"))
        if not os.path.exists(file_path) or not os.path.isfile(file_path):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        ext = os.path.splitext(file_path)[1]
        mime = {
            ".html": "text/html",
            ".css":  "text/css",
            ".js":   "application/javascript",
            ".json": "application/json",
            ".png":  "image/png",
            ".jpg":  "image/jpeg",
        }.get(ext, "application/octet-stream")

        with open(file_path, "rb") as f:
            data = f.read()

        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if not head_only:
            self.wfile.write(data)

    def do_GET(self):    self._dispatch()
    def do_POST(self):   self._dispatch()
    def do_PUT(self):    self._dispatch()
    def do_PATCH(self):  self._dispatch()
    def do_DELETE(self): self._dispatch()
    def do_HEAD(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path.startswith("/api"):
            self.send_response(405)
            self.send_header("Allow", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
            self.end_headers()
            return
        self._serve_static(path, head_only=True)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()


# ──────────────────────────────────────────────
# ENTRYPOINT
# ──────────────────────────────────────────────

if __name__ == "__main__":
    print("━" * 50)
    print("  Tom Wood Workshop — API Server")
    print("━" * 50)
    print(f"  Initialising database…")
    init_db()
    print(f"  Database: {DB_PATH}")
    print(f"  Starting server on http://localhost:{PORT}")
    print(f"  API:      http://localhost:{PORT}/api/orders")
    print(f"  Frontend: http://localhost:{PORT}/")
    print("━" * 50)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), TomWoodHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")
