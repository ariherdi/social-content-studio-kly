# Social Content Studio — Streamlit POC V1

URL artikel → ekstraksi foto/caption → copy pendek → preview 1080×1080 → download PNG.

## Deploy gratis dengan Streamlit Community Cloud
1. Buat repository GitHub, misalnya `social-content-studio`.
2. Upload `app.py`, `requirements.txt`, dan `README.md` ke root repository.
3. Buka https://share.streamlit.io/ dan sign in with GitHub.
4. Pilih repository, branch `main`, file `app.py`.
5. Deploy.

## Catatan
POC ini belum menggunakan LLM sungguhan. Fungsi `short_copy()` adalah placeholder rewrite sederhana. Website yang menggunakan JavaScript, anti-bot, atau lazy-loading tertentu mungkin tidak dapat diekstrak dengan sempurna.
