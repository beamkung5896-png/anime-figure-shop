/* แชทลูกค้า: AI ตอบก่อน แอดมินเข้ามาตอบต่อได้ — ดึงข้อความใหม่ทุก 3 วินาทีตอนเปิดแชท */
(function () {
  const root = document.getElementById("chat");
  if (!root) return;
  root.hidden = false;
  const $ = (id) => document.getElementById(id);
  const panel = $("chat-panel"), body = $("chat-body"), input = $("chat-input"), form = $("chat-form");
  const fab = $("chat-fab"), dot = $("chat-dot"), sub = $("chat-sub"), chips = $("chat-chips");
  const csrf = document.querySelector('meta[name="csrf"]').content;
  const labels = { ai: "🤖 AI", admin: "👤 แอดมิน" };
  const inline = root.classList.contains("chat-inline");   // ฝังในหน้า "ติดต่อเรา": เปิดค้างไว้ ไม่มีปุ่มลอย
  let last = 0, timer = null, busy = false, status = "ai", greeted = false, seenAdmin = 0;

  function bubble(m) {
    const row = document.createElement("div");
    row.className = "msg " + m.role;
    if (m.role !== "customer") {
      const who = document.createElement("span");
      who.className = "who"; who.textContent = labels[m.role] || "";
      row.appendChild(who);
    }
    const b = document.createElement("div");
    b.className = "bubble"; b.textContent = m.body;     // textContent = กัน XSS
    const t = document.createElement("span");
    t.className = "time"; t.textContent = m.t || "";
    row.append(b, t);
    body.appendChild(row);
    return row;
  }
  function render(list) {
    list.forEach((m) => { if (m.id > last) { bubble(m); last = m.id; if (m.role === "admin") seenAdmin = m.id; } });
    if (list.length) body.scrollTop = body.scrollHeight;
  }
  function setStatus(s) {
    status = s;
    sub.textContent = s === "human" ? "แอดมินกำลังดูแลคุณ · รอสักครู่นะ" : "AI ตอบทันที · แอดมินช่วยต่อได้";
    chips.querySelector("[data-human]").textContent = s === "human" ? "ให้ AI ช่วยต่อ" : "คุยกับแอดมิน";
  }
  // หลังเริ่มคุยซ่อนปุ่มคำถามด่วน แต่คงปุ่มสลับ "คุยกับแอดมิน/ให้ AI ช่วยต่อ" ไว้เสมอ
  function quickHide() { chips.querySelectorAll("button:not([data-human])").forEach((x) => (x.hidden = true)); }
  function typing(on) {
    let el = $("chat-typing");
    if (on && !el) {
      el = document.createElement("div"); el.id = "chat-typing"; el.className = "msg ai";
      el.innerHTML = '<div class="bubble typing"><i></i><i></i><i></i></div>';
      body.appendChild(el); body.scrollTop = body.scrollHeight;
    } else if (!on && el) el.remove();
  }
  async function api(path, opts) {
    const res = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf } }, opts));
    return { ok: res.ok, data: await res.json().catch(() => ({})) };
  }
  async function poll() {
    if (busy) return;   // กำลังส่ง: รอผลจากการส่งแทน กันข้อความซ้ำ
    try {
      const r = await fetch(root.dataset.url + "?after=" + last);
      const d = await r.json();
      const before = last;
      render(d.messages);
      setStatus(d.status);
      if (panel.hidden && last > before && d.messages.some((m) => m.role !== "customer")) dot.hidden = false;
    } catch (e) { /* เน็ตหลุดชั่วคราว ลองใหม่รอบหน้า */ }
  }
  function greet() {
    if (greeted || last) return;
    greeted = true;
    bubble({ role: "ai", body: "สวัสดีค่ะ 👋 ถามเรื่องสินค้า ค่าส่ง การชำระเงิน หรือสถานะออเดอร์ได้เลยนะคะ", t: "" });
  }
  async function send(text) {
    text = text.trim();
    if (!text || busy) return;
    busy = true; input.value = ""; quickHide();
    const tmp = bubble({ role: "customer", body: text, t: "" });
    body.scrollTop = body.scrollHeight;
    if (status === "ai") typing(true);
    const r = await api("/chat/send", { method: "POST", body: JSON.stringify({ message: text, after: last }) });
    typing(false);
    busy = false;
    if (!r.ok) { tmp.remove(); input.value = text; bubble({ role: "ai", body: r.data.error || "ส่งไม่สำเร็จ ลองใหม่อีกครั้งนะคะ", t: "" }); return; }
    tmp.remove();   // ข้อความชั่วคราวถูกแทนด้วยของจริงจากเซิร์ฟเวอร์
    render(r.data.messages); setStatus(r.data.status);
  }
  async function toggleHuman() {
    const r = await api(status === "human" ? "/chat/ai" : "/chat/human", { method: "POST", body: "{}" });
    if (r.ok) { await poll(); }
  }
  function open() {
    panel.hidden = false; fab.setAttribute("aria-expanded", "true"); dot.hidden = true;
    poll().then(greet); clearInterval(timer); timer = setInterval(poll, 3000); if (!inline) input.focus();
  }
  function close() {
    panel.hidden = true; fab.setAttribute("aria-expanded", "false"); clearInterval(timer);
    timer = setInterval(poll, 15000);   // ปิดอยู่ก็เช็กเบาๆ ถ้าแอดมินตอบจะขึ้นจุดแดงที่ปุ่ม
  }
  fab.addEventListener("click", () => (panel.hidden ? open() : close()));
  $("chat-close").addEventListener("click", close);
  form.addEventListener("submit", (e) => { e.preventDefault(); send(input.value); });
  chips.addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    if (b.hasAttribute("data-human")) toggleHuman(); else send(b.textContent);
  });
  if (inline) open(); else { poll(); timer = setInterval(poll, 15000); }
})();