# Mini Search Engine Informasi Kesehatan: Modul Crawler

Crawler multi-sumber yang patuh `robots.txt` dan menerapkan politeness
(jeda antar request, retry dengan backoff, User-Agent yang mencantumkan kontak).

## Instalasi
```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Sebelum menjalankan
1. Ganti email kontak pada `user_agent` di `config/sources.yaml`.
2. Pastikan izin penggunaan konten sudah diurus (lihat `license_note` per sumber).

## Menjalankan
```bash
python scripts/crawl.py --source rskemenkes_surabaya --limit 3   # uji dulu
python scripts/crawl.py --source rskemenkes_surabaya             # penuh
python scripts/crawl.py --all                                    # semua sumber
```
Hasil: `data/raw/<sumber>.jsonl` (satu artikel per baris). HTML mentah di-cache di
`data/raw_html/<sumber>/` sehingga perbaikan selector tidak perlu mengunduh ulang.
Proses dapat dilanjutkan (resume) bila terhenti.

## Menambah sumber baru
Salin satu blok di `config/sources.yaml` lalu ubah `list_url_template`,
`article_url_regex`, selector, dan `license_note`. Periksa hasil dengan `--limit 3`.

## Field keluaran
`id, url, source, title, date_published, author, date_reviewed, meta_description,
keywords, text, references, license_note, crawled_at`

## Etika data
- Crawling hanya untuk sumber yang robots.txt-nya mengizinkan.
- Data mentah tidak di-commit; bagikan skrip dan daftar URL saja.
- Cantumkan sumber dan lisensi pada laporan.
