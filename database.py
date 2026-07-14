import sqlite3
from datetime import datetime, timedelta

DB_NAME = "pick_confirm.db"

def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pick_lists(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            original_filename TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            uploaded_at TEXT NOT NULL,
            completed_at TEXT
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS pick_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pick_list_id INTEGER NOT NULL,
            item_code TEXT NOT NULL,
            barcode TEXT NOT NULL,
            description TEXT,
            qty_required INTEGER NOT NULL,
            qty_scanned INTEGER NOT NULL DEFAULT 0,
            qty_remaining INTEGER NOT NULL,
            FOREIGN KEY (pick_list_id) REFERENCES pick_lists(id) ON DELETE CASCADE
        )
    """)

    conn.commit()
    conn.close()


def cleanup_old_completed_lists(days_old=30):
    """
    Deletes completed pick lists older than X days.
    This keep the server from filling up with old jobs.
    """

    cutoff = datetime.now() - timedelta(days=days_old)
    cutoff_text = cutoff.isoformat(timespec="seconds")

    conn = get_db()

    old_lists = conn.execute("""
        SELECT id FROM pick_lists
        WHERE status = 'complete'
        AND completed_at IS NOT NULL
        AND completed_at < ?
    """, (cutoff_text,)).fetchall()
    
    for pick_list in old_lists:
        conn.execute(
            "DELETE FROM pick_items WHERE pick_list_id = ?",
            (pick_list["id"],)
        )

        conn.execute(
            "DELETE FROM pick_lists WHERE id = ?",
            (pick_list["id"],)
        )

        conn.commit()
        conn.close()

def update_pick_list_status(pick_list_id):
    conn = get_db()

    totals = conn.execute("""
        SELECT
            COALESCE(SUM(qty_required), 0) AS total_required,
            COALESCE(SUM(qty_scanned), 0) AS total_scanned,
            COALESCE(SUM(qty_remaining), 0) AS total_remaining
        FROM pick_items
        WHERE pick_list_id = ?
    """, (pick_list_id,)).fetchone()

    if totals["total_remaining"] == 0 and totals["total_required"] > 0:
        conn.execute("""
            UPDATE pick_lists
            SET status = 'complete',
                completed_at = ?
            WHERE id = ?
        """, (
            datetime.now().isoformat(timespec="seconds"),
            pick_list_id
        ))
    else:
        conn.execute("""
            UPDATE pick_lists
            SET status = 'open',
                completed_at = NULL
            WHERE id = ?
        """, (pick_list_id,))

    conn.commit()
    conn.close() 