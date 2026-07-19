import json
import os
import base64
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULT_TEMPLATE_PATH = Path(os.getenv("PCE_EMAIL_TEMPLATE_PATH", "/app/data/email_templates.json"))
SUPPORT_GUIDE_URL = "https://support.microsoft.com/en-us/accounts-billing/work-school/change-your-work-or-school-account-password"
IT_HELPDESK_URL = "mailto:ithelp@example.com?subject=Butuh%20Bantuan%20Ganti%20Password"
GSA_HINT_IMAGE_CID = "gsa-tray-guide"
GSA_HINT_IMAGE_NAME = "gsa-tray-guide.png"
GSA_HINT_IMAGE_STATIC_PATH = f"/static/img/{GSA_HINT_IMAGE_NAME}"
GSA_HINT_IMAGE_FILE = Path(__file__).parent / "static" / "img" / GSA_HINT_IMAGE_NAME


def _button(label: str, href: str, background: str) -> str:
    return f"""
<table role="presentation" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
  <tr>
    <td bgcolor="{background}" style="padding:0;">
      <a href="{href}" style="display:inline-block;padding:14px 22px;font-size:15px;font-weight:700;line-height:1.2;color:#ffffff;text-decoration:none;font-family:Segoe UI,Arial,sans-serif;">{label}</a>
    </td>
  </tr>
</table>
""".strip()


def _gsa_hint_block() -> str:
    return """
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;margin:22px 0;background:#f8fafc;border:1px solid #dbeafe;">
  <tr>
    <td style="padding:18px 20px;font-family:Segoe UI,Arial,sans-serif;color:#1e3a8a;font-size:14px;line-height:1.8;">
      <div style="font-size:16px;font-weight:700;color:#0f172a;margin-bottom:10px;">Panduan cepat menemukan Global Secure Access</div>
      <div style="margin:0 0 14px 0;">
        <img src="{{gsa_hint_image}}" alt="Screenshot Global Secure Access connected di taskbar Windows" width="360" style="display:block;width:100%;max-width:360px;height:auto;border:0;outline:none;text-decoration:none;background:#ffffff;border-radius:12px;" />
      </div>
      <div style="color:#334155;font-size:14px;line-height:1.8;">
        <div>1. Lihat area ikon kecil di <strong>pojok kanan bawah Windows</strong>, dekat jam, Wi-Fi, dan baterai.</div>
        <div>2. Kalau ikon belum terlihat, klik tanda <strong>^</strong> untuk membuka ikon tersembunyi seperti pada screenshot.</div>
        <div>3. Cari aplikasi <strong>Global Secure Access</strong>, lalu buka dan pastikan statusnya <strong>Connected</strong>.</div>
      </div>
    </td>
  </tr>
</table>
""".strip()


def _contact_block() -> str:
    return f"""
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;margin:22px 0;background:#ecfeff;border:1px solid #a5f3fc;">
  <tr>
    <td style="padding:18px 20px;color:#155e75;font-size:14px;line-height:1.8;font-family:Segoe UI,Arial,sans-serif;">
      Apabila Anda memerlukan bantuan lebih lanjut, silakan hubungi tim IT Support melalui tombol berikut.
      <div style="margin-top:12px;">
        {_button("Hubungi Tim IT Support", IT_HELPDESK_URL, "#0891b2")}
      </div>
    </td>
  </tr>
</table>
""".strip()


def _email_shell(accent: str, eyebrow: str, title: str, lead: str, body_html: str, footer: str) -> str:
    return f"""
<html>
  <body style="margin:0;padding:0;background:#eef2ff;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;background:#eef2ff;mso-table-lspace:0pt;mso-table-rspace:0pt;">
      <tr>
        <td align="center" style="padding:24px 12px;">
          <table role="presentation" width="720" cellpadding="0" cellspacing="0" border="0" style="width:720px;max-width:720px;border-collapse:collapse;background:#ffffff;border:1px solid #dbe4ff;mso-table-lspace:0pt;mso-table-rspace:0pt;">
            <tr>
              <td bgcolor="{accent}" style="padding:28px 32px 24px 32px;font-family:Segoe UI,Arial,sans-serif;color:#ffffff;">
                <div style="font-size:12px;letter-spacing:3px;text-transform:uppercase;opacity:.85;">{eyebrow}</div>
                <div style="font-size:28px;line-height:1.25;font-weight:700;margin-top:12px;">{title}</div>
                <div style="font-size:15px;line-height:1.8;margin-top:10px;">{lead}</div>
              </td>
            </tr>
            <tr>
              <td style="padding:28px 32px;font-family:Segoe UI,Arial,sans-serif;color:#1f2937;font-size:15px;line-height:1.9;">
                {body_html}
              </td>
            </tr>
            <tr>
              <td style="padding:18px 32px 24px 32px;border-top:1px solid #e5e7eb;background:#fafbff;color:#64748b;font-size:12px;line-height:1.8;font-family:Segoe UI,Arial,sans-serif;">
                {footer}
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
""".strip()


DEFAULT_EMAIL_TEMPLATES: dict[str, dict[str, str]] = {
    "warn_7": {
        "label": "Warning 7 Hari",
        "subject": "Pengingat: Password Akun Digiserve Anda Akan Berakhir {{days_left}} Hari Lagi",
        "html": _email_shell(
            "#4338ca",
            "Digiserve IT Compliance",
            "Waktunya menjadwalkan ganti password",
            "Password akun <strong>{{upn}}</strong> akan berakhir dalam {{days_left}} hari. Ini waktu paling aman untuk ganti password lebih awal.",
            f"""
<p style="margin:0 0 18px 0;font-size:16px;line-height:1.8;">Halo <strong>{{display_name}}</strong>,</p>
<p style="margin:0 0 18px 0;">Kami mengingatkan bahwa password akun kerja Anda akan segera berakhir. Mohon lakukan perubahan password secara mandiri sebelum masa berlaku berakhir.</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;margin:22px 0;background:#f8faff;border:1px solid #dbe4ff;">
  <tr>
    <td style="padding:18px 20px;color:#334155;font-size:15px;line-height:1.9;font-family:Segoe UI,Arial,sans-serif;">
      <div><strong>Akun:</strong> {{upn}}</div>
      <div><strong>Status:</strong> Akan berakhir dalam {{days_left}} hari</div>
    </td>
  </tr>
</table>
<h2 style="margin:24px 0 12px 0;font-size:18px;line-height:1.4;color:#111827;">Cara ganti password dari laptop kantor</h2>
<ol style="margin:0 0 18px 22px;padding:0;color:#334155;font-size:15px;line-height:1.9;">
  <li>Pastikan laptop kantor tersambung ke jaringan kantor.</li>
  <li>Tekan <strong>Ctrl + Alt + Del</strong>, lalu pilih <strong>Change a password</strong>.</li>
  <li>Masukkan password lama dan password baru.</li>
  <li>Kalau memakai Remote Desktop, gunakan <strong>Ctrl + Alt + End</strong>.</li>
  <li>Sesudah berhasil, login ulang ke Outlook, Microsoft 365, Wi-Fi kantor, dan aplikasi kerja lain.</li>
</ol>
{_gsa_hint_block()}
<h2 style="margin:24px 0 12px 0;font-size:18px;line-height:1.4;color:#111827;">Cara ganti password saat remote melalui Global Secure Access</h2>
<ol style="margin:0 0 18px 22px;padding:0;color:#334155;font-size:15px;line-height:1.9;">
  <li>Pastikan laptop sudah terhubung ke internet yang stabil, lalu buka aplikasi <strong>Global Secure Access</strong> dari taskbar Windows.</li>
  <li>Jika status masih <strong>Disconnected</strong>, klik <strong>Connect</strong> dan tunggu sampai berubah menjadi <strong>Connected</strong>.</li>
  <li>Biarkan aplikasi tetap menyala dan jangan disconnect selama proses ganti password berlangsung.</li>
  <li>Setelah connected, tekan <strong>Ctrl + Alt + Del</strong>, lalu pilih <strong>Change a password</strong>.</li>
  <li>Masukkan password lama, lalu isi password baru dan konfirmasi password baru tersebut.</li>
  <li>Jika berhasil, tunggu 3 sampai 5 menit agar perubahan password tersinkron ke email, Microsoft 365, dan akses remote.</li>
  <li>Sesudah sinkron, login ulang ke Outlook, Microsoft 365, Global Secure Access, dan aplikasi kerja lain yang sebelumnya masih memakai password lama.</li>
</ol>
<p style="margin:0 0 18px 0;font-size:14px;line-height:1.8;color:#475569;">Kalau layanan email atau aplikasi cloud belum langsung membaca password baru, biasanya perlu jeda sinkronisasi singkat. Kalau lewat beberapa menit masih gagal, silakan hubungi helpdesk.</p>
<h2 style="margin:24px 0 12px 0;font-size:18px;line-height:1.4;color:#111827;">Kalau ditunda terlalu lama</h2>
<ul style="margin:0 0 20px 22px;padding:0;color:#334155;font-size:15px;line-height:1.9;">
  <li>Akses ke laptop domain, email, Global Secure Access, dan aplikasi internal bisa mulai terganggu.</li>
  <li>Password akan masuk ke periode pengingat yang lebih kritis.</li>
  <li>Kalau sudah telat, proses pemulihan akses biasanya lebih ribet dan butuh bantuan manual dari IT.</li>
</ul>
<div style="margin:24px 0 0 0;">
  {_button("Lihat Panduan Ganti Password", SUPPORT_GUIDE_URL, "#4338ca")}
</div>
{_contact_block()}
""".strip(),
            "Email ini dikirim otomatis oleh sistem pengingat password Digiserve.",
        ),
    },
    "warn_3": {
        "label": "Warning 3 Hari",
        "subject": "Pengingat Penting: Password Akun Digiserve Anda Berakhir {{days_left}} Hari Lagi",
        "html": _email_shell(
            "#d97706",
            "Digiserve IT Compliance",
            "Password Anda akan berakhir dalam {{days_left}} hari",
            "Masih ada sedikit waktu untuk memperbarui password akun <strong>{{upn}}</strong> tanpa terburu-buru.",
            f"""
<p style="margin:0 0 18px 0;font-size:16px;line-height:1.8;">Halo <strong>{{display_name}}</strong>,</p>
<p style="margin:0 0 18px 0;">Karena sisa waktunya tinggal sedikit, kami sarankan perubahan password dilakukan hari ini juga langsung dari laptop kerja Anda supaya akses kerja tetap aman.</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;margin:22px 0;background:#fffaf5;border:1px solid #fed7aa;">
  <tr>
    <td style="padding:18px 20px;color:#334155;font-size:15px;line-height:1.9;font-family:Segoe UI,Arial,sans-serif;">
      <div><strong>Akun:</strong> {{upn}}</div>
      <div><strong>Status:</strong> Akan berakhir dalam {{days_left}} hari</div>
    </td>
  </tr>
</table>
<h2 style="margin:24px 0 12px 0;font-size:18px;line-height:1.4;color:#111827;">Cara ganti password dari laptop kantor</h2>
<ol style="margin:0 0 18px 22px;padding:0;color:#334155;font-size:15px;line-height:1.9;">
  <li>Sambungkan laptop kantor ke jaringan internal.</li>
  <li>Tekan <strong>Ctrl + Alt + Del</strong>, lalu pilih <strong>Change a password</strong>.</li>
  <li>Masukkan password lama dan password baru.</li>
  <li>Jika memakai Remote Desktop, gunakan <strong>Ctrl + Alt + End</strong>.</li>
</ol>
{_gsa_hint_block()}
<h2 style="margin:24px 0 12px 0;font-size:18px;line-height:1.4;color:#111827;">Cara ganti password saat remote melalui Global Secure Access</h2>
<ol style="margin:0 0 18px 22px;padding:0;color:#334155;font-size:15px;line-height:1.9;">
  <li>Buka aplikasi <strong>Global Secure Access</strong> dari taskbar Windows, lalu pastikan status koneksi sudah <strong>Connected</strong>.</li>
  <li>Kalau status masih belum connected, klik <strong>Connect</strong> dan tunggu sampai koneksinya stabil.</li>
  <li>Jangan tutup atau disconnect Global Secure Access selama proses perubahan password berlangsung.</li>
  <li>Setelah connected, tekan <strong>Ctrl + Alt + Del</strong>, lalu pilih <strong>Change a password</strong>.</li>
  <li>Masukkan password lama, isi password baru, lalu konfirmasi password baru Anda.</li>
  <li>Tunggu beberapa menit sampai sinkronisasi selesai, lalu login ulang ke Outlook, Microsoft 365, Global Secure Access, dan aplikasi kerja lain.</li>
</ol>
<p style="margin:0 0 18px 0;font-size:14px;line-height:1.8;color:#475569;">Kalau email atau aplikasi cloud belum ikut membaca password baru, tunggu beberapa menit agar sinkronisasi akun selesai.</p>
<ul style="margin:0 0 20px 22px;padding:0;color:#334155;font-size:15px;line-height:1.9;">
  <li>Jika terlambat, akses email, Global Secure Access, dan aplikasi kerja bisa mulai terganggu.</li>
  <li>Password akan masuk ke tahap pengingat terakhir sebelum benar-benar expired.</li>
</ul>
<div style="margin:24px 0 0 0;">
  {_button("Lihat Panduan Ganti Password", SUPPORT_GUIDE_URL, "#d97706")}
</div>
{_contact_block()}
""".strip(),
            "Email ini dikirim otomatis oleh sistem pengingat password Digiserve.",
        ),
    },
    "warn_1": {
        "label": "Warning 1 Hari",
        "subject": "Pengingat Terakhir: Password Akun Digiserve Anda Berakhir Besok",
        "html": _email_shell(
            "#ea580c",
            "Digiserve IT Compliance",
            "Pengingat terakhir sebelum password berakhir",
            "Password akun <strong>{{upn}}</strong> akan berakhir besok. Mohon segera ganti password hari ini.",
            f"""
<p style="margin:0 0 18px 0;font-size:16px;line-height:1.8;">Halo <strong>{{display_name}}</strong>,</p>
<p style="margin:0 0 18px 0;">Ini pengingat terakhir sebelum password masuk masa expired. Mohon segera ganti password dari laptop kerja Anda hari ini juga, baik saat di kantor maupun saat terhubung ke Global Secure Access perusahaan.</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;margin:22px 0;background:#fff7ed;border:1px solid #fdba74;">
  <tr>
    <td style="padding:18px 20px;color:#334155;font-size:15px;line-height:1.9;font-family:Segoe UI,Arial,sans-serif;">
      <div><strong>Akun:</strong> {{upn}}</div>
      <div><strong>Status:</strong> Berakhir dalam {{days_left}} hari</div>
    </td>
  </tr>
</table>
<h2 style="margin:24px 0 12px 0;font-size:18px;line-height:1.4;color:#111827;">Cara ganti password dari laptop kantor</h2>
<ol style="margin:0 0 18px 22px;padding:0;color:#334155;font-size:15px;line-height:1.9;">
  <li>Pastikan perangkat tersambung ke jaringan kantor.</li>
  <li>Tekan <strong>Ctrl + Alt + Del</strong>, lalu pilih <strong>Change a password</strong>.</li>
  <li>Masukkan password lama dan password baru.</li>
  <li>Jika lewat Remote Desktop, gunakan <strong>Ctrl + Alt + End</strong>.</li>
</ol>
{_gsa_hint_block()}
<h2 style="margin:24px 0 12px 0;font-size:18px;line-height:1.4;color:#111827;">Cara ganti password saat remote melalui Global Secure Access</h2>
<ol style="margin:0 0 18px 22px;padding:0;color:#334155;font-size:15px;line-height:1.9;">
  <li>Buka aplikasi <strong>Global Secure Access</strong> dari area taskbar Windows dan pastikan statusnya sudah <strong>Connected</strong>.</li>
  <li>Pastikan perangkat tidak sedang pindah jaringan Wi-Fi atau hotspot saat Anda mengganti password.</li>
  <li>Setelah connected, lakukan <strong>Ctrl + Alt + Del</strong>, lalu pilih <strong>Change a password</strong>.</li>
  <li>Masukkan password lama, isi password baru, lalu konfirmasi password baru tersebut.</li>
  <li>Tunggu beberapa menit agar password baru tersinkron ke layanan cloud dan akses remote.</li>
  <li>Sesudah password berubah, login ulang ke Outlook, Microsoft 365, Global Secure Access, dan perangkat kerja.</li>
</ol>
<p style="margin:0 0 18px 0;font-size:14px;line-height:1.8;color:#475569;">Untuk akun yang tersinkron ke layanan cloud, perubahan password bisa butuh beberapa menit untuk terbaca penuh. Jika akses Global Secure Access, email, atau aplikasi kerja masih gagal setelah itu, mohon hubungi helpdesk.</p>
<ul style="margin:0 0 20px 22px;padding:0;color:#334155;font-size:15px;line-height:1.9;">
  <li>Besok akses email, Global Secure Access, laptop domain, dan aplikasi kerja dapat terganggu jika password belum diganti.</li>
  <li>Akun dapat masuk ke penanganan enforcement setelah masa berlaku habis.</li>
</ul>
<div style="margin:24px 0 0 0;">
  {_button("Lihat Panduan Ganti Password", SUPPORT_GUIDE_URL, "#ea580c")}
</div>
{_contact_block()}
""".strip(),
            "Email ini dikirim otomatis oleh sistem pengingat password Digiserve.",
        ),
    },
    "expired": {
        "label": "Expired",
        "subject": "Tindakan Diperlukan: Reset Password Akun Digiserve Anda",
        "html": _email_shell(
            "#b91c1c",
            "Digiserve IT Compliance",
            "Akun Anda perlu segera reset password",
            "Password akun <strong>{{upn}}</strong> sudah melewati masa berlaku normal dan perlu segera diperbarui.",
            f"""
<p style="margin:0 0 18px 0;font-size:16px;line-height:1.8;">Halo <strong>{{display_name}}</strong>,</p>
<p style="margin:0 0 18px 0;">Password akun kerja Anda telah melewati masa berlaku. Mohon segera melakukan perubahan password agar akses kerja tetap tersedia.</p>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;margin:22px 0;background:#f8faff;border:1px solid #dbe4ff;">
  <tr>
    <td style="padding:18px 20px;color:#334155;font-size:15px;line-height:1.9;font-family:Segoe UI,Arial,sans-serif;">
      <div><strong>Akun:</strong> {{upn}}</div>
      <div><strong>Status:</strong> Password expired</div>
    </td>
  </tr>
</table>
<h2 style="margin:24px 0 12px 0;font-size:18px;line-height:1.4;color:#111827;">Cara ganti password dari laptop kantor</h2>
<ol style="margin:0 0 18px 22px;padding:0;color:#334155;font-size:15px;line-height:1.9;">
  <li>Pastikan laptop kantor Anda tersambung ke jaringan kantor.</li>
  <li>Tekan <strong>Ctrl + Alt + Del</strong>, lalu pilih <strong>Change a password</strong>.</li>
  <li>Masukkan password lama, kemudian isi password baru.</li>
  <li>Jika Anda sedang memakai Remote Desktop, gunakan <strong>Ctrl + Alt + End</strong>.</li>
  <li>Setelah berhasil, login ulang ke Outlook, Microsoft 365, Wi-Fi kantor, Global Secure Access, dan aplikasi lain yang masih memakai password lama.</li>
</ol>
{_gsa_hint_block()}
<h2 style="margin:24px 0 12px 0;font-size:18px;line-height:1.4;color:#111827;">Cara ganti password saat remote melalui Global Secure Access</h2>
<ol style="margin:0 0 18px 22px;padding:0;color:#334155;font-size:15px;line-height:1.9;">
  <li>Buka aplikasi <strong>Global Secure Access</strong> dari area taskbar Windows di pojok kanan bawah.</li>
  <li>Di aplikasi <strong>Global Secure Access</strong>, pastikan status koneksi berubah menjadi <strong>Connected</strong>.</li>
  <li>Kalau status masih <strong>Reconnecting</strong> atau <strong>Disconnected</strong>, tunggu sampai stabil sebelum lanjut ke proses ganti password.</li>
  <li>Setelah connected, tekan <strong>Ctrl + Alt + Del</strong>, lalu pilih <strong>Change a password</strong>.</li>
  <li>Masukkan password lama, lalu isi password baru dan konfirmasi password baru Anda.</li>
  <li>Tunggu beberapa menit agar password baru tersinkron ke Microsoft 365, email, dan akses remote perusahaan.</li>
  <li>Setelah berhasil, login ulang ke Outlook, Microsoft 365, Global Secure Access, dan aplikasi kerja lain.</li>
</ol>
<p style="margin:0 0 18px 0;font-size:14px;line-height:1.8;color:#475569;">Untuk pengguna hybrid, layanan cloud bisa perlu jeda sinkronisasi beberapa menit setelah password berubah. Jika password baru belum terbaca di Microsoft 365 atau Global Secure Access, mohon hubungi helpdesk.</p>
<h2 style="margin:24px 0 12px 0;font-size:18px;line-height:1.4;color:#111827;">Jika password tidak segera di-reset</h2>
<ul style="margin:0 0 20px 22px;padding:0;color:#334155;font-size:15px;line-height:1.9;">
  <li>Akses ke laptop domain, Global Secure Access, email, dan aplikasi internal bisa terganggu atau terputus.</li>
  <li>Akun dapat masuk ke proses enforcement berikutnya sesuai kebijakan keamanan perusahaan.</li>
  <li>Tim IT mungkin perlu melakukan penanganan manual yang memperlambat pemulihan akses Anda.</li>
</ul>
<div style="margin:24px 0 0 0;">
  {_button("Lihat Panduan Ganti Password", SUPPORT_GUIDE_URL, "#4338ca")}
</div>
{_contact_block()}
""".strip(),
            "Email ini dikirim otomatis oleh sistem pengingat password Digiserve.",
        ),
    },
}


def get_template_path() -> Path:
    return DEFAULT_TEMPLATE_PATH


def get_default_templates() -> dict[str, dict[str, str]]:
    return deepcopy(DEFAULT_EMAIL_TEMPLATES)


def load_templates() -> dict[str, dict[str, str]]:
    defaults = get_default_templates()
    path = get_template_path()
    if not path.exists():
        return defaults
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return defaults
    for key, template in payload.items():
        if key not in defaults or not isinstance(template, dict):
            continue
        subject = template.get("subject")
        html = template.get("html")
        if isinstance(subject, str):
            defaults[key]["subject"] = subject
        if isinstance(html, str):
            defaults[key]["html"] = html
    return defaults


def save_templates(templates: dict[str, dict[str, str]]) -> Path:
    path = get_template_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(templates, fh, indent=2, ensure_ascii=False)
    return path


def reset_templates() -> Path:
    defaults = {
        key: {"subject": value["subject"], "html": value["html"]}
        for key, value in get_default_templates().items()
    }
    return save_templates(defaults)


def get_gsa_hint_image_src(preview: bool = False) -> str:
    if preview:
        version = int(GSA_HINT_IMAGE_FILE.stat().st_mtime) if GSA_HINT_IMAGE_FILE.exists() else "0"
        return f"{GSA_HINT_IMAGE_STATIC_PATH}?v={version}"
    return f"cid:{GSA_HINT_IMAGE_CID}"


def get_inline_email_attachments() -> list[dict[str, Any]]:
    if not GSA_HINT_IMAGE_FILE.exists():
        return []
    content = base64.b64encode(GSA_HINT_IMAGE_FILE.read_bytes()).decode("ascii")
    return [
        {
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": GSA_HINT_IMAGE_NAME,
            "contentType": "image/png",
            "contentId": GSA_HINT_IMAGE_CID,
            "isInline": True,
            "contentBytes": content,
        }
    ]


def render_template(template: str, context: dict[str, Any]) -> str:
    rendered = template
    for key, value in context.items():
        rendered = rendered.replace(f"{{{{{key}}}}}", str(value))
        rendered = rendered.replace(f"{{{key}}}", str(value))
    return rendered
