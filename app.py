
import io
import os
import re
import zipfile
from urllib.parse import urlparse

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

CREDIT_RE = re.compile(
    r"^(?:foto|photo|sumber|source|credit|hak cipta|copyright|"
    r"instagram(?:\.com)?|ig(?:\s|:|$)|via(?:\s|:|$))",
    re.I,
)

BODY_HINT_RE = re.compile(
    r"(article[-_ ]?(body|content|detail)|content[-_ ]?(article|body|detail)|"
    r"detail[-_ ]?(article|content|body)|photo[-_ ]?(detail|content|gallery)|"
    r"gallery[-_ ]?(detail|content|body)|main[-_ ]?content)",
    re.I,
)

def clean_text(s):
    return re.sub(r"\s+", " ", (s or "")).strip()

def get_html(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    return r.text

def filename_from_url(u):
    return os.path.basename(urlparse(u).path)

def is_article_image(u):
    if not u:
        return False
    u = u.split("?")[0]
    return (
        "cdns.klimg.com/resized/" in u
        and bool(re.search(r"\.(jpg|jpeg|png|webp)$", u, re.I))
    )

def original_url(resized_url):
    f = filename_from_url(resized_url)
    m = re.search(r"(?<!\d)((?:19|20)\d{6})(?!\d)", f)
    if not m:
        return None
    d = m.group(1)
    return (
        f"https://cdns.klimg.com/kapanlagi.com/download/g/"
        f"{d[:4]}/{d[4:6]}/{d[6:8]}/r/{f}"
    )

def visible_caption_for_img(img):
    """
    Caption priority:
    1. figcaption
    2. caption-like element in same photo wrapper
    3. visible editorial paragraph near the image
    4. alt only as last fallback
    Credit lines are explicitly ignored.
    """
    def usable(t):
        t = clean_text(t)
        if not t or CREDIT_RE.search(t):
            return ""
        # Ignore navigation/UI fragments.
        if t.lower() in {"prev", "next", "share", "lihat selengkapnya"}:
            return ""
        return t

    # 1) figcaption
    node = img
    for _ in range(7):
        node = getattr(node, "parent", None)
        if not node:
            break
        for fc in node.find_all("figcaption"):
            t = usable(fc.get_text(" ", strip=True))
            if t:
                return t

    # 2) Caption-like elements
    node = img
    for _ in range(7):
        node = getattr(node, "parent", None)
        if not node:
            break
        for el in node.find_all(True):
            cls = " ".join(el.get("class", []))
            ident = el.get("id", "")
            if re.search(r"(caption|keterangan|ket-foto|photo-desc|image-desc|photo-caption|description)", cls + " " + ident, re.I):
                t = usable(el.get_text(" ", strip=True))
                if t and len(t) >= 20:
                    return t

    # 3) Look at nearby siblings. Prefer long editorial text, not credits.
    candidates = []
    parent = img.parent
    if parent:
        for sib in list(parent.children):
            if sib is img:
                continue
            if isinstance(sib, NavigableString):
                t = usable(str(sib))
            else:
                t = usable(sib.get_text(" ", strip=True))
            if t and 20 <= len(t) <= 1200:
                candidates.append(t)

        # A common KapanLagi pattern is image -> paragraph -> credit.
        sib = parent.find_next_sibling()
        hops = 0
        while sib is not None and hops < 4:
            t = usable(sib.get_text(" ", strip=True)) if hasattr(sib, "get_text") else ""
            if t and 20 <= len(t) <= 1200:
                candidates.append(t)
            sib = sib.find_next_sibling()
            hops += 1

    if candidates:
        # Editorial paragraph is usually the longest useful text near the photo.
        candidates.sort(key=lambda x: (len(x), x.count(".")), reverse=True)
        return candidates[0]

    # 4) Last fallback: alt.
    return usable(img.get("alt", ""))

def find_body_containers(soup):
    """
    Build a small set of likely article/gallery containers.
    We intentionally do NOT scan the whole page first because that
    pulls logos, recommendation cards, ads and UI images.
    """
    found = []
    seen = set()

    # Strong selectors first.
    selectors = [
        "article",
        "main article",
        "main",
        "[class*='article-body']",
        "[class*='article-content']",
        "[class*='article-detail']",
        "[class*='detail-content']",
        "[class*='photo-detail']",
        "[class*='photo-content']",
        "[class*='gallery-content']",
        "[class*='gallery-detail']",
        "[id*='article']",
        "[id*='content']",
    ]

    for sel in selectors:
        try:
            for el in soup.select(sel):
                key = id(el)
                if key not in seen:
                    seen.add(key)
                    found.append(el)
        except Exception:
            pass

    # Add containers whose class/id strongly resembles body content.
    for el in soup.find_all(True):
        token = " ".join(el.get("class", [])) + " " + el.get("id", "")
        if BODY_HINT_RE.search(token):
            key = id(el)
            if key not in seen:
                seen.add(key)
                found.append(el)

    return found

def image_size_hint(img):
    for key in ("width", "data-width", "data-original-width"):
        v = img.get(key)
        if v and str(v).isdigit():
            return int(v), None
    for key in ("height", "data-height", "data-original-height"):
        v = img.get(key)
        if v and str(v).isdigit():
            return None, int(v)
    return None, None

def download_dimensions(url):
    try:
        r = requests.get(
            url,
            headers={"User-Agent": UA, "Range": "bytes=0-65535"},
            timeout=15,
        )
        r.raise_for_status()
        im = Image.open(io.BytesIO(r.content))
        return im.width, im.height
    except Exception:
        return None, None

def extract_page(url):
    soup = BeautifulSoup(get_html(url), "html.parser")

    title = ""
    og = soup.find("meta", property="og:title")
    if og:
        title = clean_text(og.get("content", ""))
    if not title and soup.title:
        title = clean_text(soup.title.get_text())

    intro = ""
    desc = soup.find("meta", attrs={"name": "description"})
    if desc:
        intro = clean_text(desc.get("content", ""))

    photos = []
    seen = set()

    # KapanLagi photo-gallery structure:
    # <div class="pages-item" data-type="content-pages">
    #   <figure class="pages-img">
    #      <img ...>
    #      <figcaption class="pages-img-desc">
    #         <p class="pages-img-desc-copyright">instagram.com/...</p>
    #      </figcaption>
    #   </figure>
    #   <div class="pages-paragraph ..."><p>LONG EDITORIAL CAPTION...</p></div>
    # </div>
    #
    # IMPORTANT:
    # - Only images inside content-pages are accepted.
    # - Only sufficiently large source images are accepted.
    # - The long text in .pages-paragraph is the photo caption.
    # - The copyright/Instagram text inside figcaption is NOT the caption.

    gallery_pages = soup.select(".pages-item[data-type='content-pages']")

    for page in gallery_pages:
        figure = page.select_one("figure.pages-img")
        if not figure:
            continue

        img = figure.find("img")
        if not img:
            continue

        src = (
            img.get("data-src")
            or img.get("src")
            or img.get("data-original")
            or img.get("data-lazy-src")
            or ""
        )
        src = src.split("?")[0]

        if not is_article_image(src):
            continue

        # IMPORTANT:
        # KapanLagi's HTML may display the gallery image with width/height
        # attributes such as 375x514 even though the CDN source is a large
        # 670x image. Therefore the "large image" test MUST use the CDN
        # resize segment (e.g. /resized/670x/), not the HTML display size.
        resize_match = re.search(r"/resized/(\\d+)x(?:/|$)", src, re.I)
        resize_width = int(resize_match.group(1)) if resize_match else 0

        try:
            iw = int(img.get("width", 0) or 0)
            ih = int(img.get("height", 0) or 0)
        except Exception:
            iw, ih = 0, 0

        # Gallery editorial source in this KapanLagi format is 670x.
        # Reject genuinely small CDN variants such as 50x, 100x, 300x, etc.
        if resize_width and resize_width < 600:
            continue
        if not resize_width and iw and iw < 600:
            continue

        f = filename_from_url(src).lower()
        if any(x in f for x in [
            "logo", "icon", "avatar", "placeholder", "sprite",
            "banner", "ads", "advert", "close"
        ]):
            continue

        if src in seen:
            continue
        seen.add(src)

        # EXACT photo description: the .pages-paragraph after the figure.
        caption = ""
        paragraph = figure.find_next_sibling(
            lambda tag: getattr(tag, "name", None) == "div"
            and "pages-paragraph" in (tag.get("class") or [])
        )
        if paragraph:
            caption = clean_text(paragraph.get_text(" ", strip=True))

        # If HTML nesting changes slightly, search within the same page,
        # but still ONLY for .pages-paragraph — never use copyright text.
        if not caption:
            paragraphs = page.select(".pages-paragraph")
            if paragraphs:
                caption = clean_text(paragraphs[0].get_text(" ", strip=True))

        # Alt is only a last-resort fallback.
        if not caption:
            caption = clean_text(img.get("alt", ""))

        photos.append({
            "resized": src,
            "original": original_url(src) or src,
            "caption": caption,
            "width": iw,
            "height": ih,
            "cdn_width": resize_width,
        })

    return title, intro, photos

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

    model = st.secrets.get("OPENAI_MODEL", "gpt-5-mini")
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
        return clean_text(response.output_text)[:100]
    except Exception:
        return text[:100]

def load_image(url):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGBA")

def font_path(name):
    p = os.path.join("fonts", FONT_MAP.get(name, "Inter-Regular.ttf"))
    return p if os.path.exists(p) else None

def fit_cover(img, size):
    W, H = size
    iw, ih = img.size
    scale = max(W / iw, H / ih)
    nw, nh = int(iw * scale), int(ih * scale)
    img = img.resize((nw, nh), Image.LANCZOS)
    left = max(0, (nw - W) // 2)
    top = max(0, (nh - H) // 2)
    return img.crop((left, top, left + W, top + H))

def draw_text_layer(base, text, font_name, font_size, text_color,
                    highlight_color, highlight_opacity, position, align):
    canvas = base.copy().convert("RGBA")
    W, H = canvas.size
    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    fp = font_path(font_name)
    try:
        font = ImageFont.truetype(fp, font_size) if fp else ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    margin = max(32, int(W * 0.06))
    max_width = W - margin * 2

    words = clean_text(text).split()
    lines, line = [], ""
    for word in words:
        test = (line + " " + word).strip()
        bb = d.textbbox((0, 0), test, font=font)
        if bb[2] - bb[0] <= max_width:
            line = test
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)

    bb = d.textbbox((0, 0), "Ag", font=font)
    line_h = max(1, bb[3] - bb[1]) + int(font_size * 0.16)
    total_h = line_h * len(lines)
    pad = max(12, int(font_size * 0.35))

    if position == "Atas":
        y = int(H * 0.08)
    elif position == "Tengah":
        y = int((H - total_h) / 2)
    else:
        y = H - total_h - int(H * 0.10)

    # Subtle translucent text backing, useful even when custom template
    # does not provide a text-safe area.
    rect_top = max(0, y - pad)
    rect_bottom = min(H, y + total_h + pad)
    d.rounded_rectangle(
        [margin // 2, rect_top, W - margin // 2, rect_bottom],
        radius=max(8, int(font_size * 0.2)),
        fill=highlight_color + (int(255 * highlight_opacity),),
    )

    for ln in lines:
        bb = d.textbbox((0, 0), ln, font=font)
        tw = bb[2] - bb[0]
        if align == "Kiri":
            x = margin
        elif align == "Kanan":
            x = W - margin - tw
        else:
            x = (W - tw) // 2
        d.text((x, y), ln, font=font, fill=text_color + (255,))
        y += line_h

    return Image.alpha_composite(canvas, layer)

def apply_template(photo, template_img):
    if template_img is None:
        return photo
    tpl = template_img.convert("RGBA").resize(photo.size, Image.LANCZOS)
    return Image.alpha_composite(photo.convert("RGBA"), tpl)

def render_social(img, text, template_img, font_name, font_size,
                  text_color, highlight_color, opacity, position, align):
    # If a custom template exists, its canvas determines the final ratio.
    if template_img is not None:
        target_size = template_img.size
        photo = fit_cover(img.convert("RGBA"), target_size)
    else:
        photo = img.convert("RGBA")

    with_text = draw_text_layer(
        photo, text, font_name, font_size, text_color,
        highlight_color, opacity, position, align
    )
    return apply_template(with_text, template_img).convert("RGB")

st.title("Social Content Studio — KapanLagi")
st.caption("URL artikel → foto body content ukuran besar → caption asli → rewrite AI → template → preview → export")

if "photos" not in st.session_state:
    st.session_state.photos = []
if "rewrites" not in st.session_state:
    st.session_state.rewrites = {}
if "title" not in st.session_state:
    st.session_state.title = ""
if "intro" not in st.session_state:
    st.session_state.intro = ""

url = st.text_input(
    "URL artikel KapanLagi.com",
    placeholder="https://www.kapanlagi.com/foto/..."
)

if st.button("🔎 Analisis Artikel", type="primary") and url:
    try:
        title, intro, photos = extract_page(url)
        st.session_state.title = title
        st.session_state.intro = intro
        st.session_state.photos = photos
        st.session_state.rewrites = {}
        st.success(f"Ditemukan {len(photos)} foto besar dari body content.")
    except Exception as e:
        st.error(f"Gagal membaca artikel: {e}")

if st.session_state.photos:
    st.subheader("1. Deskripsi Post Utama")
    main_desc = st.text_area(
        "Deskripsi post",
        value=st.session_state.intro,
        height=110,
    )
    st.download_button(
        "⬇️ Download deskripsi-post.txt",
        main_desc,
        file_name="deskripsi-post.txt",
    )

    st.divider()
    st.subheader("2. Template Sosial Media")

    template_file = st.file_uploader(
        "Upload template PNG/JPG",
        type=["png", "jpg", "jpeg"],
        help="Paling disarankan PNG transparan. Elemen template akan berada di lapisan paling atas foto.",
    )

    template_img = None
    if template_file is not None:
        try:
            template_img = Image.open(template_file).convert("RGBA")
            st.success(f"Template aktif: {template_img.width} × {template_img.height}px")
            if template_img.getextrema()[-1] != (255, 255):
                st.caption("Template memiliki transparansi dan akan menjadi overlay di atas foto + teks.")
        except Exception as e:
            st.error(f"Template tidak dapat dibaca: {e}")

    c1, c2, c3 = st.columns(3)
    with c1:
        font_name = st.selectbox("Font", list(FONT_MAP.keys()))
        position = st.selectbox("Posisi teks", ["Bawah", "Tengah", "Atas"], index=0)
    with c2:
        font_size = st.slider("Ukuran font", 24, 120, 54)
        align = st.selectbox("Alignment", ["Kiri", "Tengah", "Kanan"], index=0)
    with c3:
        text_color_hex = st.color_picker("Warna teks", "#FFFFFF")
        highlight_color_hex = st.color_picker("Warna backing teks", "#000000")
        opacity = st.slider("Opacity backing teks", 0.0, 1.0, 0.70)

    text_color = tuple(int(text_color_hex.lstrip("#")[i:i+2], 16) for i in (0,2,4))
    highlight_color = tuple(int(highlight_color_hex.lstrip("#")[i:i+2], 16) for i in (0,2,4))

    st.divider()
    st.subheader("3. Foto, Caption & Preview")

    for i, p in enumerate(st.session_state.photos):
        if f"selected_{i}" not in st.session_state:
            st.session_state[f"selected_{i}"] = True

        with st.container(border=True):
            left, right = st.columns([0.85, 1.15])

            with left:
                selected = st.checkbox(f"Pilih foto {i+1}", key=f"selected_{i}")

                st.markdown("**Caption asli — teks editorial di bawah foto**")
                st.text_area(
                    f"Caption asli {i+1}",
                    value=p.get("caption", ""),
                    height=120,
                    disabled=True,
                    key=f"orig_{i}",
                    label_visibility="collapsed",
                )

                if i not in st.session_state.rewrites:
                    st.session_state.rewrites[i] = ai_rewrite(p.get("caption", ""))

                rewritten = st.text_area(
                    f"Rewrite AI {i+1}",
                    value=st.session_state.rewrites[i],
                    max_chars=100,
                    height=90,
                    key=f"rewrite_{i}",
                )
                st.session_state.rewrites[i] = rewritten
                st.caption(f"{len(rewritten)}/100 karakter")

                if st.button("✨ Rewrite ulang", key=f"rerun_{i}"):
                    st.session_state.rewrites[i] = ai_rewrite(p.get("caption", ""))
                    st.rerun()

                if p.get("width") and p.get("height"):
                    st.caption(f"Ukuran sumber terdeteksi: {p['width']} × {p['height']} px")

            with right:
                try:
                    source_img = load_image(p["original"])
                    rendered = render_social(
                        source_img,
                        st.session_state.rewrites[i],
                        template_img,
                        font_name,
                        font_size,
                        text_color,
                        highlight_color,
                        opacity,
                        position,
                        align,
                    )

                    st.markdown("**Preview hasil akhir**")
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
                    st.error(f"Gagal membuat preview: {e}")

    st.divider()

    if st.button("📦 Download Semua", type="primary"):
        selected_items = []

        for i, p in enumerate(st.session_state.photos):
            if not st.session_state.get(f"selected_{i}", False):
                continue
            try:
                img = load_image(p["original"])
                rendered = render_social(
                    img,
                    st.session_state.rewrites.get(i, "")[:100],
                    template_img,
                    font_name,
                    font_size,
                    text_color,
                    highlight_color,
                    opacity,
                    position,
                    align,
                )
                b = io.BytesIO()
                rendered.save(b, format="PNG")
                selected_items.append((i, b.getvalue(), p))
            except Exception:
                pass

        z = io.BytesIO()
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zz:
            for i, data, p in selected_items:
                zz.writestr(f"social-{i+1:02d}.png", data)

            zz.writestr("deskripsi-post.txt", main_desc)

            lines = [f"JUDUL: {st.session_state.title}", "", "CAPTION FOTO:"]
            for i, _, p in selected_items:
                lines += [
                    "",
                    f"FOTO {i+1}",
                    f"Caption asli: {p.get('caption', '')}",
                    f"Rewrite AI: {st.session_state.rewrites.get(i, '')[:100]}",
                ]
            zz.writestr("caption-asli-dan-rewrite.txt", "\n".join(lines))

        st.download_button(
            "⬇️ Download ZIP",
            z.getvalue(),
            file_name="social-content-kapanlagi.zip",
            mime="application/zip",
        )
