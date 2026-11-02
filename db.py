"""ฐานข้อมูลและค่าคงที่ที่ใช้ร่วมกันทั้งหน้าร้านและหลังบ้าน"""
import os
import secrets
import sqlite3
from flask import g
from werkzeug.security import generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "shop.db")
IMAGE_DIR = os.path.join(BASE_DIR, "static", "images")
PAYMENT_DIR = os.path.join(BASE_DIR, "static", "payment")   # รูป QR รับเงิน (แสดงให้ลูกค้าเห็น)
SLIP_DIR = os.path.join(BASE_DIR, "slips")                   # สลิปโอนเงินของลูกค้า (ไม่เปิดเป็นไฟล์สาธารณะ)

# สถานะออเดอร์ และลำดับที่เปลี่ยนได้ (ยกเลิกแล้วหรือจัดส่งแล้วเปลี่ยนต่อไม่ได้)
STATUSES = {
    "pending": "รอชำระเงิน",
    "paid": "ชำระเงินแล้ว",
    "shipped": "จัดส่งแล้ว",
    "cancelled": "ยกเลิก",
}
STATUS_FLOW = {
    "pending": ["paid", "cancelled"],
    "paid": ["shipped", "cancelled"],
    "shipped": [],
    "cancelled": [],
}
APPROVE_MINUTES = 10   # แจ้งลูกค้าว่าแอดมินจะตรวจสลิปและอนุมัติภายในกี่นาที
LOW_STOCK = 3  # เหลือเท่านี้หรือน้อยกว่า ถือว่าใกล้หมด
REQUIRE_LOGIN_TO_CHECKOUT = True   # True = ต้องสมัคร/ล็อกอินก่อนสั่งซื้อ, False = ให้สั่งแบบไม่ล็อกอิน (guest) ได้
# ค่าตั้งต้นของร้าน (แก้ได้ที่หลังบ้านเมนู "ตั้งค่าร้าน")
DEFAULT_SETTINGS = {
    "shipping_fee": "60",          # ค่าส่งมาตรฐาน (บาท)
    "free_shipping_min": "2000",   # ซื้อครบเท่านี้ส่งฟรี (0 = ไม่มีส่งฟรี)
    # ข้อมูลติดต่อร้าน (แก้ได้ที่หลังบ้าน "ตั้งค่าร้าน")
    "contact_email": "Beamkung5896@gmail.com",
    "contact_phone": "",
    "contact_line": "",
    "contact_hours": "",
    # ข้อมูลที่พิมพ์บนใบเสร็จ (แก้ได้ที่หลังบ้าน "ตั้งค่าร้าน")
    "shop_name": "Anime Figure Shop",
    "shop_address": "",
    "shop_tax_id": "",
    "receipt_footer": "ขอบคุณที่อุดหนุนนะคะ/ครับ",
    "chat_enabled": "1",           # 1 = เปิดแชทช่วยเหลือลูกค้า (AI + แอดมิน), 0 = ปิด
}
TEXT_SETTINGS = ("contact_email", "contact_phone", "contact_line", "contact_hours",
                 "shop_name", "shop_address", "shop_tax_id", "receipt_footer")

# สินค้าตั้งต้นของร้าน (ใส่ลงฐานข้อมูลครั้งแรกเท่านั้น หลังจากนั้นจัดการผ่านหน้าแอดมิน)
# (ชื่อ, ชื่อภาพ, หมวด, สเกล, ราคา, สต็อก, คำอธิบาย, นามสกุลภาพ, สีพื้นการ์ด, วิธีจัดภาพ)
PRODUCTS = [
    ("ลูฟี่ เกียร์ 5", "luffy", "แอ็กชัน", "ไดโอราม่า", 5990, 2,
     "ผมขาวและเสื้อคลุมขาวพลิ้วไหว ผ้าคาดเอวสีม่วง มีเอฟเฟกต์สายฟ้าสีน้ำเงินกับพลังสีแดงรอบตัว ตั้งบนฐานหินพร้อมโซ่และคลื่นน้ำ",
     "jpg", "#050a1f", "cover"),
    ("โซโล", "zoro", "แอ็กชัน", "ไดโอราม่า", 4990, 3,
     "ท่าคาบดาบพร้อมดาบอีกสองเล่มในมือ รายล้อมด้วยเอฟเฟกต์ลมสีเขียวโปร่งแสง ฐานเป็นหินและกลุ่มเมฆ",
     "jpg", "#1a1f28", "contain"),
    ("ทันจิโร่", "tanjiro", "แอ็กชัน", "ฟิกเกอร์ PVC", 1590, 8,
     "ฮาโอริลายตารางเขียวดำ สะพายดาบบนบ่า ยืนมั่นคงบนฐานวงรีสีดำ ขนาดกำลังดีสำหรับวางโต๊ะ",
     "jpg", "#ffffff", "contain"),
    ("โกโจ ซาโตรุ", "gojo", "แฟนตาซี", "ฟิกเกอร์ PVC", 1890, 5,
     "ท่านั่งเอนสบายบนเก้าอี้ มือดึงผ้าปิดตาขึ้นเผยดวงตาสีฟ้า ชุดสีน้ำเงินเข้มพับเนี้ยบ",
     "jpg", "#ffffff", "contain"),
    ("นารูโตะ โหมดเซียน", "naruto", "นินจา", "ฟิกเกอร์ PVC", 1690, 6,
     "เสื้อคลุมแดงกับผ้าคาดหน้าผาก ท่าประสานนิ้วทำสมาธิ มีม้วนคัมภีร์ด้านหลังและท่ายืนกางขาเต็มพลัง",
     "jpg", "#ffffff", "contain"),
]


def payment_qr_file():
    """คืนชื่อไฟล์ QR รับเงินใน static/payment (qr.png / qr.jpg / qr.webp) หรือ None ถ้ายังไม่ตั้งค่า"""
    for ext in ("png", "jpg", "webp"):
        if os.path.exists(os.path.join(PAYMENT_DIR, f"qr.{ext}")):
            return f"qr.{ext}"
    return None


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _add_column(db, table, name, ddl):
    cols = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
    if name not in cols:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def init_db():
    """สร้างตาราง + อัปเกรดฐานข้อมูลเก่าอัตโนมัติ (รันซ้ำได้ ข้อมูลเดิมไม่หาย)"""
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, image TEXT NOT NULL, category TEXT NOT NULL,
            scale TEXT NOT NULL, price INTEGER NOT NULL, stock INTEGER NOT NULL,
            description TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer TEXT NOT NULL, phone TEXT NOT NULL, address TEXT NOT NULL,
            total INTEGER NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL REFERENCES orders(id),
            product_id INTEGER NOT NULL REFERENCES products(id),
            qty INTEGER NOT NULL, unit_price INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            default_pw INTEGER NOT NULL DEFAULT 0
        );
        -- ลูกค้าสมาชิก
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            name TEXT NOT NULL,
            phone TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            cart TEXT NOT NULL DEFAULT '',
            reset_hash TEXT NOT NULL DEFAULT '',
            reset_expires INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_login TEXT
        );
        -- สมุดที่อยู่จัดส่ง
        CREATE TABLE IF NOT EXISTS addresses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            label TEXT NOT NULL DEFAULT '',
            recipient TEXT NOT NULL, phone TEXT NOT NULL, address TEXT NOT NULL,
            is_default INTEGER NOT NULL DEFAULT 0
        );
        -- รายการโปรด
        CREATE TABLE IF NOT EXISTS wishlist (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, product_id)
        );
        -- รีวิวสินค้า (ลูกค้าที่เคยซื้อและชำระเงินแล้วเท่านั้น)
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            rating INTEGER NOT NULL, comment TEXT NOT NULL DEFAULT '',
            visible INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (product_id, user_id)
        );
        -- คูปองส่วนลด
        CREATE TABLE IF NOT EXISTS coupons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE COLLATE NOCASE,
            kind TEXT NOT NULL DEFAULT 'percent',       -- percent | fixed
            value INTEGER NOT NULL,
            min_total INTEGER NOT NULL DEFAULT 0,
            max_discount INTEGER NOT NULL DEFAULT 0,    -- เพดานส่วนลดแบบ % (0 = ไม่จำกัด)
            usage_limit INTEGER NOT NULL DEFAULT 0,     -- 0 = ไม่จำกัดจำนวนครั้ง
            used INTEGER NOT NULL DEFAULT 0,
            once_per_user INTEGER NOT NULL DEFAULT 1,
            expires_at TEXT NOT NULL DEFAULT '',        -- YYYY-MM-DD (ว่าง = ไม่หมดอายุ)
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        -- แชทลูกค้า (AI ตอบก่อน แอดมินรับช่วงต่อได้)
        CREATE TABLE IF NOT EXISTS chat_threads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            name TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'ai',          -- ai | human | closed
            handoff INTEGER NOT NULL DEFAULT 0,         -- 1 = AI/ลูกค้าขอให้แอดมินช่วย
            admin_seen INTEGER NOT NULL DEFAULT 0,      -- id ข้อความล่าสุดที่แอดมินเปิดอ่านแล้ว
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id INTEGER NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
            role TEXT NOT NULL,                         -- customer | ai | admin
            body TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_chat_msgs_thread ON chat_messages(thread_id, id);
    """)
    # คอลัมน์ที่เพิ่มภายหลัง (ฐานข้อมูลเก่าจะถูกเติมให้)
    _add_column(db, "products", "image_ext", "TEXT NOT NULL DEFAULT 'svg'")
    _add_column(db, "products", "bg", "TEXT NOT NULL DEFAULT '#ffffff'")
    _add_column(db, "products", "fit", "TEXT NOT NULL DEFAULT 'cover'")
    _add_column(db, "products", "active", "INTEGER NOT NULL DEFAULT 1")
    _add_column(db, "orders", "status", "TEXT NOT NULL DEFAULT 'pending'")
    _add_column(db, "orders", "tracking", "TEXT NOT NULL DEFAULT ''")
    _add_column(db, "orders", "slip", "TEXT NOT NULL DEFAULT ''")
    _add_column(db, "orders", "user_id", "INTEGER")
    _add_column(db, "orders", "shipping", "INTEGER NOT NULL DEFAULT 0")
    _add_column(db, "orders", "discount", "INTEGER NOT NULL DEFAULT 0")
    _add_column(db, "orders", "coupon", "TEXT NOT NULL DEFAULT ''")
    _add_column(db, "orders", "email", "TEXT NOT NULL DEFAULT ''")
    _add_column(db, "orders", "consent_at", "TEXT NOT NULL DEFAULT ''")  # เวลา (UTC) ที่ลูกค้ากดยินยอม PDPA
    _add_column(db, "users", "consent_at", "TEXT NOT NULL DEFAULT ''")   # เวลา (UTC) ที่สมาชิกกดยินยอมตอนสมัคร
    _add_column(db, "orders", "vat", "INTEGER NOT NULL DEFAULT 0")  # ออเดอร์เก่า = 0 (ไม่มี VAT)
    db.execute("CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_reviews_product ON reviews(product_id)")
    for k, v in DEFAULT_SETTINGS.items():
        db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)", (k, v))
    os.makedirs(PAYMENT_DIR, exist_ok=True)
    os.makedirs(SLIP_DIR, exist_ok=True)

    # ขั้นตอนครั้งเดียว: ใส่สินค้าตั้งต้น และซ่อนสินค้าเก่าที่ไม่อยู่ในรายการ
    if db.execute("PRAGMA user_version").fetchone()[0] < 1:
        existing = {r[0] for r in db.execute("SELECT name FROM products")}
        for row in PRODUCTS:
            if row[0] not in existing:
                db.execute("INSERT INTO products (name,image,category,scale,price,stock,description,"
                           "image_ext,bg,fit) VALUES (?,?,?,?,?,?,?,?,?,?)", row)
        names = [r[0] for r in PRODUCTS]
        marks = ",".join("?" * len(names))
        db.execute(f"UPDATE products SET active = (name IN ({marks}))", names)
        db.execute("PRAGMA user_version = 1")

    # ขั้นตอนครั้งเดียว: คูปองต้อนรับสมาชิกใหม่ (แก้/ปิดได้ที่หลังบ้าน)
    if db.execute("PRAGMA user_version").fetchone()[0] < 2:
        db.execute("INSERT OR IGNORE INTO coupons (code,kind,value,min_total,max_discount,once_per_user) "
                   "VALUES ('WELCOME10','percent',10,500,300,1)")
        db.execute("PRAGMA user_version = 2")

    # บัญชีแอดมินแรก: ตั้งผ่านตัวแปรสภาพแวดล้อม ADMIN_USER / ADMIN_PASSWORD ได้
    if db.execute("SELECT COUNT(*) FROM admins").fetchone()[0] == 0:
        user = os.environ.get("ADMIN_USER", "admin")
        pw = os.environ.get("ADMIN_PASSWORD")
        db.execute("INSERT INTO admins (username, password_hash, default_pw) VALUES (?,?,?)",
                   (user, generate_password_hash(pw or "admin1234"), 0 if pw else 1))
    db.commit()
    db.close()
