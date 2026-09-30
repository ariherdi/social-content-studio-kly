SOCIAL CONTENT STUDIO — KAPANLAGI V24

Root cause yang ditemukan dari diagnosis V23:
- HTTP 200 dan HTML berhasil diterima (~190 KB).
- Respons live tidak memiliki .pages-item[data-type='content-pages'].
- Respons live tidak memiliki figure.pages-img.
- Marker STARTOFPAGEDESCRIPTIONBOTTOM juga tidak tersedia pada respons live.
- Karena V23 hanya mengenali CDN /resized/ + wrapper lama, 131 tag img menghasilkan 0 article_images.

Strategi V24:
1. Tidak bergantung pada class/wrapper KapanLagi.
2. Identifikasi foto artikel berdasarkan nama file yang mengandung tanggal YYYYMMDD.
3. Tetap membuat URL full-resolution sesuai aturan:
   https://cdns.klimg.com/kapanlagi.com/download/g/YYYY/MM/DD/r/FILENAME
4. Caption dicari secara DOM order: paragraph pertama yang valid setelah foto dan sebelum foto artikel berikutnya.
5. Marker KapanLagi tetap dipakai bila tersedia, tetapi bukan syarat.
6. Diagnosis ditambah raw_dated_images agar terlihat apakah file foto artikel ditemukan.

Pengujian lokal:
- HTML KapanLagi asli: 5 foto + 5 caption.
- HTML simulasi tanpa class dan tanpa komentar marker: 5 foto + 5 caption.

Upload app.py + requirements.txt.
Jangan upload __pycache__.
