// JavaScript ฝั่งหน้าเว็บ: กด "ใส่ตะกร้า" แล้วเรียก API ของ Flask (ไม่ต้องโหลดหน้าใหม่)
const toast = document.getElementById("toast");
const badge = document.getElementById("cart-badge");
let toastTimer;
const TOAST_MS = 3000;   // ข้อความแจ้งเตือนแสดง 3 วินาทีแล้วหายไป

// แถบแจ้งเตือนจากเซิร์ฟเวอร์ (เช่น เข้าสู่ระบบสำเร็จ) ก็หายเองใน 3 วินาทีเหมือนกัน
document.querySelectorAll(".flash").forEach((el) => {
  setTimeout(() => { el.classList.add("fade-out"); setTimeout(() => el.remove(), 400); }, TOAST_MS);
});

function showToast(message, isError = false) {
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (toast.hidden = true), TOAST_MS);
}

document.querySelectorAll(".js-add").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const qtyInput = btn.dataset.qtyInput ? document.querySelector(btn.dataset.qtyInput) : null;
    const qty = qtyInput ? Number(qtyInput.value) : 1;
    if (!Number.isInteger(qty) || qty < 1) {       // ใส่ -1, 0, ว่าง หรือทศนิยม = ไม่ให้สั่ง
      if (qtyInput) qtyInput.value = 1;
      return showToast("จำนวนต้องเป็นตัวเลขตั้งแต่ 1 ขึ้นไป", true);
    }
    try {
      const res = await fetch(`/api/cart/add/${btn.dataset.id}`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": document.querySelector('meta[name="csrf"]').content,
        },
        body: JSON.stringify({ qty }),
      });
      const data = await res.json();
      if (!data.ok) return showToast(data.message, true);
      badge.textContent = data.count;
      badge.hidden = false;
      showToast(data.message);
      const old = btn.textContent;
      btn.textContent = "เพิ่มแล้ว";
      btn.classList.add("is-done");
      setTimeout(() => { btn.textContent = old; btn.classList.remove("is-done"); }, 1200);
    } catch (err) {
      showToast("เชื่อมต่อเซิร์ฟเวอร์ไม่ได้ ลองใหม่อีกครั้ง", true);
    }
  });
});

// ---------- หัวใจ (รายการโปรด): สลับสถานะด้วย fetch ไม่รีเฟรชหน้าฟีด ----------
document.addEventListener("submit", async (e) => {
  const form = e.target.closest("form.heart, form.wish-form, form.wish-remove");
  if (!form) return;
  e.preventDefault();
  const btn = form.querySelector("button");
  if (btn.disabled) return;
  btn.disabled = true;
  try {
    const res = await fetch(form.action, { method: "POST", body: new FormData(form), headers: { Accept: "application/json" } });
    const data = await res.json().catch(() => ({}));
    if (res.status === 401 && data.login_url) { window.location.href = data.login_url; return; }
    if (!data.ok) return showToast(data.message || "ทำรายการไม่สำเร็จ", true);
    if (form.classList.contains("wish-remove")) {          // หน้ารายการโปรด: เอาการ์ดออกเลย
      const card = form.closest(".case");
      card.remove();
      if (!document.querySelector(".small-grid .case")) location.reload();
    } else if (form.classList.contains("heart")) {
      btn.textContent = data.on ? "♥" : "♡";
      btn.classList.toggle("on", data.on);
      btn.setAttribute("aria-label", data.on ? "เอาออกจากรายการโปรด" : "เพิ่มในรายการโปรด");
    } else {
      btn.textContent = data.on ? "♥ อยู่ในรายการโปรด (กดเพื่อเอาออก)" : "♡ เพิ่มในรายการโปรด";
    }
    showToast(data.message);
  } catch (err) {
    showToast("เชื่อมต่อเซิร์ฟเวอร์ไม่ได้ ลองใหม่อีกครั้ง", true);
  } finally {
    btn.disabled = false;
  }
});

// ---------- ตะกร้า: ปุ่ม + / − เปลี่ยนจำนวนโดยไม่โหลดหน้าใหม่ ----------
document.addEventListener("submit", async (e) => {
  const form = e.target.closest("form.cart-qty, form.cart-remove");
  if (!form) return;
  e.preventDefault();
  const section = document.querySelector("section.page");
  try {
    const res = await fetch(form.action, { method: "POST", body: new FormData(form, e.submitter) });   // เซิร์ฟเวอร์ redirect กลับ /cart แล้ว
    const doc = new DOMParser().parseFromString(await res.text(), "text/html");
    const fresh = doc.querySelector("section.page");
    if (!fresh) return form.submit();
    section.innerHTML = fresh.innerHTML;
    const count = [...document.querySelectorAll(".cart-row .qty-num")].reduce((n, el) => n + Number(el.textContent), 0);
    badge.textContent = count; badge.hidden = count === 0;
    const flash = doc.querySelector(".flash");
    if (flash) showToast(flash.textContent.trim(), true);
  } catch (err) {
    showToast("เชื่อมต่อเซิร์ฟเวอร์ไม่ได้ ลองใหม่อีกครั้ง", true);
  }
});
