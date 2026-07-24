"""Persistencia SQLite: participantes de UN proyecto (una instancia = un cliente),
cada uno con su propia conversacion, adjuntos, y las reglas de negocio que Catequil va
curando a partir de lo relevado."""
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
    conversacion_json TEXT NOT NULL DEFAULT '[]',
    turnos INTEGER NOT NULL DEFAULT 0,
    completado_en TEXT
);

CREATE TABLE IF NOT EXISTS adjuntos (
    id TEXT PRIMARY KEY,
    participante_id TEXT NOT NULL,
    tipo TEXT NOT NULL,  -- 'audio' | 'archivo'
    nombre_archivo TEXT NOT NULL,
    ruta TEXT NOT NULL,
    turno_indice INTEGER,
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

# Estados de participante: pendiente -> en_progreso -> completado
MAX_TURNOS = 40  # 20 idas y vueltas -- techo por si el agente no cierra solo


def _conn():
    conn = sqlite3.connect(settings.DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _conn() as c:
        c.executescript(SCHEMA)


def sembrar_participantes(participantes: list[dict]) -> list[dict]:
    """Crea en la DB los participantes definidos en config/proyecto.yaml que todavia no
    existen (match por email). Devuelve la lista completa con sus tokens -- se usa para
    armar los links que se comparten con cada stakeholder. Idempotente: correr de nuevo
    no duplica a quien ya esta."""
    creados = []
    with _conn() as c:
        existentes = {r["email"] for r in c.execute("SELECT email FROM participantes").fetchall() if r["email"]}
        for p in participantes:
            if p.get("email") and p["email"] in existentes:
                continue
            pid = str(uuid.uuid4())
            token = secrets.token_urlsafe(28)
            c.execute(
                "INSERT INTO participantes (id, token, nombre, cargo, email, creado_en, estado) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pendiente')",
                (pid, token, p["nombre"], p.get("cargo", ""), p.get("email", ""), datetime.now(timezone.utc).isoformat()),
            )
            creados.append({"id": pid, "token": token, **p})
    return creados


def listar_participantes() -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT id, token, nombre, cargo, email, estado, turnos, completado_en FROM participantes ORDER BY creado_en"
        ).fetchall()
        return [dict(r) for r in rows]


def obtener(pid: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM participantes WHERE id = ?", (pid,)).fetchone()
        return dict(row) if row else None


def obtener_por_token(token: str) -> dict | None:
    with _conn() as c:
        row = c.execute("SELECT * FROM participantes WHERE token = ?", (token,)).fetchone()
        return dict(row) if row else None


def get_conversacion(p: dict) -> list[dict]:
    return json.loads(p["conversacion_json"])


def marcar_en_progreso(pid: str):
    with _conn() as c:
        c.execute("UPDATE participantes SET estado = 'en_progreso' WHERE id = ? AND estado = 'pendiente'", (pid,))


def append_turno(pid: str, conversacion: list[dict]):
    with _conn() as c:
        c.execute(
            "UPDATE participantes SET conversacion_json = ?, turnos = ? WHERE id = ?",
            (json.dumps(conversacion, ensure_ascii=False), len(conversacion), pid),
        )


def marcar_completado(pid: str, conversacion: list[dict]):
    with _conn() as c:
        c.execute(
            "UPDATE participantes SET conversacion_json = ?, turnos = ?, estado = 'completado', completado_en = ? WHERE id = ?",
            (json.dumps(conversacion, ensure_ascii=False), len(conversacion), datetime.now(timezone.utc).isoformat(), pid),
        )


def agregar_adjunto(participante_id: str, tipo: str, nombre_archivo: str, ruta: str, turno_indice: int | None = None) -> str:
    aid = str(uuid.uuid4())
    with _conn() as c:
        c.execute(
            "INSERT INTO adjuntos (id, participante_id, tipo, nombre_archivo, ruta, turno_indice, creado_en) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (aid, participante_id, tipo, nombre_archivo, ruta, turno_indice, datetime.now(timezone.utc).isoformat()),
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


def progreso(p: dict, total_temas: int) -> dict:
    """Aproximacion simple: turnos de cliente contados / total de temas del proyecto,
    tope en total. La cobertura real (que temas se cubrieron) la decide interview.py
    turno a turno, igual que en Smart Blueprint -- esto es solo para la barra visual
    cuando todavia no corrio la clasificacion de ese turno."""
    turnos_cliente = len([t for t in json.loads(p["conversacion_json"]) if t["rol"] == "participante"])
    return {"cubiertos": min(turnos_cliente, total_temas), "total": total_temas}
