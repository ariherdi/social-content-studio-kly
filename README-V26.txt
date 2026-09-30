Social Content Studio — KapanLagi V26

Akar masalah V25 (parse_gallery_html -> upsert):
- Caption dipasangkan dengan foto memakai INDEKS GAMBAR DI DALAM RESPONS yang sama.
- Respons ?page=N hanya punya 1 blok caption (milik foto N) tetapi memuat beberapa gambar.
  Blok itu selalu jatuh ke gambar indeks 0 (foto 1, yang sudah terisi) lalu dibuang.
  Foto 2-5 tidak pernah menerima caption dari ?page=N.

Perbaikan V26:
1. Blok caption dipetakan ke foto berdasarkan nomor halaman (?page=N -> foto N) atau,
   bila respons hanya memuat satu gambar, berdasarkan filename gambar itu.
2. Satu teks caption tidak boleh dipakai dua foto (kalau server mengabaikan ?page=N dan
   selalu mengembalikan caption 1, foto 2-5 tidak akan ikut terisi caption 1).
3. Fallback halaman detail foto: link .html yang mengandung filename dicari di SEMUA respons
   (a href, script, JSON), lalu caption diambil dari marker halaman detail.
4. Heuristik DOM lama hanya dipakai paling akhir dan diberi label "LEMAH".
5. Regex fallback URL gambar di extract_article_image_urls_raw diperbaiki (sebelumnya tidak
   pernah cocok karena salah escape).
6. Fallback .pages-paragraph tidak lagi mengambil intro artikel (page-intro).
7. Panel diagnosis (tetap tampil setelah rerun): status/bytes/MD5/jumlah marker/caption per
   halaman, status per foto beserta log, pencarian potongan teks di respons mentah, dan
   tombol download ZIP respons mentah yang diterima Streamlit Cloud.

Deploy: ganti app.py di GitHub, commit, tunggu redeploy, analisis URL yang sama.
Fitur lain (UI, template, font, PNG/ZIP, rewrite AI) tidak diubah.
