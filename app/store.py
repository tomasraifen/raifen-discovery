"""Persistencia SQLite: participantes de UN proyecto (una instancia = un cliente), cada
uno con sus respuestas al formulario tipado (sin IA -- cada pregunta tiene un tipo fijo y
se guarda tal cual la completa el participante), adjuntos, y las reglas de negocio que
Catequil va curando a partir de lo relevado."""
import json
import secrets
import sqlite3
import uuid
from datetime import datetime, timezone

from . import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS participantes (
    id TEXT PRIMARY KEY,
    token TEXT UNIQUE NOT NULL,
    nombre TEXT NOT NULL,
    cargo TEXT,
    email TEXT,
    creado_en TEXT NOT NULL,
    estado TEXT NOT NULL DEFAULT 'pendiente',
    respuestas_json TEXT NOT NULL DEFAULT '{}',
    correcciones_ya_sabemos TEXT,
    completado_en TEXT,
    formulario_id TEXT NOT NULL DEFAULT 'principal'
);

CREATE TABLE IF NOT EXISTS adjuntos (
    id TEXT PRIMARY KEY,
    participante_id TEXT NOT NULL,
    pregunta_id TEXT,
    tipo TEXT NOT NULL,  -- 'audio' | 'archivo'
    nombre_archivo TEXT NOT NULL,
    ruta TEXT NOT NULL,
    creado_en TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reglas_negocio (
    id TEXT PRIMARY KEY,
    texto TEXT NOT NULL,
    participante_id TEXT,
    estado TEXT NOT NULL DEFAULT 'borrador',  -- borrador | confirmada | entregada_oscar | validada_consume
    creado_en TEXT NOT NULL,
    actualizado_en TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS aprobaciones (
    id TEXT PRIMARY KEY,
    documento_md TEXT NOT NULL,
    enviado_a TEXT NOT NULL,
    enviado_en TEXT NOT NULL,
    resultado TEXT
);
"""

# Estados de participante: pendiente -> en_progreso -> completado (completado = el
# participante hizo click en "Finalizar", no implica que respondio todo -- puede volver
# a editar despues, no queda bloqueado).

_MIGRACIONES = [
    "ALTER TABLE participantes ADD COLUMN formulario_id TEXT NOT NULL DEFAULT 'principal'",
]


def _conn():
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _conn() as c:
        c.executescript(SCHEMA)
        for stmt in _MIGRACIONES:
            try:
                c.execute(stmt)
            except sqlite3.OperationalError:
                pass  # la columna ya existe -- tabla creada antes de este cambio


def sembrar_participantes(participantes: list[dict]) -> list[dict]:
    """Crea en la DB los participantes definidos en config/proyecto.yaml que todavia no
    existen (match por email). Idempotente: correr de nuevo no duplica a quien ya esta."""
    creados = []
    with _conn() as c:
        existentes = {r["email"] for r in c.execute("SELECT email FROM participantes").fetchall() if r["email"]}
        for p in participantes:
            if p.get("email") and p["email"] in existentes:
                continue
            pid = str(uuid.uuid4())
            token = secrets.token_urlsafe(28)
            c.execute(
                "INSERT INTO participantes (id, token, nombre, cargo, email, creado_en, estado, formulario_id) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pendiente', ?)",
                (
                    pid, token, p["nombre"], p.get("cargo", ""), p.get("email", ""),
                    datetime.now(timezone.utc).isoformat(), p.get("formulario_id", "principal"),
                ),
            )
            creados.append({"id": pid, "token": token, **p})
    return creados


def listar_participantes() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, token, nombre, cargo, email, estado, respuestas_json, completado_en, formulario_id "
            "FROM participantes ORDER BY creado_en"
        ).fetchall()
        return [dict(r) for r in rows]


def actualizar_formulario_participante(pid: str, formulario_id: str):
    with _conn() as c:
        c.execute("UPDATE participantes SET formulario_id = ? WHERE id = ?", (formulario_id, pid))


def obtener(pid: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM participantes WHERE id = ?", (pid,)).fetchone()
        return dict(row) if row else None


def obtener_por_token(token: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM participantes WHERE token = ?", (token,)).fetchone()
        return dict(row) if row else None


def get_respuestas(p: dict) -> dict:
    return json.loads(p["respuestas_json"])


def guardar_respuesta(pid: str, pregunta_id: str, valor, fuente: str = "texto"):
    """fuente: 'texto' (escrito a mano) | 'audio' (transcripto, editable despues)."""
    p = obtener(pid)
    respuestas = get_respuestas(p)
    respuestas[pregunta_id] = {"valor": valor, "fuente": fuente}
    nuevo_estado = "en_progreso" if p["estado"] == "pendiente" else p["estado"]
    with _conn() as c:
        c.execute(
            "UPDATE participantes SET respuestas_json = ?, estado = ? WHERE id = ?",
            (json.dumps(respuestas, ensure_ascii=False), nuevo_estado, pid),
        )


def eliminar_respuesta(pid: str, pregunta_id: str):
    """Para que el admin pueda limpiar una respuesta cargada por error (ej. datos de
    prueba) sin tener que resetear todo el participante."""
    p = obtener(pid)
    respuestas = get_respuestas(p)
    respuestas.pop(pregunta_id, None)
    with _conn() as c:
        c.execute("UPDATE participantes SET respuestas_json = ? WHERE id = ?", (json.dumps(respuestas, ensure_ascii=False), pid))


def guardar_correcciones(pid: str, texto: str):
    with _conn() as c:
        c.execute("UPDATE participantes SET correcciones_ya_sabemos = ? WHERE id = ?", (texto, pid))


def marcar_completado(pid: str):
    with _conn() as c:
        c.execute(
            "UPDATE participantes SET estado = 'completado', completado_en = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), pid),
        )


def agregar_adjunto(participante_id: str, tipo: str, nombre_archivo: str, ruta: str, pregunta_id: str | None = None) -> str:
    aid = str(uuid.uuid4())
    with _conn() as c:
        c.execute(
            "INSERT INTO adjuntos (id, participante_id, pregunta_id, tipo, nombre_archivo, ruta, creado_en) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (aid, participante_id, pregunta_id, tipo, nombre_archivo, ruta, datetime.now(timezone.utc).isoformat()),
        )
    return aid


def listar_adjuntos(participante_id: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM adjuntos WHERE participante_id = ? ORDER BY creado_en", (participante_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# ---------- Curaduria: reglas de negocio ----------

def crear_regla(texto: str, participante_id: str | None = None) -> dict:
    rid = str(uuid.uuid4())
    ahora = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        c.execute(
            "INSERT INTO reglas_negocio (id, texto, participante_id, estado, creado_en, actualizado_en) "
            "VALUES (?, ?, ?, 'borrador', ?, ?)",
            (rid, texto, participante_id, ahora, ahora),
        )
    return {"id": rid, "texto": texto, "participante_id": participante_id, "estado": "borrador"}


def actualizar_estado_regla(rid: str, estado: str):
    with _conn() as c:
        c.execute(
            "UPDATE reglas_negocio SET estado = ?, actualizado_en = ? WHERE id = ?",
            (estado, datetime.now(timezone.utc).isoformat(), rid),
        )


def listar_reglas() -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM reglas_negocio ORDER BY creado_en").fetchall()
        return [dict(r) for r in rows]


def registrar_aprobacion(documento_md: str, enviado_a: str, resultado: str | None = None) -> str:
    aid = str(uuid.uuid4())
    with _conn() as c:
        c.execute(
            "INSERT INTO aprobaciones (id, documento_md, enviado_a, enviado_en, resultado) VALUES (?, ?, ?, ?, ?)",
            (aid, documento_md, enviado_a, datetime.now(timezone.utc).isoformat(), resultado),
        )
    return aid


def listar_aprobaciones() -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM aprobaciones ORDER BY enviado_en DESC").fetchall()
        return [dict(r) for r in rows]


def progreso(p: dict, total_preguntas: int) -> dict:
    respondidas = len(get_respuestas(p))
    return {"cubiertos": min(respondidas, total_preguntas), "total": total_preguntas}


def resumen_por_tema(proyecto: dict, participantes: list[dict]) -> list[dict]:
    """Vista de panel de control: por cada tema (de cualquier formulario), cuantas
    preguntas del total ya tienen al menos una respuesta guardada -- responde "que areas
    ya se saben algo y cuales siguen en blanco"."""
    resumen = []
    ids_respondidos = set()
    for p in participantes:
        p_completo = obtener(p["id"])
        ids_respondidos |= set(get_respuestas(p_completo).keys())
    formularios = proyecto.get("formularios", [])
    multi = len(formularios) > 1
    for formulario in formularios:
        temas = formulario.get("temas", [])
        if not temas:
            # El formulario existe pero todavia no tiene areas/preguntas cargadas --
            # se muestra igual (con 0/0) para que no desaparezca de la vista, en vez de
            # simplemente no listarlo.
            titulo = f"{formulario['nombre']} — sin preguntas todavía" if multi else "Sin preguntas todavía"
            resumen.append({"titulo": titulo, "cubiertas": 0, "total": 0})
            continue
        for tema in temas:
            preguntas = tema.get("preguntas", [])
            cubiertas = sum(1 for pr in preguntas if pr["id"] in ids_respondidos)
            titulo = f"{tema['titulo']} — {formulario['nombre']}" if multi else tema["titulo"]
            resumen.append({"titulo": titulo, "cubiertas": cubiertas, "total": len(preguntas)})
    return resumen
