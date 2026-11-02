"""ตัวช่วยเรื่องวันที่สำหรับหลังบ้าน — ใช้เวลาไทย (UTC+7) ทั้งหมด
ทุกหน้าที่มีตัวเลือกวันที่ใช้พารามิเตอร์ชุดเดียวกัน: d1 = จากวันที่, d2 = ถึงวันที่ (รูปแบบ YYYY-MM-DD)"""
import re
from datetime import date, datetime, timedelta, timezone


def thai_today():
    return (datetime.now(timezone.utc) + timedelta(hours=7)).date()


def parse_day(s):
    """'2026-10-27' -> date หรือ None ถ้าว่าง/รูปแบบผิด/วันที่ไม่มีจริง"""
    if not s or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def day_range(args):
    """อ่าน d1/d2 จาก query string; ถ้าใส่สลับกันให้สลับให้เอง; ใส่ด้านเดียวก็ได้ (ไม่จำกัดอีกด้าน)"""
    d1, d2 = parse_day(args.get("d1", "")), parse_day(args.get("d2", ""))
    if d1 and d2 and d1 > d2:
        d1, d2 = d2, d1
    return d1, d2


def date_clause(col, d1, d2):
    """ต่อเงื่อนไข SQL กรองคอลัมน์เวลา (เก็บเป็น UTC) ตามวันที่ไทย — คืน (sql, params)"""
    sql, params = "", []
    if d1:
        sql += f" AND date({col}, '+7 hours') >= ?"
        params.append(d1.isoformat())
    if d2:
        sql += f" AND date({col}, '+7 hours') <= ?"
        params.append(d2.isoformat())
    return sql, params


def presets():
    """ปุ่มลัดเลือกช่วงวันที่"""
    t, one = thai_today(), timedelta(days=1)
    items = [("วันนี้", t, t), ("เมื่อวาน", t - one, t - one), ("7 วันล่าสุด", t - 6 * one, t),
             ("30 วันล่าสุด", t - 29 * one, t), ("เดือนนี้", t.replace(day=1), t)]
    return [{"label": l, "d1": a.isoformat(), "d2": b.isoformat()} for l, a, b in items]


def thai_date(d):
    """date -> '27/10/2569' (พ.ศ.)"""
    return f"{d.day:02d}/{d.month:02d}/{d.year + 543}"
