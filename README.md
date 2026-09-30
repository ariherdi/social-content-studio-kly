# Social Content Studio — KapanLagi V14

POC Streamlit untuk mengambil artikel foto KapanLagi.com.

## Extraction rules
- Deskripsi utama: `.pages-item[data-type="page-intro"] .pages-paragraph`
- Foto: `.pages-item[data-type="content-pages"] figure.pages-img img`
- Teks editorial foto: `.pages-paragraph` tepat setelah `figure.pages-img`, pada blok `STARTOFPAGEDESCRIPTIONBOTTOM`
- `figcaption.pages-img-desc` / credit tidak digunakan sebagai caption editorial.
- `img alt` hanya fallback jika teks editorial panjang benar-benar kosong.
- URL final foto memakai format `download/g/YYYY/MM/DD/r/{filename}` berdasarkan tanggal 8 digit pada filename.
