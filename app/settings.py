"""Carga de configuracion: .env + config/proyecto.yaml (un proyecto por instancia)."""
import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
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

CONFIG_DIR = ROOT / "config"


def load_proyecto() -> dict:
    path = CONFIG_DIR / "proyecto.yaml"
    if not path.exists():
        raise RuntimeError(
            "Falta config/proyecto.yaml -- copiar config/proyecto.example.yaml y completar con los datos del cliente."
        )
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)
