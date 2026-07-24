"""Motor conversacional del discovery: en cada turno decide si preguntar algo mas
(adaptado a lo que ya se sabe, no un formulario fijo) o si ya cubrio los temas del
proyecto y cierra. Mismo patron que raifen-smart-blueprint/app/interview.py, adaptado a
tono de discovery de negocio (no venta) y con el bloque "lo que ya sabemos" del cliente."""
import json
import re

import requests

from . import settings

_INVITA_RE = re.compile(
    r"(algo\s+m[aá]s|comentario\s+adicional|agregar\s+algo|hay\s+algo\s+(m[aá]s|que)|"
    r"quier[ae]s?\s+(agregar|a[ñn]adir|sumar|mencionar|comentar|contar|decir|aportar)|"
    r"(te\s+gustar[ií]a|desea[s]?)\s+(agregar|a[ñn]adir|comentar|sumar))",
    re.IGNORECASE,
)

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_ENDPOINT_STREAM = "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent"

_SCHEMA_PASO = {
    "type": "OBJECT",
    "properties": {
        "accion": {"type": "STRING", "enum": ["preguntar", "cerrar"]},
        "mensaje": {
            "type": "STRING",
            "description": "texto que se le muestra al usuario: la siguiente pregunta (una sola, corta), o el agradecimiento de cierre",
        },
        "temas_cubiertos": {
            "type": "ARRAY",
            "items": {"type": "STRING"},
            "description": "ids (de la lista de temas del prompt) que ya quedaron cubiertos -- respondidos con algo concreto, o explícitamente diferidos ('no sé', 'te confirmo después'). NO marques un tema como cubierto si todavía no se lo preguntaste.",
        },
    },
    "required": ["accion", "mensaje", "temas_cubiertos"],
}

_SCHEMA_CLASIFICAR = {
    "type": "OBJECT",
    "properties": {
        "accion": {"type": "STRING", "enum": ["preguntar", "cerrar"]},
        "temas_cubiertos": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["accion", "temas_cubiertos"],
}

MIN_TEMAS_PARA_CERRAR_RATIO = 0.7


def _ids_temas(proyecto: dict) -> list[str]:
    return [p["id"] for p in proyecto.get("preguntas", [])]


def _system_prompt(proyecto: dict, participante: dict) -> str:
    temas = "\n".join(f"- [{p['id']}] {p['texto']}" for p in proyecto.get("preguntas", []))
    lo_que_ya_sabemos = (proyecto.get("lo_que_ya_sabemos") or "").strip()

    bloque_ya_sabemos = ""
    if lo_que_ya_sabemos:
        bloque_ya_sabemos = f"""

Esto es lo que Raifen ya sabe del negocio de "{proyecto['cliente']}", de contexto previo
(reuniones comerciales, investigación previa). Se lo compartiste al participante al
arrancar la charla de forma transparente -- si en la conversación el participante corrige
o matiza algo de este contexto, tomalo como dato valioso y priorizalo sobre lo que sigue:
\"\"\"
{lo_que_ya_sabemos}
\"\"\""""

    return f"""Eres un consultor senior de Raifen (consultora de datos e IA) haciendo el
discovery de negocio de un proyecto de datalake con "{proyecto['cliente']}". Estás
hablando con {participante['nombre']} ({participante.get('cargo') or 'sin cargo especificado'}).
Esto NO es una charla de venta -- el proyecto ya está en marcha, tu objetivo es entender
el negocio con suficiente profundidad y rigor como para que el equipo de Raifen pueda
traducir lo que te cuenten en reglas de negocio explícitas y verificables (ej. "qué
cuenta como una venta válida", no solo "quiero ver ventas").

Importante: dirígete siempre a la persona de "usted". Español neutro en toda la
conversación -- sin modismos regionales (nada de "vos", "che" ni expresiones similares).

Estilo: cercano pero profesional, como un consultor senior que trabaja codo a codo con
operadores reales. UNA pregunta a la vez, corta y clara. Si una respuesta es vaga, o
menciona una excepción/caso borde, profundiza ahí antes de cambiar de tema -- las
excepciones son exactamente lo que después rompe un modelo de datos mal relevado.

Temas que debes cubrir, cada uno con su id (no uses este texto literal ni este orden --
adáptalos con tus propias palabras):
{temas}
{bloque_ya_sabemos}

En cada turno, devuelve en "temas_cubiertos" los ids que ya quedaron cubiertos.

**Regla de cierre, estricta:** no cierres hasta cubrir la gran mayoría de los temas
(idealmente todos, como mucho 1 sin tocar).

**Cierre en DOS pasos, en este orden exacto:**
1. Tu ANTEÚLTIMO mensaje es una única pregunta que invita a agregar algo más ("¿hay algo
   más que quiera mencionar o algún punto que sienta que no llegamos a tocar?"). Es una
   PREGUNTA (accion=preguntar), espera respuesta.
2. Recién en tu ÚLTIMO mensaje, después de esa respuesta, cierras en firme
   (accion=cerrar): agradece, resume en 1-2 frases lo que entendiste, avisa que el
   equipo de Raifen sigue trabajando con esto. No vuelvas a preguntar nada."""


def _conversacion_a_contents(conversacion: list[dict]) -> list[dict]:
    return [
        {"role": "model" if t["rol"] == "agente" else "user", "parts": [{"text": t["texto"]}]}
        for t in conversacion
    ]


def _llamar_gemini(contents: list[dict], system_instruction: str | None = None, schema: dict | None = None) -> str:
    if not settings.GEMINI_API_KEY:
        raise RuntimeError("Falta GEMINI_API_KEY en .env")
    gen_config = {"temperature": 0.6}
    if schema:
        gen_config["responseMimeType"] = "application/json"
        gen_config["responseSchema"] = schema
    body = {"contents": contents, "generationConfig": gen_config}
    if system_instruction:
        body["systemInstruction"] = {"parts": [{"text": system_instruction}]}
    url = _ENDPOINT.format(model=settings.GEMINI_MODEL)
    r = requests.post(
        url, headers={"x-goog-api-key": settings.GEMINI_API_KEY, "Content-Type": "application/json"},
        json=body, timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _llamar_gemini_stream(contents: list[dict], system_instruction: str | None = None):
    if not settings.GEMINI_API_KEY:
        raise RuntimeError("Falta GEMINI_API_KEY en .env")
    body = {"contents": contents, "generationConfig": {"temperature": 0.6}}
    if system_instruction:
        body["systemInstruction"] = {"parts": [{"text": system_instruction}]}
    url = _ENDPOINT_STREAM.format(model=settings.GEMINI_MODEL) + "?alt=sse"
    with requests.post(
        url, headers={"x-goog-api-key": settings.GEMINI_API_KEY, "Content-Type": "application/json"},
        json=body, timeout=60, stream=True,
    ) as r:
        r.raise_for_status()
        r.encoding = "utf-8"
        for line in r.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue
            chunk = line[len("data: "):]
            try:
                data = json.loads(chunk)
                texto = data["candidates"][0]["content"]["parts"][0]["text"]
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
            if texto:
                yield texto


def clasificar_paso(proyecto: dict, participante: dict, conversacion: list[dict]) -> dict:
    system = _system_prompt(proyecto, participante)
    contents = _conversacion_a_contents(conversacion)
    contents.append({"role": "user", "parts": [{"text": (
        "Mirando tu último mensaje (el más reciente en la conversación de arriba), "
        "clasificá: ¿fue una pregunta (accion=preguntar) o un cierre/agradecimiento final "
        "(accion=cerrar)? Y qué temas de la lista quedaron cubiertos hasta ahora."
    )}]})
    raw = _llamar_gemini(contents, system_instruction=system, schema=_SCHEMA_CLASIFICAR)
    return json.loads(raw)


def iniciar(proyecto: dict, participante: dict) -> str:
    system = _system_prompt(proyecto, participante)
    contents = [{"role": "user", "parts": [{"text": "Inicia la conversación: saludo breve + tu primera pregunta."}]}]
    raw = _llamar_gemini(contents, system_instruction=system, schema=_SCHEMA_PASO)
    return json.loads(raw)["mensaje"]


def iniciar_stream(proyecto: dict, participante: dict):
    system = _system_prompt(proyecto, participante)
    contents = [{"role": "user", "parts": [{"text": "Inicia la conversación: saludo breve + tu primera pregunta. Texto plano, sin JSON."}]}]
    yield from _llamar_gemini_stream(contents, system_instruction=system)


def siguiente_paso(proyecto: dict, participante: dict, conversacion: list[dict]) -> dict:
    system = _system_prompt(proyecto, participante)
    ids = _ids_temas(proyecto)
    minimo = max(1, round(len(ids) * MIN_TEMAS_PARA_CERRAR_RATIO)) if ids else 0

    contents = _conversacion_a_contents(conversacion)
    raw = _llamar_gemini(contents, system_instruction=system, schema=_SCHEMA_PASO)
    paso = json.loads(raw)

    if paso.get("accion") == "cerrar" and ids:
        cubiertos = set(paso.get("temas_cubiertos") or [])
        faltantes = [i for i in ids if i not in cubiertos]
        if len(cubiertos) < minimo and faltantes:
            faltan_texto = ", ".join(faltantes)
            system_forzado = system + (
                f"\n\n**RECORDATORIO URGENTE:** todavía faltan cubrir estos temas: {faltan_texto}. "
                "NO cierres la conversación todavía -- elegí uno de esos temas pendientes y "
                "formulá la siguiente pregunta sobre eso."
            )
            raw2 = _llamar_gemini(contents, system_instruction=system_forzado, schema=_SCHEMA_PASO)
            paso2 = json.loads(raw2)
            paso2["accion"] = "preguntar"
            return paso2

    return paso


def siguiente_paso_stream(proyecto: dict, participante: dict, conversacion: list[dict]):
    system = _system_prompt(proyecto, participante) + (
        "\n\nRespondé con tu siguiente mensaje: o bien una pregunta puntual, o un cierre "
        "con agradecimiento (si ya cubriste la gran mayoría de los temas). IMPORTANTE: tu "
        "respuesta entera es texto plano mostrado directo en un chat -- NUNCA incluyas "
        "JSON, listas de ids, ni anotación interna al final del mensaje."
    )
    contents = _conversacion_a_contents(conversacion)
    yield from _llamar_gemini_stream(contents, system_instruction=system)


def es_invitacion_a_responder(texto: str) -> bool:
    if not texto:
        return False
    t = texto.strip()
    if t.endswith("?") or t.endswith("？"):
        return True
    return bool(_INVITA_RE.search(t))


def limpiar_fuga_metadata(texto: str) -> str:
    lineas_limpias = []
    for linea in texto.split("\n"):
        low = linea.strip().lower()
        if low.startswith("temas_cubiertos") or low.startswith("accion:") or low.startswith('"temas_cubiertos"'):
            continue
        lineas_limpias.append(linea)
    return "\n".join(lineas_limpias).rstrip()


def generar_md_crudo(proyecto: dict, participante: dict, conversacion: list[dict]) -> str:
    """Sintetiza la charla de UN participante en un documento crudo -- insumo para que
    Catequil haga la curaduria de reglas de negocio (ver render.py)."""
    texto_conv = "\n".join(
        f"{'AGENTE' if t['rol'] == 'agente' else participante['nombre'].upper()}: {t['texto']}" for t in conversacion
    )
    prompt = f"""Eres un analista de Raifen. Tienes esta conversación de discovery de
negocio con {participante['nombre']} ({participante.get('cargo') or 'sin cargo'}) de
"{proyecto['cliente']}":

{texto_conv}

Escribe un documento en Markdown, en español neutro (sin modismos regionales), con esta
estructura EXACTA:

# Relevamiento crudo — {participante['nombre']} ({proyecto['cliente']})

## Contexto aportado
(qué agregó esta persona sobre cómo opera el negocio)

## Reglas de negocio mencionadas
(cada definición o regla concreta que dijo -- "una venta se considera X cuando...",
excepciones incluidas. Sé literal, no generalices.)

## Sistemas y fuentes de datos mencionados

## Dolores / decisiones bloqueadas

## Correcciones a "lo que ya sabíamos"
(si el participante corrigió o matizó algo del contexto previo que se le compartió al
arrancar -- si no corrigió nada, escribí "Sin correcciones")

## Preguntas que quedaron abiertas
(temas que no se cubrieron o quedaron ambiguos)

Sé fiel a lo que se dijo -- no inventes datos que no surgieron de la charla."""
    contents = [{"role": "user", "parts": [{"text": prompt}]}]
    return _llamar_gemini(contents, schema=None)
