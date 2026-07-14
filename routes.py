from flask import render_template, request, redirect, url_for, flash, abort
from database import get_db, update_pick_list_status
import csv
import os
from werkzeug.utils import secure_filename
from datetime import datetime, timedelta

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def register_routes(app):
    @app.route("/", methods=["GET"])
    def index():
        conn = get_db()
        
        pick_lists = conn.execute("""
            SELECT
                pl.*,
                COALESCE(SUM(pi.qty_required), 0) AS total_required,
                COALESCE(SUM(pi.qty_scanned), 0) AS total_scanned,
                COALESCE(SUM(pi.qty_remaining), 0) AS total_remaining
            FROM pick_lists pl
            LEFT JOIN pick_items pi ON pi.pick_list_id = pl.id
            WHERE pl.status != 'complete'
            GROUP BY pl.id
            ORDER BY pl.uploaded_at DESC
        """).fetchall()

        conn.close()

        return render_template(
            "index.html", 
            pick_lists=pick_lists
        )
    
    @app.route("/upload", methods=["POST"])
    def upload():
        file = request.files.get("pick_file")
        pick_list_name = request.form.get("pick_list_name", "").strip()

        if not file or file.filename == "":
            flash("Please choose a CSV file.", "error")
            return redirect(url_for("index"))
        
        filename = secure_filename(file.filename)

        if not pick_list_name:
            pick_list_name = os.path.splitext(filename)[0]


        path = os.path.join(UPLOAD_FOLDER, filename)
        file.save(path)

        conn = get_db()

        try:
            cursor = conn.execute("""
                INSERT INTO pick_lists
                (name, original_filename, status, uploaded_at)
                VALUES (?, ?, 'open', ?)
            """, (
                pick_list_name,
                filename,
                datetime.now().isoformat(timespec="seconds")
            ))

            pick_list_id = cursor.lastrowid

            with open(path, newline="", encoding="utf-8-sig") as csvfile:
                reader = csv.DictReader(csvfile)

                required_columns = {"item_code", "barcode", "description", "quantity"}

                if not required_columns.issubset(set(reader.fieldnames or [])):
                    raise ValueError("CSV must have columns: item_code, barcode, description, quantity")

                rows_added = 0


                for row in reader:
                    item_code = row["item_code"].strip()
                    barcode = row["barcode"].strip()
                    description = row.get("description", "").strip()

                    quantity_text = row.get("quantity", "").strip()

                    if not item_code or not barcode or not quantity_text:
                        continue

                    try:
                        quantity_required = int(quantity_text)
                    except ValueError:
                        continue
                    
                    if quantity_required <= 0:
                        continue


                    conn.execute("""
                        INSERT INTO pick_items
                        (pick_list_id, item_code, barcode, description, qty_required, qty_scanned, qty_remaining)
                        VALUES (?, ?, ?, ?, ?, 0, ?)
                    """, (pick_list_id, item_code, barcode, description, quantity_required, quantity_required))

                    rows_added += 1

                if rows_added == 0:
                    raise ValueError("No valid rows found in CSV")

                conn.commit()
                flash(f"Pick list uploaded successfully.", "success")

        except Exception as e:
            conn.rollback()
            flash(f"Upload failed: {e}", "error")

        finally:
            conn.close()
        
        return redirect(url_for("index"))
    
    @app.route("/pick-list/<int:pick_list_id>", methods=["GET"])
    def pick_list_detail(pick_list_id):
        conn = get_db()

        pick_list = conn.execute("""
            SELECT * FROM pick_lists
            WHERE id = ?
        """, (pick_list_id,)).fetchone()

        if pick_list is None:
            conn.close()
            abort(404)

        if pick_list["status"] == "complete":
            conn.close()
            flash("That pick list is already complete.", "warning")
            return redirect(url_for("index"))

        items = conn.execute("""
            SELECT * FROM pick_items
            WHERE pick_list_id = ?
            ORDER BY qty_remaining = 0 ASC, id ASC
        """, (pick_list_id,)).fetchall()

        total_required = sum(item["qty_required"] for item in items)
        total_scanned = sum(item["qty_scanned"] for item in items)
        total_remaining = sum(item["qty_remaining"] for item in items)

        conn.close()

        return render_template(
            "pick_list.html",
            pick_list=pick_list,
            items=items,
            total_required=total_required,
            total_scanned=total_scanned,
            total_remaining=total_remaining
        )
    
    @app.route("/pick-list/<int:pick_list_id>/scan", methods=["POST"])
    def scan(pick_list_id):
        barcode = request.form.get("barcode", "").strip()

        if not barcode:
            flash("No barcode scanned.", "error")
            return redirect(url_for("pick_list_detail", pick_list_id=pick_list_id))

        conn = get_db()

        pick_list = conn.execute("""
            SELECT * FROM pick_lists
            WHERE id = ?
        """, (pick_list_id,)).fetchone()

        if pick_list is None:
            conn.close()
            abort(404)

        if pick_list["status"] == "complete":
            conn.close()
            flash("This pick list is already complete.", "warning")
            return redirect(url_for("index"))

        item = conn.execute("""
            SELECT * FROM pick_items
            WHERE pick_list_id = ?
            AND barcode = ?
            AND qty_remaining > 0
            ORDER BY id ASC
            LIMIT 1
        """, (pick_list_id, barcode)).fetchone()

        if item is None:
            existing = conn.execute("""
                SELECT * FROM pick_items
                WHERE pick_list_id = ?
                AND barcode = ?
                LIMIT 1
            """, (pick_list_id, barcode)).fetchone()

            conn.close()

            if existing:
                flash(f"Item already fully picked: {barcode}", "warning")
            else:
                flash(f"Wrong item or not on this pick list: {barcode}", "error")

            return redirect(url_for("pick_list_detail", pick_list_id=pick_list_id))

        conn.execute("""
            UPDATE pick_items
            SET qty_scanned = qty_scanned + 1,
                qty_remaining = qty_remaining - 1
            WHERE id = ?
        """, (item["id"],))

        conn.commit()
        conn.close()

        flash(f"Scanned Ok: {item['item_code']} - {item['description']}", "success")

        return redirect(url_for("pick_list_detail", pick_list_id=pick_list_id))
    

    @app.route("/pick-list/<int:pick_list_id>/confirm-complete", methods=["POST"])
    def confirm_complete(pick_list_id):
        conn = get_db()

        totals = conn.execute("""
            SELECT
                COALESCE(SUM(qty_required), 0) AS total_required,
                COALESCE(SUM(qty_scanned), 0) AS total_scanned,
                COALESCE(SUM(qty_remaining), 0) AS total_remaining
            FROM pick_items
            WHERE pick_list_id = ?
        """, (pick_list_id,)).fetchone()

        if totals["toal_required"] == 0:
            conn.close()
            flash(f"Cannot complete an empty pick list.", "error")
            return redirect(url_for("pick_list_detail", pick_list_id=pick_list_id))
        
        if totals["total_remaing"] > 0:
            conn.close()
            flash(f"Cannot confirm complete. There are still items remaining.", "error")
            return redirect(url_for("pick_list_detail", "error"))
        
        conn.execute("""
            UPDATE pick_lists
            SET status = 'complete'
            WHERE id = ?
        """, (pick_list_id))

        conn.commi()
        conn.close()

        flash(f"Pick list confirmed complete. Return to SAP and mark it done there.", "success")
        return redirect(url_for("index"))
    
    @app.route("/pick-list/<int:pick_list_id>/delete", methods=["POST"])
    def delete_pick_list(pick_list_id):
        conn = get_db()

        conn.execute("""
            DELETE FROM pick_items
            WHERE pick_list_id = ?
        """, (pick_list_id,))

        conn.execute("""
            DELETE FROM pick_lists
            WHERE id = ?
        """, (pick_list_id,))

        conn.commit()
        conn.close()

        flash("Pick list deleted.", "warning")
        return redirect(url_for("index"))