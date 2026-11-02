/* หน้าแชทฝั่งแอดมิน: อัปเดตข้อความใหม่ทุก 3 วินาที และส่งคำตอบโดยไม่โหลดหน้าใหม่ */
(function () {
  const box = document.querySelector(".chat-admin");
  if (!box) return;
  const body = document.getElementById("cl-body"), form = document.getElementById("cl-form"), input = document.getElementById("cl-input");
  const names = { customer: "ลูกค้า", ai: "🤖 AI", admin: "👤 แอดมิน" };
  const pill = document.getElementById("mode-pill");
  let last = 0;
  body.querySelectorAll(".cl-msg").forEach((el) => { last = Math.max(last, +el.dataset.id); });
  body.scrollTop = body.scrollHeight;

  function add(m) {
    const row = document.createElement("div");
    row.className = "cl-msg " + m.role; row.dataset.id = m.id;
    const who = document.createElement("span"); who.className = "who"; who.textContent = (names[m.role] || "") + " · " + m.t;
    const b = document.createElement("div"); b.className = "bubble"; b.textContent = m.body;
    row.append(who, b); body.appendChild(row);
  }
  function apply(d) {
    const stick = body.scrollHeight - body.scrollTop - body.clientHeight < 80;
    d.messages.forEach((m) => { if (m.id > last) { add(m); last = m.id; } });
    if (stick || d.messages.length) body.scrollTop = body.scrollHeight;
    if (d.status === "human") { pill.textContent = "แอดมินดูแล"; pill.className = "pill shipped"; }
    else if (d.status === "ai") { pill.textContent = "AI ตอบ"; pill.className = "pill paid"; }
  }
  async function poll() {
    try { apply(await (await fetch(box.dataset.poll + "?after=" + last)).json()); } catch (e) { /* ลองใหม่รอบหน้า */ }
  }
  setInterval(poll, 3000);

  async function send() {
    const text = input.value.trim();
    if (!text) return;
    const fd = new FormData(form);
    input.value = "";
    const res = await fetch(box.dataset.reply, { method: "POST", body: fd, headers: { Accept: "application/json" } });
    if (!res.ok) { input.value = text; alert("ส่งไม่สำเร็จ ลองใหม่อีกครั้ง"); return; }
    await poll();
  }
  form.addEventListener("submit", (e) => { e.preventDefault(); send(); });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } });
})();
