"""Aviso por correo cuando un participante marca su formulario como finalizado.
Best-effort: si no hay SMTP configurado (o falla el envio), no rompe el flujo -- el
participante ya vio la pantalla de gracias igual, esto es solo la notificacion interna."""
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from . import settings


def notificar_completado(nombre_participante: str, cliente: str, link_admin: str) -> tuple[bool, str | None]:
    """Devuelve (enviado, error). error es None si salio bien o si se salteo por falta
    de config (en ese caso enviado=False pero sin excepcion)."""
    if not settings.MAIL_SMTP_HOST:
        return False, "SMTP no configurado -- se salteo el aviso"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[Discovery] {nombre_participante} completó su formulario — {cliente}"
    msg["From"] = settings.MAIL_FROM
    msg["To"] = settings.ADMIN_REVIEW_EMAIL

    texto = f"{nombre_participante} marcó su formulario como finalizado en el proyecto {cliente}.\n\nRevisar: {link_admin}"
    html = f"""\
<div style="font-family:Arial,Helvetica,sans-serif;max-width:520px;margin:0 auto;padding:20px;">
  <p><b>{nombre_participante}</b> marcó su formulario como finalizado en el proyecto <b>{cliente}</b>.</p>
  <p><a href="{link_admin}" style="background:#348AB7;color:#fff;padding:10px 18px;border-radius:8px;
    text-decoration:none;font-weight:600;">Ver respuestas →</a></p>
</div>"""
    msg.attach(MIMEText(texto, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))

    try:
        with smtplib.SMTP(settings.MAIL_SMTP_HOST, settings.MAIL_SMTP_PORT, timeout=20) as srv:
            srv.starttls(context=ssl.create_default_context())
            srv.login(settings.MAIL_SMTP_USER, settings.MAIL_SMTP_PASS)
            srv.send_message(msg)
        return True, None
    except Exception as e:
        return False, str(e)
