"""ระบบสมาชิกลูกค้า (Flask Blueprint)
สมัคร/ล็อกอิน/ออกจากระบบ/ลืมรหัสผ่าน · โปรไฟล์ · เปลี่ยนรหัสผ่าน · สมุดที่อยู่
ประวัติและยกเลิกออเดอร์ · รายการโปรด · รีวิวสินค้า · ลบบัญชี"""
from datetime import datetime, timezone

from flask import (Blueprint, abort, flash, jsonify, redirect, render_template, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from common import (get_text_setting, EMAIL_RE, cancel_order, current_user, login_required, merge_saved_cart,
                    new_reset_token, notify_order, owns_order, safe_next, send_mail, throttle_clear,
                    throttle_fail, throttled, user_by_reset_token)
from db import get_db

bp = Blueprint("customers", __name__)


def start_session(user):
    """ออก session ใหม่ทุกครั้งที่ล็อกอิน (กัน session fixation) แต่เก็บตะกร้า/ออเดอร์ guest/แอดมินไว้"""
    keep = {k: session[k] for k in ("cart", "my_orders", "admin") if k in session}
    session.clear()
    session.update(keep)
    session["uid"] = user["id"]
    session.permanent = True
    merge_saved_cart(user)
    db = get_db()
    db.execute("UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?", (user["id"],))
    db.commit()


def check_password(pw, again=None):
    errors = []
    if len(pw) < 8:
        errors.append("รหัสผ่านต้องยาวอย่างน้อย 8 ตัวอักษร")
    if pw.isdigit() or pw.isalpha():
        errors.append("รหัสผ่านควรผสมตัวอักษรและตัวเลข")
    if again is not None and pw != again:
        errors.append("ยืนยันรหัสผ่านไม่ตรงกัน")
    return errors


def clean_phone(raw):
    return "".join(ch for ch in raw if ch.isdigit())


# ---------- สมัครสมาชิก / ล็อกอิน ----------
@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user():
        return redirect(url_for("customers.account"))
    errors, form = [], {}
    if request.method == "POST":
        form = request.form
        email = form.get("email", "").strip().lower()
        name = form.get("name", "").strip()
        phone = clean_phone(form.get("phone", ""))
        pw = form.get("password", "")
        if len(name) < 2 or len(name) > 80:
            errors.append("กรอกชื่อ-นามสกุล (2–80 ตัวอักษร)")
        if not EMAIL_RE.match(email) or len(email) > 120:
            errors.append("อีเมลไม่ถูกต้อง")
        if phone and len(phone) != 10:
            errors.append("เบอร์โทรต้องเป็นตัวเลข 10 หลัก (หรือเว้นว่างไว้)")
        errors += check_password(pw, form.get("again", ""))
        if not form.get("accept"):
            errors.append("กรุณายอมรับข้อตกลงการใช้งานและนโยบายความเป็นส่วนตัว")
        db = get_db()
        if not errors and db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            errors.append("อีเมลนี้สมัครสมาชิกไว้แล้ว ลองเข้าสู่ระบบหรือกด “ลืมรหัสผ่าน”")
        if not errors:
            cur = db.execute("INSERT INTO users (email, password_hash, name, phone, consent_at) VALUES (?,?,?,?,?)",
                             (email, generate_password_hash(pw), name, phone,
                              datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")))   # เก็บเวลายินยอมเป็นหลักฐาน
            db.commit()
            user = db.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
            start_session(user)
            flash("สมัครสมาชิกสำเร็จ ยินดีต้อนรับ! ลองใช้โค้ด WELCOME10 ลด 10% (ขั้นต่ำ ฿500) ได้เลย")
            return redirect(safe_next(request.args.get("next"), url_for("customers.account")))
    return render_template("register.html", errors=errors, form=form,
                           shop_name_text=get_text_setting("shop_name"))


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(safe_next(request.args.get("next"), url_for("customers.account")))
    error, email = None, ""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        key = f"user:{request.remote_addr}:{email}"
        if throttled(key):
            error = "ใส่รหัสผิดหลายครั้งเกินไป กรุณารอ 10 นาทีแล้วลองใหม่ หรือกด “ลืมรหัสผ่าน”"
        else:
            user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
                if not user["active"]:
                    error = "บัญชีนี้ถูกระงับการใช้งาน กรุณาติดต่อร้านค้า"
                else:
                    throttle_clear(key)
                    start_session(user)
                    return redirect(safe_next(request.args.get("next"), url_for("index")))
            else:
                throttle_fail(key)
                error = "อีเมลหรือรหัสผ่านไม่ถูกต้อง"
    return render_template("login.html", error=error, email=email)


@bp.post("/logout")
def logout():
    session.pop("uid", None)
    session.pop("cart", None)      # ตะกร้าถูกบันทึกไว้กับบัญชีแล้ว ล้างเพื่อไม่ให้ค้างบนเครื่องสาธารณะ
    flash("ออกจากระบบแล้ว")
    return redirect(url_for("index"))


# ---------- ลืมรหัสผ่าน ----------
@bp.route("/forgot", methods=["GET", "POST"])
def forgot():
    sent = False
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        key = f"forgot:{request.remote_addr}"
        if not throttled(key):
            throttle_fail(key)           # จำกัดจำนวนครั้งที่ขอลิงก์ต่อ IP
            db = get_db()
            user = db.execute("SELECT * FROM users WHERE email = ? AND active = 1", (email,)).fetchone()
            if user:
                token = new_reset_token(db, user["id"])
                link = url_for("customers.reset", token=token, _external=True)
                send_mail(user["email"], "ตั้งรหัสผ่านใหม่ — Anime Figure Shop",
                          f"สวัสดีคุณ {user['name']}\n\nกดลิงก์นี้เพื่อตั้งรหัสผ่านใหม่ (ใช้ได้ 1 ชั่วโมง):\n{link}\n\n"
                          "ถ้าไม่ได้เป็นคนขอ ไม่ต้องทำอะไร รหัสผ่านเดิมยังใช้ได้ตามปกติ")
        sent = True       # ตอบเหมือนกันเสมอ เพื่อไม่ให้คนนอกใช้เช็กว่าอีเมลไหนเป็นสมาชิก
    return render_template("forgot.html", sent=sent)


@bp.route("/reset/<token>", methods=["GET", "POST"])
def reset(token):
    user = user_by_reset_token(token)
    if user is None:
        return render_template("reset.html", invalid=True, errors=[])
    errors = []
    if request.method == "POST":
        pw = request.form.get("password", "")
        errors = check_password(pw, request.form.get("again", ""))
        if not errors:
            db = get_db()
            db.execute("UPDATE users SET password_hash = ?, reset_hash = '', reset_expires = 0 WHERE id = ?",
                       (generate_password_hash(pw), user["id"]))
            db.commit()
            flash("ตั้งรหัสผ่านใหม่แล้ว เข้าสู่ระบบด้วยรหัสใหม่ได้เลย")
            return redirect(url_for("customers.login"))
    return render_template("reset.html", invalid=False, errors=errors)


# ---------- บัญชีของฉัน ----------
@bp.route("/account")
@login_required
def account():
    user, db = current_user(), get_db()
    recent = db.execute("SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT 3", (user["id"],)).fetchall()
    stats = db.execute("""SELECT COUNT(*) AS n,
                                 COALESCE(SUM(CASE WHEN status IN ('paid','shipped') THEN total END), 0) AS spent
                          FROM orders WHERE user_id = ?""", (user["id"],)).fetchone()
    wish = db.execute("SELECT COUNT(*) FROM wishlist WHERE user_id = ?", (user["id"],)).fetchone()[0]
    addr = db.execute("SELECT * FROM addresses WHERE user_id = ? ORDER BY is_default DESC, id LIMIT 1",
                      (user["id"],)).fetchone()
    return render_template("account/home.html", recent=recent, stats=stats, wish=wish, addr=addr, tab="home")


@bp.route("/account/profile", methods=["GET", "POST"])
@login_required
def profile():
    user, errors = current_user(), []
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        phone = clean_phone(request.form.get("phone", ""))
        email = request.form.get("email", "").strip().lower()
        db = get_db()
        if len(name) < 2 or len(name) > 80:
            errors.append("กรอกชื่อ-นามสกุล (2–80 ตัวอักษร)")
        if phone and len(phone) != 10:
            errors.append("เบอร์โทรต้องเป็นตัวเลข 10 หลัก (หรือเว้นว่างไว้)")
        if not EMAIL_RE.match(email):
            errors.append("อีเมลไม่ถูกต้อง")
        elif email != user["email"].lower():
            if db.execute("SELECT 1 FROM users WHERE email = ? AND id != ?", (email, user["id"])).fetchone():
                errors.append("อีเมลนี้มีสมาชิกอื่นใช้อยู่แล้ว")
            elif not check_password_hash(user["password_hash"], request.form.get("current", "")):
                errors.append("การเปลี่ยนอีเมลต้องใส่รหัสผ่านปัจจุบันเพื่อยืนยัน")
        if not errors:
            db.execute("UPDATE users SET name = ?, phone = ?, email = ? WHERE id = ?",
                       (name, phone, email, user["id"]))
            db.commit()
            flash("บันทึกข้อมูลส่วนตัวแล้ว")
            return redirect(url_for("customers.profile"))
        user = {**dict(user), "name": name, "phone": phone, "email": email}
    return render_template("account/profile.html", u=user, errors=errors, tab="profile")


@bp.route("/account/password", methods=["GET", "POST"])
@login_required
def change_password():
    user, errors = current_user(), []
    if request.method == "POST":
        cur, new = request.form.get("current", ""), request.form.get("password", "")
        if not check_password_hash(user["password_hash"], cur):
            errors.append("รหัสผ่านปัจจุบันไม่ถูกต้อง")
        errors += check_password(new, request.form.get("again", ""))
        if new and new == cur:
            errors.append("รหัสผ่านใหม่ต้องไม่ซ้ำกับรหัสเดิม")
        if not errors:
            db = get_db()
            db.execute("UPDATE users SET password_hash = ?, reset_hash = '', reset_expires = 0 WHERE id = ?",
                       (generate_password_hash(new), user["id"]))
            db.commit()
            flash("เปลี่ยนรหัสผ่านแล้ว")
            return redirect(url_for("customers.change_password"))
    return render_template("account/password.html", errors=errors, tab="password")


@bp.post("/account/delete")
@login_required
def delete_account():
    user, db = current_user(), get_db()
    if not check_password_hash(user["password_hash"], request.form.get("current", "")):
        flash("รหัสผ่านไม่ถูกต้อง ลบบัญชีไม่สำเร็จ")
        return redirect(url_for("customers.profile"))
    db.execute("UPDATE orders SET user_id = NULL WHERE user_id = ?", (user["id"],))   # ประวัติการขายของร้านยังอยู่
    for table in ("reviews", "wishlist", "addresses"):
        db.execute(f"DELETE FROM {table} WHERE user_id = ?", (user["id"],))
    db.execute("DELETE FROM users WHERE id = ?", (user["id"],))
    db.commit()
    session.pop("uid", None)
    session.pop("cart", None)
    flash("ลบบัญชีและข้อมูลส่วนตัวของคุณแล้ว")
    return redirect(url_for("index"))


# ---------- สมุดที่อยู่ ----------
def parse_address(form):
    d = {"label": form.get("label", "").strip()[:30],
         "recipient": form.get("recipient", "").strip(),
         "phone": clean_phone(form.get("phone", "")),
         "address": form.get("address", "").strip()}
    errors = []
    if len(d["recipient"]) < 2:
        errors.append("กรอกชื่อผู้รับ")
    if len(d["phone"]) != 10:
        errors.append("เบอร์โทรต้องเป็นตัวเลข 10 หลัก")
    if len(d["address"]) < 10:
        errors.append("กรอกที่อยู่จัดส่งให้ครบ")
    return d, errors


def _set_default(db, uid, aid):
    db.execute("UPDATE addresses SET is_default = (id = ?) WHERE user_id = ?", (aid, uid))


@bp.route("/account/addresses", methods=["GET", "POST"])
@bp.route("/account/addresses/<int:aid>/edit", methods=["GET", "POST"])
@login_required
def addresses(aid=None):
    user, db = current_user(), get_db()
    editing = None
    if aid is not None:
        editing = db.execute("SELECT * FROM addresses WHERE id = ? AND user_id = ?", (aid, user["id"])).fetchone()
        if editing is None:
            abort(404)
    errors, form = [], (dict(editing) if editing else {})
    if request.method == "POST":
        data, errors = parse_address(request.form)
        form = data
        if not errors and editing is None and db.execute(
                "SELECT COUNT(*) FROM addresses WHERE user_id = ?", (user["id"],)).fetchone()[0] >= 10:
            errors.append("เก็บที่อยู่ได้สูงสุด 10 รายการ ลบรายการเก่าก่อนนะ")
        if not errors:
            if editing is None:
                cur = db.execute("INSERT INTO addresses (user_id,label,recipient,phone,address) VALUES (?,?,?,?,?)",
                                 (user["id"], data["label"], data["recipient"], data["phone"], data["address"]))
                target = cur.lastrowid
                first = db.execute("SELECT COUNT(*) FROM addresses WHERE user_id = ?", (user["id"],)).fetchone()[0] == 1
                if first or request.form.get("is_default"):
                    _set_default(db, user["id"], target)
                flash("เพิ่มที่อยู่แล้ว")
            else:
                db.execute("UPDATE addresses SET label=?, recipient=?, phone=?, address=? WHERE id = ?",
                           (data["label"], data["recipient"], data["phone"], data["address"], editing["id"]))
                if request.form.get("is_default"):
                    _set_default(db, user["id"], editing["id"])
                flash("บันทึกที่อยู่แล้ว")
            db.commit()
            return redirect(url_for("customers.addresses"))
    rows = db.execute("SELECT * FROM addresses WHERE user_id = ? ORDER BY is_default DESC, id", (user["id"],)).fetchall()
    return render_template("account/addresses.html", rows=rows, editing=editing, form=form, errors=errors, tab="addresses")


@bp.post("/account/addresses/<int:aid>/default")
@login_required
def address_default(aid):
    db, user = get_db(), current_user()
    if db.execute("SELECT 1 FROM addresses WHERE id = ? AND user_id = ?", (aid, user["id"])).fetchone() is None:
        abort(404)
    _set_default(db, user["id"], aid)
    db.commit()
    flash("ตั้งเป็นที่อยู่หลักแล้ว")
    return redirect(url_for("customers.addresses"))


@bp.post("/account/addresses/<int:aid>/delete")
@login_required
def address_delete(aid):
    db, user = get_db(), current_user()
    row = db.execute("SELECT * FROM addresses WHERE id = ? AND user_id = ?", (aid, user["id"])).fetchone()
    if row is None:
        abort(404)
    db.execute("DELETE FROM addresses WHERE id = ?", (aid,))
    if row["is_default"]:      # ลบที่อยู่หลัก → ให้รายการที่เหลืออันแรกเป็นที่อยู่หลักแทน
        nxt = db.execute("SELECT id FROM addresses WHERE user_id = ? ORDER BY id LIMIT 1", (user["id"],)).fetchone()
        if nxt:
            _set_default(db, user["id"], nxt["id"])
    db.commit()
    flash("ลบที่อยู่แล้ว")
    return redirect(url_for("customers.addresses"))


# ---------- ประวัติออเดอร์ ----------
@bp.route("/account/orders")
@login_required
def my_orders():
    user, db = current_user(), get_db()
    orders = db.execute("""
        SELECT o.*, datetime(o.created_at, '+7 hours') AS local_time,
               (SELECT COALESCE(SUM(qty), 0) FROM order_items WHERE order_id = o.id) AS items
        FROM orders o WHERE o.user_id = ? ORDER BY o.id DESC""", (user["id"],)).fetchall()
    return render_template("account/orders.html", orders=orders, tab="orders")


@bp.post("/order/<int:oid>/cancel")
def order_cancel(oid):
    """ลูกค้ายกเลิกออเดอร์ของตัวเองได้ ตราบที่ยังไม่ชำระเงิน (คืนสต็อกและสิทธิ์คูปองอัตโนมัติ)"""
    db = get_db()
    order = db.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone()
    if not owns_order(order):
        abort(404)
    if order["status"] != "pending":
        flash("ยกเลิกเองได้เฉพาะออเดอร์ที่ยังไม่ชำระเงิน หากต้องการยกเลิกกรุณาติดต่อร้าน")
    else:
        cancel_order(db, oid)
        notify_order(db, oid, "cancelled")
        flash(f"ยกเลิกออเดอร์ #{oid:05d} แล้ว")
    return redirect(url_for("order_done", oid=oid))


# ---------- รายการโปรด ----------
@bp.route("/account/wishlist")
@login_required
def wishlist():
    rows = get_db().execute("""
        SELECT p.* FROM wishlist w JOIN products p ON p.id = w.product_id
        WHERE w.user_id = ? AND p.active = 1 ORDER BY w.created_at DESC""", (current_user()["id"],)).fetchall()
    return render_template("account/wishlist.html", products=rows, tab="wishlist")


@bp.post("/wishlist/toggle/<int:pid>")
def wishlist_toggle(pid):
    user = current_user()
    nxt = safe_next(request.form.get("next"), url_for("index"))
    wants_json = request.headers.get("Accept", "").startswith("application/json")
    if user is None:
        if wants_json:
            return jsonify(ok=False, login_url=url_for("customers.login", next=nxt),
                           message="เข้าสู่ระบบก่อนจึงจะกดถูกใจสินค้าได้"), 401
        flash("เข้าสู่ระบบก่อนจึงจะกดถูกใจสินค้าได้")
        return redirect(url_for("customers.login", next=nxt))
    db = get_db()
    if db.execute("SELECT 1 FROM products WHERE id = ? AND active = 1", (pid,)).fetchone() is None:
        abort(404)
    added = db.execute("DELETE FROM wishlist WHERE user_id = ? AND product_id = ?", (user["id"], pid)).rowcount == 0
    if added:
        db.execute("INSERT INTO wishlist (user_id, product_id) VALUES (?,?)", (user["id"], pid))
    db.commit()
    msg = "เพิ่มในรายการโปรดแล้ว" if added else "เอาออกจากรายการโปรดแล้ว"
    if wants_json:       # JavaScript เรียก: ตอบเป็น JSON ไม่ต้องโหลดหน้าใหม่ (หน้าฟีดไม่รีเฟรช)
        return jsonify(ok=True, on=added, message=msg)
    flash(msg)
    return redirect(nxt)


# ---------- รีวิวสินค้า ----------
@bp.post("/product/<int:pid>/review")
@login_required
def review(pid):
    user, db = current_user(), get_db()
    if db.execute("SELECT 1 FROM products WHERE id = ? AND active = 1", (pid,)).fetchone() is None:
        abort(404)
    back = redirect(url_for("product", pid=pid) + "#reviews")
    bought = db.execute("""SELECT 1 FROM order_items oi JOIN orders o ON o.id = oi.order_id
                           WHERE o.user_id = ? AND oi.product_id = ? AND o.status IN ('paid','shipped')""",
                        (user["id"], pid)).fetchone()
    if not bought:
        flash("รีวิวได้เฉพาะลูกค้าที่ซื้อสินค้านี้และชำระเงินแล้ว")
        return back
    rating = request.form.get("rating", "")
    comment = request.form.get("comment", "").strip()[:1000]
    if rating not in ("1", "2", "3", "4", "5"):
        flash("เลือกคะแนน 1–5 ดาว")
        return back
    db.execute("""INSERT INTO reviews (product_id, user_id, rating, comment) VALUES (?,?,?,?)
                  ON CONFLICT(product_id, user_id) DO UPDATE SET rating = excluded.rating,
                  comment = excluded.comment, created_at = CURRENT_TIMESTAMP, visible = 1""",
               (pid, user["id"], int(rating), comment))
    db.commit()
    flash("ขอบคุณสำหรับรีวิว!")
    return back


@bp.post("/review/<int:rid>/delete")
@login_required
def review_delete(rid):
    db, user = get_db(), current_user()
    row = db.execute("SELECT product_id FROM reviews WHERE id = ? AND user_id = ?", (rid, user["id"])).fetchone()
    if row is None:
        abort(404)
    db.execute("DELETE FROM reviews WHERE id = ?", (rid,))
    db.commit()
    flash("ลบรีวิวแล้ว")
    return redirect(url_for("product", pid=row["product_id"]) + "#reviews")
