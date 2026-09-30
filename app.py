
import io
import os
import re
import zipfile
from urllib.parse import urljoin, urlparse

import requests
import streamlit as st
from bs4 import BeautifulSoup, NavigableString
from PIL import Image, ImageDraw, ImageFont

try:
    from openai import OpenAI
except Exception:
    OpenAI = None

st.set_page_config(page_title="Social Content Studio — KapanLagi", layout="wide")

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/140 Safari/537.36"

FONT_MAP = {
    "Poppins": "Poppins-Regular.ttf",
    "Montserrat": "Montserrat-Regular.ttf",
    "Oswald": "Oswald-Regular.ttf",
    "Playfair Display": "PlayfairDisplay-Regular.ttf",
    "Anton": "Anton-Regular.ttf",
    "Bebas Neue": "BebasNeue-Regular.ttf",
    "Inter": "Inter-Regular.ttf",
}

def clean_text(s):
    return re.sub(r"\s+", " ", (s or "")).strip()

def get_html(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=25)
    r.raise_for_status()
    return r.text

def filename_from_url(u):
    return os.path.basename(urlparse(u).path)

def is_article_image(u):
    if not u:
        return False
    u = u.split("?")[0]
    return "cdns.klimg.com/resized/" in u and bool(re.search(r"\.(jpg|jpeg|png|webp)$", u, re.I))

def original_url(resized_url):
    f = filename_from_url(resized_url)
    m = re.search(r"(?<!\d)((?:19|20)\d{6})(?!\d)", f)
    if not m:
        return None
    d = m.group(1)
    return f"https://cdns.klimg.com/kapanlagi.com/download/g/{d[:4]}/{d[4:6]}/{d[6:8]}/r/{f}"

def is_credit_text(t):
    t = clean_text(t)
    if not t:
        return True
    low = t.lower()
    # Credits/metadata should never win over the long editorial caption.
    if re.search(r'^(instagram|tiktok|x\.com|twitter|youtube|facebook)\\.com', low):
        return True
    if re.search(r'^(foto|photo|credit|sumber|source|dok|dokumentasi|via)\\s*:', low):
        return True
    if re.fullmatch(r'(instagram|tiktok|x|twitter|facebook|youtube)\\s*[:@].*', low):
        return True
    if re.search(r'instagram\\.com|tiktok\\.com|twitter\\.com|x\\.com', low) and len(t) < 120:
        return True
    return False

def candidate_text(el):
    if isinstance(el, NavigableString):
        return clean_text(str(el))
    if not getattr(el, 'name', None):
        return ''
    return clean_text(el.get_text(' ', strip=True))

def nearby_caption(img):
    """Extract the long editorial text displayed below the photo.

    KapanLagi photo pages can place a short Instagram/photo credit immediately
    before the actual paragraph caption. Therefore we collect several nearby
    candidates and choose the most caption-like/long candidate instead of
    returning the first text node.
    """
    candidates = []

    # 1) Explicit figcaption is the strongest signal.
    node = img
    for _ in range(6):
        node = getattr(node, 'parent', None)
        if not node:
            break
        for fc in node.find_all('figcaption', recursive=False):
            t = candidate_text(fc)
            if t and not is_credit_text(t):
                candidates.append((10000 + min(len(t), 1000), t))

    # 2) Search the nearest wrappers for elements whose class/id clearly says
    # caption/description/keterangan. Prefer the longest matching text.
    patterns = re.compile(
        r'(caption|photo-caption|image-caption|photo-desc|photo-description|'
        r'keterangan|ket-foto|ket_foto|deskripsi-foto|description)', re.I
    )
    node = img
    for depth in range(7):
        node = getattr(node, 'parent', None)
        if not node:
            break
        for el in node.find_all(True):
            if el is img:
                continue
            cls = ' '.join(el.get('class', []))
            ident = el.get('id', '')
            if patterns.search(cls) or patterns.search(ident):
                t = candidate_text(el)
                if 15 <= len(t) <= 1200 and not is_credit_text(t):
                    # Closer wrapper + longer text gets higher score.
                    score = 7000 - depth * 100 + min(len(t), 1000)
                    candidates.append((score, t))

    # 3) Collect text blocks following the image inside its immediate wrapper.
    # This is the important KapanLagi case: credit first, long paragraph next.
    parent = img.parent
    if parent:
        children = list(parent.children)
        try:
            idx = children.index(img)
        except ValueError:
            idx = -1

        if idx >= 0:
            for sib in children[idx + 1:idx + 9]:
                t = candidate_text(sib)
                if 15 <= len(t) <= 1200 and not is_credit_text(t):
                    # Long editorial paragraph should beat a short credit.
                    score = 5000 + min(len(t) * 2, 1800)
                    candidates.append((score, t))

        # Also inspect the next few sibling blocks of the image wrapper.
        sib = parent.find_next_sibling()
        hops = 0
        while sib is not None and hops < 5:
            t = candidate_text(sib)
            if 15 <= len(t) <= 1200 and not is_credit_text(t):
                score = 4300 + min(len(t) * 2, 1800)
                candidates.append((score, t))
            sib = sib.find_next_sibling()
            hops += 1

    # 4) As a broader fallback, inspect nearby ancestor children, but do not
    # cross another image. This helps when the caption is nested deeper.
    node = img
    for depth in range(1, 6):
        node = getattr(node, 'parent', None)
        if not node:
            break
        seen_image = False
        for el in node.find_all(recursive=False):
            if el.find('img') is not None:
                if el is img or el.find(img) is not None:
                    continue
                seen_image = True
            if seen_image:
                break
            t = candidate_text(el)
            if 20 <= len(t) <= 1200 and not is_credit_text(t):
                # Only a fallback; explicit caption candidates remain stronger.
                score = 2500 - depth * 100 + min(len(t), 1000)
                candidates.append((score, t))

    if candidates:
        # Deduplicate and choose highest-scoring candidate. If scores are close,
        # the longer text wins, which is exactly what we want for the screenshot.
        best = max(candidates, key=lambda x: (x[0], len(x[1])))
        return best[1]

    # 5) Last fallback: alt text.
    return clean_text(img.get('alt', ''))

def extract_page(url):
    soup = BeautifulSoup(get_html(url), "html.parser")

    title = clean_text((soup.find("meta", property="og:title") or {}).get("content", "")) if soup.find("meta", property="og:title") else ""
    if not title and soup.title:
        title = clean_text(soup.title.get_text())

    intro = ""
    desc = soup.find("meta", attrs={"name": "description"})
    if desc:
        intro = clean_text(desc.get("content", ""))

    candidates = []
    seen = set()

    # Prefer article/gallery area.
    scopes = []
    for sel in ["article", "main", "[class*='article']", "[class*='gallery']", "[class*='detail']"]:
        scopes.extend(soup.select(sel))

    search_nodes = scopes if scopes else [soup]

    for scope in search_nodes:
        for img in scope.find_all("img"):
            src = img.get("src") or img.get("data-src") or img.get("data-original") or ""
            if not is_article_image(src):
                continue
            src = src.split("?")[0]
            f = filename_from_url(src).lower()
            if any(x in f for x in ["logo", "icon", "avatar", "placeholder", "sprite", "banner", "ads", "advert"]):
                continue
            if src in seen:
                continue
            seen.add(src)
            candidates.append({
                "resized": src,
                "original": original_url(src) or src,
                "caption": nearby_caption(img),
            })

    # If scoped extraction missed images, scan whole page.
    if not candidates:
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src") or img.get("data-original") or ""
            if not is_article_image(src):
                continue
            src = src.split("?")[0]
            f = filename_from_url(src).lower()
            if any(x in f for x in ["logo", "icon", "avatar", "placeholder", "sprite", "banner", "ads", "advert"]):
                continue
            if src in seen:
                continue
            seen.add(src)
            candidates.append({
                "resized": src,
                "original": original_url(src) or src,
                "caption": nearby_caption(img),
            })

    # Limit to editorial gallery photos; dedupe by filename.
    out, filenames = [], set()
    for p in candidates:
        fn = filename_from_url(p["resized"])
        if fn in filenames:
            continue
        filenames.add(fn)
        out.append(p)

    return title, intro, out

def get_openai_client():
    if OpenAI is None:
        return None
    key = st.secrets.get("OPENAI_API_KEY", "")
    if not key:
        return None
    return OpenAI(api_key=key)

def ai_rewrite(text):
    text = clean_text(text)
    if not text:
        return ""
    client = get_openai_client()
    if not client:
        return text[:100]

    model = st.secrets.get("OPENAI_MODEL", "gpt-5.6-luna")
    try:
        response = client.responses.create(
            model=model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "Kamu adalah editor sosial media berita Indonesia. "
                        "Rewrite caption foto menjadi satu kalimat/frasa singkat, jelas, menarik, dan faktual. "
                        "Jangan menambah informasi baru. Maksimal 100 karakter. "
                        "Jangan memakai tanda kutip. Jangan menyebut kata caption."
                    ),
                },
                {"role": "user", "content": text},
            ],
        )
        result = clean_text(response.output_text)
        return result[:100]
    except Exception as e:
        st.warning(f"AI rewrite gagal: {e}")
        return text[:100]

def load_image(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGB")

def font_path(name):
    local = os.path.join("fonts", FONT_MAP.get(name, "Inter-Regular.ttf"))
    return local if os.path.exists(local) else None

def render_image(img, text, template, font_name, font_size, text_color, highlight_color, opacity):
    base = img.copy().convert("RGBA")
    W, H = base.size
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    fp = font_path(font_name)
    if fp:
        try:
            font = ImageFont.truetype(fp, font_size)
        except Exception:
            font = ImageFont.load_default()
    else:
        font = ImageFont.load_default()

    margin = max(24, int(W * 0.035))
    max_width = W - margin * 2

    words = text.split()
    lines, line = [], ""
    for word in words:
        test = (line + " " + word).strip()
        box = d.textbbox((0, 0), test, font=font)
        if box[2] - box[0] <= max_width:
            line = test
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)

    bbox = d.textbbox((0, 0), "Ag", font=font)
    line_h = bbox[3] - bbox[1] + 12
    total_h = line_h * len(lines)
    pad = max(18, int(font_size * 0.45))

    if template == "bar bawah solid":
        y0 = H - total_h - pad * 2
        d.rectangle([0, y0, W, H], fill=highlight_color + (int(255 * opacity),))
        y = y0 + pad
    elif template == "bar bawah gradient":
        grad_h = total_h + pad * 2
        y0 = H - grad_h
        for yy in range(y0, H):
            a = int(210 * ((yy - y0) / max(1, grad_h)))
            d.line([(0, yy), (W, yy)], fill=(0, 0, 0, a))
        y = y0 + pad
    else:
        ribbon_h = total_h + pad * 2
        y0 = pad
        d.rounded_rectangle([margin//2, y0, W-margin//2, y0+ribbon_h], radius=18,
                            fill=highlight_color + (int(255 * opacity),))
        y = y0 + pad

    for ln in lines:
        box = d.textbbox((0, 0), ln, font=font)
        tw = box[2] - box[0]
        x = (W - tw) // 2
        d.text((x, y), ln, font=font, fill=text_color + (255,))
        y += line_h

    return Image.alpha_composite(base, overlay).convert("RGB")

def color_tuple(hex_color):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0,2,4))

st.title("Social Content Studio — KapanLagi")
st.caption("POC: URL artikel → foto editorial → caption asli → rewrite AI → visual siap posting")

url = st.text_input("URL artikel KapanLagi.com", placeholder="https://www.kapanlagi.com/foto/...")

if "photos" not in st.session_state:
    st.session_state.photos = []
if "rewrites" not in st.session_state:
    st.session_state.rewrites = {}
if "title" not in st.session_state:
    st.session_state.title = ""
if "intro" not in st.session_state:
    st.session_state.intro = ""

if st.button("🔎 Analisis Artikel", type="primary") and url:
    try:
        title, intro, photos = extract_page(url)
        st.session_state.title = title
        st.session_state.intro = intro
        st.session_state.photos = photos
        st.session_state.rewrites = {}
        st.success(f"Ditemukan {len(photos)} foto editorial.")
    except Exception as e:
        st.error(f"Gagal membaca artikel: {e}")

if st.session_state.photos:
    st.subheader("Deskripsi Post Utama")
    main_desc = st.text_area("Teks yang akan menjadi deskripsi post", value=st.session_state.intro, height=110)
    st.download_button("⬇️ Download deskripsi-post.txt", main_desc, file_name="deskripsi-post.txt")

    st.divider()
    st.subheader("Pengaturan Visual")

    c1, c2, c3 = st.columns(3)
    with c1:
        template = st.selectbox("Template", ["bar bawah solid", "bar bawah gradient", "pita atas"])
        font_name = st.selectbox("Font", list(FONT_MAP.keys()))
    with c2:
        font_size = st.slider("Ukuran font", 24, 120, 54)
        opacity = st.slider("Opacity highlight", 0.10, 1.00, 0.85)
    with c3:
        text_color_hex = st.color_picker("Warna teks", "#FFFFFF")
        highlight_color_hex = st.color_picker("Warna highlight", "#000000")

    text_color = color_tuple(text_color_hex)
    highlight_color = color_tuple(highlight_color_hex)

    st.divider()
    st.subheader("Foto & Caption")

    for i, p in enumerate(st.session_state.photos):
        if f"selected_{i}" not in st.session_state:
            st.session_state[f"selected_{i}"] = True

        with st.container(border=True):
            col_img, col_edit = st.columns([1.15, 1])

            with col_edit:
                selected = st.checkbox(f"Pilih foto {i+1}", key=f"selected_{i}")

                st.markdown("**Caption asli — teks di bawah foto**")
                st.text_area(
                    f"Caption asli {i+1}",
                    value=p["caption"],
                    height=90,
                    disabled=True,
                    key=f"orig_{i}",
                    label_visibility="collapsed",
                )

                if i not in st.session_state.rewrites:
                    st.session_state.rewrites[i] = ai_rewrite(p["caption"])

                rewritten = st.text_area(
                    f"Caption rewrite AI {i+1}",
                    value=st.session_state.rewrites[i],
                    max_chars=100,
                    height=90,
                    key=f"rewrite_{i}",
                    help="Teks ini yang akan ditempel ke foto saat diekspor.",
                )
                st.session_state.rewrites[i] = rewritten
                st.caption(f"{len(rewritten)}/100 karakter — teks yang ditempel ke foto")

                if st.button("✨ Rewrite ulang", key=f"rerun_{i}"):
                    st.session_state.rewrites[i] = ai_rewrite(p["caption"])
                    st.rerun()

            with col_img:
                try:
                    preview = load_image(p["original"])
                    rendered = render_image(
                        preview,
                        st.session_state.rewrites[i],
                        template,
                        font_name,
                        font_size,
                        text_color,
                        highlight_color,
                        opacity,
                    )
                    st.image(rendered, use_container_width=True)

                    bio = io.BytesIO()
                    rendered.save(bio, format="PNG")
                    st.download_button(
                        "⬇️ Download PNG",
                        bio.getvalue(),
                        file_name=f"social-{i+1:02d}.png",
                        mime="image/png",
                        key=f"dl_{i}",
                    )
                except Exception as e:
                    st.error(f"Gagal memuat/render foto: {e}")

    if st.button("📦 Download Semua", type="primary"):
        selected_items = []
        for i, p in enumerate(st.session_state.photos):
            if st.session_state.get(f"selected_{i}", False):
                try:
                    img = load_image(p["original"])
                    out = render_image(
                        img,
                        st.session_state.rewrites.get(i, "")[:100],
                        template,
                        font_name,
                        font_size,
                        text_color,
                        highlight_color,
                        opacity,
                    )
                    b = io.BytesIO()
                    out.save(b, format="PNG")
                    selected_items.append((i, b.getvalue(), p))
                except Exception:
                    pass

        z = io.BytesIO()
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zz:
            for i, data, p in selected_items:
                zz.writestr(f"social-{i+1:02d}.png", data)

            lines = [
                f"JUDUL: {st.session_state.title}",
                "",
                "DESKRIPSI POST:",
                main_desc,
                "",
                "CAPTION FOTO:",
            ]
            for i, data, p in selected_items:
                lines += [
                    "",
                    f"FOTO {i+1}",
                    f"Caption asli: {p['caption']}",
                    f"Rewrite AI: {st.session_state.rewrites.get(i, '')[:100]}",
                ]
            zz.writestr("deskripsi-post.txt", main_desc)
            zz.writestr("caption-asli-dan-rewrite.txt", "\n".join(lines))

        st.download_button(
            "⬇️ Download ZIP",
            z.getvalue(),
            file_name="social-content-kapanlagi.zip",
            mime="application/zip",
        )
