"""รายงานยอดขายรายเดือน/รายปี + อันดับสินค้าขายดี-ขายช้า (หลังบ้าน /admin/reports)
ใช้ช่วยตัดสินใจว่าสินค้าไหนควรสั่งเพิ่ม และสินค้าไหนไม่มีคนสนใจ"""
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone

from flask import render_template, request

from dates import parse_day, thai_date, thai_today
from db import LOW_STOCK, get_db

MONTHS = ["มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
          "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"]
SHORT = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."]
RESTOCK_DAYS = 14      # สต็อกพอขายไม่ถึงกี่วัน ถึงแนะนำให้เตรียมสั่งเพิ่ม
SLOW_RATIO = 0.5       # ขายได้น้อยกว่า 50% ของค่าเฉลี่ยต่อสินค้า = ขายช้า


def shift(start, kind, n):
    """เลื่อนช่วงเวลาไปข้างหน้า/ถอยหลัง n เดือน(หรือปี)"""
    if kind == "year":
        return date(start.year + n, 1, 1)
    idx = start.year * 12 + start.month - 1 + n
    return date(idx // 12, idx % 12 + 1, 1)


MAX_RANGE_DAYS = 366     # ช่วงวันที่เองเลือกได้ไม่เกินนี้
DAILY_MAX = 62           # ช่วงที่ไม่เกินกี่วันแสดงกราฟรายวัน ถ้ายาวกว่านั้นรวมเป็นรายเดือน


def parse_period(args):
    """คืน (kind, start, end(ไม่รวมวันนี้), today)  kind = month | year | range"""
    today = thai_today()
    if args.get("period") == "range":
        d1, d2 = parse_day(args.get("d1", "")), parse_day(args.get("d2", ""))
        d2 = d2 or today
        d1 = d1 or d2 - timedelta(days=29)          # ไม่ได้เลือก = 30 วันล่าสุด
        if d1 > d2:
            d1, d2 = d2, d1
        d1 = max(d1, d2 - timedelta(days=MAX_RANGE_DAYS - 1))
        return "range", d1, d2 + timedelta(days=1), today
    kind = "year" if args.get("period") == "year" else "month"
    y = args.get("y", "")
    m = args.get("m", "")
    y = int(y) if y.isdigit() and 2000 <= int(y) <= 2100 else today.year
    m = int(m) if m.isdigit() and 1 <= int(m) <= 12 else today.month
    start = date(y, 1, 1) if kind == "year" else date(y, m, 1)
    return kind, start, shift(start, kind, 1), today


def verdicts(rows, total_qty, days):
    """ใส่คำแนะนำให้สินค้าแต่ละตัวจากยอดขายในช่วงที่เลือก (เป็นแนวทางประกอบการตัดสินใจเท่านั้น)"""
    n = sum(1 for r in rows if r["active"])
    avg = total_qty / n if n else 0
    ranked = [r for r in rows if r["qty"] > 0]
    top_ids = {r["id"] for r in ranked[:3]}
    out = []
    for r in rows:
        r = dict(r)
        rate = r["qty"] / days if days else 0
        r["days_left"] = int(r["stock"] / rate) if rate > 0 else None
        r["share"] = round(r["qty"] * 100 / total_qty) if total_qty else 0
        r["kind"], r["note"] = "ok", "ปกติ"
        if not total_qty:
            r["kind"], r["note"] = "none", "ยังไม่มียอดขายในช่วงนี้"
        elif not r["active"]:
            r["kind"], r["note"] = "none", "ซ่อนอยู่ (เคยมียอดขายในช่วงนี้)"
        elif r["qty"] == 0:
            r["kind"] = "cold"
            r["note"] = "ไม่มียอดขายเลย — ไม่ควรสั่งเพิ่ม"
            if r["hearts"]:
                r["note"] += f" (มีคนกดถูกใจ {r['hearts']} แต่ไม่ซื้อ ลองดูราคา/รูป/รายละเอียด)"
        elif r["stock"] <= 0:
            r["kind"], r["note"] = "restock", "ขายได้แต่สินค้าหมดแล้ว — ควรสั่งเพิ่ม"
        elif r["id"] in top_ids and r["qty"] >= avg:
            r["kind"] = "hot"
            r["note"] = "ขายดี"
            if r["days_left"] is not None and r["days_left"] <= RESTOCK_DAYS:
                r["kind"] = "restock"
                r["note"] = f"ขายดีและสต็อกพอขายอีก ~{r['days_left']} วัน — ควรเตรียมสั่งเพิ่ม"
        elif r["qty"] < avg * SLOW_RATIO:
            r["kind"], r["note"] = "slow", "ขายช้า — ชะลอการสั่งเพิ่ม"
        out.append(r)
    return out


def register(abp):
    @abp.route("/reports")
    def reports():
        db = get_db()
        kind, start, end, today = parse_period(request.args)
        pending = bool(request.args.get("pending"))
        st = ("paid", "shipped", "pending") if pending else ("paid", "shipped")
        marks = ",".join("?" * len(st))
        s, e = start.isoformat(), end.isoformat()
        where = f"o.status IN ({marks}) AND date(o.created_at, '+7 hours') >= ? AND date(o.created_at, '+7 hours') < ?"

        def totals(a, b):
            r = db.execute(f"""SELECT COALESCE(SUM(o.total),0) AS revenue, COUNT(*) AS orders,
                               COALESCE((SELECT SUM(oi.qty) FROM order_items oi JOIN orders o2 ON o2.id = oi.order_id
                                         WHERE o2.status IN ({marks}) AND date(o2.created_at, '+7 hours') >= ?
                                         AND date(o2.created_at, '+7 hours') < ?), 0) AS units
                               FROM orders o WHERE {where}""", (*st, a, b, *st, a, b)).fetchone()
            return dict(r)

        cur = totals(s, e)
        if kind == "range":     # เทียบกับช่วงก่อนหน้าที่ยาวเท่ากัน
            span = end - start
            prev_start = start - span
        else:
            prev_start = shift(start, kind, -1)
        prev = totals(prev_start.isoformat(), s)
        cur["aov"] = round(cur["revenue"] / cur["orders"]) if cur["orders"] else 0
        change = round((cur["revenue"] - prev["revenue"]) * 100 / prev["revenue"]) if prev["revenue"] else None

        # กราฟ: เดือน/ช่วงสั้น = รายวัน, ปี/ช่วงยาว = รายเดือน
        n_days = (end - start).days
        gran = "day" if (kind == "month" or (kind == "range" and n_days <= DAILY_MAX)) else "month"
        if gran == "day":
            keys = [(start + timedelta(days=i)).isoformat() for i in range(n_days)]
            labels = [str(i + 1) for i in range(n_days)] if kind == "month" else \
                     [f"{d[8:]}/{d[5:7]}" for d in keys]
            sel = "date(o.created_at, '+7 hours')"
        else:
            if kind == "year":
                months = [(start.year, i) for i in range(1, 13)]
            else:
                last, months, cur_m = end - timedelta(days=1), [], date(start.year, start.month, 1)
                while cur_m <= last:
                    months.append((cur_m.year, cur_m.month))
                    cur_m = shift(cur_m, "month", 1)
            keys = [f"{y}-{m:02d}" for y, m in months]
            labels = SHORT if kind == "year" else [f"{SHORT[m - 1]} {str(y + 543)[2:]}" for y, m in months]
            sel = "strftime('%Y-%m', o.created_at, '+7 hours')"
        got = {r["k"]: r["amount"] for r in db.execute(
            f"SELECT {sel} AS k, SUM(o.total) AS amount FROM orders o WHERE {where} GROUP BY k", (*st, s, e))}
        chart = [{"label": lb, "amount": got.get(k, 0)} for k, lb in zip(keys, labels)]
        top = max([c["amount"] for c in chart] + [1])
        for c in chart:
            c["pct"] = round(c["amount"] * 100 / top)
        best_slot = max(chart, key=lambda c: c["amount"]) if cur["revenue"] else None

        rows = db.execute(f"""
            SELECT p.id, p.name, p.stock, p.active, p.price,
                   COALESCE(s.qty, 0) AS qty, COALESCE(s.amount, 0) AS amount,
                   (SELECT COUNT(*) FROM wishlist w WHERE w.product_id = p.id) AS hearts
            FROM products p LEFT JOIN (
                SELECT oi.product_id AS pid, SUM(oi.qty) AS qty, SUM(oi.qty * oi.unit_price) AS amount
                FROM order_items oi JOIN orders o ON o.id = oi.order_id WHERE {where} GROUP BY oi.product_id
            ) s ON s.pid = p.id
            WHERE p.active = 1 OR s.qty > 0
            ORDER BY qty DESC, hearts DESC, p.name""", (*st, s, e)).fetchall()
        # ช่วงที่ยังไม่จบ (เดือนนี้/ปีนี้) คิดอัตราขายต่อวันจากจำนวนวันที่ผ่านมาแล้วเท่านั้น
        days = max(1, ((min(today + timedelta(days=1), end)) - start).days)
        total_qty = sum(r["qty"] for r in rows)
        items = verdicts(rows, total_qty, days)
        first = db.execute("SELECT MIN(date(created_at, '+7 hours')) FROM orders").fetchone()[0]
        years = list(range(int(first[:4]) if first else today.year, today.year + 1))
        if start.year not in years:
            years.append(start.year)
        last_day = end - timedelta(days=1)
        range_text = thai_date(start) if start == last_day else f"{thai_date(start)} – {thai_date(last_day)}"
        return render_template(
            "admin/reports.html", kind=kind, start=start, gran=gran, range_text=range_text,
            d1=start.isoformat(), d2=last_day.isoformat(), n_days=n_days, cur=cur, prev=prev, change=change, chart=chart,
            best_slot=best_slot, items=items, total_qty=total_qty, pending=pending, years=sorted(years),
            months=MONTHS, month_name=MONTHS[start.month - 1], low=LOW_STOCK, days=days,
            prev_label=("ช่วงก่อนหน้า" if kind == "range" else
                        str(shift(start, kind, -1).year) if kind == "year"
                        else f"{MONTHS[shift(start, kind, -1).month - 1]} {shift(start, kind, -1).year}"),
            restock=[i for i in items if i["kind"] == "restock"],
            cold=[i for i in items if i["kind"] in ("cold", "slow")])
