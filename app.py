import io
import os
import re
import zipfile
from urllib.parse import urlparse

import requests
import streamlit as st
from bs4 import BeautifulSoup, NavigableString, Comment
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

def normalize_candidate_url(u):
    if not u:
        return ""
    u = str(u).strip().replace("&amp;", "&")
    if u.startswith("//"):
        u = "https:" + u
    return u.split("?")[0].strip()


def filename_from_url(u):
    return os.path.basename(urlparse(normalize_candidate_url(u)).path)


def looks_like_article_filename(filename):
    """KapanLagi gallery filenames carry an 8-digit YYYYMMDD date."""
    if not filename or not re.search(r"\.(jpg|jpeg|png|webp)$", filename, re.I):
        return False
    if not re.search(r"(?<!\d)(?:19|20)\d{6}(?!\d)", filename):
        return False
    return True


def is_article_image(u):
    """Accept KapanLagi image variants, not only /resized/ URLs.

    The live Streamlit response can rewrite/remove the original CDN path while
    retaining the article filename. Filename/date is therefore the stronger
    identifier for this gallery.
    """
    u = normalize_candidate_url(u)
    if not u:
        return False
    f = filename_from_url(u)
    return looks_like_article_filename(f)


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
    """Extract the most useful image URL from common and rewritten attrs."""
    attrs = ["data-src", "data-original", "data-lazy-src", "data-url", "src"]
    for key in attrs:
        v = normalize_candidate_url(img.get(key))
        if v and is_article_image(v):
            return v

    for key in ("data-srcset", "srcset"):
        raw = img.get(key, "")
        if raw:
            candidates = []
            for part in raw.split(","):
                bits = part.strip().split()
                if not bits:
                    continue
                u = normalize_candidate_url(bits[0])
                if is_article_image(u):
                    score = 0
                    if len(bits) > 1:
                        m = re.match(r"(\d+)w", bits[1])
                        if m:
                            score = int(m.group(1))
                    candidates.append((score, u))
            if candidates:
                candidates.sort(reverse=True)
                return candidates[-1][1] if candidates[0][0] == 0 else candidates[0][1]
    return ""


def extract_article_image_urls_raw(page_html):
    """Find gallery image URLs from the raw response even when KapanLagi wrappers change."""
    soup = BeautifulSoup(page_html or "", "html.parser")
    urls, seen = [], set()

    for img in soup.find_all("img"):
        u = extract_img_url(img)
        if not u:
            # Some server variants leave the URL only in arbitrary data attrs.
            for key, val in img.attrs.items():
                if not isinstance(val, str):
                    continue
                m = re.search(r"https?://[^\\\"'<>\\s]+\\.(?:jpg|jpeg|png|webp)(?:\\?[^\\\"'<>\\s]*)?", val, re.I)
                if m and is_article_image(m.group(0)):
                    u = normalize_candidate_url(m.group(0))
                    break
        if u and u not in seen:
            seen.add(u)
            urls.append(u)

    # Last-resort raw URL scan catches HTML/JSON where img tags are absent.
    if not urls:
        for m in re.finditer(r"https?://[^\"'<>\s]+\.(?:jpg|jpeg|png|webp)(?:\?[^\"'<>\s]*)?", page_html or "", re.I):
            u = normalize_candidate_url(m.group(0))
            if is_article_image(u) and u not in seen:
                seen.add(u)
                urls.append(u)

    return urls


def caption_candidates_near_image(soup, target_img):
    """Collect editorial text between this image and the next gallery image.

    In the stripped live response there may be no KapanLagi CSS classes or
    HTML comments. The safest association is therefore DOM order: the first
    qualifying paragraph after an article image belongs to that image.
    """
    article_imgs = [im for im in soup.find_all("img") if extract_img_url(im)]
    next_article = None
    try:
        idx = article_imgs.index(target_img)
        if idx + 1 < len(article_imgs):
            next_article = article_imgs[idx + 1]
    except ValueError:
        pass

    def valid(text):
        if len(text) < 60 or len(text) > 900:
            return False
        low = text.lower()
        if any(x in low for x in ["advertisement", "scroll untuk melanjutkan", "google_ads", "copyright"]):
            return False
        if CREDIT_RE.match(text):
            return False
        return "." in text

    # First pass: real paragraphs. This is the most reliable signal and keeps
    # the photo/caption pairing strictly positional.
    for node in target_img.find_all_next("p"):
        if next_article is not None and (node is next_article or next_article in node.parents):
            break
        if node.find("img"):
            continue
        text = clean_text(node.get_text(" ", strip=True))
        if valid(text):
            return [(2000 + min(len(text), 500), text)]

    # Second pass: paragraph-like containers for unusual markup.
    for node in target_img.find_all_next(["div", "section", "article"]):
        if next_article is not None and (node is next_article or next_article in node.parents):
            break
        if node.find("img"):
            continue
        text = clean_text(node.get_text(" ", strip=True))
        if valid(text):
            return [(1000 + min(len(text), 500), text)]

    return []

def extract_caption_by_filename(page_html, filename):
    """Fallback caption extraction for responses without KapanLagi marker/classes."""
    soup = BeautifulSoup(page_html or "", "html.parser")
    target = None
    for img in soup.find_all("img"):
        attrs = [img.get(k, "") for k in ("src", "data-src", "data-original", "data-lazy-src", "data-url", "srcset", "data-srcset")]
        if any(filename in str(v) for v in attrs):
            target = img
            break
    if target is None:
        return ""
    cands = caption_candidates_near_image(soup, target)
    return cands[0][1] if cands else ""

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

def extract_main_intro(soup):
    """Extract the actual KapanLagi photo-article intro, not meta description."""
    intro_box = soup.select_one('.pages-item[data-type="page-intro"]')
    if intro_box:
        para = intro_box.select_one('.pages-paragraph')
        if para:
            # Keep paragraph separation, but normalize whitespace inside paragraphs.
            parts = [clean_text(p.get_text(" ", strip=True)) for p in para.find_all("p")]
            parts = [x for x in parts if x]
            if parts:
                return "\n\n".join(parts)

    # Fallback for variants where the intro wrapper is not typed as page-intro.
    for para in soup.select('.pages-paragraph'):
        parent = para.find_parent(class_='pages-item')
        if parent and parent.get('data-type') == 'page-intro':
            parts = [clean_text(p.get_text(" ", strip=True)) for p in para.find_all("p")]
            parts = [x for x in parts if x]
            if parts:
                return "\n\n".join(parts)
    return ""


def extract_caption_blocks_raw(page_html):
    """Extract KapanLagi editorial captions using the page's own markers.

    This intentionally does NOT try to associate a caption with a filename.
    KapanLagi puts the gallery photo and its editorial paragraph in the same
    page block, in the same order. We therefore pair images and caption blocks
    by position. This avoids failures when lazy-loaded image URLs are rewritten
    or normalized before BeautifulSoup sees them.
    """
    if not page_html:
        return []

    captions = []

    # Primary source: explicit KapanLagi description markers.
    marker_re = re.compile(
        r"<!--\s*STARTOFPAGEDESCRIPTIONBOTTOM\s*-->([\s\S]*?)"
        r"<!--\s*ENDOFPAGEDESCRIPTIONBOTTOM\s*-->",
        re.I,
    )
    for m in marker_re.finditer(page_html):
        frag = BeautifulSoup(m.group(1), "html.parser")
        # Prefer the actual paragraph text, never image alt/credit text.
        ps = frag.select(".pages-paragraph p")
        if ps:
            text = clean_text(" ".join(x.get_text(" ", strip=True) for x in ps))
        else:
            node = frag.select_one(".pages-paragraph")
            text = clean_text(node.get_text(" ", strip=True)) if node else ""
        if len(text) >= 40 and not CREDIT_RE.match(text):
            captions.append(text)

    if captions:
        return captions

    # Secondary source for KapanLagi variants without explicit markers:
    # collect pages-paragraph blocks that occur after gallery figures.
    soup = BeautifulSoup(page_html, "html.parser")
    out = []
    for para in soup.select("div.pages-paragraph, p.pages-paragraph"):
        text = clean_text(para.get_text(" ", strip=True))
        if len(text) < 40 or CREDIT_RE.match(text):
            continue
        # Do not take the article intro. A gallery caption has a preceding
        # pages-img figure in the same page item.
        parent = para.find_parent(".pages-item[data-type='content-pages']")
        if parent is None:
            parent = para.find_parent(class_=lambda c: c and "pages-item" in c)
        if parent is not None:
            out.append(text)
    return out


def extract_gallery_images_raw(page_html):
    """Return article image URLs in DOM order from a gallery response."""
    soup = BeautifulSoup(page_html or "", "html.parser")
    urls = []
    seen = set()

    # Do not depend on .pages-item / figure classes; the live response can omit them.
    return extract_article_image_urls_raw(page_html)


def extract_photo_editorial_text(figure):
    """Extract the editorial paragraph directly below one gallery photo."""
    if not figure:
        return ""

    # Exact marker is the strongest signal and survives wrapper changes.
    marker = figure.find_next(string=lambda x: isinstance(x, Comment) and
                              "STARTOFPAGEDESCRIPTIONBOTTOM" in x)
    if marker:
        parent = marker.parent
        node = parent.find_next(class_=lambda c: c and "pages-paragraph" in c)
        if node:
            text = clean_text(node.get_text(" ", strip=True))
            if len(text) >= 40 and not CREDIT_RE.match(text):
                return text

    # DOM fallback: first pages-paragraph after this figure before another figure.
    for node in figure.find_all_next():
        if getattr(node, "name", None) == "figure" and node is not figure:
            break
        classes = node.get("class") or [] if getattr(node, "name", None) else []
        if "pages-paragraph" in classes:
            text = clean_text(node.get_text(" ", strip=True))
            if len(text) >= 40 and not CREDIT_RE.match(text):
                return text
    return ""


def extract_photo_editorial_text_from_page_html(page_html):
    captions = extract_caption_blocks_raw(page_html)
    return captions[0] if captions else ""


def parse_gallery_html(html, source_url, photos, seen):
    """Parse gallery images and pair captions by KapanLagi page order.

    V23 deliberately uses a different association strategy from previous
    versions: image filename matching is NOT required for caption extraction.
    Each gallery page item contains one image followed by one marked editorial
    description, so we pair them positionally.
    """
    soup = BeautifulSoup(html, "html.parser")
    pages = soup.select(".pages-item[data-type='content-pages']")
    diag = {
        "url": source_url,
        "content_pages": len(pages),
        "pages_img": len(soup.select("figure.pages-img")),
        "imgs": len(soup.find_all("img")),
        "article_images": 0,
        "editorial_captions": 0,
        "caption_blocks_raw": len(extract_caption_blocks_raw(html)),
        "raw_dated_images": len(extract_article_image_urls_raw(html)),
        "pairing": "positional",
    }

    raw_captions = extract_caption_blocks_raw(html)

    # New V24 path: identify dated KapanLagi filenames first. This works even
    # when the live response has no pages-item, figure, or marker comments.
    raw_article_urls = extract_article_image_urls_raw(html)
    if raw_article_urls:
        for idx, resized in enumerate(raw_article_urls):
            filename = filename_from_url(resized)
            original = original_url(resized)
            if not filename or not original or original in seen:
                continue
            caption = ""
            if idx < len(raw_captions):
                caption = raw_captions[idx]
            if not caption:
                caption = extract_caption_by_filename(html, filename)
            seen.add(original)
            diag["article_images"] += 1
            if caption:
                diag["editorial_captions"] += 1
            photos.append({
                "resized": resized,
                "original": original,
                "caption": caption,
                "filename": filename,
                "source_page": source_url,
                "caption_source": "KapanLagi marker/order" if caption else "NOT FOUND",
            })
        if photos:
            return diag, soup

    # Best path: process each content-page independently. This guarantees
    # caption #1 belongs to photo #1 even if the page contains ad markup.
    if pages:
        for idx, page in enumerate(pages):
            img = page.select_one("figure.pages-img img")
            if not img:
                continue
            resized = extract_img_url(img)
            if not is_article_image(resized):
                continue

            filename = filename_from_url(resized)
            original = original_url(resized)
            if not filename or not original or original in seen:
                continue

            caption = ""
            # First try the exact page item, then the raw page-level caption list.
            page_caps = extract_caption_blocks_raw(str(page))
            if page_caps:
                caption = page_caps[0]
            elif idx < len(raw_captions):
                caption = raw_captions[idx]
            else:
                figure = page.select_one("figure.pages-img")
                caption = extract_photo_editorial_text(figure)

            seen.add(original)
            diag["article_images"] += 1
            if caption:
                diag["editorial_captions"] += 1

            photos.append({
                "resized": resized,
                "original": original,
                "caption": caption,
                "filename": filename,
                "source_page": source_url,
                "caption_source": "KapanLagi marker/order" if caption else "NOT FOUND",
            })
        return diag, soup

    # Fallback for responses without content-page wrappers.
    imgs = extract_gallery_images_raw(html)
    for idx, resized in enumerate(imgs):
        filename = filename_from_url(resized)
        original = original_url(resized)
        if not filename or not original or original in seen:
            continue
        caption = raw_captions[idx] if idx < len(raw_captions) else ""
        seen.add(original)
        diag["article_images"] += 1
        if caption:
            diag["editorial_captions"] += 1
        photos.append({
            "resized": resized,
            "original": original,
            "caption": caption,
            "filename": filename,
            "source_page": source_url,
            "caption_source": "KapanLagi marker/order" if caption else "NOT FOUND",
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

    # KapanLagi photo articles store the real article introduction inside
    # the page-intro block. Meta description is not the editorial intro.
    intro = extract_main_intro(soup)
    if not intro:
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
        with st.expander("🔧 Diagnosis Fetch & Parser", expanded=False):
            st.dataframe(st.session_state.diagnostics, use_container_width=True)
            st.caption("V23 memasangkan caption berdasarkan urutan blok gallery KapanLagi, bukan berdasarkan filename.")
        if not photos:
            st.warning("Gallery tidak ditemukan pada respons server.")
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
                source_label = p.get("caption_source", "")
                if source_label == "NOT FOUND":
                    st.warning("Caption editorial belum ditemukan.")
                elif source_label:
                    st.caption(f"Sumber caption: {source_label}")
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