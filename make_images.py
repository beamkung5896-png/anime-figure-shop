"""สร้างภาพตัวละครอนิเมะสไตล์ชิบิ (ออริจินัล) เป็นไฟล์ SVG ลงโฟลเดอร์ static/images
รันครั้งเดียว:  python make_images.py
อยากใช้ภาพของตัวเอง? แค่วางไฟล์ .png/.jpg ชื่อเดียวกันใน static/images แล้วแก้ชื่อไฟล์ใน app.py
"""
import os

CHARACTERS = {
    "sakura": dict(bg=("#FFD6E7", "#F7A8C8"), hair="#F28DB2", hair_dark="#D9628F", eye="#B03A7A",
                   outfit="#2B3A6B", trim="#FFFFFF", style="twin", acc="ribbon", acc_color="#E8466F"),
    "haruto": dict(bg=("#BFE3FF", "#7DB8F0"), hair="#23262E", hair_dark="#101217", eye="#3C7BD9",
                   outfit="#2F6FD0", trim="#FFFFFF", style="spiky", acc="headband", acc_color="#FFFFFF"),
    "yuki": dict(bg=("#E8F1FF", "#BCD3F5"), hair="#EEF2FA", hair_dark="#B9C4DA", eye="#55A8E0",
                 outfit="#8FB9E8", trim="#FFFFFF", style="long", acc="flower", acc_color="#FFFFFF"),
    "rei": dict(bg=("#FFE2B8", "#FFB36B"), hair="#4AA7E8", hair_dark="#2F7DBD", eye="#F2A31B",
                outfit="#F2742C", trim="#FFE9D2", style="twin", acc="goggles", acc_color="#2B2E3A"),
    "kaede": dict(bg=("#D3F5DF", "#8FD9A8"), hair="#58C27B", hair_dark="#3A9A5B", eye="#2C7A52",
                  outfit="#3C6B4C", trim="#F3E3A2", style="short", acc="leaf", acc_color="#F3E3A2"),
    "rin": dict(bg=("#FFD0C8", "#F28C7C"), hair="#D9342B", hair_dark="#A31F19", eye="#7A2A1E",
                outfit="#F5F5F5", trim="#D9342B", style="pony", acc="band", acc_color="#FFFFFF"),
    "mio": dict(bg=("#E3D3FF", "#B493F0"), hair="#8E5FE0", hair_dark="#6A3DB8", eye="#E5C13C",
                outfit="#3B2A6E", trim="#E5C13C", style="long", acc="witchhat", acc_color="#2A1D52"),
    "sora": dict(bg=("#FFF3B8", "#F5D25E"), hair="#F7D046", hair_dark="#D1A421", eye="#2E86C9",
                 outfit="#3A3F55", trim="#E8C441", style="spiky", acc="ninja", acc_color="#2A2E40"),
}
SKIN, SKIN_SHADE, OUT = "#FFE3CF", "#F4C7AB", "#3B2A33"


def back_hair(c):
    h, hd, s = c["hair"], c["hair_dark"], c["style"]
    if s == "long":
        return f'<path d="M98 200 Q96 92 200 90 Q304 92 302 200 L322 410 Q200 438 78 410 Z" fill="{hd}"/>'
    if s == "twin":
        return (f'<path d="M104 190 Q40 260 60 400 Q95 410 112 330 Z" fill="{hd}"/>'
                f'<path d="M296 190 Q360 260 340 400 Q305 410 288 330 Z" fill="{hd}"/>')
    if s == "pony":
        return (f'<path d="M290 150 Q380 170 350 300 Q335 380 300 400 Q320 300 280 220 Z" fill="{hd}"/>')
    return ""


def top_hair(c):
    h, hd, s = c["hair"], c["hair_dark"], c["style"]
    cap = f'<path d="M100 200 Q96 88 200 84 Q304 88 300 200 Q282 150 248 164 Q226 138 200 168 Q174 138 152 164 Q118 150 100 200 Z" fill="{h}"/>'
    shine = f'<path d="M130 120 Q170 98 215 102" stroke="#fff" stroke-opacity=".45" stroke-width="9" fill="none" stroke-linecap="round"/>'
    spikes = ""
    if s == "spiky":
        spikes = (f'<path d="M110 130 L92 70 L150 100 L168 48 L200 96 L236 44 L250 100 L308 66 L292 134 Z" fill="{h}"/>')
    if s == "short":
        spikes = f'<path d="M104 150 Q110 70 200 66 Q290 70 296 150 Q200 110 104 150 Z" fill="{h}"/>'
    side = (f'<path d="M100 200 L96 262 Q118 250 122 200 Z" fill="{h}"/>'
            f'<path d="M300 200 L304 262 Q282 250 278 200 Z" fill="{h}"/>') if s in ("long", "short", "spiky", "pony") else ""
    return spikes + side + cap + shine


def accessory(c):
    a, col = c["acc"], c["acc_color"]
    if a == "ribbon":
        return (f'<path d="M112 120 L70 96 L78 150 Z" fill="{col}"/><path d="M112 120 L150 90 L146 142 Z" fill="{col}"/>'
                f'<circle cx="112" cy="120" r="12" fill="{col}" stroke="#fff" stroke-width="3"/>')
    if a == "headband":
        return f'<path d="M104 150 Q200 112 296 150" stroke="{col}" stroke-width="14" fill="none"/><circle cx="200" cy="128" r="12" fill="#C9D6EA" stroke="#9AA9C4" stroke-width="3"/>'
    if a == "flower":
        return "".join(f'<circle cx="{262+dx}" cy="{118+dy}" r="11" fill="{col}" stroke="#9FB8DC" stroke-width="2"/>' for dx, dy in [(0,-14),(14,-4),(9,12),(-9,12),(-14,-4)]) + '<circle cx="262" cy="118" r="8" fill="#F5C842"/>'
    if a == "goggles":
        return (f'<rect x="120" y="96" width="160" height="30" rx="15" fill="{col}"/>'
                f'<circle cx="162" cy="111" r="17" fill="#8FE0FF" stroke="#fff" stroke-width="4"/><circle cx="238" cy="111" r="17" fill="#8FE0FF" stroke="#fff" stroke-width="4"/>')
    if a == "leaf":
        return f'<path d="M262 100 Q310 70 330 100 Q300 130 262 100 Z" fill="#7FD88F" stroke="{col}" stroke-width="3"/>'
    if a == "band":
        return f'<circle cx="300" cy="170" r="14" fill="{col}" stroke="#D9342B" stroke-width="4"/>'
    if a == "witchhat":
        return (f'<ellipse cx="200" cy="96" rx="130" ry="20" fill="{col}"/><path d="M140 96 Q165 20 215 -6 Q205 50 260 96 Z" fill="{col}"/>'
                f'<rect x="150" y="76" width="104" height="14" fill="#E5C13C"/>')
    if a == "ninja":
        return (f'<path d="M98 142 Q200 108 302 142 L302 166 Q200 132 98 166 Z" fill="{col}"/>'
                f'<rect x="170" y="128" width="60" height="30" rx="6" fill="#C8CCD8" stroke="#8C91A3" stroke-width="3"/><path d="M200 135 L200 152 M190 143 L210 143" stroke="#555" stroke-width="3"/>')
    return ""


def svg(key, c):
    g1, g2 = c["bg"]
    ex1, ex2 = 160, 240
    eyes = ""
    for ex in (ex1, ex2):
        eyes += (f'<ellipse cx="{ex}" cy="208" rx="19" ry="26" fill="#fff" stroke="{OUT}" stroke-width="3"/>'
                 f'<ellipse cx="{ex}" cy="212" rx="14" ry="21" fill="{c["eye"]}"/>'
                 f'<ellipse cx="{ex}" cy="204" rx="14" ry="11" fill="{OUT}" fill-opacity=".35"/>'
                 f'<circle cx="{ex}" cy="214" r="7" fill="#1B1620"/>'
                 f'<circle cx="{ex-6}" cy="202" r="6" fill="#fff"/><circle cx="{ex+6}" cy="220" r="3" fill="#fff"/>'
                 f'<path d="M{ex-21} 192 Q{ex} 178 {ex+21} 192" stroke="{OUT}" stroke-width="5" fill="none" stroke-linecap="round"/>')
    stars = "".join(f'<circle cx="{x}" cy="{y}" r="{r}" fill="#fff" fill-opacity=".55"/>' for x, y, r in
                    [(40,60,6),(350,90,8),(60,330,5),(345,330,7),(320,40,4),(30,200,4),(370,220,5)])
    legs = (f'<rect x="160" y="404" width="30" height="42" rx="10" fill="{SKIN}"/><rect x="210" y="404" width="30" height="42" rx="10" fill="{SKIN}"/>'
            f'<ellipse cx="172" cy="450" rx="26" ry="12" fill="{OUT}"/><ellipse cx="228" cy="450" rx="26" ry="12" fill="{OUT}"/>')
    body = (f'<path d="M138 288 Q200 270 262 288 L282 410 Q200 424 118 410 Z" fill="{c["outfit"]}"/>'
            f'<path d="M168 282 L200 330 L232 282 Z" fill="{c["trim"]}"/>'
            f'<path d="M118 410 Q200 424 282 410" stroke="{c["trim"]}" stroke-width="8" fill="none"/>'
            f'<rect x="92" y="296" width="40" height="92" rx="20" fill="{c["outfit"]}" transform="rotate(8 112 296)"/>'
            f'<rect x="268" y="296" width="40" height="92" rx="20" fill="{c["outfit"]}" transform="rotate(-8 288 296)"/>'
            f'<circle cx="100" cy="392" r="17" fill="{SKIN}"/><circle cx="300" cy="392" r="17" fill="{SKIN}"/>')
    head = (f'<rect x="184" y="262" width="32" height="34" rx="10" fill="{SKIN_SHADE}"/>'
            f'<ellipse cx="200" cy="200" rx="98" ry="92" fill="{SKIN}"/>'
            f'<ellipse cx="128" cy="244" rx="17" ry="10" fill="#FF9DB0" fill-opacity=".55"/>'
            f'<ellipse cx="272" cy="244" rx="17" ry="10" fill="#FF9DB0" fill-opacity=".55"/>'
            f'<path d="M184 252 Q200 268 216 252" stroke="{OUT}" stroke-width="4" fill="none" stroke-linecap="round"/>')
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 400 500" role="img" aria-label="ตัวละคร {key}">
<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{g1}"/><stop offset="1" stop-color="{g2}"/></linearGradient></defs>
<rect width="400" height="500" fill="url(#g)"/>{stars}
<ellipse cx="200" cy="462" rx="130" ry="20" fill="#000" fill-opacity=".18"/>
{back_hair(c)}{legs}{body}{head}{eyes}{top_hair(c)}{accessory(c)}
</svg>'''


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(__file__), "static", "images")
    os.makedirs(out, exist_ok=True)
    for k, c in CHARACTERS.items():
        with open(os.path.join(out, f"{k}.svg"), "w", encoding="utf-8") as f:
            f.write(svg(k, c))
    print("สร้างภาพครบ", len(CHARACTERS), "ตัว")
