# Checklist: Reminder & Enforcement Hardening

## Legend

- `[ ]` belum dikerjakan
- `[/]` sedang berjalan / partial
- `[x]` selesai

## A. Data Model & Snapshot

- [x] simpan user disabled di snapshot
- [x] tampilkan disabled user di dashboard
- [x] tambah filter `Disabled`
- [x] tambah action `Enable`
- [ ] simpan reason/status classification yang lebih eksplisit
- [x] pisahkan `locked` vs `disabled` vs `expired` pada ringkasan KPI

## B. Reminder Logic

- [/] reminder hanya untuk user di bucket warning aktif
- [ ] gunakan satu definisi `expiring` di UI, API, dan KPI
- [ ] pastikan disabled user tidak ikut reminder
- [ ] pastikan locked user tidak ikut reminder bila policy mengharuskan
- [ ] tambahkan preview target sebelum broadcast

## C. Enforcement Logic

- [/] action hasil run harus sesuai state nyata AD
- [ ] `Run Policy` benar-benar mengeksekusi `ForceChange`
- [ ] `Run Policy` benar-benar mengeksekusi `Disable`
- [ ] implement `ActionAfterGrace` secara dinamis
- [ ] implement `MaxDisablesPerRun`
- [ ] implement `RequireConfirmationAboveThreshold`
- [ ] implement `WhatIfMode` sebagai global safety override

## D. User Management Actions

- [x] disable manual dari UI
- [x] unlock manual dari UI
- [x] force change manual dari UI
- [x] password reset manual dari UI
- [x] enable manual dari UI
- [ ] bulk enable
- [ ] action confirmation copy yang lebih jelas per risiko

## E. Dashboard & UX

- [x] history email per user
- [x] status email terakhir di list user
- [x] live log basic stream
- [ ] definisi KPI konsisten
- [/] status badge konsisten antar page
- [x] tooltip/perbedaan `Test Policy` vs `Run Policy`
- [ ] indikator mode aktif: monitoring / reminder / enforcement

## F. Security

- [ ] lindungi halaman UI, bukan hanya endpoint API
- [ ] stop inject default token ke browser
- [ ] amankan SSE stream
- [ ] batasi CORS
- [ ] rotasi seluruh secret yang pernah dipakai di config aktif

## G. Operations

- [ ] tambah run mutex agar tidak ada run paralel
- [ ] tambah retention cleanup untuk live logs
- [ ] tambah retention cleanup untuk email/action history bila perlu
- [ ] tambah backup/restore procedure untuk SQLite

## H. Configuration Hygiene

- [x] sediakan `.env.example`
- [x] sediakan `config.example.json`
- [ ] pisahkan config aktif vs config publish sepenuhnya
- [ ] beri validator startup untuk field wajib
- [ ] tandai config yang belum dipakai runner Python

## I. Integrations

- [/] Graph mail flow aktif
- [ ] revoke session flow benar-benar terhubung ke disable/lock policy
- [ ] Teams integration untuk runner Python
- [ ] SMTP/IMAP section diperjelas: dipakai atau deprecated

## J. Testing

- [ ] unit test untuk status classification
- [ ] unit test untuk reminder target selection
- [ ] unit test untuk action translation policy -> AD action
- [ ] integration smoke test untuk endpoint utama
- [ ] regression checklist sebelum release

## Immediate Next Actions

- [ ] samakan definisi `expiring` menjadi satu sumber kebenaran
- [ ] rapikan `Run Policy` agar action benar-benar dieksekusi atau diberi label simulasi
- [ ] tambah filter dan ringkasan disabled di dashboard utama
- [ ] tambah bulk enable dan verifikasi manual flow

## Current Audit Notes

- [/] histori run lama masih bisa menampilkan angka `disabled` yang tercampur dengan akun `locked` dari build policy versi sebelumnya; data historis perlu dibaca dengan konteks perubahan engine terbaru
- [ ] badge `excluded` / `whitelisted` belum tampil langsung di list user, jadi operator belum bisa cepat membedakan akun yang memang sengaja dikecualikan policy
- [ ] aksi manual ke AD masih bergantung penuh pada bind credential aktif; jika `BindUser` / `BindPassword` salah, UI akan terlihat sehat tetapi action nyata ke AD gagal
- [ ] trend chart masih mengandalkan data tabel `runs`, jadi koreksi KPI real-time di snapshot belum otomatis membetulkan histori chart lama
- [ ] belum ada guard khusus untuk memperingatkan operator saat menjalankan action manual ke akun yang masuk `Excluded Users`
- [ ] live SSE/dashboard masih mengandalkan token browser lokal; ini belum ideal untuk multi-operator atau publish internal yang lebih luas
