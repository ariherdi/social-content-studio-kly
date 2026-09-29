import io, re
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
import streamlit as st

st.set_page_config(page_title='Social Content Studio', page_icon='📲', layout='wide')

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36'


def clean_text(text):
    return re.sub(r'\s+', ' ', text or '').strip()


def is_kapanlagi(url):
    host = urlparse(url).netloc.lower()
    return host.endswith('kapanlagi.com') or host.endswith('kapanlagi.com.')


def image_url_from_tag(img, base_url):
    # Prefer high-resolution/lazy attributes used by modern news sites.
    attrs = [
        'data-original', 'data-src', 'data-lazy-src', 'data-image',
        'data-srcset', 'srcset', 'src'
    ]
    for attr in attrs:
        val = img.get(attr)
        if not val:
            continue
        if attr in ('srcset', 'data-srcset'):
            # Pick the last/largest candidate in srcset.
            candidates = [x.strip().split(' ')[0] for x in val.split(',') if x.strip()]
            if candidates:
                val = candidates[-1]
        if val.startswith('data:'):
            continue
        return urljoin(base_url, val)
    return ''


def image_is_large(img):
    try:
        w = int(re.sub(r'[^0-9]', '', str(img.get('width', '')))) if img.get('width') else 0
        h = int(re.sub(r'[^0-9]', '', str(img.get('height', '')))) if img.get('height') else 0
        if w >= 500 or h >= 350:
            return True
    except Exception:
        pass
    src = image_url_from_tag(img, '')
    # KapanLagi CDN image URLs are generally the actual editorial assets.
    return 'cdns.klimg.com' in src.lower()


def nearby_caption(img):
    """Find caption immediately below/near an image, avoiding credits and UI text."""
    fig = img.find_parent('figure')
    if fig:
        cap = fig.find('figcaption')
        if cap:
            text = clean_text(cap.get_text(' ', strip=True))
            if text and not text.lower().startswith(('hak cipta', 'copyright')):
                return text

    # Prefer a sibling paragraph immediately following the image/container.
    node = img
    for _ in range(4):
        node = node.find_next_sibling()
        if not node:
            break
        text = clean_text(node.get_text(' ', strip=True))
        if not text:
            continue
        low = text.lower()
        if low.startswith(('hak cipta', 'copyright', 'prev', 'next')):
            continue
        # Avoid swallowing navigation or article-wide text.
        if len(text) <= 500:
            return text
    return ''


def kapanlagi_extract(soup, url):
    # Remove obvious non-content areas before analysis.
    for tag in soup(['script', 'style', 'noscript', 'svg', 'iframe']):
        tag.decompose()

    h1 = soup.find('h1')
    title = clean_text(h1.get_text(' ', strip=True)) if h1 else ''
    if not title:
        meta = soup.find('meta', property='og:title')
        title = clean_text(meta.get('content')) if meta else ''

    # Find intro: first meaningful paragraph after the H1 and before the first editorial image.
    intro = ''
    if h1:
        for node in h1.find_all_next(['p', 'div']):
            if node.name == 'div' and node.find('img'):
                break
            text = clean_text(node.get_text(' ', strip=True))
            if len(text) >= 40 and not text.lower().startswith(('diterbitkan', 'oleh')):
                intro = text
                break

    # Find candidate editorial images. KapanLagi photo pages use klimg CDN assets.
    candidates = []
    if h1:
        nodes = h1.find_all_next('img')
    else:
        nodes = soup.find_all('img')

    for img in nodes:
        src = image_url_from_tag(img, url)
        if not src or 'cdns.klimg.com' not in src.lower():
            continue
        if not image_is_large(img):
            continue
        alt = clean_text(img.get('alt'))
        caption = nearby_caption(img)
        # Score image based on editorial characteristics.
        score = 0
        if caption:
            score += 5
        if alt:
            score += 1
        parent_text = clean_text(img.parent.get_text(' ', strip=True)) if img.parent else ''
        if len(parent_text) < 300:
            score += 1
        candidates.append({'url': src, 'alt': alt, 'caption': caption, 'score': score})

    # Deduplicate and remove obvious repeated assets.
    clean = []
    seen = set()
    for item in sorted(candidates, key=lambda x: -x['score']):
        key = item['url'].split('?')[0]
        if key in seen:
            continue
        seen.add(key)
        clean.append(item)

    # Preserve page order rather than score order.
    order = {item['url'].split('?')[0]: i for i, item in enumerate(candidates)}
    clean.sort(key=lambda x: order.get(x['url'].split('?')[0], 99999))

    # Remove obvious tiny/UI assets by filename hints.
    filtered = []
    for item in clean:
        low = item['url'].lower()
        if any(x in low for x in ['/logo', '/icon', '/avatar', '/placeholder', 'sprite']):
            continue
        filtered.append(item)

    return {'title': title, 'intro': intro, 'images': filtered[:30], 'source': 'KapanLagi'}


@st.cache_data(ttl=600, show_spinner=False)
def fetch_article(url):
    r = requests.get(url, headers={'User-Agent': UA}, timeout=25)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    if is_kapanlagi(url):
        return kapanlagi_extract(soup, url)

    # Generic fallback for non-KapanLagi URLs.
    for tag in soup(['script', 'style', 'noscript', 'svg', 'iframe']):
        tag.decompose()
    title_tag = soup.find('meta', property='og:title') or soup.find('title')
    title = clean_text(title_tag.get('content') if hasattr(title_tag, 'get') else title_tag.get_text(' ', strip=True) if title_tag else '')
    imgs = []
    for img in soup.find_all('img'):
        src = image_url_from_tag(img, url)
        if not src or not image_is_large(img):
            continue
        imgs.append({'url': src, 'alt': clean_text(img.get('alt')), 'caption': nearby_caption(img)})
    seen = set(); clean = []
    for x in imgs:
        if x['url'] not in seen:
            seen.add(x['url']); clean.append(x)
    return {'title': title, 'intro': '', 'images': clean[:30], 'source': 'Generic'}


def short_copy(text, title=''):
    text = clean_text(text)
    if not text:
        text = title
    text = re.sub(r'\b(?:Foto|Dok|Sumber|Hak Cipta)\s*:\s*[^.]+\.?', '', text, flags=re.I).strip()
    words = text.split()
    if len(words) > 9:
        text = ' '.join(words[:9])
    return text.rstrip(' ,.-')


def load_image(url):
    r = requests.get(url, headers={'User-Agent': UA}, timeout=25)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert('RGB')


def make_card(img, text):
    w, h = 1080, 1080
    img.thumbnail((w, h))
    canvas = Image.new('RGB', (w, h), '#111111')
    x = (w - img.width) // 2; y = (h - img.height) // 2
    canvas.paste(img, (x, y))
    draw = ImageDraw.Draw(canvas, 'RGBA')
    box_h = 250
    draw.rectangle((0, h-box_h, w, h), fill=(0, 0, 0, 185))
    try:
        font = ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', 58)
    except Exception:
        font = ImageFont.load_default()
    lines=[]; cur=''
    for word in text.split():
        test=(cur+' '+word).strip()
        if draw.textbbox((0,0), test, font=font)[2] > w-100 and cur:
            lines.append(cur); cur=word
        else: cur=test
    if cur: lines.append(cur)
    total=sum(draw.textbbox((0,0),ln,font=font)[3] for ln in lines)+(len(lines)-1)*10
    yy=h-box_h+(box_h-total)//2
    for ln in lines:
        bbox=draw.textbbox((0,0),ln,font=font); tw=bbox[2]-bbox[0]
        draw.text(((w-tw)//2,yy),ln,font=font,fill='white'); yy+=bbox[3]-bbox[1]+10
    out=io.BytesIO(); canvas.save(out,format='PNG'); out.seek(0); return out


st.title('📲 Social Content Studio')
st.caption('POC V2 — KapanLagi: intro + foto editorial besar + caption foto → copy pendek → preview → download')

url=st.text_input('URL Artikel', placeholder='https://www.kapanlagi.com/foto/berita-foto/...')
if st.button('🔎 Analisis Artikel', type='primary'):
    if not url.startswith(('http://','https://')):
        st.error('Masukkan URL lengkap yang diawali http:// atau https://')
    elif not is_kapanlagi(url):
        st.warning('POC V2 saat ini difokuskan untuk KapanLagi.com. URL lain masih memakai extractor generik.')
        try:
            with st.spinner('Membaca artikel...'):
                st.session_state.article=fetch_article(url)
        except Exception as e:
            st.error(f'Gagal membaca URL: {e}')
    else:
        try:
            with st.spinner('Membaca struktur foto KapanLagi...'):
                st.session_state.article=fetch_article(url)
        except Exception as e:
            st.error(f'Gagal membaca URL: {e}')

article=st.session_state.get('article')
if article:
    st.divider()
    st.subheader(article['title'] or 'Artikel')
    st.caption(f"Extractor: {article.get('source','Generic')}")

    st.markdown('### 📝 Intro / Deskripsi Artikel')
    intro=st.text_area('Intro', value=article.get('intro',''), key='article_intro', height=110)
    if not intro:
        st.warning('Intro belum terdeteksi otomatis.')

    st.markdown(f"### 🖼️ Foto Editorial — {len(article['images'])} ditemukan")
    st.caption('Hanya aset besar dari CDN KapanLagi yang diprioritaskan; caption dicari pada teks yang berada tepat di bawah/sekitar foto.')

    if not article['images']:
        st.warning('Belum ada foto editorial yang cocok. Website mungkin memuat galeri melalui JavaScript atau struktur HTML berubah.')

    for i,item in enumerate(article['images']):
        with st.container(border=True):
            c1,c2=st.columns([1,2])
            with c1:
                try: st.image(item['url'], use_container_width=True)
                except: st.warning('Preview foto gagal dimuat')
            with c2:
                st.markdown(f'### Foto {i+1}')
                original=item.get('caption') or item.get('alt') or ''
                st.text_area('Keterangan foto yang ditemukan', value=original, key=f'orig_{i}', height=90)
                default=short_copy(original,article['title'])
                copy=st.text_area('Copy untuk foto', value=default, key=f'copy_{i}', max_chars=120, height=80)
                if len(copy)>70: st.warning(f'Teks cukup panjang: {len(copy)} karakter')
                else: st.success(f'{len(copy)} karakter — masih ringkas')
                try:
                    img=load_image(item['url'])
                    card=make_card(img,copy)
                    st.image(card,use_container_width=True,caption='Live preview 1080 × 1080')
                    st.download_button('⬇️ Download PNG',data=card,file_name=f'social-{i+1}.png',mime='image/png',key=f'dl_{i}')
                except Exception as e:
                    st.info(f'Preview/download belum tersedia untuk foto ini: {e}')
