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
# manda siempre a este correo para que un humano de Raifen lo revise antes de reenviarlo
# a mano si corresponde.
ADMIN_REVIEW_EMAIL = os.getenv("ADMIN_REVIEW_EMAIL", "tomas@raifen.ai")

# Aviso por correo cuando un participante finaliza su formulario -- opcional: si
# MAIL_SMTP_HOST no esta seteado, el aviso se saltea sin romper nada (ver mailer.py).
MAIL_SMTP_HOST = os.getenv("MAIL_SMTP_HOST", "")
MAIL_SMTP_PORT = int(os.getenv("MAIL_SMTP_PORT", "587") or "587")
MAIL_SMTP_USER = os.getenv("MAIL_SMTP_USER", "")
MAIL_SMTP_PASS = os.getenv("MAIL_SMTP_PASS", "")
MAIL_FROM = os.getenv("MAIL_FROM", "Raifen Discovery <no-reply@raifen.ai>")

CONFIG_DIR = ROOT / "config"

DEFAULT_FORMULARIO_ID = "principal"

# opcion_unica/opcion_multiple requieren "opciones". numero/archivo no piden opciones ni
# el boton de grabar por voz (eso solo aplica a texto_libre).
TIPOS_PREGUNTA = ("texto_libre", "opcion_unica", "opcion_multiple", "booleano", "numero", "archivo")


def _normalizar_formularios(proyecto: dict) -> dict:
    """Migra el schema viejo (temas sueltos a nivel raiz -- un solo formulario
    implicito) al nuevo (proyecto["formularios"] = lista de {id, nombre, temas}).
    Mutacion in-place, idempotente, no persiste sola -- el llamador decide cuando
    guardar. Necesario para no romper instancias ya desplegadas con el schema viejo
    (ver config/proyecto.yaml de discovery.raifen.ai, pegado a mano antes de este
    cambio)."""
    if "formularios" not in proyecto:
        temas_legacy = proyecto.pop("temas", [])
        proyecto["formularios"] = [
            {"id": DEFAULT_FORMULARIO_ID, "nombre": "Formulario principal", "temas": temas_legacy}
        ]
    for p in proyecto.get("participantes", []):
        # Schema viejo: un solo "formulario_id" por persona. Nuevo: "formulario_ids"
        # (lista) -- una persona puede tener 0, 1 o varios formularios asignados por
        # default (ver knowledge/proyectos/raifen_discovery_plataforma.md).
        if "formulario_ids" not in p:
            antiguo = p.pop("formulario_id", None)
            p["formulario_ids"] = [antiguo] if antiguo else []
    return proyecto


def load_proyecto() -> dict:
    path = CONFIG_DIR / "proyecto.yaml"
    if not path.exists():
        raise RuntimeError(
            "Falta config/proyecto.yaml -- copiar config/proyecto.example.yaml y completar con los datos del cliente."
        )
    with open(path, encoding="utf-8") as f:
        proyecto = yaml.safe_load(f)
    return _normalizar_formularios(proyecto)


def formulario_por_id(proyecto: dict, formulario_id: str) -> dict | None:
    for f in proyecto.get("formularios", []):
        if f["id"] == formulario_id:
            return f
    return None


def preguntas_planas(proyecto: dict, formulario_id: str | None = None) -> list[dict]:
    """Todas las preguntas -- de un formulario puntual, o de todos si no se filtra.
    Usado para calcular progreso total y para validar ids al guardar una respuesta."""
    planas = []
    formularios = proyecto.get("formularios", [])
    if formulario_id:
        formularios = [f for f in formularios if f["id"] == formulario_id]
    for formulario in formularios:
        for tema in formulario.get("temas", []):
            for p in tema.get("preguntas", []):
                planas.append({**p, "tema_id": tema["id"], "tema_titulo": tema["titulo"], "formulario_id": formulario["id"]})
    return planas


def _slug(texto: str) -> str:
    import re
    import unicodedata
    ascii_txt = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "_", ascii_txt.lower()).strip("_") or "item"


def _dedup_id(base_id: str, ids_existentes: set[str]) -> str:
    if base_id not in ids_existentes:
        return base_id
    sufijo = 2
    candidato = f"{base_id}_{sufijo}"
    while candidato in ids_existentes:
        sufijo += 1
        candidato = f"{base_id}_{sufijo}"
    return candidato


def _todos_los_ids_temas(proyecto: dict) -> set[str]:
    return {t["id"] for f in proyecto.get("formularios", []) for t in f.get("temas", [])}


def _todos_los_ids_preguntas(proyecto: dict) -> set[str]:
    return {
        p["id"] for f in proyecto.get("formularios", []) for t in f.get("temas", []) for p in t.get("preguntas", [])
    }


def _construir_pregunta(pd: dict, ids_preguntas: set[str]) -> dict:
    """pd: {"texto", "tipo", "opciones"?, "ayuda"?}. ids_preguntas debe venir sembrado
    con TODOS los ids de pregunta del proyecto (no solo del tema/formulario actual) --
    los ids de pregunta son unicos a nivel de proyecto entero, se buscan globalmente
    (ver settings._pregunta_por_id / api._pregunta_por_id) asi que dos preguntas en
    formularios distintos no pueden colisionar. Muta ids_preguntas."""
    pregunta_id = _dedup_id(_slug(pd["texto"]), ids_preguntas)
    ids_preguntas.add(pregunta_id)
    nueva = {"id": pregunta_id, "texto": pd["texto"], "tipo": pd["tipo"]}
    if pd.get("ayuda"):
        nueva["ayuda"] = pd["ayuda"]
    if pd["tipo"] in ("opcion_unica", "opcion_multiple") and pd.get("opciones"):
        nueva["opciones"] = pd["opciones"]
    return nueva


def _construir_tema(tema_data: dict, ids_temas: set[str], ids_preguntas: set[str]) -> dict:
    """tema_data: {"titulo", "preguntas": [pd, ...]}. Ambos sets deben venir sembrados a
    nivel de proyecto entero -- ver _construir_pregunta. Muta los dos sets."""
    tema_id = _dedup_id(_slug(tema_data["titulo"]), ids_temas)
    ids_temas.add(tema_id)
    preguntas = [_construir_pregunta(pd, ids_preguntas) for pd in tema_data.get("preguntas", [])]
    return {"id": tema_id, "titulo": tema_data["titulo"], "preguntas": preguntas}


def guardar_proyecto(proyecto: dict):
    """Escribe config/proyecto.yaml de vuelta -- lo usa el editor de formularios/temas/
    preguntas/participantes del panel admin. El archivo es un bind mount con permiso de
    escritura (ver docker-compose.yml), así que esto persiste tanto local como en
    Coolify."""
    path = CONFIG_DIR / "proyecto.yaml"
    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(proyecto, f, allow_unicode=True, sort_keys=False, width=1000)


def agregar_formulario(proyecto: dict, nombre: str) -> dict:
    formulario_id = _slug(nombre)
    ids_existentes = {f["id"] for f in proyecto.setdefault("formularios", [])}
    sufijo = 2
    formulario_id_final = formulario_id
    while formulario_id_final in ids_existentes:
        formulario_id_final = f"{formulario_id}_{sufijo}"
        sufijo += 1
    proyecto["formularios"].append({"id": formulario_id_final, "nombre": nombre, "temas": []})
    guardar_proyecto(proyecto)
    return proyecto


def agregar_tema(proyecto: dict, formulario_id: str, titulo: str) -> dict:
    formulario = formulario_por_id(proyecto, formulario_id)
    if not formulario:
        raise ValueError(f"formulario '{formulario_id}' no existe")
    formulario.setdefault("temas", [])
    formulario["temas"].append(_construir_tema(
        {"titulo": titulo, "preguntas": []}, _todos_los_ids_temas(proyecto), _todos_los_ids_preguntas(proyecto)
    ))
    guardar_proyecto(proyecto)
    return proyecto


def agregar_pregunta(
    proyecto: dict, formulario_id: str, tema_id: str, texto: str, tipo: str,
    opciones: list[str] | None = None, ayuda: str | None = None,
) -> dict:
    formulario = formulario_por_id(proyecto, formulario_id)
    if not formulario:
        raise ValueError(f"formulario '{formulario_id}' no existe")
    for tema in formulario.get("temas", []):
        if tema["id"] == tema_id:
            tema.setdefault("preguntas", [])
            tema["preguntas"].append(_construir_pregunta(
                {"texto": texto, "tipo": tipo, "opciones": opciones, "ayuda": ayuda}, _todos_los_ids_preguntas(proyecto)
            ))
            break
    else:
        raise ValueError(f"tema '{tema_id}' no existe en el formulario '{formulario_id}'")
    guardar_proyecto(proyecto)
    return proyecto


# ---------- Alta en bloque -- para que Catequil suba contenido ya acordado con Tom en
# una sola llamada (una request, no una por pregunta) ----------

def crear_formulario_completo(proyecto: dict, nombre: str, temas_data: list[dict]) -> dict:
    """temas_data: [{"titulo": ..., "preguntas": [{"texto","tipo","opciones"?,"ayuda"?}, ...]}, ...]"""
    formulario_id = _dedup_id(_slug(nombre), {f["id"] for f in proyecto.setdefault("formularios", [])})
    ids_temas = _todos_los_ids_temas(proyecto)
    ids_preguntas = _todos_los_ids_preguntas(proyecto)
    temas = [_construir_tema(td, ids_temas, ids_preguntas) for td in temas_data]
    proyecto["formularios"].append({"id": formulario_id, "nombre": nombre, "temas": temas})
    guardar_proyecto(proyecto)
    return proyecto


def agregar_temas_a_formulario(proyecto: dict, formulario_id: str, temas_data: list[dict]) -> dict:
    """Agrega uno o mas temas (con sus preguntas) a un formulario YA EXISTENTE, en una
    sola llamada -- para cuando Catequil decide sumar preguntas nuevas a un formulario
    en curso en vez de crear uno aparte."""
    formulario = formulario_por_id(proyecto, formulario_id)
    if not formulario:
        raise ValueError(f"formulario '{formulario_id}' no existe")
    formulario.setdefault("temas", [])
    ids_temas = _todos_los_ids_temas(proyecto)
    ids_preguntas = _todos_los_ids_preguntas(proyecto)
    for tema_data in temas_data:
        formulario["temas"].append(_construir_tema(tema_data, ids_temas, ids_preguntas))
    guardar_proyecto(proyecto)
    return proyecto


def agregar_preguntas_a_tema(proyecto: dict, formulario_id: str, tema_id: str, preguntas_data: list[dict]) -> dict:
    """Agrega varias preguntas de una vez a un tema ya existente."""
    formulario = formulario_por_id(proyecto, formulario_id)
    if not formulario:
        raise ValueError(f"formulario '{formulario_id}' no existe")
    for tema in formulario.get("temas", []):
        if tema["id"] == tema_id:
            tema.setdefault("preguntas", [])
            ids_preguntas = _todos_los_ids_preguntas(proyecto)
            for pd in preguntas_data:
                tema["preguntas"].append(_construir_pregunta(pd, ids_preguntas))
            break
    else:
        raise ValueError(f"tema '{tema_id}' no existe en el formulario '{formulario_id}'")
    guardar_proyecto(proyecto)
    return proyecto


def eliminar_pregunta(proyecto: dict, formulario_id: str, tema_id: str, pregunta_id: str) -> dict:
    formulario = formulario_por_id(proyecto, formulario_id)
    if formulario:
        for tema in formulario.get("temas", []):
            if tema["id"] == tema_id:
                tema["preguntas"] = [p for p in tema.get("preguntas", []) if p["id"] != pregunta_id]
                break
    guardar_proyecto(proyecto)
    return proyecto


def agregar_participante_a_yaml(
    proyecto: dict, nombre: str, cargo: str, email: str, formulario_ids: list[str] | None = None
) -> dict:
    proyecto.setdefault("participantes", []).append(
        {"nombre": nombre, "cargo": cargo, "email": email, "formulario_ids": formulario_ids or []}
    )
    guardar_proyecto(proyecto)
    return proyecto


def actualizar_lo_que_sabemos(proyecto: dict, texto: str) -> dict:
    proyecto["lo_que_ya_sabemos"] = texto
    guardar_proyecto(proyecto)
    return proyecto


def actualizar_datos_proyecto(proyecto: dict, cliente: str, nombre_proyecto: str, vertical: str, logo_url: str = "") -> dict:
    proyecto["cliente"] = cliente
    proyecto["proyecto"] = nombre_proyecto
    proyecto["vertical"] = vertical
    proyecto["logo_url"] = logo_url
    guardar_proyecto(proyecto)
    return proyecto
