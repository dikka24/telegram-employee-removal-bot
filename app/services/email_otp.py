import secrets
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from ..config import Settings


def generate_otp() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def send_otp_email(settings: Settings, to_email: str, otp: str) -> None:
    if not settings.smtp_host or not settings.smtp_user or not settings.smtp_password:
        raise RuntimeError("SMTP не настроен (SMTP_HOST/SMTP_USER/SMTP_PASSWORD).")

    subject = "Код подтверждения регистрации"

    text_body = (
        "Ваш код подтверждения:\n\n"
        f"{otp}\n\n"
        "Код действует ограниченное время.\n"
        "Если это были не вы, просто проигнорируйте письмо."
    )

    html_body = f"""
    <div style="background:#f4f7fb;padding:28px 0;font-family:Arial,sans-serif;">
      <div style="max-width:560px;margin:0 auto;background:#ffffff;border-radius:16px;padding:28px;border:1px solid #e8edf3;">
        <p style="margin:0 0 12px 0;color:#3a4759;font-size:14px;">Подтверждение регистрации</p>
        <h2 style="margin:0 0 12px 0;color:#121826;font-size:24px;">Ваш код:</h2>
        <div style="display:inline-block;background:#121826;color:#ffffff;padding:12px 18px;border-radius:10px;font-size:32px;font-weight:700;letter-spacing:6px;">
          {otp}
        </div>
        <p style="margin:18px 0 0 0;color:#4b5563;font-size:14px;line-height:1.6;">
          Код действует ограниченное время.<br/>
          Если это были не вы, просто проигнорируйте это письмо.
        </p>
      </div>
    </div>
    """

    from_email = settings.smtp_from or settings.smtp_user

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email

    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    # Port 465 expects implicit SSL (SMTP_SSL), while 587 typically uses STARTTLS.
    if int(settings.smtp_port) == 465:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20) as server:
            server.ehlo()
            server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(from_email, [to_email], msg.as_string())
        return

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
        server.ehlo()
        if settings.smtp_use_tls:
            server.starttls()
            server.ehlo()
        server.login(settings.smtp_user, settings.smtp_password)
        server.sendmail(from_email, [to_email], msg.as_string())
