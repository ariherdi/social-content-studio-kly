
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
    r = requests.get(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.text

def fetch_html(url):
    """Fetch and return diagnostics as well as HTML."""
    r = requests.get(
        url,
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
        },
        timeout=30,
        allow_redirects=True,
    )
    return r.text, {
        "requested_url": url,
        "final_url": r.url,
        "status": r.status_code,
        "content_type": r.headers.get("content-type", ""),
        "bytes": len(r.content),
    }

def filename_from_url(u):
    return os.path.basename(urlparse(u).path)

def is_article_image(u):
    if not u:
        return False
    u = u.split("?")[0].strip()
    return (
        "cdns.klimg.com/resized/" in u
        and bool(re.search(r"\.(jpg|jpeg|png|webp)$", u, re.I))
    )

def original_url(resized_url):
    """Build KapanLagi full-resolution download URL from filename date."""
    f = filename_from_url(resized_url)
    m = re.search(r"(?<!\d)((?:19|20)\d{6})(?!\d)", f)
    if not m:
        return None
    d = m.group(1)
    return (
        f"https://cdns.klimg.com/kapanlagi.com/"
        f"download/g/{d[:4]}/{d[4:6]}/{d[6:8]}/r/{f}"
    )

def extract_img_url(img):
    """Get the KapanLagi resized image URL from common lazy-loading attributes."""
    attrs = ["data-src", "data-original", "data-lazy-src", "src"]
    for key in attrs:
        v = img.get(key)
        if is_article_image(v):
            return v.split("?")[0]

    for key in ("data-srcset", "srcset"):
        raw = img.get(key, "")
        if raw:
            # Prefer the largest candidate in srcset.
            candidates = []
            for part in raw.split(","):
                bits = part.strip().split()
                if not bits:
                    continue
                u = bits[0]
                if is_article_image(u):
                    score = 0
                    if len(bits) > 1:
                        m = re.match(r"(\d+)w", bits[1])
                        if m:
                            score = int(m.group(1))
                    candidates.append((score, u.split("?")[0]))
            if candidates:
                candidates.sort(reverse=True)
                return candidates[0][1]
    return ""

def page_urls_from_soup(soup, base_url):
    urls = []
    seen = set()

    # The supplied KapanLagi DOM exposes exact gallery page URLs in data-pageurl.
    for el in soup.select(".pages-item[data-pageurl], [data-pageurl]"):
        u = (el.get("data-pageurl") or "").strip()
        if u and "kapanlagi.com" in u and u not in seen:
            seen.add(u)
            urls.append(u)

    # If the live response does not expose those nodes, generate page URLs.
    # KapanLagi photo galleries use ?page=1, ?page=2, ... for the gallery photos.
    max_page = 10
    for el in soup.select("[data-pagemax]"):
        try:
            max_page = max(max_page, min(30, int(el.get("data-pagemax"))))
        except Exception:
            pass

    parsed = urlparse(base_url)
    base_clean = parsed._replace(query="", fragment="").geturl()
    for n in range(1, max_page + 1):
        u = f"{base_clean}?page={n}"
        if u not in seen:
            urls.append(u)
            seen.add(u)

    return urls

def parse_gallery_html(html, source_url, photos, seen):
    soup = BeautifulSoup(html, "html.parser")
    diag = {
        "url": source_url,
        "content_pages": len(soup.select(".pages-item[data-type='content-pages']")),
        "pages_img": len(soup.select("figure.pages-img")),
        "imgs": len(soup.find_all("img")),
        "article_images": 0,
    }

    # First choice: exact gallery structure from the supplied KapanLagi HTML.
    pages = soup.select(".pages-item[data-type='content-pages']")
    if not pages:
        # Some page responses contain the figure directly without the wrapper.
        pages = soup.select("figure.pages-img")

    for page in pages:
        if getattr(page, "name", None) == "figure":
            figure = page
        else:
            figure = page.select_one("figure.pages-img") or page

        img = figure.find("img") if figure else None
        caption = ""

        # IMPORTANT: KapanLagi's real editorial text is NOT the image alt/caption.
        # It is the .pages-paragraph that appears AFTER the figure, inside the
        # same .box-body. We deliberately find that paragraph relative to the
        # figure so ads/other markup inside the figure cannot break extraction.
        if figure:
            box_body = figure.find_parent(class_="box-body")
            if box_body:
                para = figure.find_next("div", class_="pages-paragraph")
                if para and para.find_parent(class_="box-body") is box_body:
                    caption = clean_text(para.get_text(" ", strip=True))

                # Extra fallback: inspect all paragraphs in this exact box and
                # take the first one that occurs after the figure in the DOM.
                if not caption:
                    for candidate in box_body.select(".pages-paragraph"):
                        if candidate.find_parent(class_="box-body") is box_body:
                            previous_figures = candidate.find_all_previous("figure", class_="pages-img")
                            if previous_figures and previous_figures[0] is figure:
                                caption = clean_text(candidate.get_text(" ", strip=True))
                                break

        if not img:
            continue

        resized = extract_img_url(img)
        if not is_article_image(resized):
            continue
        diag["article_images"] += 1

        filename = filename_from_url(resized)
        original = original_url(resized)
        if not filename or not original or original in seen:
            continue
        seen.add(original)

        if not caption:
            caption = clean_text(img.get("alt", ""))

        photos.append({
            "resized": resized,
            "original": original,
            "caption": caption,
            "filename": filename,
            "source_page": source_url,
        })

    # Diagnostic fallback: inspect all resized CDN images, but only accept filenames
    # that contain the KapanLagi YYYYMMDD pattern used by original_url().
    if not pages:
        for img in soup.find_all("img"):
            resized = extract_img_url(img)
            if not is_article_image(resized):
                continue
            filename = filename_from_url(resized)
            original = original_url(resized)
            if not filename or not original or original in seen:
                continue
            seen.add(original)
            photos.append({
                "resized": resized,
                "original": original,
                "caption": clean_text(img.get("alt", "")),
                "filename": filename,
                "source_page": source_url,
            })

    return diag, soup

def extract_page(url):
    # Fetch initial document.
    html, first_diag = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")

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
    diagnostics = []

    # 1. Parse whatever gallery is already present in the initial response.
    d, _ = parse_gallery_html(html, url, photos, seen)
    d.update({k: first_diag[k] for k in ("status", "bytes", "content_type", "final_url")})
    diagnostics.append(d)

    # 2. Crucial fallback: crawl the gallery page URLs.
    page_urls = page_urls_from_soup(soup, url)
    for page_url in page_urls:
        if page_url == url:
            continue
        if len(photos) >= 30:
            break
        try:
            page_html, pd = fetch_html(page_url)
            pdg, _ = parse_gallery_html(page_html, page_url, photos, seen)
            pdg.update({k: pd[k] for k in ("status", "bytes", "content_type", "final_url")})
            diagnostics.append(pdg)
        except Exception as e:
            diagnostics.append({"url": page_url, "error": str(e)})

    # Remove duplicate diagnostics for repeated URLs while preserving order.
    unique_diag = []
    seen_diag = set()
    for d in diagnostics:
        key = d.get("url")
        if key in seen_diag:
            continue
        seen_diag.add(key)
        unique_diag.append(d)

    return title, intro, photos, unique_diag

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
if "diagnostics" not in st.session_state:
    st.session_state.diagnostics = []

url = st.text_input(
    "URL artikel KapanLagi.com",
    placeholder="https://www.kapanlagi.com/foto/..."
)

if st.button("🔎 Analisis Artikel", type="primary") and url:
    try:
        title, intro, photos, diagnostics = extract_page(url)
        st.session_state.diagnostics = diagnostics
        st.session_state.title = title
        st.session_state.intro = intro
        st.session_state.photos = photos
        st.session_state.rewrites = {}
        st.success(f"Ditemukan {len(photos)} foto besar dari body content.")
        if not photos:
            st.warning("Gallery tidak muncul pada HTML awal, jadi app sudah mencoba URL halaman foto ?page=1, ?page=2, dan seterusnya. Lihat Diagnosis Fetch di bawah untuk mengetahui respons server.")
            with st.expander("🔧 Diagnosis Fetch & Parser", expanded=True):
                st.dataframe(st.session_state.diagnostics, use_container_width=True)
                st.caption("content_pages = jumlah .pages-item[data-type='content-pages']; pages_img = jumlah figure.pages-img; article_images = CDN resized yang berhasil dikenali.")
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
            zz.writestr("teks-asli-dan-rewrite.txt", "\n".join(lines))

        st.download_button(
            "⬇️ Download ZIP",
            z.getvalue(),
            file_name="social-content-kapanlagi.zip",
            mime="application/zip",
        )
