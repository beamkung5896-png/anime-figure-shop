"""ระบบหลังบ้านสำหรับเจ้าของร้าน (Flask Blueprint) — ทุกหน้าอยู่ใต้ /admin
ฟีเจอร์: ล็อกอิน, แดชบอร์ดยอดขาย, จัดการสินค้า (เพิ่ม/แก้/ซ่อน/ลบ/อัปโหลดรูป),
        จัดการออเดอร์ (เปลี่ยนสถานะ/เลขพัสดุ/ยกเลิกแล้วคืนสต็อก), ส่งออก CSV, เปลี่ยนรหัสผ่าน
"""
import csv
import hmac
import io
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import (Blueprint, Response, abort, flash, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

import chat
from dates import date_clause, day_range, presets, thai_date, thai_today
from common import cancel_order, get_setting, get_text_setting, shop_contact, new_reset_token, notify_order, send_mail
from db import (TEXT_SETTINGS, DEFAULT_SETTINGS, IMAGE_DIR, LOW_STOCK, PAYMENT_DIR, STATUS_FLOW, STATUSES,
                get_db, payment_qr_file)

bp = Blueprint("admin", __name__, url_prefix="/admin")

MAX_FAILS, LOCK_SECONDS = 5, 10 * 60
_fails = {}   # ip -> [จำนวนครั้งที่ผิด, เวลาที่เริ่มนับ]  (เก็บในหน่วยความจำ รีเซ็ตเมื่อรีสตาร์ท)


# ---------- ความปลอดภัยพื้นฐาน ----------
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


@bp.app_context_processor
def inject_admin_helpers():
    ctx = {"csrf_token": csrf_token, "today_iso": thai_today().isoformat(), "date_presets": presets()}
    if request.blueprint == "admin" and session.get("admin"):
        ctx["pending_count"] = get_db().execute(
            "SELECT COUNT(*) FROM orders WHERE status = 'pending'").fetchone()[0]
        ctx["chat_unread"] = chat.unread_count(get_db())
    return ctx


@bp.before_request
def guard():
    # 1) ทุกฟอร์ม POST ต้องมี CSRF token ที่ตรงกับ session (กันเว็บอื่นแอบสั่งงานแทนแอดมิน)
    if request.method == "POST":
        expected = session.get("csrf")
        sent = request.form.get("csrf_token", "")
        if not expected or not hmac.compare_digest(sent.encode(), expected.encode()):
            abort(400)
    # 2) ทุกหน้า (ยกเว้นหน้าล็อกอิน) ต้องล็อกอินแล้ว
    if request.endpoint != "admin.login" and not session.get("admin"):
        return redirect(url_for("admin.login", next=request.path))


@bp.after_request
def no_cache(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------- ล็อกอิน / ออกจากระบบ / รหัสผ่าน ----------
@bp.route("/login", methods=["GET", "POST"])
def login():
    if session.get("admin"):
        return redirect(url_for("admin.dashboard"))
    error = None
    if request.method == "POST":
        ip = request.remote_addr or "?"
        rec = _fails.get(ip)
        if rec and time.time() - rec[1] > LOCK_SECONDS:
            _fails.pop(ip, None)
            rec = None
        if rec and rec[0] >= MAX_FAILS:
            error = "ใส่รหัสผิดหลายครั้งเกินไป กรุณารอ 10 นาทีแล้วลองใหม่"
        else:
            row = get_db().execute("SELECT * FROM admins WHERE username = ?",
                                   (request.form.get("username", "").strip(),)).fetchone()
            if row and check_password_hash(row["password_hash"], request.form.get("password", "")):
                _fails.pop(ip, None)
                keep = {k: session[k] for k in ("cart", "my_orders", "uid") if k in session}
                session.clear()                       # ออก session ใหม่ทุกครั้งที่ล็อกอิน
                session.update(keep)
                session["admin"] = row["username"]
                session.permanent = True
                nxt = request.args.get("next", "")
                if not nxt.startswith("/admin") or nxt.startswith("//"):
                    nxt = url_for("admin.dashboard")
                return redirect(nxt)
            rec = _fails.setdefault(ip, [0, time.time()])
            rec[0] += 1
            error = "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"
    return render_template("admin/login.html", error=error)


@bp.post("/logout")
def logout():
    session.pop("admin", None)
    return redirect(url_for("admin.login"))


@bp.route("/password", methods=["GET", "POST"])
def password():
    errors = []
    if request.method == "POST":
        db = get_db()
        row = db.execute("SELECT * FROM admins WHERE username = ?", (session["admin"],)).fetchone()
        cur, new, again = (request.form.get(k, "") for k in ("current", "new", "again"))
        if not check_password_hash(row["password_hash"], cur):
            errors.append("รหัสผ่านปัจจุบันไม่ถูกต้อง")
        if len(new) < 8:
            errors.append("รหัสผ่านใหม่ต้องยาวอย่างน้อย 8 ตัวอักษร")
        if new != again:
            errors.append("ยืนยันรหัสผ่านใหม่ไม่ตรงกัน")
        if new == cur:
            errors.append("รหัสผ่านใหม่ต้องไม่ซ้ำกับรหัสเดิม")
        if not errors:
            db.execute("UPDATE admins SET password_hash = ?, default_pw = 0 WHERE id = ?",
                       (generate_password_hash(new), row["id"]))
            db.commit()
            flash("เปลี่ยนรหัสผ่านแล้ว")
            return redirect(url_for("admin.dashboard"))
    return render_template("admin/password.html", errors=errors)


# ---------- แดชบอร์ด ----------
@bp.route("/")
def dashboard():
    db = get_db()
    one = lambda sql, *a: db.execute(sql, a).fetchone()[0]
    revenue = one("SELECT COALESCE(SUM(total),0) FROM orders WHERE status IN ('paid','shipped')")
    awaiting = one("SELECT COALESCE(SUM(total),0) FROM orders WHERE status = 'pending'")
    with_slip = one("SELECT COUNT(*) FROM orders WHERE status = 'pending' AND slip != ''")
    counts = {s: one("SELECT COUNT(*) FROM orders WHERE status = ?", s) for s in STATUSES}

    # ยอดขาย 7 วันล่าสุด (เวลาไทย = UTC+7) ไม่นับออเดอร์ที่ยกเลิก
    today = (datetime.now(timezone.utc) + timedelta(hours=7)).date()
    days = [today - timedelta(days=i) for i in range(6, -1, -1)]
    sales = {r["d"]: r for r in db.execute("""
        SELECT date(created_at, '+7 hours') AS d, SUM(total) AS amount, COUNT(*) AS n
        FROM orders WHERE status != 'cancelled' AND date(created_at, '+7 hours') >= ?
        GROUP BY d""", (days[0].isoformat(),))}
    chart = [{"label": d.strftime("%d/%m"), "iso": d.isoformat(),
              "amount": sales[d.isoformat()]["amount"] if d.isoformat() in sales else 0,
              "n": sales[d.isoformat()]["n"] if d.isoformat() in sales else 0} for d in days]
    top = max([c["amount"] for c in chart] + [1])
    for c in chart:
        c["pct"] = round(c["amount"] * 100 / top)

    low_stock = db.execute("SELECT * FROM products WHERE active = 1 AND stock <= ? ORDER BY stock, name",
                           (LOW_STOCK,)).fetchall()
    best = db.execute("""
        SELECT p.name, SUM(oi.qty) AS sold, SUM(oi.qty * oi.unit_price) AS amount
        FROM order_items oi JOIN orders o ON o.id = oi.order_id JOIN products p ON p.id = oi.product_id
        WHERE o.status != 'cancelled' GROUP BY p.id ORDER BY sold DESC LIMIT 5""").fetchall()
    recent = db.execute("""SELECT *, datetime(created_at, '+7 hours') AS local_time
                           FROM orders ORDER BY id DESC LIMIT 6""").fetchall()
    default_pw = one("SELECT default_pw FROM admins WHERE username = ?", session["admin"])
    customers = one("SELECT COUNT(*) FROM users")
    new_customers = one("SELECT COUNT(*) FROM users WHERE date(created_at, '+7 hours') >= ?", days[0].isoformat())
    new_reviews = one("SELECT COUNT(*) FROM reviews WHERE visible = 1 AND date(created_at, '+7 hours') >= ?",
                      days[0].isoformat())
    return render_template("admin/dashboard.html", revenue=revenue, awaiting=awaiting, counts=counts, with_slip=with_slip,
                           chart=chart, low_stock=low_stock, best=best, recent=recent,
                           default_pw=default_pw, low=LOW_STOCK, customers=customers,
                           new_customers=new_customers, new_reviews=new_reviews,
                           product_count=one("SELECT COUNT(*) FROM products WHERE active = 1"))


# ---------- สินค้า ----------
def sniff_image(data):
    """ตรวจชนิดไฟล์จากเนื้อหาจริง (ไม่เชื่อนามสกุลที่ผู้ใช้ส่งมา)"""
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def parse_product_form(form):
    errors, d = [], {}
    d["name"] = form.get("name", "").strip()
    d["category"] = form.get("category", "").strip()
    d["scale"] = form.get("scale", "").strip() or "-"
    d["description"] = form.get("description", "").strip()
    if not (2 <= len(d["name"]) <= 120):
        errors.append("ชื่อสินค้าต้องยาว 2–120 ตัวอักษร")
    if not d["category"]:
        errors.append("ระบุหมวดหมู่")
    for key, label in (("price", "ราคา"), ("stock", "จำนวนสต็อก")):
        raw = form.get(key, "").strip()
        if raw.isdigit() and int(raw) <= 10_000_000:
            d[key] = int(raw)
        else:
            errors.append(f"{label}ต้องเป็นตัวเลขจำนวนเต็มตั้งแต่ 0 ขึ้นไป")
    d["bg"] = form.get("bg", "#ffffff")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", d["bg"]):
        d["bg"] = "#ffffff"
    d["fit"] = form.get("fit") if form.get("fit") in ("contain", "cover") else "contain"
    return d, errors


def remove_upload(stem, ext, db, except_id):
    """ลบไฟล์รูปที่แอดมินอัปโหลดเอง (ขึ้นต้น p_) เมื่อไม่มีสินค้าอื่นใช้อยู่"""
    if not stem.startswith("p_"):
        return
    if db.execute("SELECT 1 FROM products WHERE image = ? AND id != ?", (stem, except_id)).fetchone():
        return
    try:
        os.remove(os.path.join(IMAGE_DIR, f"{stem}.{ext}"))
    except OSError:
        pass


@bp.route("/products")
def products():
    rows = get_db().execute("""
        SELECT p.*, (SELECT COALESCE(SUM(oi.qty),0) FROM order_items oi
                     JOIN orders o ON o.id = oi.order_id
                     WHERE oi.product_id = p.id AND o.status != 'cancelled') AS sold
        FROM products p ORDER BY p.active DESC, p.id""").fetchall()
    return render_template("admin/products.html", products=rows, low=LOW_STOCK)


@bp.route("/products/new", methods=["GET", "POST"])
@bp.route("/products/<int:pid>/edit", methods=["GET", "POST"])
def product_form(pid=None):
    db = get_db()
    row = None
    if pid is not None:
        row = db.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
        if row is None:
            abort(404)
    errors, form = [], (row or {"bg": "#ffffff", "fit": "contain", "stock": 1})
    categories = [r[0] for r in db.execute("SELECT DISTINCT category FROM products ORDER BY category")]
    if request.method == "POST":
        data, errors = parse_product_form(request.form)
        form = {**data, "image": row["image"] if row else "", "image_ext": row["image_ext"] if row else ""}
        upload = request.files.get("image")
        blob = upload.read() if upload and upload.filename else b""
        kind = sniff_image(blob) if blob else None
        if blob and not kind:
            errors.append("รองรับเฉพาะไฟล์รูป JPG, PNG หรือ WEBP")
        if row is None and not blob:
            errors.append("เลือกรูปสินค้า")
        if not errors:
            image, ext = (row["image"], row["image_ext"]) if row else ("", "")
            if blob:
                new_image = "p_" + secrets.token_hex(6)
                with open(os.path.join(IMAGE_DIR, f"{new_image}.{kind}"), "wb") as f:
                    f.write(blob)
                old = (image, ext)
                image, ext = new_image, kind
            if row is None:
                cur = db.execute("""INSERT INTO products (name,image,category,scale,price,stock,description,
                                    image_ext,bg,fit,active) VALUES (?,?,?,?,?,?,?,?,?,?,1)""",
                                 (data["name"], image, data["category"], data["scale"], data["price"],
                                  data["stock"], data["description"], ext, data["bg"], data["fit"]))
                flash(f"เพิ่ม “{data['name']}” แล้ว")
            else:
                db.execute("""UPDATE products SET name=?, image=?, category=?, scale=?, price=?, stock=?,
                              description=?, image_ext=?, bg=?, fit=? WHERE id=?""",
                           (data["name"], image, data["category"], data["scale"], data["price"],
                            data["stock"], data["description"], ext, data["bg"], data["fit"], pid))
                if blob:
                    remove_upload(old[0], old[1], db, pid)
                flash(f"บันทึก “{data['name']}” แล้ว")
            db.commit()
            return redirect(url_for("admin.products"))
    return render_template("admin/product_form.html", p=row, form=form, errors=errors, categories=categories)


@bp.post("/products/<int:pid>/toggle")
def product_toggle(pid):
    db = get_db()
    row = db.execute("SELECT name, active FROM products WHERE id = ?", (pid,)).fetchone()
    if row is None:
        abort(404)
    db.execute("UPDATE products SET active = ? WHERE id = ?", (0 if row["active"] else 1, pid))
    db.commit()
    flash(f"{'ซ่อน' if row['active'] else 'แสดง'} “{row['name']}” {'ออกจาก' if row['active'] else 'ในหน้า'}ร้านแล้ว")
    return redirect(url_for("admin.products"))


@bp.post("/products/<int:pid>/delete")
def product_delete(pid):
    db = get_db()
    row = db.execute("SELECT * FROM products WHERE id = ?", (pid,)).fetchone()
    if row is None:
        abort(404)
    if db.execute("SELECT 1 FROM order_items WHERE product_id = ?", (pid,)).fetchone():
        flash("ลบไม่ได้ เพราะสินค้านี้มีประวัติคำสั่งซื้อ ใช้ปุ่ม “ซ่อน” แทน")
        return redirect(url_for("admin.products"))
    db.execute("DELETE FROM products WHERE id = ?", (pid,))
    remove_upload(row["image"], row["image_ext"], db, pid)
    db.commit()
    flash(f"ลบ “{row['name']}” แล้ว")
    return redirect(url_for("admin.products"))


# ---------- ออเดอร์ ----------
def order_query(status, q, d1=None, d2=None):
    sql = "SELECT *, datetime(created_at, '+7 hours') AS local_time FROM orders WHERE 1=1"
    params = []
    if status in STATUSES:
        sql += " AND status = ?"
        params.append(status)
    if q:
        sql += " AND (customer LIKE ? OR phone LIKE ? OR CAST(id AS TEXT) = ?)"
        params += [f"%{q}%", f"%{q}%", q.lstrip("#").lstrip("0") or "0"]
    dsql, dparams = date_clause("created_at", d1, d2)
    return sql + dsql + " ORDER BY id DESC", params + dparams


@bp.route("/orders")
def orders():
    status, q = request.args.get("status", ""), request.args.get("q", "").strip()
    d1, d2 = day_range(request.args)
    sql, params = order_query(status, q, d1, d2)
    db = get_db()
    rows = db.execute(sql + " LIMIT 200", params).fetchall()
    # สรุปตามตัวกรองที่เลือก (ยอดรวมไม่นับออเดอร์ที่ยกเลิก)
    found, money = db.execute(
        f"SELECT COUNT(*), COALESCE(SUM(CASE WHEN status != 'cancelled' THEN total END), 0) FROM ({sql})",
        params).fetchone()
    # ตัวเลขบนแท็บสถานะ นับตามช่วงวันที่ที่เลือก
    dsql, dparams = date_clause("created_at", d1, d2)
    counts = {r["status"]: r["n"] for r in db.execute(
        f"SELECT status, COUNT(*) AS n FROM orders WHERE 1=1{dsql} GROUP BY status", dparams)}
    return render_template("admin/orders.html", orders=rows, status=status, q=q, counts=counts,
                           total_all=sum(counts.values()), d1=d1.isoformat() if d1 else "",
                           d2=d2.isoformat() if d2 else "", found=found, money=money,
                           range_label=range_label(d1, d2))


def range_label(d1, d2):
    if d1 and d2:
        return thai_date(d1) if d1 == d2 else f"{thai_date(d1)} – {thai_date(d2)}"
    if d1:
        return f"ตั้งแต่ {thai_date(d1)}"
    if d2:
        return f"ถึง {thai_date(d2)}"
    return ""


@bp.route("/orders/<int:oid>")
def order_detail(oid):
    db = get_db()
    order = db.execute("SELECT *, datetime(created_at, '+7 hours') AS local_time FROM orders WHERE id = ?",
                       (oid,)).fetchone()
    if order is None:
        abort(404)
    lines = db.execute("""SELECT p.name, p.image, p.image_ext, oi.qty, oi.unit_price
                          FROM order_items oi JOIN products p ON p.id = oi.product_id
                          WHERE oi.order_id = ?""", (oid,)).fetchall()
    member = db.execute("SELECT id, name, email FROM users WHERE id = ?", (order["user_id"],)).fetchone() \
        if order["user_id"] else None
    return render_template("admin/order_detail.html", order=order, lines=lines, member=member,
                           next_statuses=STATUS_FLOW[order["status"]])


@bp.post("/orders/<int:oid>/status")
def order_status(oid):
    db = get_db()
    order = db.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone()
    if order is None:
        abort(404)
    new = request.form.get("status", "")
    tracking = request.form.get("tracking", "").strip()[:60]
    if new:
        if new not in STATUS_FLOW[order["status"]]:
            flash("เปลี่ยนสถานะแบบนี้ไม่ได้")
        else:
            if new == "cancelled":   # ยกเลิก = คืนของเข้าสต็อก + คืนสิทธิ์คูปอง
                cancel_order(db, oid)
                notify_order(db, oid, "cancelled")
            else:
                db.execute("UPDATE orders SET status = ?, tracking = ? WHERE id = ?",
                           (new, tracking if new == "shipped" else order["tracking"], oid))
                db.commit()
                notify_order(db, oid, new)
            flash(f"ออเดอร์ #{oid:05d} → {STATUSES[new]}" + (" (คืนสต็อกแล้ว)" if new == "cancelled" else ""))
    elif order["status"] == "shipped":
        db.execute("UPDATE orders SET tracking = ? WHERE id = ?", (tracking, oid))
        db.commit()
        if tracking != order["tracking"]:
            notify_order(db, oid, "tracking")
        flash("บันทึกเลขพัสดุแล้ว")
    return redirect(url_for("admin.order_detail", oid=oid))


@bp.route("/payment", methods=["GET", "POST"])
def payment():
    """เปลี่ยนรูป QR รับเงินที่แสดงให้ลูกค้าหลังกดสั่งซื้อ"""
    error = None
    if request.method == "POST":
        upload = request.files.get("qr")
        blob = upload.read() if upload and upload.filename else b""
        kind = sniff_image(blob) if blob else None
        if not kind:
            error = "เลือกไฟล์รูป QR (JPG, PNG หรือ WEBP)"
        else:
            for ext in ("png", "jpg", "webp"):      # เหลือไฟล์ QR ไฟล์เดียวเสมอ
                try:
                    os.remove(os.path.join(PAYMENT_DIR, f"qr.{ext}"))
                except OSError:
                    pass
            with open(os.path.join(PAYMENT_DIR, f"qr.{kind}"), "wb") as f:
                f.write(blob)
            flash("เปลี่ยนรูป QR รับเงินแล้ว")
            return redirect(url_for("admin.payment"))
    return render_template("admin/payment.html", error=error, qr_file=payment_qr_file())


@bp.route("/orders.csv")
def orders_csv():
    """ส่งออกออเดอร์เป็น CSV เปิดใน Excel ได้ (ใส่ BOM ให้ภาษาไทยไม่เพี้ยน)"""
    d1, d2 = day_range(request.args)
    sql, params = order_query(request.args.get("status", ""), request.args.get("q", "").strip(), d1, d2)
    db = get_db()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["เลขที่ออเดอร์", "วันเวลา (ไทย)", "ชื่อผู้รับ", "เบอร์โทร", "ที่อยู่", "รายการ", "ค่าส่ง", "ส่วนลด", "โค้ด", "ยอดรวม", "สถานะ", "เลขพัสดุ", "อีเมลสมาชิก"])
    for o in db.execute(sql, params):
        items = "; ".join(f"{r['name']} x{r['qty']}" for r in db.execute(
            "SELECT p.name, oi.qty FROM order_items oi JOIN products p ON p.id = oi.product_id WHERE oi.order_id = ?",
            (o["id"],)))
        email = (db.execute("SELECT email FROM users WHERE id = ?", (o["user_id"],)).fetchone() or [""])[0] \
            if o["user_id"] else ""
        w.writerow([f"{o['id']:05d}", o["local_time"], o["customer"], o["phone"], o["address"],
                    items, o["shipping"], o["discount"], o["coupon"], o["total"],
                    STATUSES.get(o["status"], o["status"]), o["tracking"], email])
    stamp = (datetime.now(timezone.utc) + timedelta(hours=7)).strftime("%Y%m%d")
    if d1 or d2:       # ถ้าเลือกช่วงวันที่ ให้ชื่อไฟล์บอกช่วงนั้นด้วย
        stamp = f"{d1.strftime('%Y%m%d') if d1 else 'start'}-{d2.strftime('%Y%m%d') if d2 else stamp}"
    return Response("\ufeff" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=orders-{stamp}.csv"})


# ---------- ลูกค้าสมาชิก ----------
@bp.route("/customers")
def customers():
    q = request.args.get("q", "").strip()
    sql = """SELECT u.*, datetime(u.created_at, '+7 hours') AS joined,
                    (SELECT COUNT(*) FROM orders o WHERE o.user_id = u.id) AS orders_n,
                    (SELECT COALESCE(SUM(total), 0) FROM orders o
                      WHERE o.user_id = u.id AND o.status IN ('paid','shipped')) AS spent
             FROM users u WHERE 1=1"""
    params = []
    if q:
        sql += " AND (u.name LIKE ? OR u.email LIKE ? OR u.phone LIKE ?)"
        params += [f"%{q}%"] * 3
    rows = get_db().execute(sql + " ORDER BY u.id DESC LIMIT 300", params).fetchall()
    return render_template("admin/customers.html", rows=rows, q=q)


@bp.route("/customers/<int:uid>")
def customer_detail(uid):
    db = get_db()
    u = db.execute("SELECT *, datetime(created_at, '+7 hours') AS joined, "
                   "CASE WHEN consent_at != '' THEN datetime(consent_at, '+7 hours') END AS consent_local, "
                   "datetime(last_login, '+7 hours') AS last_seen FROM users WHERE id = ?", (uid,)).fetchone()
    if u is None:
        abort(404)
    orders = db.execute("SELECT *, datetime(created_at, '+7 hours') AS local_time FROM orders "
                        "WHERE user_id = ? ORDER BY id DESC", (uid,)).fetchall()
    addrs = db.execute("SELECT * FROM addresses WHERE user_id = ? ORDER BY is_default DESC, id", (uid,)).fetchall()
    spent = sum(o["total"] for o in orders if o["status"] in ("paid", "shipped"))
    return render_template("admin/customer_detail.html", u=u, orders=orders, addrs=addrs, spent=spent)


@bp.post("/customers/<int:uid>/toggle")
def customer_toggle(uid):
    db = get_db()
    u = db.execute("SELECT name, active FROM users WHERE id = ?", (uid,)).fetchone()
    if u is None:
        abort(404)
    db.execute("UPDATE users SET active = ? WHERE id = ?", (0 if u["active"] else 1, uid))
    db.commit()
    flash(f"{'ระงับ' if u['active'] else 'เปิดใช้งาน'}บัญชีของ {u['name']} แล้ว")
    return redirect(url_for("admin.customer_detail", uid=uid))


@bp.post("/customers/<int:uid>/reset-link")
def customer_reset_link(uid):
    """สร้างลิงก์ตั้งรหัสผ่านใหม่ให้ลูกค้า (ใช้เมื่ออีเมลอัตโนมัติยังไม่ได้ตั้งค่า) แล้วส่งต่อให้ลูกค้าเอง"""
    db = get_db()
    if db.execute("SELECT 1 FROM users WHERE id = ?", (uid,)).fetchone() is None:
        abort(404)
    token = new_reset_token(db, uid, minutes=60)
    flash("ลิงก์ตั้งรหัสผ่านใหม่ (ใช้ได้ 1 ชั่วโมง ส่งให้ลูกค้าทางแชต): "
          + url_for("customers.reset", token=token, _external=True))
    return redirect(url_for("admin.customer_detail", uid=uid))


# ---------- คูปอง ----------
def parse_coupon(form, cid=None):
    errors, d = [], {}
    d["code"] = re.sub(r"\s+", "", form.get("code", "")).upper()
    d["kind"] = form.get("kind") if form.get("kind") in ("percent", "fixed") else "percent"
    if not re.fullmatch(r"[A-Z0-9_-]{3,20}", d["code"]):
        errors.append("โค้ดต้องเป็นตัวอักษรอังกฤษ/ตัวเลข 3–20 ตัว (ห้ามเว้นวรรค)")
    for key, label in (("value", "มูลค่าส่วนลด"), ("min_total", "ยอดซื้อขั้นต่ำ"),
                       ("max_discount", "ส่วนลดสูงสุด"), ("usage_limit", "จำนวนสิทธิ์")):
        raw = form.get(key, "").strip() or "0"
        if raw.isdigit() and int(raw) <= 10_000_000:
            d[key] = int(raw)
        else:
            errors.append(f"{label}ต้องเป็นตัวเลขจำนวนเต็มตั้งแต่ 0 ขึ้นไป")
    if not errors and d["value"] <= 0:
        errors.append("มูลค่าส่วนลดต้องมากกว่า 0")
    if not errors and d["kind"] == "percent" and d["value"] > 100:
        errors.append("ส่วนลดเป็น % ต้องไม่เกิน 100")
    d["expires_at"] = form.get("expires_at", "").strip()
    if d["expires_at"] and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d["expires_at"]):
        errors.append("วันหมดอายุไม่ถูกต้อง")
    d["once_per_user"] = 1 if form.get("once_per_user") else 0
    d["active"] = 1 if form.get("active") else 0
    if not errors:
        dup = get_db().execute("SELECT id FROM coupons WHERE code = ? AND id != ?", (d["code"], cid or 0)).fetchone()
        if dup:
            errors.append("มีโค้ดนี้อยู่แล้ว")
    return d, errors


@bp.route("/coupons")
def coupons():
    rows = get_db().execute("SELECT * FROM coupons ORDER BY active DESC, id DESC").fetchall()
    return render_template("admin/coupons.html", rows=rows, today=(datetime.now(timezone.utc) + timedelta(hours=7)).date().isoformat())


@bp.route("/coupons/new", methods=["GET", "POST"])
@bp.route("/coupons/<int:cid>/edit", methods=["GET", "POST"])
def coupon_form(cid=None):
    db = get_db()
    row = None
    if cid is not None:
        row = db.execute("SELECT * FROM coupons WHERE id = ?", (cid,)).fetchone()
        if row is None:
            abort(404)
    errors, form = [], (dict(row) if row else {"kind": "percent", "once_per_user": 1, "active": 1, "value": 10})
    if request.method == "POST":
        d, errors = parse_coupon(request.form, cid)
        form = d
        if not errors:
            if row is None:
                db.execute("""INSERT INTO coupons (code,kind,value,min_total,max_discount,usage_limit,once_per_user,
                              expires_at,active) VALUES (?,?,?,?,?,?,?,?,?)""",
                           (d["code"], d["kind"], d["value"], d["min_total"], d["max_discount"], d["usage_limit"],
                            d["once_per_user"], d["expires_at"], d["active"]))
            else:
                db.execute("""UPDATE coupons SET code=?, kind=?, value=?, min_total=?, max_discount=?, usage_limit=?,
                              once_per_user=?, expires_at=?, active=? WHERE id=?""",
                           (d["code"], d["kind"], d["value"], d["min_total"], d["max_discount"], d["usage_limit"],
                            d["once_per_user"], d["expires_at"], d["active"], cid))
            db.commit()
            flash(f"บันทึกโค้ด {d['code']} แล้ว")
            return redirect(url_for("admin.coupons"))
    return render_template("admin/coupon_form.html", c=row, form=form, errors=errors)


@bp.post("/coupons/<int:cid>/toggle")
def coupon_toggle(cid):
    db = get_db()
    row = db.execute("SELECT code, active FROM coupons WHERE id = ?", (cid,)).fetchone()
    if row is None:
        abort(404)
    db.execute("UPDATE coupons SET active = ? WHERE id = ?", (0 if row["active"] else 1, cid))
    db.commit()
    flash(f"{'ปิด' if row['active'] else 'เปิด'}ใช้งานโค้ด {row['code']} แล้ว")
    return redirect(url_for("admin.coupons"))


@bp.post("/coupons/<int:cid>/delete")
def coupon_delete(cid):
    db = get_db()
    row = db.execute("SELECT code FROM coupons WHERE id = ?", (cid,)).fetchone()
    if row is None:
        abort(404)
    db.execute("DELETE FROM coupons WHERE id = ?", (cid,))     # ออเดอร์เก่าเก็บรหัสโค้ดเป็นข้อความ ไม่กระทบ
    db.commit()
    flash(f"ลบโค้ด {row['code']} แล้ว")
    return redirect(url_for("admin.coupons"))


# ---------- รีวิว ----------
def back_to_reviews():
    """กลับหน้ารีวิวพร้อมตัวกรองเดิม (ฟอร์มซ่อน/ลบส่ง qs มาด้วย)"""
    qs = request.form.get("qs", "")
    return redirect(url_for("admin.reviews") + (f"?{qs}" if qs else ""))


@bp.route("/reviews")
def reviews():
    db = get_db()
    pid, rating = request.args.get("product", ""), request.args.get("rating", "")
    vis, q = request.args.get("vis", ""), request.args.get("q", "").strip()
    d1, d2 = day_range(request.args)
    where, params = "WHERE 1=1", []
    if pid.isdigit():
        where += " AND r.product_id = ?"
        params.append(int(pid))
    if rating in ("1", "2", "3", "4", "5"):
        where += " AND r.rating = ?"
        params.append(int(rating))
    if vis in ("shown", "hidden"):
        where += " AND r.visible = ?"
        params.append(1 if vis == "shown" else 0)
    if q:
        where += " AND (r.comment LIKE ? OR u.name LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    dsql, dparams = date_clause("r.created_at", d1, d2)
    base = ("FROM reviews r JOIN products p ON p.id = r.product_id JOIN users u ON u.id = r.user_id "
            + where + dsql)
    rows = db.execute("SELECT r.*, p.name AS product, u.name AS author, "
                      "datetime(r.created_at, '+7 hours') AS local_time " + base + " ORDER BY r.id DESC LIMIT 300",
                      params + dparams).fetchall()
    found, avg = db.execute("SELECT COUNT(*), AVG(r.rating) " + base, params + dparams).fetchone()
    # รายการสินค้าสำหรับตัวกรอง: นับรีวิวทั้งหมดของแต่ละชิ้น (ไม่ขึ้นกับตัวกรองอื่น)
    products = db.execute("""SELECT p.id, p.name, COUNT(r.id) AS n, ROUND(AVG(r.rating), 1) AS avg
                             FROM products p LEFT JOIN reviews r ON r.product_id = p.id
                             GROUP BY p.id ORDER BY n DESC, p.name""").fetchall()
    return render_template("admin/reviews.html", rows=rows, products=products, found=found,
                           avg=round(avg, 1) if avg else None, pid=pid if pid.isdigit() else "",
                           rating=rating, vis=vis, q=q, d1=d1.isoformat() if d1 else "",
                           d2=d2.isoformat() if d2 else "", qs=request.query_string.decode(),
                           filtered=any((pid, rating, vis, q, d1, d2)))


@bp.post("/reviews/<int:rid>/toggle")
def review_toggle(rid):
    db = get_db()
    row = db.execute("SELECT visible FROM reviews WHERE id = ?", (rid,)).fetchone()
    if row is None:
        abort(404)
    db.execute("UPDATE reviews SET visible = ? WHERE id = ?", (0 if row["visible"] else 1, rid))
    db.commit()
    flash("ซ่อนรีวิวแล้ว" if row["visible"] else "แสดงรีวิวแล้ว")
    return back_to_reviews()


@bp.post("/reviews/<int:rid>/delete")
def review_delete(rid):
    db = get_db()
    db.execute("DELETE FROM reviews WHERE id = ?", (rid,))
    db.commit()
    flash("ลบรีวิวแล้ว")
    return back_to_reviews()


@bp.post("/settings/test-mail")
def test_mail():
    to = request.form.get("to", "").strip()
    if "@" not in to:
        flash("กรอกอีเมลปลายทางให้ถูกต้อง")
    elif send_mail(to, "ทดสอบอีเมลจาก Anime Figure Shop", "ถ้าคุณอ่านข้อความนี้ แปลว่าตั้งค่าส่งอีเมลสำเร็จแล้ว"):
        flash(f"ส่งอีเมลทดสอบไปที่ {to} แล้ว")
    else:
        flash("ส่งไม่สำเร็จ — ยังไม่ได้ตั้ง SMTP หรือตั้งค่าผิด (ดูข้อความในหน้าจอเซิร์ฟเวอร์)")
    return redirect(url_for("admin.settings"))


# ---------- ตั้งค่าร้าน ----------
@bp.route("/settings", methods=["GET", "POST"])
def settings():
    db, errors = get_db(), []
    if request.method == "POST":
        vals = {}
        for key, label in (("shipping_fee", "ค่าส่ง"), ("free_shipping_min", "ยอดส่งฟรี")):
            raw = request.form.get(key, "").strip()
            if raw.isdigit() and int(raw) <= 1_000_000:
                vals[key] = raw
            else:
                errors.append(f"{label}ต้องเป็นตัวเลขจำนวนเต็มตั้งแต่ 0 ขึ้นไป")
        for k, label in (("contact_email", "อีเมลร้าน"), ("contact_phone", "เบอร์โทรร้าน"),
                         ("contact_line", "LINE ร้าน"), ("contact_hours", "เวลาทำการ")):
            vals[k] = request.form.get(k, "").strip()[:100]
        for k in ("shop_name", "shop_tax_id", "receipt_footer"):
            vals[k] = request.form.get(k, "").strip()[:100]
        vals["chat_enabled"] = "1" if request.form.get("chat_enabled") else "0"
        vals["shop_address"] = request.form.get("shop_address", "").strip()[:300]
        if not vals["shop_name"]:
            vals["shop_name"] = DEFAULT_SETTINGS["shop_name"]
        if vals["contact_email"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", vals["contact_email"]):
            errors.append("อีเมลร้านไม่ถูกต้อง")
        if not errors:
            for k, v in vals.items():
                db.execute("INSERT INTO settings (key, value) VALUES (?,?) "
                           "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (k, v))
            db.commit()
            flash("บันทึกการตั้งค่าแล้ว")
            return redirect(url_for("admin.settings"))
    cur = {k: (get_text_setting(k) if k in TEXT_SETTINGS else get_setting(k))
           for k in DEFAULT_SETTINGS}
    return render_template("admin/settings.html", cur=cur, errors=errors,
                           smtp=bool(os.environ.get("SMTP_HOST")), ai=chat.ai_configured(), provider=chat.ai_provider(), ai_error=chat.AI_STATE["error"], ai_model=chat.AI_STATE["model"] or os.environ.get("GEMINI_MODEL", ""))


import reports
reports.register(bp)    # หน้า "รายงานยอดขาย"
chat.register_admin(bp)    # หน้า "แชทลูกค้า" ของแอดมิน (ต้องเรียกก่อน app.register_blueprint)
