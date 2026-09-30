# Social Content Studio — KapanLagi V6

V6 fokus pada 3 hal:

1. Hanya mengambil foto editorial besar dari body/article content.
2. Mendukung upload template sosial media PNG/JPG.
3. Menampilkan preview hasil akhir setelah foto + teks + template.

## Filter foto

Aplikasi tidak lagi melakukan scan seluruh halaman sebagai langkah utama.
Foto dicari dari container yang terindikasi sebagai article/body/content/photo/gallery.
Logo, icon, avatar, banner, ads, thumbnail, dan gambar UI disaring.

Foto kecil disaring berdasarkan ukuran yang terdeteksi. Ambang default:
- minimal lebar 600px
- minimal tinggi 400px

Jika HTML tidak memberikan ukuran, aplikasi mencoba membaca dimensi gambar.

## Template

Upload template PNG transparan untuk hasil seperti desain social media.
Layer:
1. foto
2. backing teks + teks
3. template PNG

Template akan di-resize mengikuti ukuran template sehingga rasio output mengikuti template.

## OpenAI

Di Streamlit Cloud → Settings → Secrets:

```toml
OPENAI_API_KEY = "ISI_API_KEY_ANDA"
OPENAI_MODEL = "gpt-5-mini"
```

Jangan commit API key ke GitHub.

## Jalankan

```bash
pip install -r requirements.txt
streamlit run app.py
```


## V7 — Struktur gallery KapanLagi diperketat

Untuk halaman photo gallery KapanLagi, extractor sekarang hanya mengambil:

```html
.pages-item[data-type="content-pages"]
  └── figure.pages-img
       └── img
  └── .pages-paragraph
```

Artinya:
- gambar di luar `content-pages` tidak diambil;
- gambar kecil/UI/thumbnail tidak diambil;
- credit seperti `instagram.com/yoona__lim` tidak dianggap caption;
- caption foto diambil dari `.pages-paragraph`, yaitu teks panjang yang berada setelah foto;
- `alt` hanya fallback terakhir.

Untuk template sosial media, gunakan PNG transparan agar template menjadi layer di atas foto.


## V8 — Perbaikan filter ukuran foto

KapanLagi dapat memiliki URL CDN seperti:

`/resized/670x/...`

tetapi atribut HTML-nya dapat berupa `width="375" height="514"` karena ukuran tersebut adalah ukuran display halaman, bukan ukuran sumber CDN.

V8 menggunakan ukuran pada URL CDN (`670x`) untuk menentukan apakah foto termasuk foto besar. Jadi foto gallery 670x tetap diambil meskipun display HTML-nya 375px.


## V9 — Bugfix extractor

V8 memiliki bug escaping pada regex pembaca ukuran CDN. Pola:

`/resized/670x/`

sekarang dibaca dengan regex yang benar sehingga `670` terdeteksi sebagai ukuran CDN.

Atribut `width="375"` pada HTML tetap tidak digunakan untuk menolak foto karena itu adalah ukuran display, bukan ukuran sumber CDN.


## V10 — Full-res download URL

Foto yang digunakan sekarang selalu dibentuk dari pola KapanLagi:

`https://cdns.klimg.com/kapanlagi.com/download/g/YYYY/MM/DD/r/{filename}`

Tanggal `YYYYMMDD` diambil dari filename foto pada URL resized.

Tidak ada lagi filter ukuran HTML atau filter `670x`. Foto ditentukan hanya dari struktur gallery `content-pages`, kemudian URL final diganti ke URL `download` full-res.

Caption tetap diambil dari `.pages-paragraph`.


## V11 — Critical regex fix

V10 masih memiliki escaping regex yang salah pada pembacaan tanggal filename.
V11 menggunakan regex Python yang benar:

`(?<!\d)((?:19|20)\d{6})(?!\d)`

Sehingga filename seperti:

`yoona_girls_generation-20260926-006-non_fotografer_kly.jpg`

berhasil menghasilkan:

`https://cdns.klimg.com/kapanlagi.com/download/g/2026/09/26/r/yoona_girls_generation-20260926-006-non_fotografer_kly.jpg`
