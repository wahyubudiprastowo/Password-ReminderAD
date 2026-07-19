# PRD: Reminder & Enforcement Hardening

## 1. Latar Belakang

Sistem Password Compliance Enforcer sudah bisa:

- membaca user dari Active Directory
- menghitung masa berlaku password
- mengirim reminder email
- menyediakan dashboard operasional
- menjalankan beberapa aksi manual dari UI

Namun masih ada gap besar antara:

- apa yang tampil di UI
- apa yang dihitung oleh policy
- apa yang benar-benar dieksekusi ke Active Directory

PRD ini dibuat untuk merapikan fondasi reminder, enforcement, keamanan operasional, dan pengalaman operator.

## 2. Problem Statement

Masalah utama saat ini:

1. hasil run dapat terlihat seolah enforcement sudah dijalankan, padahal sebagian action baru tercatat di database
2. user disabled sempat hilang dari snapshot sehingga tidak bisa dikelola ulang dari UI
3. definisi status `expiring`, `expired`, `locked`, dan `disabled` belum konsisten di seluruh sistem
4. dashboard belum cukup aman untuk penggunaan multi-operator atau publish internal
5. safety guard production belum lengkap

## 3. Tujuan

### Tujuan bisnis

- reminder password berjalan tepat sasaran
- operator helpdesk bisa melihat status user dengan jelas
- operator bisa melakukan recovery dasar tanpa keluar dari dashboard
- enforcement berjalan sesuai policy nyata, bukan sekadar simulasi tersamar

### Tujuan teknis

- sinkronkan policy, UI, dan eksekusi backend
- pertahankan user disabled di dashboard
- tambahkan jalur `Enable` untuk akun yang sudah disabled
- pisahkan mode monitoring, reminder, dan enforcement
- tambah safety control untuk menghindari aksi massal yang tidak disengaja

## 4. Non-Goals

Fase ini belum menargetkan:

- SSO ke dashboard
- RBAC multi-role penuh
- audit trail enterprise-grade ke SIEM
- approval workflow berbasis manager
- self-service password reset portal baru

## 5. Persona

### 1. IT Helpdesk

Butuh:

- cari user cepat
- lihat status password
- kirim reminder manual
- unlock, enable, disable, force change, reset password

### 2. IT Infrastructure / AD Admin

Butuh:

- validasi policy
- kontrol enforcement
- tahu apakah action benar-benar dieksekusi
- bisa audit history run dan outcome

### 3. IT Compliance / Security

Butuh:

- angka ringkas yang akurat
- distribusi user expiring/expired/disabled
- bukti reminder terkirim atau gagal

## 6. Scope

### In Scope

- auto reminder hardening
- enforcement execution hardening
- disabled user management
- enable action dari UI
- snapshot accuracy
- run safety control
- documentation readiness
- production behavior clarification

### Out of Scope

- redesign visual total
- migration ke PostgreSQL
- replacement total arsitektur Python runner

## 7. Functional Requirements

### FR-01 User status model

Sistem harus punya status operasional yang jelas:

- compliant
- expiring
- expired
- locked
- disabled
- unknown

### FR-02 Disabled users stay visible

Jika akun disabled, user tidak boleh hilang dari dashboard.

Acceptance:

- tetap muncul di `All`
- muncul di filter `Disabled`
- bisa dicari lewat search
- bisa dibuka detailnya

### FR-03 Enable action

Operator harus bisa mengaktifkan kembali akun disabled dari UI.

Acceptance:

- tombol `Enable` tersedia untuk user disabled
- bulk `Enable` tersedia
- history action tercatat

### FR-04 Reminder targeting

Reminder otomatis hanya boleh dikirim ke user yang memang masuk bucket warning policy aktif.

Acceptance:

- target reminder mengikuti `WarningDays`
- disabled user tidak ikut reminder
- user tanpa email tidak menimbulkan crash
- outcome email tercatat sukses/gagal

### FR-05 Enforcement targeting

Enforcement hanya boleh diterapkan ke user yang memenuhi policy enforcement aktual.

Acceptance:

- action yang dieksekusi ke AD sama dengan action yang ditampilkan di run result
- what-if tidak mengubah AD
- run nyata benar-benar mengeksekusi action

### FR-06 Monitoring only mode

Harus ada mode aman untuk observasi tanpa action perubahan akun.

Acceptance:

- scan tetap jalan
- dashboard tetap update
- reminder otomatis bisa dimatikan
- disable/force otomatis tidak dijalankan

### FR-07 Safety control

Sistem harus mencegah aksi massal yang tidak aman.

Acceptance:

- ada batas maksimum disable per run
- ada guard kalau hasil disable melebihi threshold
- ada lock run agar tidak ada run paralel

### FR-08 Auditability

Semua action utama harus punya jejak.

Acceptance:

- run history ada
- action history ada
- email delivery history ada
- disabled/enabled action terlihat jelas

## 8. Non-Functional Requirements

### NFR-01 Security

- token tidak boleh bocor ke semua visitor dashboard
- endpoint sensitif wajib auth
- SSE harus diproteksi atau proxied aman

### NFR-02 Reliability

- kegagalan Graph atau AD tidak membuat UI blank
- error message harus cukup jelas untuk operator

### NFR-03 Portability

- repo bisa dipindah ke folder mana saja
- sample config aman untuk GitHub/GitLab

### NFR-04 Maintainability

- struktur policy dapat dipahami operator
- fitur punya checklist implementasi

## 9. Policy Modes

### Mode A: Monitoring Only

- scan aktif
- UI aktif
- reminder off
- enforcement off

### Mode B: Reminder Only

- scan aktif
- reminder hanya untuk bucket warning
- enforcement off

### Mode C: Controlled Enforcement

- reminder aktif
- force change aktif
- disable aktif sesuai threshold dan guard

## 10. UX Requirements

- operator harus tahu bedanya `Test Policy` dan `Run Policy`
- status user harus konsisten di table, modal, KPI, dan filter
- user disabled harus punya warna/status/action berbeda
- hasil email harus terlihat per user

## 11. Open Risks

- LDAPS reset password masih bergantung kesiapan Domain Controller
- Graph permission bisa berubah dan memutus flow revoke/reminder
- data AD legacy lama dapat membuat dashboard penuh expired user historis
- histori run lama dapat mengandung klasifikasi `locked` yang dulu sempat masuk ke bucket `disabled`
- excluded user belum punya visual marker yang kuat di list user dan modal detail
- manual action masih sangat sensitif terhadap drift credential bind Active Directory

## 12. Release Plan

### Phase 1

- disabled user visibility
- enable action
- filter status cleanup
- email target cleanup

### Phase 2

- real enforcement execution alignment
- monitoring-only mode
- reminder-only mode
- run lock

### Phase 3

- auth hardening dashboard
- retention cleanup
- test automation

## 13. Success Metrics

- 100% user disabled tetap terlihat di dashboard
- 100% action manual enable/disable tercatat di history
- mismatch antara run result dan AD real state turun ke 0
- false target reminder turun signifikan
