Social Content Studio — KapanLagi V25

Perbaikan utama V25:
- Foto yang sudah ditemukan pada HTML artikel tidak lagi dianggap selesai jika caption masih kosong.
- App tetap mengambil ?page=1, ?page=2, dst. untuk memperoleh caption per foto.
- Jika response halaman berikutnya menemukan caption untuk foto yang sama, caption tersebut mengisi record foto yang sebelumnya kosong.
- Foto tidak diduplikasi karena tetap dicocokkan berdasarkan URL original.
- Tidak menggunakan alt atau credit sebagai caption.
- URL full-resolution tetap mengikuti aturan /download/g/YYYY/MM/DD/r/filename.

Deploy:
1. Replace app.py dan requirements.txt di GitHub.
2. Commit/push.
3. Tunggu Streamlit Cloud redeploy.
4. Analisis URL KapanLagi yang sama.
