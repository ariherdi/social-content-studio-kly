import io,re,zipfile,html as html_lib
from pathlib import Path
from urllib.parse import urljoin,urlparse
import requests
from bs4 import BeautifulSoup
from PIL import Image,ImageDraw,ImageFont
import streamlit as st

st.set_page_config(page_title="Social Content Studio",page_icon="📸",layout="wide")
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/140 Safari/537.36"

def clean(s): return re.sub(r"\s+"," ",html_lib.unescape(s or "")).strip()
def fname(u): return Path(urlparse(u).path).name

def original_url(u):
    f=fname(u); m=re.search(r"(?<!\d)((?:19|20)\d{6})(?!\d)",f)
    if not m:return None
    d=m.group(1)
    return f"https://cdns.klimg.com/kapanlagi.com/download/g/{d[:4]}/{d[4:6]}/{d[6:8]}/r/{f}"

def strip_credit(t):
    t=re.sub(r"\s*(?:Foto|Dok|Credit|Sumber)\s*:\s*[^|]+$","",t,flags=re.I)
    t=re.sub(r"\s*©\s*[^|]+$","",t)
    return clean(t)

def caption(img):
    p=img
    for _ in range(4):
        p=getattr(p,"parent",None)
        if not p: break
        n=p.find("figcaption")
        if n:
            t=strip_credit(n.get_text(" ",strip=True))
            if t:return t
    p=img.parent
    for _ in range(3):
        if not p:break
        for n in p.find_all(["p","div","span"],limit=15):
            cls=" ".join(n.get("class",[]))
            if re.search(r"caption|photo.?caption|image.?caption|keterangan",cls,re.I):
                t=strip_credit(n.get_text(" ",strip=True))
                if len(t)>8:return t
        p=p.parent
    a=clean(img.get("alt",""))
    return strip_credit(a) if len(a)>8 else ""

@st.cache_data(ttl=3600,show_spinner=False)
def fetch(u):
    r=requests.get(u,headers={"User-Agent":UA},timeout=25);r.raise_for_status()
    return r.text,r.url

def extract(u):
    html,final=fetch(u);s=BeautifulSoup(html,"html.parser")
    h=s.find("h1");title=clean(h.get_text(" ",strip=True)) if h else ""
    intro=""
    for attrs in [{"name":"description"},{"property":"og:description"},{"name":"twitter:description"}]:
        n=s.find("meta",attrs=attrs)
        if n and len(clean(n.get("content")))>40: intro=clean(n["content"]);break
    if not intro:
        main=s.find("article") or s.find("main") or s.body
        for p in main.find_all("p") if main else []:
            t=clean(p.get_text(" ",strip=True))
            if len(t)>50 and not re.search(r"cookie|newsletter",t,re.I): intro=t;break
    photos=[];seen=set()
    for img in s.find_all("img"):
        cand=[]
        for a in ("src","data-src","data-original","data-lazy-src"):
            if img.get(a):cand.append(urljoin(final,img[a]))
        for u2 in cand:
            if "cdns.klimg.com/resized/" not in u2:continue
            f=fname(u2)
            if not f or f in seen or re.search(r"logo|icon|avatar|placeholder|sprite|banner|ads?",f,re.I):continue
            org=original_url(u2)
            if not org:continue
            seen.add(f);photos.append({"resized":u2,"original":org,"filename":f,"caption":caption(img)});break
    return title,intro,photos

@st.cache_data(ttl=3600,show_spinner=False)
def getimg(u):
    r=requests.get(u,headers={"User-Agent":UA,"Referer":"https://www.kapanlagi.com/"},timeout=30);r.raise_for_status()
    return r.content

def font(size):
    p="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    return ImageFont.truetype(p,size)

def wrap(draw,text,f,maxw):
    out=[];cur=""
    for w in text.split():
        x=w if not cur else cur+" "+w
        if draw.textbbox((0,0),x,font=f)[2]<=maxw:cur=x
        else:
            if cur:out.append(cur)
            cur=w
    if cur:out.append(cur)
    return out[:4]

def render(raw,text,template,size,tc,hc,opacity):
    im=Image.open(io.BytesIO(raw)).convert("RGB"); W=H=1080;im.thumbnail((W,H))
    c=Image.new("RGB",(W,H),"black");c.paste(im,((W-im.width)//2,(H-im.height)//2))
    ov=Image.new("RGBA",(W,H),(0,0,0,0));d=ImageDraw.Draw(ov);f=font(size);lines=wrap(d,text,f,W-130)
    lh=size+10;bh=len(lines)*lh+56
    if template=="Bar bawah solid": y=H-bh;d.rectangle((0,y,W,H),fill=(*hc,opacity));ty=y+28
    elif template=="Pita atas": y=45;d.rectangle((35,y,W-35,y+bh),fill=(*hc,opacity));ty=y+28
    else:
        y=H-bh-20
        for i in range(bh+20):
            a=int(opacity*i/(bh+19));d.line((0,y+i,W,y+i),fill=(*hc,a))
        ty=y+28
    for line in lines:
        box=d.textbbox((0,0),line,font=f);x=(W-(box[2]-box[0]))//2
        d.text((x,ty),line,font=f,fill=tc);ty+=lh
    return Image.alpha_composite(c.convert("RGBA"),ov).convert("RGB")

st.title("📸 Social Content Studio")
st.caption("KapanLagi V3 — foto asli + intro + caption + visual editor")
url=st.text_input("URL artikel KapanLagi",placeholder="https://www.kapanlagi.com/foto/...")
if "data" not in st.session_state:st.session_state.data=None
if st.button("🔎 Analisis Artikel",type="primary",use_container_width=True):
    if "kapanlagi.com" not in url:st.error("V3 difokuskan untuk kapanlagi.com.")
    else:
        try:
            with st.spinner("Menganalisis..."):st.session_state.data=extract(url)
            st.success(f"Ditemukan {len(st.session_state.data[2])} foto editorial.")
        except Exception as e:st.error(str(e))

if st.session_state.data:
    title,intro,photos=st.session_state.data
    st.subheader(title)
    post=st.text_area("Deskripsi Post",intro,height=120,max_chars=1000)
    st.caption(f"{len(post)} karakter")
    st.divider()
    a,b,c=st.columns(3)
    with a:template=st.selectbox("Template",["Bar bawah solid","Bar bawah gradient","Pita atas"])
    with b:fontname=st.selectbox("Font",["Poppins","Montserrat","Oswald","Playfair Display","Anton","Bebas Neue","Inter"])
    with c:size=st.slider("Ukuran font",36,110,68)
    a,b,c=st.columns(3)
    with a:tc=st.color_picker("Warna teks","#FFFFFF")
    with b:hc=st.color_picker("Warna highlight","#000000")
    with c:op=st.slider("Opacity highlight",0,100,75)
    rgb=lambda x:tuple(int(x[i:i+2],16) for i in (1,3,5))
    outputs=[]
    st.subheader(f"Foto ({len(photos)})")
    for i,p in enumerate(photos):
        l,r=st.columns([1,1.1])
        raw=None
        with l:
            try:raw=getimg(p["original"]);st.image(raw,use_container_width=True)
            except Exception as e:st.warning(f"Foto asli gagal: {e}")
        with r:
            use=st.checkbox("Gunakan foto ini",True,key=f"use{i}")
            cap=st.text_area(f"Caption Foto {i+1}",p["caption"][:100],max_chars=100,height=90,key=f"cap{i}")
            st.caption(f"{len(cap)}/100 karakter")
            if raw:
                try:
                    out=render(raw,cap,template,size,rgb(tc)+(255,),rgb(hc),int(op*2.55))
                    st.image(out,caption="Live preview",use_container_width=True)
                    bio=io.BytesIO();out.save(bio,"PNG");data=bio.getvalue()
                    st.download_button(f"⬇️ Download PNG {i+1}",data,f"{i+1:02d}-{p['filename']}.png","image/png",key=f"dl{i}")
                    if use:outputs.append((f"{i+1:02d}-{p['filename']}.png",data))
                except Exception as e:st.error(f"Preview gagal: {e}")
        st.divider()
    if outputs:
        z=io.BytesIO()
        with zipfile.ZipFile(z,"w",zipfile.ZIP_DEFLATED) as f:
            for n,d in outputs:f.writestr(n,d)
            f.writestr("deskripsi-post.txt",post)
        st.download_button("📦 Download Semua + deskripsi-post.txt",z.getvalue(),"social-content-package.zip","application/zip",type="primary",use_container_width=True)
