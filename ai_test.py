"""ทดสอบคีย์ Gemini: รัน  py ai_test.py  (ไม่พิมพ์คีย์ออกหน้าจอ) บอกว่าคีย์ใช้ได้ไหม และโมเดลไหนใช้ได้"""
import json, os, sys
import common            # โหลด .env
import chat

key = chat._clean_key("GEMINI_API_KEY")
if not key:
    sys.exit("ไม่พบ GEMINI_API_KEY ใน .env (ต้องเป็นบรรทัดเดียว: GEMINI_API_KEY=คีย์ ไม่มีช่องว่าง)")
print(f"พบคีย์ (ยาว {len(key)} ตัวอักษร, ขึ้นต้น {key[:3]}...)")
try:
    data = chat.gemini_request("/v1beta/models?pageSize=200", key)
    names = [m["name"].replace("models/", "") for m in data.get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    print(f"คีย์ใช้ได้ — มีโมเดลที่ใช้ generateContent ได้ {len(names)} ตัว:")
    print("  " + "\n  ".join(n for n in names if "flash" in n or "pro" in n)[:1500])
    pick = chat.discover_gemini_model(key)
    want = os.environ.get("GEMINI_MODEL", "").strip()
    print(f"\nGEMINI_MODEL ใน .env = {want or '(ไม่ได้ตั้ง)'}", "→ ใช้ได้" if want in names else "→ ไม่มีในรายการ! ให้ลบบรรทัดนี้หรือเปลี่ยนเป็น " + (pick or "ชื่อจากรายการข้างบน"))
    use = want if want in names else pick
    r = chat.gemini_request(f"/v1beta/models/{use}:generateContent", key,
                            {"contents": [{"role": "user", "parts": [{"text": "ตอบว่า OK คำเดียว"}]}]})
    print("ทดสอบถามจริงด้วย", use, "→", r["candidates"][0]["content"]["parts"][0].get("text", "").strip())
    print("\nสำเร็จ ✅ รีสตาร์ทร้านแล้วใช้แชทได้เลย")
except Exception as e:
    print("ไม่สำเร็จ ❌", chat.short_error(e, key))
    print("ถ้าเป็น 503 → เซิร์ฟเวอร์โมเดลแน่น ไม่ใช่โค้ดหรือคีย์ผิด; ร้านจะลองโมเดล Flash สำรองอัตโนมัติเมื่อมีโมเดลอื่นให้ใช้ · 429 → ตรวจโควตา/ขีดจำกัด · 400/403 → ตรวจคีย์และสิทธิ์ · เชื่อมต่อไม่ได้ → ตรวจอินเทอร์เน็ต/ไฟร์วอลล์")
