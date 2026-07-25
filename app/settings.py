"""Carga de configuracion: .env + config/proyecto.yaml (un proyecto por instancia)."""
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DB_PATH = os.getenv("DB_PATH") or str(ROOT / "data" / "discovery.db")
if not os.path.isabs(DB_PATH):
    DB_PATH = str(ROOT / DB_PATH)

UPLOADS_DIR = os.getenv("UPLOADS_DIR") or str(ROOT / "data" / "adjuntos")
if not os.path.isabs(UPLOADS_DIR):
    UPLOADS_DIR = str(ROOT / UPLOADS_DIR)
Path(UPLOADS_DIR).mkdir(parents=True, exist_ok=True)

BASIC_AUTH_USER = os.getenv("BASIC_AUTH_USER", "admin")
BASIC_AUTH_PASS = os.getenv("BASIC_AUTH_PASS", "Raifen2026!")

# URL publica de esta instancia (una por cliente), para armar los links /r/<token>.
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8020").rstrip("/")

BRAND_NAME = os.getenv("BRAND_NAME", "Raifen")

# Transcripcion de audio -- mismo servicio que ya usa Raifen (transcribe-remote skill),
# pero llamado directo por HTTP desde el backend en vez de correr un script local.
TRANSCRIBE_URL = os.getenv("TRANSCRIBE_URL", "https://transcribe.raifen.ai").rstrip("/")
TRANSCRIBE_API_KEY = os.getenv("TRANSCRIBE_API_KEY", "")

# Documento formal de aprobacion -- webhook n8n "Utils - Markdown Raifen"
# (POST /webhook/markdown-raifen, ver knowledge/sistemas/infra.md en Raifen_Claude_System).
N8N_MARKDOWN_WEBHOOK = os.getenv("N8N_MARKDOWN_WEBHOOK", "https://n8n.raifen.ai/webhook/markdown-raifen")

# Regla dura: el documento de reglas de negocio NUNCA se manda directo al cliente. Se
# manda siempre a este correo para que un humano de Raifen lo revise antes de reenviarlo.
# proyecto["correo_aprobacion"] queda solo como referencia informativa dentro del
# documento (a quien se lo reenvia Raifen despues de revisarlo), nunca como destinatario
# real del webhook.
ADMIN_REVIEW_EMAIL = os.getenv("ADMIN_REVIEW_EMAIL", "tomas@raifen.ai")

CONFIG_DIR = ROOT / "config"


def load_proyecto() -> dict:
    path = CONFIG_DIR / "proyecto.yaml"
    if not path.exists():
        raise RuntimeError(
            "Falta config/proyecto.yaml -- copiar config/proyecto.example.yaml y completar con los datos del cliente."
        )
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def preguntas_planas(proyecto: dict) -> list[dict]:
    """Todas las preguntas de todos los temas, en una sola lista -- para calcular
    progreso total y para validar ids al guardar una respuesta."""
    planas = []
    for tema in proyecto.get("temas", []):
        for p in tema.get("preguntas", []):
            planas.append({**p, "tema_id": tema["id"], "tema_titulo": tema["titulo"]})
    return planas


def _slug(texto: str) -> str:
    import re
    import unicodedata
    ascii_txt = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "_", ascii_txt.lower()).strip("_") or "item"


def guardar_proyecto(proyecto: dict):
    """Escribe config/proyecto.yaml de vuelta -- lo usa el editor de preguntas/temas/
    participantes del panel admin. El archivo es un bind mount con permiso de escritura
    (ver docker-compose.yml), así que esto persiste tanto local como en Coolify."""
    path = CONFIG_DIR / "proyecto.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(proyecto, f, allow_unicode=True, sort_keys=False, width=1000)


def agregar_tema(proyecto: dict, titulo: str) -> dict:
    tema_id = _slug(titulo)
    ids_existentes = {t["id"] for t in proyecto.setdefault("temas", [])}
    sufijo = 2
    tema_id_final = tema_id
    while tema_id_final in ids_existentes:
        tema_id_final = f"{tema_id}_{sufijo}"
        sufijo += 1
    proyecto["temas"].append({"id": tema_id_final, "titulo": titulo, "preguntas": []})
    guardar_proyecto(proyecto)
    return proyecto


def agregar_pregunta(
    proyecto: dict, tema_id: str, texto: str, tipo: str,
    opciones: list[str] | None = None, ayuda: str | None = None,
) -> dict:
    for tema in proyecto.get("temas", []):
        if tema["id"] == tema_id:
            pregunta_id = _slug(texto)
            ids_existentes = {p["id"] for p in tema.setdefault("preguntas", [])}
            sufijo = 2
            pregunta_id_final = pregunta_id
            while pregunta_id_final in ids_existentes:
                pregunta_id_final = f"{pregunta_id}_{sufijo}"
                sufijo += 1
            nueva = {"id": pregunta_id_final, "texto": texto, "tipo": tipo}
            if ayuda:
                nueva["ayuda"] = ayuda
            if tipo in ("opcion_unica", "opcion_multiple") and opciones:
                nueva["opciones"] = opciones
            tema["preguntas"].append(nueva)
            break
    else:
        raise ValueError(f"tema '{tema_id}' no existe")
    guardar_proyecto(proyecto)
    return proyecto


def eliminar_pregunta(proyecto: dict, tema_id: str, pregunta_id: str) -> dict:
    for tema in proyecto.get("temas", []):
        if tema["id"] == tema_id:
            tema["preguntas"] = [p for p in tema.get("preguntas", []) if p["id"] != pregunta_id]
            break
    guardar_proyecto(proyecto)
    return proyecto


def agregar_participante_a_yaml(proyecto: dict, nombre: str, cargo: str, email: str) -> dict:
    proyecto.setdefault("participantes", []).append({"nombre": nombre, "cargo": cargo, "email": email})
    guardar_proyecto(proyecto)
    return proyecto


def actualizar_lo_que_sabemos(proyecto: dict, texto: str) -> dict:
    proyecto["lo_que_ya_sabemos"] = texto
    guardar_proyecto(proyecto)
    return proyecto
