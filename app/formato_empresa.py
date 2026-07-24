"""Envio del documento formal de reglas de negocio para aprobacion del cliente -- via el
webhook n8n "Utils - Markdown Raifen" (mismo flujo que /herramientas/formato-empresa en
Raifen_Claude_System). El portal es transito; esto es lo que de verdad se le manda al
cliente para que apruebe."""
import requests

from . import settings


def enviar_para_aprobacion(
    markdown_text: str, correo_referencia: str, nombre_documento: str, nombre_proyecto: str, nombre_cliente: str = ""
) -> tuple[bool, str | None]:
    """Devuelve (ok, error)."""
    payload = {
        "markdown_text": markdown_text,
        "correo_referencia": correo_referencia,
        "nombre_documento": nombre_documento,
        "nombre_proyecto": nombre_proyecto,
    }
    if nombre_cliente:
        payload["nombre_cliente"] = nombre_cliente
    try:
        r = requests.post(settings.N8N_MARKDOWN_WEBHOOK, json=payload, timeout=60)
        r.raise_for_status()
        return True, None
    except Exception as e:
        return False, str(e)
