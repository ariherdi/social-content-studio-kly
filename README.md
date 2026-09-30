# Social Content Studio — KapanLagi V12

V12 memperbaiki ekstraksi gallery KapanLagi dengan fallback multi-page:
- baca `.pages-item[data-type="content-pages"]` bila tersedia;
- baca `data-pageurl` dari DOM bila tersedia;
- fallback crawl `?page=1..N`;
- ekstraksi gambar dari `data-src`, `src`, `data-original`, `data-lazy-src`, dan srcset;
- original image selalu dibentuk dari filename + tanggal YYYYMMDD ke format `/kapanlagi.com/download/g/YYYY/MM/DD/r/filename`;
- menampilkan diagnosis fetch/parser jika hasil 0 foto.
