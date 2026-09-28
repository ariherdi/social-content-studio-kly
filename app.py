import io, re
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageDraw, ImageFont
import streamlit as st

st.set_page_config(page_title='Social Content Studio', page_icon='📲', layout='wide')

UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126 Safari/537.36'

@st.cache_data(ttl=600, show_spinner=False)
def fetch_article(url):
    r = requests.get(url, headers={'User-Agent': UA}, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    for tag in soup(['script','style','noscript','svg','iframe']): tag.decompose()
    title = (soup.find('meta', property='og:title') or soup.find('title'))
    if hasattr(title, 'get'):
        title = title.get('content') or title.get_text(' ', strip=True)
    else: title = title.get_text(' ', strip=True) if title else ''
    imgs=[]
    for img in soup.find_all('img'):
        src = img.get('src') or img.get('data-src') or img.get('data-lazy-src') or img.get('data-original')
        if not src: continue
        src=urljoin(url,src)
        if src.startswith('data:'): continue
        alt=(img.get('alt') or '').strip()
        parent=img.parent
        caption=''
        if parent:
            cap=parent.find('figcaption')
            if cap: caption=cap.get_text(' ',strip=True)
        if not caption:
            fig=img.find_parent('figure')
            if fig:
                cap=fig.find('figcaption') if fig else None
                if cap: caption=cap.get_text(' ',strip=True)
        imgs.append({'url':src,'alt':alt,'caption':caption})
    # de-duplicate by URL
    seen=set(); clean=[]
    for x in imgs:
        if x['url'] not in seen:
            seen.add(x['url']); clean.append(x)
    return {'title':title,'images':clean}

def short_copy(text, title=''):
    text=re.sub(r'\s+',' ',text or '').strip()
    if not text: text=title
    text=re.sub(r'\b(?:Foto|Dok|Sumber)\s*:\s*[^.]+\.?','',text,flags=re.I).strip()
    words=text.split()
    if len(words)>9: text=' '.join(words[:9])
    return text.rstrip(' ,.-')

def load_image(url):
    r=requests.get(url,headers={'User-Agent':UA},timeout=20)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert('RGB')

def make_card(img, text):
    w,h=1080,1080
    img.thumbnail((w,h))
    canvas=Image.new('RGB',(w,h),'#111111')
    x=(w-img.width)//2; y=(h-img.height)//2
    canvas.paste(img,(x,y))
    draw=ImageDraw.Draw(canvas,'RGBA')
    box_h=250
    draw.rectangle((0,h-box_h,w,h),fill=(0,0,0,185))
    try: font=ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',58)
    except: font=ImageFont.load_default()
    # simple wrapping
    lines=[]; cur=''
    for word in text.split():
        test=(cur+' '+word).strip()
        if draw.textbbox((0,0),test,font=font)[2] > w-100 and cur:
            lines.append(cur); cur=word
        else: cur=test
    if cur: lines.append(cur)
    total=sum((draw.textbbox((0,0),ln,font=font)[3] for ln in lines),0)+(len(lines)-1)*10
    yy=h-box_h+(box_h-total)//2
    for ln in lines:
        bbox=draw.textbbox((0,0),ln,font=font); tw=bbox[2]-bbox[0]
        draw.text(((w-tw)//2,yy),ln,font=font,fill='white'); yy+=bbox[3]-bbox[1]+10
    out=io.BytesIO(); canvas.save(out,format='PNG'); out.seek(0); return out

st.title('📲 Social Content Studio')
st.caption('POC V1 — URL artikel → foto & caption → copy pendek → preview → download')

url=st.text_input('URL Artikel', placeholder='https://www.contoh.com/artikel/...')
if st.button('🔎 Analisis Artikel', type='primary'):
    if not url.startswith(('http://','https://')):
        st.error('Masukkan URL lengkap yang diawali http:// atau https://')
    else:
        try:
            with st.spinner('Membaca artikel dan mencari foto...'):
                st.session_state.article=fetch_article(url)
        except Exception as e:
            st.error(f'Gagal membaca URL: {e}')

article=st.session_state.get('article')
if article:
    st.divider()
    st.subheader(article['title'] or 'Artikel')
    st.write(f"**{len(article['images'])} foto ditemukan**")
    if not article['images']:
        st.warning('Tidak ada gambar yang berhasil ditemukan. Website mungkin membutuhkan JavaScript atau memiliki proteksi anti-bot.')
    for i,item in enumerate(article['images'][:30]):
        with st.container(border=True):
            c1,c2=st.columns([1,2])
            with c1:
                try: st.image(item['url'], use_container_width=True)
                except: st.warning('Preview foto gagal dimuat')
            with c2:
                st.markdown(f'### Foto {i+1}')
                st.caption('Caption / ALT yang ditemukan')
                original=item['caption'] or item['alt'] or article['title']
                st.text_area('Original', value=original, key=f'orig_{i}', height=80)
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
