"""
Tom Wood Workshop — Database Schema & Seed Data
SQLite via Python stdlib only
"""
import copy
import hashlib
import json
import os
import sqlite3
from datetime import datetime, date, timedelta
import random

from env_config import load_env

load_env()

DEFAULT_DB_DIR = os.path.join(os.path.dirname(__file__), "db")
DEFAULT_DB_PATH = os.path.join(DEFAULT_DB_DIR, "tomwood.db")
DB_PATH = os.path.abspath(os.getenv("DB_PATH", DEFAULT_DB_PATH))
DB_DIR = os.path.dirname(DB_PATH)

DEFAULT_SETTINGS = {
    "workshop_name": "Tom Wood",
    "workshop_subtitle": "Workshop System",
    "currency_code": "NOK",
    "currency_locale": "nb-NO",
    "dashboard_refresh_seconds": 30,
    "default_sort": "due_date",
    "default_priority": "med",
    "default_due_days": {
        "high": 2,
        "med": 7,
        "low": 14,
    },
    "current_goldsmith_id": 1,
    "item_types": [
        "Ring",
        "Necklace",
        "Bracelet",
        "Earrings",
        "Brooch",
        "Watch",
        "Other",
    ],
    "item_materials": [
        "18k Yellow Gold",
        "18k White Gold",
        "18k Rose Gold",
        "Sterling Silver",
        "Platinum",
        "Mixed / Unknown",
    ],
    "service_types": [
        "Polishing & Cleaning",
        "Sizing",
        "Sizing + Polishing",
        "Stone Setting",
        "Stone Replacement",
        "Clasp Replacement",
        "Clasp Repair",
        "Soldering — Broken Link",
        "Prong Re-tipping",
        "Engraving",
        "Rhodium Plating",
        "Polish + Rhodium Plating",
        "Full Restoration",
        "Custom Work",
        "Assessment Only",
    ],
    "contact_methods": [
        "email",
        "phone",
        "sms",
        "whatsapp",
    ],
}


def get_db():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _dump_setting(value):
    return json.dumps(value, ensure_ascii=False)


def _load_setting(value):
    try:
        return json.loads(value)
    except Exception:
        return value


def _settings_copy():
    return copy.deepcopy(DEFAULT_SETTINGS)


def ensure_default_settings(conn):
    rows = conn.execute("SELECT key FROM app_settings").fetchall()
    existing = {r["key"] for r in rows}
    for key, value in DEFAULT_SETTINGS.items():
        if key in existing:
            continue
        conn.execute(
            "INSERT INTO app_settings (key, value) VALUES (?, ?)",
            (key, _dump_setting(value)),
        )
    conn.commit()


def get_settings(conn):
    ensure_default_settings(conn)
    settings = _settings_copy()
    rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    for row in rows:
        settings[row["key"]] = _load_setting(row["value"])
    return settings


def save_settings(conn, updates):
    for key, value in updates.items():
        conn.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = datetime('now')
            """,
            (key, _dump_setting(value)),
        )
    conn.commit()
    return get_settings(conn)


def init_db():
    conn = get_db()
    c = conn.cursor()

    # ── TABLES ──
    c.executescript("""
    CREATE TABLE IF NOT EXISTS clients (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL,
        email       TEXT UNIQUE NOT NULL,
        phone       TEXT,
        preferred_contact TEXT DEFAULT 'email',  -- email|phone|sms|whatsapp
        notes       TEXT,
        created_at  TEXT DEFAULT (datetime('now')),
        updated_at  TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS goldsmiths (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL,
        role        TEXT DEFAULT 'Goldsmith',
        access_level TEXT DEFAULT 'staff',
        email       TEXT,
        active      INTEGER DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS orders (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        order_number TEXT UNIQUE NOT NULL,  -- e.g. TW-2406
        client_id   INTEGER NOT NULL REFERENCES clients(id),
        goldsmith_id INTEGER REFERENCES goldsmiths(id),

        -- Item details
        item_name   TEXT NOT NULL,
        item_type   TEXT NOT NULL,  -- Ring|Necklace|Bracelet|Earrings|Brooch|Watch|Other
        item_material TEXT,
        item_condition TEXT,
        item_description TEXT,

        -- Service
        service_type TEXT NOT NULL,
        service_notes TEXT,
        internal_notes TEXT,

        -- Workflow
        status      TEXT NOT NULL DEFAULT 'intake',
        -- intake|assessment|approved|progress|qc|ready|complete|hold|cancelled
        priority    TEXT DEFAULT 'med',  -- high|med|low

        -- Financials
        price_estimate REAL,
        price_final    REAL,
        deposit_paid   REAL DEFAULT 0,
        currency    TEXT DEFAULT 'NOK',

        -- Dates
        received_at  TEXT DEFAULT (datetime('now')),
        due_date     TEXT,
        completed_at TEXT,
        collected_at TEXT,

        created_at  TEXT DEFAULT (datetime('now')),
        updated_at  TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS order_timeline (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
        event       TEXT NOT NULL,
        note        TEXT,
        goldsmith_id INTEGER REFERENCES goldsmiths(id),
        created_at  TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS order_photos (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
        filename    TEXT NOT NULL,
        label       TEXT DEFAULT 'intake',  -- intake|progress|final
        created_at  TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS order_status_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id    INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
        from_status TEXT,
        to_status   TEXT NOT NULL,
        changed_by  INTEGER REFERENCES goldsmiths(id),
        changed_at  TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS app_settings (
        key         TEXT PRIMARY KEY,
        value       TEXT NOT NULL,
        updated_at  TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS staff_sessions (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        session_token_hash  TEXT UNIQUE NOT NULL,
        goldsmith_id        INTEGER NOT NULL REFERENCES goldsmiths(id) ON DELETE CASCADE,
        email               TEXT NOT NULL,
        google_sub          TEXT NOT NULL,
        expires_at          TEXT NOT NULL,
        created_at          TEXT DEFAULT (datetime('now')),
        last_seen_at        TEXT DEFAULT (datetime('now'))
    );

    -- Indexes
    CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
    CREATE INDEX IF NOT EXISTS idx_orders_client ON orders(client_id);
    CREATE INDEX IF NOT EXISTS idx_orders_due ON orders(due_date);
    CREATE INDEX IF NOT EXISTS idx_timeline_order ON order_timeline(order_id);
    CREATE INDEX IF NOT EXISTS idx_staff_sessions_expires ON staff_sessions(expires_at);
    """)

    conn.commit()

    columns = {row["name"] for row in c.execute("PRAGMA table_info(goldsmiths)").fetchall()}
    if "access_level" not in columns:
        c.execute("ALTER TABLE goldsmiths ADD COLUMN access_level TEXT DEFAULT 'staff'")
        conn.commit()

    # ── SEED DATA (only if empty) ──
    row = c.execute("SELECT COUNT(*) FROM goldsmiths").fetchone()
    if row[0] == 0:
        _seed(conn, c)

    c.execute(
        """
        UPDATE goldsmiths
        SET access_level = 'staff'
        WHERE access_level IS NULL OR TRIM(access_level) = ''
        """
    )
    conn.commit()

    admin_count = c.execute(
        "SELECT COUNT(*) FROM goldsmiths WHERE LOWER(COALESCE(access_level, 'staff')) = 'admin'"
    ).fetchone()[0]
    if admin_count == 0:
        c.execute(
            """
            UPDATE goldsmiths
            SET access_level = 'admin'
            WHERE id = (
                SELECT id FROM goldsmiths
                ORDER BY id
                LIMIT 1
            )
            """
        )
        conn.commit()

    ensure_default_settings(conn)

    conn.close()


def _seed(conn, c):
    # Goldsmiths
    goldsmiths = [
        ("Mia Larsen", "Senior Goldsmith", "admin", "mia@tomwood.no"),
        ("Lars Holst", "Goldsmith", "staff", "lars@tomwood.no"),
        ("Frida Berg", "Goldsmith", "staff", "frida@tomwood.no"),
    ]
    c.executemany("INSERT INTO goldsmiths (name, role, access_level, email) VALUES (?,?,?,?)", goldsmiths)

    # Clients
    clients = [
        ("Isabella Crane", "i.crane@mail.com", "+47 900 11 222", "email"),
        ("Frida Halvorsen", "frida@n.no", "+47 920 33 444", "phone"),
        ("Oskar Bryngelson", "o.b@studio.com", "+47 930 55 666", "email"),
        ("Leah Mossberg", "leah.m@hey.com", "+47 940 77 888", "whatsapp"),
        ("Marcus Thiel", "m.thiel@firm.de", "+49 151 234 5678", "email"),
        ("Agnes Nyström", "agnes@design.se", "+46 70 123 4567", "sms"),
        ("Bjørn Dahlkvist", "bd@law.no", "+47 910 22 333", "email"),
        ("Sigrid Eilertsen", "sigrid.e@uni.no", "+47 922 44 555", "email"),
        ("Halvard Moen", "h.moen@archi.no", "+47 933 66 777", "phone"),
    ]
    c.executemany("INSERT INTO clients (name, email, phone, preferred_contact) VALUES (?,?,?,?)", clients)

    # Orders with realistic data
    today = date.today()

    orders_data = [
        # (order_number, client_id, goldsmith_id, item_name, item_type, item_material,
        #  item_condition, service_type, internal_notes, status, priority,
        #  price_estimate, due_date, received_at)
        ("TW-2406", 1, 1, "Chunky Molten Ring", "Ring", "18k Yellow Gold",
         "Light surface scratches, good structural integrity.",
         "Sizing + Polishing",
         "Size 52 → 55. Client prefers mirror finish over brushed. Rush due to anniversary.",
         "progress", "high", 3400.0,
         str(today + timedelta(days=2)), str(today - timedelta(days=5))),

        ("TW-2405", 2, 2, "Pearl Drop Necklace", "Necklace", "Sterling Silver",
         "Lobster clasp broken, chain intact.",
         "Clasp Replacement",
         "Replace with TW signature clasp. Client approved via email 12/03.",
         "ready", "med", 890.0,
         str(today - timedelta(days=2)), str(today - timedelta(days=8))),

        ("TW-2404", 3, 3, "Pavé Eternity Band", "Ring", "18k White Gold",
         "Two 1.2mm diamonds missing from pavé setting. Prongs otherwise secure.",
         "Stone Replacement",
         "Confirm diamond grade (VS1 or VS2) with client before ordering stones.",
         "assessment", "med", 5200.0,
         str(today + timedelta(days=8)), str(today - timedelta(days=1))),

        ("TW-2403", 4, 2, "Vintage Floral Brooch", "Brooch", "18k Rose Gold",
         "Antique piece ca. 1940s. General wear, dulling, catch mechanism stiff.",
         "Full Restoration",
         "Re-plate, tighten all stone settings, re-pin catch. Handle with extreme care.",
         "approved", "low", 12800.0,
         str(today + timedelta(days=16)), str(today - timedelta(days=10))),

        ("TW-2402", 5, 1, "Club Chain Bracelet", "Bracelet", "Sterling Silver",
         "One link cracked at solder joint. Rest of chain excellent.",
         "Soldering — Broken Link",
         "Rush order. Repair complete, under QC. Client arrives 21/03.",
         "qc", "high", 1100.0,
         str(today), str(today - timedelta(days=4))),

        ("TW-2401", 6, None, "Snake Chain Necklace", "Necklace", "18k Yellow Gold",
         "No damage. Client wants engraving on clasp interior.",
         "Engraving",
         "Text: 'always' — Cormorant italic, 12pt. Confirm font rendering with client before proceeding.",
         "intake", "low", 650.0,
         str(today + timedelta(days=21)), str(today)),

        ("TW-2400", 7, 3, "Men's Signet Ring", "Ring", "Platinum",
         "Surface scratches, mild oxidation on edges.",
         "Polishing + Rhodium Plating",
         "Standard polish + rhodium. Collected 14/03. Client satisfied.",
         "complete", "med", 2200.0,
         str(today - timedelta(days=6)), str(today - timedelta(days=12))),

        ("TW-2399", 8, 1, "Diamond Solitaire Ring", "Ring", "18k White Gold",
         "Prong holding centre stone slightly bent inward.",
         "Prong Re-tipping",
         "Re-tip 4 prongs. Stone is approx 1.2ct — secure during work.",
         "hold", "high", 4500.0,
         str(today + timedelta(days=3)), str(today - timedelta(days=6))),

        ("TW-2398", 9, 2, "Link Cuff Bracelet", "Bracelet", "18k Yellow Gold",
         "Scratched throughout. Clasp slightly misaligned.",
         "Polish + Clasp Adjustment",
         "Delivered and collected. Client requested invoice copy.",
         "complete", "low", 1800.0,
         str(today - timedelta(days=10)), str(today - timedelta(days=18))),
    ]

    for od in orders_data:
        c.execute("""
            INSERT INTO orders
              (order_number, client_id, goldsmith_id, item_name, item_type, item_material,
               item_condition, service_type, internal_notes, status, priority,
               price_estimate, due_date, received_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, od)

    # Timeline events
    timelines = {
        "TW-2406": [
            (1, "Order created", "Received from client in store.", 1),
            (1, "Item received & photographed", "3 intake photos taken.", 1),
            (1, "Assessment complete — sizing approved", "Price confirmed: NOK 3,400.", 1),
            (1, "Sizing in progress", None, 1),
            (1, "Polishing started", "Mirror finish as requested.", 1),
        ],
        "TW-2405": [
            (2, "Order created", None, 2),
            (2, "Assessment — clasp replacement agreed", "Client approved via email.", 2),
            (2, "TW signature clasp fitted", None, 2),
            (2, "QC passed", "Chain hang and clasp action confirmed.", 2),
            (2, "Ready for collection", "SMS sent to client.", 2),
        ],
        "TW-2404": [
            (3, "Order created", None, 3),
            (3, "Under assessment", "Identifying missing stone specs.", 3),
        ],
        "TW-2403": [
            (4, "Order created", "Item handled with cotton gloves throughout.", 2),
            (4, "Detailed assessment completed", "Full restoration scope documented.", 2),
            (4, "Client approved quote (NOK 12,800)", None, 2),
            (4, "Queued — awaiting workshop slot", None, 2),
        ],
        "TW-2402": [
            (5, "Order created", "Rush flagged.", 1),
            (5, "Solder repair executed", "Link re-soldered and cleaned.", 1),
            (5, "QC inspection in progress", None, 1),
        ],
        "TW-2401": [
            (6, "Order created", "Intake form completed.", None),
        ],
        "TW-2400": [
            (7, "Order created", None, 3),
            (7, "Polish complete", None, 3),
            (7, "Rhodium plating applied", None, 3),
            (7, "QC passed", None, 3),
            (7, "Client notified — ready for collection", None, 3),
            (7, "Collected by client", "Client satisfied. Invoice issued.", 3),
        ],
        "TW-2399": [
            (8, "Order created", None, 1),
            (8, "On hold — awaiting client confirmation", "Client travelling. Will contact 25/03.", 1),
        ],
        "TW-2398": [
            (9, "Order created", None, 2),
            (9, "Polish complete", None, 2),
            (9, "Clasp adjusted", None, 2),
            (9, "QC passed", None, 2),
            (9, "Collected by client", "Invoice copy sent by email.", 2),
        ],
    }

    # Get order id map
    rows = c.execute("SELECT id, order_number FROM orders").fetchall()
    order_map = {r["order_number"]: r["id"] for r in rows}

    for order_num, events in timelines.items():
        oid = order_map.get(order_num)
        if not oid:
            continue
        for (_, event, note, gs_id) in events:
            c.execute(
                "INSERT INTO order_timeline (order_id, event, note, goldsmith_id) VALUES (?,?,?,?)",
                (oid, event, note, gs_id)
            )

    # Status log
    for order_num, events in timelines.items():
        oid = order_map.get(order_num)
        if not oid:
            continue

    conn.commit()
    print("✓ Database seeded successfully")


if __name__ == "__main__":
    init_db()
    print(f"✓ Database ready at {DB_PATH}")
