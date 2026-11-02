"""ฟังก์ชันกลางที่หน้าร้าน ระบบสมาชิก และหลังบ้านใช้ร่วมกัน:
CSRF, ผู้ใช้ปัจจุบัน, ค่าส่ง/คูปอง, ยกเลิกออเดอร์, จำกัดการเดารหัส, ส่งอีเมล"""
import hashlib
import json
import os
import re
import secrets
import smtplib
import threading
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from functools import wraps

from flask import abort, flash, g, redirect, request, session, url_for

from db import BASE_DIR, DEFAULT_SETTINGS, get_db


def load_dotenv():
    """อ่านไฟล์ .env (บรรทัด KEY=VALUE) เข้าตัวแปรสภาพแวดล้อม โดยไม่ทับค่าที่ตั้งไว้ในระบบอยู่แล้ว"""
    path = os.path.join(BASE_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip()
                # รองรับคอมเมนต์ท้ายบรรทัด เช่น AI_DAILY_LIMIT=500  # จำนวนครั้ง/วัน
                # ตัดเฉพาะ # ที่มีช่องว่างนำหน้า เพื่อไม่ตัดค่าที่มี # ติดกัน
                if v[:1] not in ("'", '"'):
                    v = re.split(r"\s+#", v, maxsplit=1)[0].strip()
                else:
                    quote = v[0]
                    end = v.find(quote, 1)
                    if end >= 0:
                        v = v[1:end]
                if k:
                    os.environ.setdefault(k, v)


load_dotenv()


# ---------- CSRF ----------
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def csrf_ok():
    expected = session.get("csrf")
    sent = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    return bool(expected) and secrets.compare_digest(sent.encode(), expected.encode())


# ---------- ผู้ใช้ปัจจุบัน ----------
def current_user():
    """คืนแถวผู้ใช้ที่ล็อกอินอยู่ (หรือ None) — ถ้าบัญชีถูกระงับระหว่างใช้งานจะถูกออกจากระบบทันที"""
    if "user" not in g:
        g.user = None
        uid = session.get("uid")
        if uid:
            row = get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
            if row is not None and row["active"]:
                g.user = row
            else:
                session.pop("uid", None)
    return g.user


def login_required(view):
    @wraps(view)
    def wrapper(*a, **kw):
        if current_user() is None:
            flash("กรุณาเข้าสู่ระบบก่อน")
            return redirect(url_for("customers.login", next=request.full_path.rstrip("?")))
        return view(*a, **kw)
    return wrapper


def safe_next(nxt, default):
    """กัน open redirect: รับเฉพาะ path ภายในเว็บ และไม่พาไปหลังบ้าน"""
    if (nxt and nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt
            and not nxt.startswith("/admin")):
        return nxt
    return default


def owns_order(order):
    """เจ้าของออเดอร์ = สมาชิกที่สั่ง หรือ (กรณี guest) เบราว์เซอร์ที่สั่ง"""
    if order is None:
        return False
    uid = session.get("uid")
    if uid and order["user_id"] == uid:
        return True
    return order["id"] in session.get("my_orders", [])


# ---------- ตะกร้าที่ผูกกับบัญชี ----------
def save_cart():
    """ถ้าล็อกอินอยู่ เก็บตะกร้าลงบัญชี เพื่อให้เปิดจากเครื่องอื่นแล้วตะกร้ายังอยู่"""
    uid = session.get("uid")
    if uid:
        db = get_db()
        db.execute("UPDATE users SET cart = ? WHERE id = ?",
                   (json.dumps(session.get("cart", {})), uid))
        db.commit()


def merge_saved_cart(user):
    """ตอนล็อกอิน: รวมตะกร้าที่บันทึกไว้กับตะกร้าที่หยิบไว้ก่อนล็อกอิน (ชิ้นเดียวกันใช้จำนวนมากกว่า)"""
    try:
        saved = json.loads(user["cart"] or "{}")
    except ValueError:
        saved = {}
    cart = dict(session.get("cart", {}))
    for pid, qty in saved.items():
        if str(pid).isdigit() and isinstance(qty, int) and qty > 0:
            cart[str(pid)] = max(cart.get(str(pid), 0), qty)
    if cart:
        session["cart"] = cart


# ---------- ตั้งค่าร้าน / ค่าส่ง / คูปอง ----------
def get_setting(key):
    row = get_db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    raw = row["value"] if row else DEFAULT_SETTINGS.get(key, "0")
    return int(raw) if str(raw).lstrip("-").isdigit() else int(DEFAULT_SETTINGS.get(key, 0))


def shop_contact():
    """ข้อมูลติดต่อร้าน (อ่านจากตั้งค่า) ใช้แสดงท้ายเว็บ หน้าติดต่อเรา และอีเมล"""
    db = get_db()
    out = {}
    for k in ("contact_email", "contact_phone", "contact_line", "contact_hours"):
        row = db.execute("SELECT value FROM settings WHERE key = ?", (k,)).fetchone()
        out[k.replace("contact_", "")] = row["value"] if row else DEFAULT_SETTINGS.get(k, "")
    return out


def get_text_setting(key):
    """อ่านค่าตั้งค่าแบบข้อความ (ถ้ายังไม่เคยบันทึกใช้ค่าตั้งต้น)"""
    row = get_db().execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else DEFAULT_SETTINGS.get(key, "")


def contact_lines():
    c = shop_contact()
    lines = [f"อีเมล: {c['email']}"] if c["email"] else []
    if c["phone"]:
        lines.append(f"โทร: {c['phone']}")
    if c["line"]:
        lines.append(f"LINE: {c['line']}")
    if c["hours"]:
        lines.append(f"เวลาทำการ: {c['hours']}")
    return "\n".join(lines)


def thai_today():
    return (datetime.now(timezone.utc) + timedelta(hours=7)).date()


def shipping_fee(amount_after_discount):
    fee, free_min = get_setting("shipping_fee"), get_setting("free_shipping_min")
    if amount_after_discount <= 0 or (free_min > 0 and amount_after_discount >= free_min):
        return 0
    return fee


def check_coupon(code, subtotal, user_id):
    """คืน (แถวคูปอง, ส่วนลดเป็นบาท, ข้อความผิดพลาด)"""
    code = (code or "").strip()
    if not code:
        return None, 0, None
    db = get_db()
    c = db.execute("SELECT * FROM coupons WHERE code = ?", (code,)).fetchone()
    if c is None or not c["active"]:
        return None, 0, "ไม่พบโค้ดส่วนลดนี้หรือโค้ดถูกปิดใช้งานแล้ว"
    if c["expires_at"] and c["expires_at"] < thai_today().isoformat():
        return None, 0, "โค้ดนี้หมดอายุแล้ว"
    if c["usage_limit"] and c["used"] >= c["usage_limit"]:
        return None, 0, "โค้ดนี้ถูกใช้ครบจำนวนสิทธิ์แล้ว"
    if subtotal < c["min_total"]:
        return None, 0, f"โค้ดนี้ใช้ได้เมื่อซื้อขั้นต่ำ ฿{c['min_total']:,}"
    if c["once_per_user"]:
        if not user_id:
            return None, 0, "โค้ดนี้ใช้ได้เฉพาะสมาชิก กรุณาเข้าสู่ระบบ"
        if db.execute("SELECT 1 FROM orders WHERE user_id = ? AND coupon = ? AND status != 'cancelled'",
                      (user_id, c["code"])).fetchone():
            return None, 0, "คุณใช้โค้ดนี้ไปแล้ว (จำกัด 1 ครั้งต่อสมาชิก)"
    if c["kind"] == "percent":
        disc = subtotal * c["value"] // 100
        if c["max_discount"]:
            disc = min(disc, c["max_discount"])
    else:
        disc = c["value"]
    return c, min(disc, subtotal), None


VAT_RATE = 7  # ภาษีมูลค่าเพิ่ม 7% (บวกเพิ่มจากราคาสินค้า)


def calc_vat(amount):
    """VAT 7% ปัดเป็นบาทเต็ม (ปัดครึ่งขึ้น)"""
    return (amount * VAT_RATE + 50) // 100


def price_order(subtotal, code, user_id):
    """คำนวณยอดทั้งหมด: (ราคาสินค้า − ส่วนลด + ค่าส่ง) + VAT 7%"""
    coupon, discount, error = check_coupon(code, subtotal, user_id)
    ship = shipping_fee(subtotal - discount)
    before_vat = subtotal - discount + ship
    vat = calc_vat(before_vat)
    return {"subtotal": subtotal, "discount": discount, "shipping": ship,
            "before_vat": before_vat, "vat": vat, "vat_rate": VAT_RATE,
            "total": before_vat + vat, "coupon": coupon, "coupon_error": error,
            "free_min": get_setting("free_shipping_min")}


# ---------- ยกเลิกออเดอร์ ----------
def cancel_order(db, oid):
    """ยกเลิกออเดอร์: คืนสต็อกและคืนสิทธิ์คูปอง (เรียกซ้ำกับออเดอร์ที่ยกเลิกแล้วไม่ทำอะไร)"""
    order = db.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone()
    if order is None or order["status"] == "cancelled":
        return False
    for it in db.execute("SELECT product_id, qty FROM order_items WHERE order_id = ?", (oid,)).fetchall():
        db.execute("UPDATE products SET stock = stock + ? WHERE id = ?", (it["qty"], it["product_id"]))
    if order["coupon"]:
        db.execute("UPDATE coupons SET used = MAX(used - 1, 0) WHERE code = ?", (order["coupon"],))
    db.execute("UPDATE orders SET status = 'cancelled' WHERE id = ?", (oid,))
    db.commit()
    return True


# ---------- กันเดารหัสผ่าน (เก็บในหน่วยความจำ) ----------
_fail_log = {}
MAX_FAILS, LOCK_SECONDS = 5, 10 * 60


def throttled(key):
    rec = _fail_log.get(key)
    if rec and time.time() - rec[1] > LOCK_SECONDS:
        _fail_log.pop(key, None)
        return False
    return bool(rec and rec[0] >= MAX_FAILS)


def throttle_fail(key):
    rec = _fail_log.setdefault(key, [0, time.time()])
    rec[0] += 1


def throttle_clear(key):
    _fail_log.pop(key, None)


# ---------- รีเซ็ตรหัสผ่าน ----------
def new_reset_token(db, user_id, minutes=60):
    token = secrets.token_urlsafe(32)
    db.execute("UPDATE users SET reset_hash = ?, reset_expires = ? WHERE id = ?",
               (hashlib.sha256(token.encode()).hexdigest(), int(time.time()) + minutes * 60, user_id))
    db.commit()
    return token


def user_by_reset_token(token):
    h = hashlib.sha256((token or "").encode()).hexdigest()
    return get_db().execute("SELECT * FROM users WHERE reset_hash = ? AND reset_hash != '' "
                            "AND reset_expires > ? AND active = 1", (h, int(time.time()))).fetchone()


REPLY_TO = {}   # อีเมลร้านสำหรับให้ลูกค้ากดตอบกลับ (ตั้งตอนส่งจากข้อมูลติดต่อร้าน)


def send_mail(to, subject, body):
    """ส่งอีเมลผ่าน SMTP ถ้าตั้งตัวแปร SMTP_HOST ไว้ (SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM)
    ถ้าไม่ได้ตั้ง จะพิมพ์ข้อความลงหน้าจอเซิร์ฟเวอร์แทน คืน True เมื่อส่งออกจริง"""
    host = os.environ.get("SMTP_HOST")
    if not host:
        print(f"\n[อีเมล (ยังไม่ได้ตั้ง SMTP — แสดงในหน้าจอแทน)] ถึง: {to}\nหัวข้อ: {subject}\n{body}\n")
        return False
    msg = EmailMessage()
    msg["To"], msg["Subject"] = to, subject
    if os.environ.get("SMTP_REPLY_TO") or REPLY_TO.get("v"):
        msg["Reply-To"] = os.environ.get("SMTP_REPLY_TO") or REPLY_TO["v"]
    msg["From"] = os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USER", "no-reply@localhost")
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587")), timeout=15) as s:
            s.starttls()
            if os.environ.get("SMTP_USER"):
                s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
            s.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException) as exc:
        print("ส่งอีเมลไม่สำเร็จ:", exc)
        return False


def send_mail_async(to, subject, body):
    """ส่งอีเมลเบื้องหลัง ไม่ให้หน้าเว็บค้างรอ SMTP (ส่งไม่สำเร็จก็ไม่กระทบการสั่งซื้อ)"""
    threading.Thread(target=send_mail, args=(to, subject, body), daemon=True).start()


EVENT_TEXT = {
    "placed": ("รับออเดอร์ของคุณแล้ว", "ขอบคุณที่สั่งซื้อ ร้านได้รับออเดอร์ของคุณเรียบร้อย\nกรุณาชำระเงินและแนบสลิปที่หน้าออเดอร์ แอดมินจะตรวจสอบและอนุมัติภายใน 10 นาทีหลังได้รับสลิป"),
    "paid": ("ยืนยันการชำระเงินแล้ว", "ร้านได้รับเงินของคุณเรียบร้อยแล้ว กำลังเตรียมจัดส่งสินค้า"),
    "shipped": ("จัดส่งสินค้าแล้ว", "สินค้าของคุณถูกส่งออกจากร้านแล้ว"),
    "tracking": ("อัปเดตเลขพัสดุ", "ร้านอัปเดตเลขพัสดุของออเดอร์คุณ"),
    "cancelled": ("ออเดอร์ถูกยกเลิก", "ออเดอร์นี้ถูกยกเลิกแล้ว สินค้าถูกคืนเข้าสต็อก หากไม่ได้เป็นผู้ยกเลิกหรือมีข้อสงสัย กรุณาติดต่อร้าน"),
}


def notify_order(db, oid, event):
    """แจ้งลูกค้าทางอีเมลเมื่อออเดอร์เปลี่ยนสถานะ (placed/paid/shipped/tracking/cancelled)
    ออเดอร์ที่ไม่มีอีเมล (guest ที่ไม่กรอก) จะข้ามไปเฉยๆ"""
    order = db.execute("SELECT * FROM orders WHERE id = ?", (oid,)).fetchone()
    if order is None or not order["email"] or event not in EVENT_TEXT:
        return
    title, intro = EVENT_TEXT[event]
    lines = db.execute("SELECT p.name, oi.qty, oi.unit_price FROM order_items oi "
                       "JOIN products p ON p.id = oi.product_id WHERE oi.order_id = ?", (oid,)).fetchall()
    items = "\n".join(f"  - {l['name']} x{l['qty']}  ฿{l['qty'] * l['unit_price']:,}" for l in lines)
    extra = ""
    if event in ("shipped", "tracking"):
        extra = f"\nเลขพัสดุ: {order['tracking']}\n" if order["tracking"] else "\n(ร้านยังไม่ได้ระบุเลขพัสดุ)\n"
    extra += (f"\nส่วนลด: -฿{order['discount']:,}" if order["discount"] else "")
    extra += (f"\nVAT {VAT_RATE}%: ฿{order['vat']:,}" if order["vat"] else "")
    link = url_for("order_done", oid=oid, _external=True)
    body = (f"สวัสดีคุณ {order['customer']}\n\n{intro}\n\n"
            f"ออเดอร์ #{oid:05d}\n{items}\nค่าจัดส่ง: ฿{order['shipping']:,}{extra}\n"
            f"ยอดรวม: ฿{order['total']:,}\n\nจัดส่งไปที่: {order['address']}\n\n"
            f"ดูรายละเอียดออเดอร์: {link}\n\nมีคำถามติดต่อร้านได้ที่\n{contact_lines()}\n\n— Anime Figure Shop")
    REPLY_TO["v"] = shop_contact()["email"]
    send_mail_async(order["email"], f"[Anime Figure Shop] {title} (#{oid:05d})", body)


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")