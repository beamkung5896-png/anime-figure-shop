"""Anime Figure Shop - เว็บแอปร้านขายโมเดลอนิเมะด้วย Flask + SQLite
รัน:  python app.py
  หน้าร้าน   http://127.0.0.1:5000
  หลังบ้าน   http://127.0.0.1:5000/admin   (เริ่มต้น: admin / admin1234 — เปลี่ยนรหัสผ่านทันทีหลังเข้าสู่ระบบ)
"""
import os
import secrets
from datetime import datetime, timedelta, timezone

from flask import (Flask, abort, flash, jsonify, make_response, redirect, render_template,
                   request, send_from_directory, session, url_for)

import admin
import chat
import customers
from common import (EMAIL_RE, get_setting, get_text_setting, shop_contact, csrf_ok, csrf_token, current_user, notify_order, owns_order,
                    price_order, save_cart)
from db import (APPROVE_MINUTES, BASE_DIR, REQUIRE_LOGIN_TO_CHECKOUT, SLIP_DIR, STATUSES, close_db, get_db,
                init_db, payment_qr_file)


def load_secret_key():
    """ใช้ SECRET_KEY จากตัวแปรสภาพแวดล้อม ถ้าไม่มีให้สร้างไฟล์ secret.key เก็บไว้ (ห้ามอัปโหลดขึ้น Git)"""
    if os.environ.get("SECRET_KEY"):
        return os.environ["SECRET_KEY"]
    path = os.path.join(BASE_DIR, "secret.key")
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(secrets.token_hex(32))
    with open(path) as f:
        return f.read().strip()


app = Flask(__name__)
app.secret_key = load_secret_key()
app.permanent_session_lifetime = timedelta(hours=8)
app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024      # อัปโหลดรูปได้ไม่เกิน ~5MB
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.teardown_appcontext(close_db)
app.register_blueprint(admin.bp)
app.register_blueprint(customers.bp)
app.register_blueprint(chat.bp)


@app.before_request
def csrf_guard():
    """ทุกฟอร์ม/คำขอ POST ของหน้าร้านต้องมี CSRF token (หลังบ้านตรวจในตัวเองอยู่แล้ว)"""
    if request.method == "POST" and request.blueprint != "admin" and not csrf_ok():
        abort(400)


# ---------- ตะกร้าสินค้า (เก็บใน session) ----------
def cart_items():
    """คืนรายการในตะกร้าพร้อมข้อมูลสินค้า และยอดรวม"""
    cart = session.get("cart", {})
    items, total = [], 0
    if cart:
        marks = ",".join("?" * len(cart))
        rows = get_db().execute(
            f"SELECT * FROM products WHERE active = 1 AND id IN ({marks})", list(cart)).fetchall()
        for row in rows:
            qty = cart[str(row["id"])]
            subtotal = qty * row["price"]
            total += subtotal
            items.append({"product": row, "qty": qty, "subtotal": subtotal})
    return items, total


def cart_count():
    return sum(session.get("cart", {}).values())


def wished_ids():
    user = current_user()
    if user is None:
        return set()
    return {r[0] for r in get_db().execute("SELECT product_id FROM wishlist WHERE user_id = ?", (user["id"],))}


RATING_SQL = ("(SELECT ROUND(AVG(rating), 1) FROM reviews r WHERE r.product_id = p.id AND r.visible = 1) AS avg_rating, "
              "(SELECT COUNT(*) FROM reviews r WHERE r.product_id = p.id AND r.visible = 1) AS review_count")


@app.context_processor
def inject_globals():
    qr = payment_qr_file()
    return {"cart_count": cart_count(), "user": current_user(), "csrf_token": csrf_token,
            "wished": wished_ids(),
            "payment_qr": url_for("static", filename="payment/" + qr) if qr else None}


@app.context_processor
def inject_shop():
    return {"shop": shop_contact(), "chat_enabled": get_setting("chat_enabled") == 1,
            "approve_minutes": APPROVE_MINUTES}


@app.route("/privacy")
def privacy():
    back = url_for("customers.register") if request.args.get("from") == "register" else url_for("checkout")
    return render_template("privacy.html", shop_name=get_text_setting("shop_name") or "Anime Figure Shop",
                           back=back, back_label="กลับไปหน้าสมัครสมาชิก" if "register" in back else "กลับไปหน้าชำระเงิน")


@app.route("/contact")
def contact():
    return render_template("contact.html")


@app.template_filter("baht")
def baht(n):
    return f"฿{n:,}"


@app.template_filter("scale_label")
def scale_label(v):
    """'1/7' -> 'สเกล 1/7', ส่วน 'ไดโอราม่า' แสดงตามที่เก็บไว้"""
    return f"สเกล {v}" if v[:1].isdigit() else v


@app.template_filter("status_label")
def status_label(s):
    return STATUSES.get(s, s)


# ---------- หน้าร้าน ----------
@app.route("/")
def index():
    db = get_db()
    categories = ["ทั้งหมด"] + [r[0] for r in db.execute(
        "SELECT DISTINCT category FROM products WHERE active = 1 ORDER BY category")]
    cat = request.args.get("cat", "ทั้งหมด")
    q = request.args.get("q", "").strip()
    wish = bool(request.args.get("wish"))          # ?wish=1 = แสดงเฉพาะรายการโปรดของฉัน
    user = current_user()
    if wish and user is None:
        flash("เข้าสู่ระบบก่อนจึงจะดูรายการโปรดได้")
        return redirect(url_for("customers.login", next=url_for("index", wish=1)))
    sql, params = f"SELECT p.*, {RATING_SQL} FROM products p WHERE p.active = 1", []
    if wish:
        sql += " AND p.id IN (SELECT product_id FROM wishlist WHERE user_id = ?)"
        params.append(user["id"])
    if cat != "ทั้งหมด":
        sql += " AND category = ?"
        params.append(cat)
    if q:
        sql += " AND (name LIKE ? OR description LIKE ?)"
        params += [f"%{q}%", f"%{q}%"]
    sql += " ORDER BY id"
    products = db.execute(sql, params).fetchall()
    # ชิ้นเด่นบนแบนเนอร์: ตัวที่ราคาสูงสุดในบรรดาของที่ยังมีสต็อก
    featured = db.execute(
        "SELECT * FROM products WHERE active = 1 AND stock > 0 ORDER BY price DESC LIMIT 1").fetchone()
    # ป้าย "ขายดี": 3 อันดับแรกที่ชำระเงินแล้วใน 90 วันล่าสุด (ต้องมียอดขายจริงอย่างน้อย 1 ชิ้น)
    hot = {r[0] for r in db.execute("""
        SELECT oi.product_id FROM order_items oi JOIN orders o ON o.id = oi.order_id
        WHERE o.status IN ('paid','shipped') AND date(o.created_at, '+7 hours') >= date('now', '+7 hours', '-90 days')
        GROUP BY oi.product_id HAVING SUM(oi.qty) >= 1 ORDER BY SUM(oi.qty) DESC LIMIT 3""")}
    return render_template("index.html", products=products, categories=categories,
                           cat=cat, q=q, featured=featured, hot=hot, wish=wish)


@app.route("/product/<int:pid>")
def product(pid):
    row = get_db().execute("SELECT * FROM products WHERE id = ? AND active = 1", (pid,)).fetchone()
    if row is None:
        abort(404)
    db = get_db()
    row = db.execute(f"SELECT p.*, {RATING_SQL} FROM products p WHERE p.id = ?", (pid,)).fetchone()
    reviews = db.execute("""SELECT r.*, u.name, date(r.created_at, '+7 hours') AS day FROM reviews r
                            JOIN users u ON u.id = r.user_id WHERE r.product_id = ? AND r.visible = 1
                            ORDER BY r.id DESC""", (pid,)).fetchall()
    user, mine, can_review = current_user(), None, False
    if user:
        mine = db.execute("SELECT * FROM reviews WHERE product_id = ? AND user_id = ?", (pid, user["id"])).fetchone()
        can_review = bool(db.execute("""SELECT 1 FROM order_items oi JOIN orders o ON o.id = oi.order_id
                                        WHERE o.user_id = ? AND oi.product_id = ? AND o.status IN ('paid','shipped')""",
                                     (user["id"], pid)).fetchone())
    return render_template("product.html", p=row, reviews=reviews, mine=mine, can_review=can_review)


@app.route("/cart")
def cart():
    items, total = cart_items()
    return render_template("cart.html", items=items, total=total)


@app.post("/api/cart/add/<int:pid>")
def api_cart_add(pid):
    """JavaScript เรียกจุดนี้เพื่อเพิ่มสินค้าโดยไม่ต้องโหลดหน้าใหม่"""
    row = get_db().execute("SELECT stock FROM products WHERE id = ? AND active = 1", (pid,)).fetchone()
    if row is None:
        return jsonify(ok=False, message="ไม่พบสินค้า"), 404
    if row["stock"] <= 0:
        return jsonify(ok=False, message="สินค้าหมดแล้ว"), 400
    raw = request.json.get("qty", 1) if request.is_json and isinstance(request.json, dict) else 1
    if isinstance(raw, str) and raw.strip().lstrip("-").isdigit():
        raw = int(raw)
    qty = raw if isinstance(raw, int) and not isinstance(raw, bool) else 0    # ทศนิยม/ข้อความ/null = ปฏิเสธ
    if qty < 1:      # ใส่ 0 / ติดลบ (เช่น -1) / ไม่ใช่ตัวเลข = ปฏิเสธ ไม่แอบปัดเป็น 1
        return jsonify(ok=False, message="จำนวนต้องเป็นตัวเลขตั้งแต่ 1 ขึ้นไป"), 400
    cart = session.get("cart", {})
    new_qty = cart.get(str(pid), 0) + qty
    if new_qty > row["stock"]:
        return jsonify(ok=False, message=f"สินค้าเหลือ {row['stock']} ชิ้น เพิ่มในตะกร้าไม่ได้อีก"), 400
    cart[str(pid)] = new_qty
    session["cart"] = cart
    save_cart()
    return jsonify(ok=True, count=cart_count(), message="ใส่ตะกร้าแล้ว")


@app.post("/cart/update/<int:pid>")
def cart_update(pid):
    try:
        qty = int(request.form.get("qty", ""))
    except ValueError:
        qty = -1
    if qty < 0:      # ติดลบหรือไม่ใช่ตัวเลข: ไม่แตะตะกร้า (ใส่ 0 = ลบสินค้าออก)
        flash("จำนวนสินค้าไม่ถูกต้อง")
        return redirect(url_for("cart"))
    cart = session.get("cart", {})
    row = get_db().execute("SELECT stock FROM products WHERE id = ? AND active = 1", (pid,)).fetchone()
    if qty == 0 or row is None or row["stock"] <= 0:
        cart.pop(str(pid), None)
    else:
        if qty > row["stock"]:
            flash(f"สินค้าเหลือ {row['stock']} ชิ้น เพิ่มได้ไม่เกินนี้")
        cart[str(pid)] = min(qty, row["stock"])
    session["cart"] = cart
    save_cart()
    return redirect(url_for("cart"))


@app.route("/checkout", methods=["GET", "POST"])
def checkout():
    user = current_user()
    if REQUIRE_LOGIN_TO_CHECKOUT and user is None:
        flash("กรุณาเข้าสู่ระบบหรือสมัครสมาชิกก่อนสั่งซื้อ")
        return redirect(url_for("customers.login", next=url_for("checkout")))
    items, subtotal = cart_items()
    if not items:
        flash("ตะกร้ายังว่างอยู่ เลือกฟิกเกอร์ก่อนนะ")
        return redirect(url_for("index"))
    db = get_db()
    saved = []
    if user:
        saved = db.execute("SELECT * FROM addresses WHERE user_id = ? ORDER BY is_default DESC, id",
                           (user["id"],)).fetchall()
    uid = user["id"] if user else None
    form = request.form if request.method == "POST" else {}
    if request.method == "GET":
        first = saved[0] if saved else None
        form = {"address_id": str(first["id"]) if first else "new",
                "customer": user["name"] if user else "", "phone": (user["phone"] if user else "")}
    code = (form.get("coupon") or "").strip()
    pricing = price_order(subtotal, code, uid)

    def page(errors=()):
        return render_template("checkout.html", items=items, errors=list(errors), form=form,
                               saved=saved, pr=pricing, code=code)

    if request.method == "POST":
        if form.get("action") == "apply":        # กดปุ่ม "ใช้โค้ด" แค่คำนวณยอดใหม่ ยังไม่สั่งซื้อ
            return page()
        # สต็อกอาจเปลี่ยนระหว่างที่ลูกค้าเลือกของ (เช่น คนอื่นซื้อไปก่อน) ต้องเช็กอีกรอบ
        short = [it for it in items if it["qty"] > it["product"]["stock"]]
        if short:
            for it in short:
                flash(f"{it['product']['name']} เหลือ {it['product']['stock']} ชิ้น กรุณาปรับจำนวนในตะกร้า")
            return redirect(url_for("cart"))
        errors = []
        if not form.get("consent"):
            errors.append("กรุณาติ๊กยอมรับนโยบายความเป็นส่วนตัวและเงื่อนไขการสั่งซื้อก่อนยืนยันสั่งซื้อ")
        if code and pricing["coupon_error"]:
            errors.append(pricing["coupon_error"])
        chosen = None
        if form.get("address_id", "new") != "new":
            chosen = next((a for a in saved if str(a["id"]) == form.get("address_id")), None)
        if chosen:
            name, phone, address = chosen["recipient"], chosen["phone"], chosen["address"]
        else:
            name = form.get("customer", "").strip()
            phone = form.get("phone", "").strip()
            address = form.get("address", "").strip()
            if len(name) < 2:
                errors.append("กรอกชื่อผู้รับ")
            if not (phone.isdigit() and len(phone) == 10):
                errors.append("เบอร์โทรต้องเป็นตัวเลข 10 หลัก")
            if len(address) < 10:
                errors.append("กรอกที่อยู่จัดส่งให้ครบ")
        email = user["email"] if user else form.get("email", "").strip().lower()
        if email and not EMAIL_RE.match(email):
            errors.append("อีเมลไม่ถูกต้อง (หรือเว้นว่างไว้ถ้าไม่ต้องการรับอีเมลแจ้งสถานะ)")
        if errors:
            return page(errors)
        coupon = pricing["coupon"]
        cur = db.execute("""INSERT INTO orders (customer,phone,address,total,user_id,shipping,discount,coupon,email,vat,consent_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                         (name, phone, address, pricing["total"], uid, pricing["shipping"], pricing["discount"],
                          coupon["code"] if coupon else "", email, pricing["vat"],
                          datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")))   # เก็บเวลาที่ลูกค้ากดยินยอมไว้เป็นหลักฐาน
        order_id = cur.lastrowid
        problem = None
        for it in items:
            # ตัดสต็อกแบบมีเงื่อนไข: ถ้ามีคนซื้อตัดหน้าไปแล้วจะตัดไม่สำเร็จ ไม่ปล่อยให้สต็อกติดลบ
            ok = db.execute("UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ? AND active = 1",
                            (it["qty"], it["product"]["id"], it["qty"])).rowcount
            if not ok:
                problem = f"{it['product']['name']} เหลือไม่พอแล้ว กรุณาปรับจำนวนในตะกร้า"
                break
            db.execute("INSERT INTO order_items (order_id,product_id,qty,unit_price) VALUES (?,?,?,?)",
                       (order_id, it["product"]["id"], it["qty"], it["product"]["price"]))
        if not problem and coupon:
            ok = db.execute("UPDATE coupons SET used = used + 1 WHERE id = ? AND (usage_limit = 0 OR used < usage_limit)",
                            (coupon["id"],)).rowcount
            if not ok:
                problem = "โค้ดส่วนลดถูกใช้ครบสิทธิ์พอดี กรุณาตรวจสอบยอดอีกครั้ง"
        if problem:
            db.rollback()
            flash(problem)
            return redirect(url_for("cart"))
        if user and form.get("save_address") and not chosen:
            has = db.execute("SELECT COUNT(*) FROM addresses WHERE user_id = ?", (uid,)).fetchone()[0]
            if has < 10:
                db.execute("INSERT INTO addresses (user_id,label,recipient,phone,address,is_default) VALUES (?,?,?,?,?,?)",
                           (uid, "", name, phone, address, 0 if has else 1))
        if user and not user["phone"]:
            db.execute("UPDATE users SET phone = ? WHERE id = ?", (phone, uid))
        db.commit()
        notify_order(db, order_id, "placed")
        session.pop("cart", None)
        save_cart()
        # จำเลขออเดอร์ไว้ใน session ด้วย (ใช้กับ guest และเป็นอีกชั้นกันคนเดาเลขแล้วเปิดดูที่อยู่คนอื่น)
        session["my_orders"] = (session.get("my_orders", []) + [order_id])[-20:]
        return redirect(url_for("order_done", oid=order_id, new=1))
    return page()


@app.route("/order/<int:oid>")
def order_done(oid):
    order = get_db().execute("SELECT *, datetime(created_at, '+7 hours') AS local_time FROM orders WHERE id = ?",
                             (oid,)).fetchone()
    if not owns_order(order):
        abort(404)
    lines = get_db().execute("""
        SELECT p.id, p.name, oi.qty, oi.unit_price FROM order_items oi
        JOIN products p ON p.id = oi.product_id WHERE oi.order_id = ?""", (oid,)).fetchall()
    return render_template("order_done.html", order=order, lines=lines, new=bool(request.args.get("new")))


@app.route("/order/<int:oid>/receipt")
def receipt(oid):
    """ใบเสร็จ/สลิปสำหรับพิมพ์ — เปิดได้เฉพาะแอดมินหรือเจ้าของออเดอร์
    ?size=a4 (ค่าตั้งต้น) หรือ ?size=slip (เครื่องพิมพ์ใบเสร็จความร้อน 80 มม.), ?print=1 เปิดหน้าต่างพิมพ์ทันที"""
    db = get_db()
    order = db.execute("SELECT *, datetime(created_at, '+7 hours') AS local_time FROM orders WHERE id = ?",
                       (oid,)).fetchone()
    if not (session.get("admin") or owns_order(order)) or order is None:
        abort(404)
    lines = db.execute("""
        SELECT p.name, oi.qty, oi.unit_price FROM order_items oi
        JOIN products p ON p.id = oi.product_id WHERE oi.order_id = ? ORDER BY oi.id""", (oid,)).fetchall()
    info = {k: get_text_setting(k) for k in ("shop_name", "shop_address", "shop_tax_id", "receipt_footer")}
    size = "slip" if request.args.get("size") == "slip" else "a4"
    printed = (datetime.now(timezone.utc) + timedelta(hours=7)).strftime("%Y-%m-%d %H:%M")
    resp = make_response(render_template(
        "receipt.html", order=order, lines=lines, info=info, size=size, printed=printed,
        subtotal=order["total"] - order["vat"] - order["shipping"] + order["discount"],
        auto=bool(request.args.get("print")), is_admin=bool(session.get("admin"))))
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@app.post("/order/<int:oid>/slip")
def upload_slip(oid):
    """ลูกค้าแนบสลิปโอนเงิน (ได้เฉพาะออเดอร์ของตัวเองที่ยังรอชำระเงิน)"""
    db = get_db()
    order = db.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone()
    if not owns_order(order):
        abort(404)
    if order["status"] != "pending":
        flash("ออเดอร์นี้ไม่ได้อยู่ในสถานะรอชำระเงิน")
        return redirect(url_for("order_done", oid=oid))
    blob = request.files["slip"].read() if "slip" in request.files else b""
    kind = admin.sniff_image(blob) if blob else None
    if not kind:
        flash("แนบสลิปเป็นไฟล์รูป JPG, PNG หรือ WEBP เท่านั้น")
        return redirect(url_for("order_done", oid=oid))
    name = f"order{oid}_{secrets.token_hex(6)}.{kind}"
    with open(os.path.join(SLIP_DIR, name), "wb") as f:
        f.write(blob)
    if order["slip"]:                      # ส่งใหม่ = แทนที่ไฟล์เดิม
        try:
            os.remove(os.path.join(SLIP_DIR, order["slip"]))
        except OSError:
            pass
    db.execute("UPDATE orders SET slip = ? WHERE id = ?", (name, oid))
    db.commit()
    flash(f"ส่งสลิปแล้ว แอดมินจะตรวจสอบและอนุมัติภายใน {APPROVE_MINUTES} นาที")
    return redirect(url_for("order_done", oid=oid))


@app.route("/slip/<int:oid>")
def slip_file(oid):
    """สลิปมีข้อมูลบัญชี จึงเปิดดูได้เฉพาะแอดมิน หรือเจ้าของออเดอร์เท่านั้น"""
    row = get_db().execute("SELECT id, user_id, slip FROM orders WHERE id = ?", (oid,)).fetchone()
    if not (session.get("admin") or owns_order(row)):
        abort(404)
    if row is None or not row["slip"]:
        abort(404)
    resp = send_from_directory(SLIP_DIR, row["slip"])
    resp.headers["Cache-Control"] = "private, no-store"
    return resp


@app.errorhandler(404)
def not_found(_e):
    return render_template("404.html"), 404


@app.errorhandler(413)
def too_large(_e):
    return "ไฟล์ใหญ่เกินไป (จำกัดประมาณ 5MB) กลับไปเลือกรูปที่เล็กลง", 413


init_db()    # สร้าง/อัปเกรดฐานข้อมูลทุกครั้งที่เริ่มแอป (รันผ่าน gunicorn/waitress ก็ใช้ได้)

if __name__ == "__main__":
    app.run(debug=True)
