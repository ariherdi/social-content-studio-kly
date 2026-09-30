# Social Content Studio — KapanLagi V4

POC Streamlit untuk workflow social media:
URL artikel → foto editorial → caption asli di bawah foto → rewrite AI ≤100 karakter → visual → ZIP.

## 1. Streamlit Secrets

Di Streamlit Cloud, buka **Settings → Secrets** dan masukkan:

```toml
OPENAI_API_KEY = "ISI_API_KEY_ANDA"
OPENAI_MODEL = "gpt-5.6-luna"
```

Jangan masukkan API key ke GitHub.

## 2. Jalankan lokal

```bash
pip install -r requirements.txt
streamlit run app.py
```

## 3. Catatan

- Fokus V4: KapanLagi.com.
- Caption foto diprioritaskan dari teks yang tampil di bawah foto/figcaption.
- `alt` hanya menjadi fallback terakhir.
- Original/full-res image dibentuk dari pola CDN KapanLagi dan tanggal YYYYMMDD yang ada di filename.
- Rewrite AI dibatasi maksimal 100 karakter.
- Hasil rewrite tetap bisa diedit manual.
- ZIP berisi PNG terpilih, `deskripsi-post.txt`, dan `caption-asli-dan-rewrite.txt`.
