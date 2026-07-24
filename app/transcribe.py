"""Cliente HTTP del servicio de transcripcion propio de Raifen (transcribe.raifen.ai,
faster-whisper medium) -- mismo servicio que ya usa el skill transcribe-remote, pero
llamado directo desde el backend (el audio llega por upload del navegador, no de disco
local). Best-effort: si falla, devuelve None y el flujo sigue -- el participante puede
reescribir a texto."""
import requests

from . import settings

_TIMEOUT = 1800  # medium en CPU es ~1x tiempo real -- audios largos tardan


def transcribir(audio_bytes: bytes, nombre_archivo: str, idioma: str | None = "es") -> str | None:
    if not settings.TRANSCRIBE_API_KEY:
        return None
    headers = {"Authorization": f"Bearer {settings.TRANSCRIBE_API_KEY}"}
    files = {"file": (nombre_archivo, audio_bytes, "application/octet-stream")}
    data = {"vad": "true"}
    if idioma:
        data["language"] = idioma
    try:
        r = requests.post(
            f"{settings.TRANSCRIBE_URL}/transcribe", headers=headers, files=files, data=data, timeout=_TIMEOUT
        )
        r.raise_for_status()
        return (r.json().get("text") or "").strip() or None
    except Exception:
        return None
