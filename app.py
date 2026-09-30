import hashlib
import io
import os
import re
import zipfile
from urllib.parse import urljoin, urlparse

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
                m = re.search(r"https?://[^\"'<>\s]+\.(?:jpg|jpeg|png|webp)(?:\?[^\"'<>\s]*)?", val, re.I)
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


MARKER_RE = re.compile(
    r"<!--\s*STARTOFPAGEDESCRIPTIONBOTTOM\s*-->([\s\S]*?)"
    r"<!--\s*ENDOFPAGEDESCRIPTIONBOTTOM\s*-->",
    re.I,
)


def _caption_text_from_fragment(fragment_html):
    frag = BeautifulSoup(fragment_html or "", "html.parser")
    for bad in frag.find_all(["figure", "figcaption", "img", "script", "style"]):
        bad.decompose()
    ps = frag.select(".pages-paragraph p") or frag.find_all("p")
    if ps:
        return clean_text(" ".join(x.get_text(" ", strip=True) for x in ps))
    return clean_text(frag.get_text(" ", strip=True))


def marker_blocks_with_pos(page_html):
    """[(char_position, caption_text)] for every STARTOFPAGEDESCRIPTIONBOTTOM block."""
    out = []
    for m in MARKER_RE.finditer(page_html or ""):
        text = _caption_text_from_fragment(m.group(1))
        if len(text) >= 40 and not CREDIT_RE.match(text):
            out.append((m.start(), text))
    return out


def extract_caption_blocks_raw(page_html):
    """Editorial caption texts of one response, in document order."""
    if not page_html:
        return []
    blocks = marker_blocks_with_pos(page_html)
    if blocks:
        return [t for _, t in blocks]

    # Secondary: .pages-paragraph inside gallery items (never the page-intro).
    soup = BeautifulSoup(page_html, "html.parser")
    out = []
    for para in soup.select("div.pages-paragraph, p.pages-paragraph"):
        text = clean_text(para.get_text(" ", strip=True))
        if len(text) < 40 or CREDIT_RE.match(text):
            continue
        parent = para.find_parent(class_=lambda c: c and "pages-item" in c)
        if parent is None or parent.get("data-type") == "page-intro":
            continue
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


def page_number_from_url(u):
    m = re.search(r"[?&]page=(\d+)", u or "")
    return int(m.group(1)) if m else None


def analyse_response(page_html):
    """Evidence found in ONE HTTP response."""
    texts = list(dict.fromkeys(extract_caption_blocks_raw(page_html)))
    return {
        "texts": texts,
        "img_urls": extract_article_image_urls_raw(page_html),
        "marker_raw": (page_html or "").upper().count("STARTOFPAGEDESCRIPTIONBOTTOM"),
        "marker_blocks": len(marker_blocks_with_pos(page_html)),
    }


def register_images(photos, img_urls, source_url):
    known = {p["original"] for p in photos}
    for u in img_urls:
        orig, fn = original_url(u), filename_from_url(u)
        if not orig or not fn or orig in known:
            continue
        known.add(orig)
        photos.append({
            "resized": u, "original": orig, "filename": fn,
            "caption": "", "caption_source": "NOT FOUND",
            "source_page": source_url, "log": [], "detail_urls": [],
        })


def set_caption(photos, idx, text, source):
    """Fill ONLY an empty caption; never reuse one caption for two photos."""
    p = photos[idx]
    text = clean_text(text)
    if not text:
        return False
    if p.get("caption"):
        if p["caption"] != text:
            p["log"].append(f"konflik: {source} memberi teks lain; teks lama dipertahankan")
        return False
    for j, q in enumerate(photos):
        if j != idx and q.get("caption") == text:
            p["log"].append(f"ditolak dari {source}: teks identik dengan foto {j+1}")
            return False
    p["caption"], p["caption_source"] = text, source
    p["log"].append(f"terisi dari {source}")
    return True


def apply_response(photos, ev, page_no, is_base=False):
    """Map the caption blocks of one response onto gallery photos.

    Blocks in a response are consecutive gallery items starting at the photo
    the response is about: base -> photo 1; ?page=N -> photo N (or the single
    image the response contains). This replaces V25's per-response image index,
    which paired the ONLY caption of a ?page=N response with image #0 of that
    response (already filled), so photos 2-5 stayed empty.
    """
    texts = ev["texts"]
    if not texts:
        return "tidak ada blok caption"
    fnames = list(dict.fromkeys(filename_from_url(u) for u in ev["img_urls"]))
    idx_by_fn = {p["filename"]: i for i, p in enumerate(photos)}

    if is_base:
        start, how = 0, "respons dasar"
    elif len(texts) > 1 and len(texts) >= len(photos):
        start, how = 0, "galeri penuh"
    elif len(fnames) == 1 and fnames[0] in idx_by_fn:
        start, how = idx_by_fn[fnames[0]], "satu gambar di respons"
    elif page_no:
        start, how = page_no - 1, f"nomor halaman {page_no}"
    else:
        return "foto target tidak dapat ditentukan"

    filled = []
    for j, t in enumerate(texts):
        k = start + j
        if k >= len(photos):
            break
        if set_caption(photos, k, t, f"KapanLagi marker ({how})"):
            filled.append(k + 1)
    return f"mulai foto {start+1} [{how}]; terisi foto: {filled or '-'}"


def find_detail_urls(raw_pages, filename, base_url):
    """Find individual photo-detail URLs (.../<id><filename-stem>.html) in ANY raw response."""
    stem = os.path.splitext(filename)[0]
    pat = re.compile(
        r"""(?:https?:)?(?://|/)[^\s"'<>\\]*?""" + re.escape(stem) + r"""[^\s"'<>\\]*?\.html""",
        re.I,
    )
    found = []
    for html in raw_pages.values():
        for m in pat.finditer((html or "").replace("\\/", "/")):
            u = urljoin(base_url, m.group(0))
            if "kapanlagi.com" in urlparse(u).netloc and u.split("?")[0] != base_url.split("?")[0] and u not in found:
                found.append(u)
    return found


def caption_from_detail(detail_html, filename):
    blocks = marker_blocks_with_pos(detail_html)
    if blocks:
        if len(blocks) == 1:
            return blocks[0][1]
        fpos = [m.start() for m in re.finditer(re.escape(filename), detail_html)]
        for pos, t in blocks:
            if any(fp < pos for fp in fpos):
                return t
        return blocks[0][1]
    texts = extract_caption_blocks_raw(detail_html)
    return texts[0] if texts else ""


def extract_page(url):
    html, first_diag = fetch_html(url)
    soup = BeautifulSoup(html, "html.parser")

    title = ""
    og = soup.find("meta", property="og:title")
    if og:
        title = clean_text(og.get("content", ""))
    if not title and soup.title:
        title = clean_text(soup.title.get_text())

    intro = extract_main_intro(soup)
    if not intro:
        desc = soup.find("meta", attrs={"name": "description"})
        if desc:
            intro = clean_text(desc.get("content", ""))

    photos, rows, raw_pages = [], [], {url: html}

    def row(u, meta, ev, note, page_no):
        fns = [filename_from_url(x) for x in ev["img_urls"]]
        return {
            "url": u, "page": page_no,
            "status": meta.get("status"), "bytes": meta.get("bytes"),
            "md5": hashlib.md5(raw_pages[u].encode("utf-8", "ignore")).hexdigest()[:10],
            "marker_raw": ev["marker_raw"], "marker_blocks": ev["marker_blocks"],
            "distinct_captions": len(ev["texts"]),
            "caption_starts": " | ".join(t[:28] for t in ev["texts"]),
            "article_imgs": len(fns),
            "filenames": ", ".join(dict.fromkeys(re.sub(r"^.*?-(?:19|20)\d{6}-", "", f) for f in fns)),
            "decision": note,
        }

    # 1. Base response = photo 1
    ev = analyse_response(html)
    register_images(photos, ev["img_urls"], url)
    rows.append(row(url, first_diag, ev, apply_response(photos, ev, 1, is_base=True), None))

    # 2. ?page=N responses
    pagemap = {}
    for el in soup.select("[data-pageurl]"):
        u = (el.get("data-pageurl") or "").strip()
        n = page_number_from_url(u)
        if n and u and "kapanlagi.com" in u:
            pagemap.setdefault(n, u)
    base_clean = urlparse(url)._replace(query="", fragment="").geturl()

    n = 1
    while n <= min(30, max(len(photos), 5)):
        if photos and all(p["caption"] for p in photos) and n > len(photos):
            break
        purl = pagemap.get(n) or f"{base_clean}?page={n}"
        try:
            ph, meta = fetch_html(purl)
        except Exception as e:
            rows.append({"url": purl, "page": n, "decision": f"ERROR: {e}"})
            n += 1
            continue
        raw_pages[purl] = ph
        pev = analyse_response(ph)
        register_images(photos, pev["img_urls"], purl)
        rows.append(row(purl, meta, pev, apply_response(photos, pev, n), n))
        n += 1

    # 3. Fallback: individual photo detail page for photos still empty
    for i, p in enumerate(photos):
        if p["caption"]:
            continue
        p["detail_urls"] = find_detail_urls(raw_pages, p["filename"], url)
        if not p["detail_urls"]:
            p["log"].append("tidak ada link halaman detail di respons mana pun")
        for du in p["detail_urls"][:2]:
            try:
                dh, dmeta = fetch_html(du)
                raw_pages[du] = dh
                cap = caption_from_detail(dh, p["filename"])
                p["log"].append(f"detail {du}: status {dmeta['status']}, {dmeta['bytes']} B, caption {'ada' if cap else 'kosong'}")
                if cap and set_caption(photos, i, cap, "Halaman detail foto"):
                    break
            except Exception as e:
                p["log"].append(f"detail {du}: ERROR {e}")

    # 4. Last resort, clearly labelled weak: first paragraph after the <img> in the DOM
    for i, p in enumerate(photos):
        if p["caption"]:
            continue
        for h in raw_pages.values():
            cap = extract_caption_by_filename(h, p["filename"])
            if cap and cap not in intro and set_caption(photos, i, cap, "Heuristik DOM (LEMAH - periksa manual)"):
                break

    for p in photos:
        p["caption_found"] = bool(p["caption"])
    return title, intro, photos, rows, raw_pages


def search_raw(raw_pages, needle):
    """Where (which response, inside <script>?) does a text snippet occur?"""
    out, nl = [], needle.lower().strip()
    if not nl:
        return out
    for u, h in raw_pages.items():
        hl, start, c = h.lower(), 0, 0
        while True:
            i = hl.find(nl, start)
            if i < 0:
                break
            c += 1
            if c <= 2:
                in_script = hl.rfind("<script", 0, i) > hl.rfind("</script>", 0, i)
                out.append({"response": u, "pos": i, "in_script": in_script,
                            "context": clean_text(h[max(0, i-120):i+len(needle)+120])})
            start = i + len(nl)
        if c == 0:
            out.append({"response": u, "pos": None, "in_script": None, "context": "tidak ditemukan"})
    return out


def raw_pages_zip(raw_pages):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        for i, (u, h) in enumerate(raw_pages.items()):
            z.writestr(f"{i:02d}.html", f"<!-- {u} -->\n{h}")
    return b.getvalue()


def _secret(name, default=""):
    try:
        v = st.secrets.get(name, "")
    except Exception:
        v = ""
    return v or os.environ.get(name, "") or default


def get_openai_client():
    key = _secret("OPENAI_API_KEY")
    if OpenAI is None:
        return None, "Library openai tidak terpasang (cek requirements.txt)."
    if not key:
        return None, "OPENAI_API_KEY tidak ditemukan di Secrets Streamlit."
    try:
        return OpenAI(api_key=key), ""
    except Exception as e:
        return None, f"Gagal membuat client OpenAI: {e}"


AI_SYSTEM_PROMPT = (
    "Kamu adalah editor sosial media berita Indonesia. "
    "Rewrite caption foto menjadi satu kalimat/frasa singkat, jelas, menarik, dan faktual. "
    "Jangan menambah informasi baru. Maksimal 100 karakter. "
    "Jangan memakai tanda kutip. Jangan menyebut kata caption."
)


def call_openai(client, model, text):
    """Try the Responses API, then Chat Completions. Raises with a readable reason."""
    errors = []
    if hasattr(client, "responses"):
        try:
            r = client.responses.create(
                model=model,
                input=[{"role": "system", "content": AI_SYSTEM_PROMPT},
                       {"role": "user", "content": text}],
            )
            out = clean_text(getattr(r, "output_text", ""))
            if out:
                return out
            errors.append("Responses API mengembalikan teks kosong")
        except Exception as e:
            errors.append(f"Responses API: {type(e).__name__}: {e}")
    else:
        errors.append("library openai terlalu lama (tidak punya client.responses)")
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": AI_SYSTEM_PROMPT},
                      {"role": "user", "content": text}],
        )
        out = clean_text(r.choices[0].message.content or "")
        if out:
            return out
        errors.append("Chat Completions mengembalikan teks kosong")
    except Exception as e:
        errors.append(f"Chat Completions: {type(e).__name__}: {e}")
    raise RuntimeError(" | ".join(errors))


def ai_rewrite(text):
    """Rewrite <= 100 chars. On failure returns a plain cut of the caption and
    records the reason in st.session_state.ai_last_error (shown in the UI)."""
    text = clean_text(text)
    if not text:
        return ""
    client, err = get_openai_client()
    if not client:
        st.session_state.ai_last_error = err
        return text[:100]
    try:
        out = call_openai(client, _secret("OPENAI_MODEL", "gpt-5-mini"), text)
        st.session_state.ai_last_error = ""
        out = out.strip().strip('"\u201c\u201d')
        return out[:100]
    except Exception as e:
        st.session_state.ai_last_error = str(e)
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
if "ai_last_error" not in st.session_state:
    st.session_state.ai_last_error = ""
if "raw_pages" not in st.session_state:
    st.session_state.raw_pages = {}

url = st.text_input(
    "URL artikel KapanLagi.com",
    placeholder="https://www.kapanlagi.com/foto/..."
)

if st.button("🔎 Analisis Artikel", type="primary") and url:
    try:
        title, intro, photos, diagnostics, raw_pages = extract_page(url)
        st.session_state.diagnostics = diagnostics
        st.session_state.raw_pages = raw_pages
        st.session_state.title = title
        st.session_state.intro = intro
        st.session_state.photos = photos
        st.session_state.rewrites = {}
        for k in [k for k in st.session_state.keys() if k.startswith(("selected_", "orig_", "rewrite_"))]:
            del st.session_state[k]
        n_cap = sum(1 for p in photos if p.get("caption"))
        st.success(f"Ditemukan {len(photos)} foto besar; caption editorial ditemukan untuk {n_cap} foto.")
        if not photos:
            st.warning("Gallery tidak ditemukan pada respons server.")
    except Exception as e:
        st.error(f"Gagal membaca artikel: {e}")

if st.session_state.photos:
    with st.expander("🔧 Diagnosis V26: respons per halaman & caption per foto", expanded=any(not p.get("caption") for p in st.session_state.photos)):
        st.markdown("**A. Respons yang diterima server (base + ?page=N)**")
        st.dataframe(st.session_state.diagnostics, use_container_width=True)
        st.markdown("**B. Status caption per foto**")
        st.dataframe(
            [{
                "foto": i + 1,
                "filename": p["filename"],
                "caption": "ya" if p.get("caption") else "TIDAK",
                "panjang": len(p.get("caption", "")),
                "sumber": p.get("caption_source", ""),
                "detail_urls": " ; ".join(p.get("detail_urls", [])),
                "log": " || ".join(p.get("log", [])),
            } for i, p in enumerate(st.session_state.photos)],
            use_container_width=True,
        )
        st.markdown("**C. Cari potongan teks di respons mentah** (mis. 30 karakter pertama caption yang seharusnya)")
        needle = st.text_input("Potongan teks", key="dbg_needle", placeholder="Selain di photo wall")
        if needle:
            st.dataframe(search_raw(st.session_state.get("raw_pages", {}), needle), use_container_width=True)
        if st.session_state.get("raw_pages"):
            st.download_button(
                "⬇️ Download respons mentah (ZIP)",
                raw_pages_zip(st.session_state.raw_pages),
                file_name="respons-mentah-kapanlagi.zip",
                mime="application/zip",
            )

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

    _client, _cerr = get_openai_client()
    _model = _secret("OPENAI_MODEL", "gpt-5-mini")
    if _client is None:
        st.error(f"AI belum terhubung: {_cerr} Rewrite saat ini hanya memotong caption 100 karakter.")
    else:
        st.info(f"API key terbaca. Model: {_model}")
    if st.session_state.get("ai_last_error"):
        st.warning(f"Rewrite AI gagal, dipakai potongan caption. Penyebab: {st.session_state.ai_last_error}")
    if st.button("🧪 Tes koneksi AI"):
        if _client is None:
            st.error(_cerr)
        else:
            try:
                st.success("Berhasil: " + call_openai(_client, _model, "YoonA hadir di Milan Fashion Week mengenakan setelan jas cokelat."))
            except Exception as e:
                st.error(str(e))

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
                elif source_label.startswith("Heuristik"):
                    st.warning(f"Sumber caption: {source_label}")
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