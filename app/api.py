"""Raifen Discovery -- FastAPI.

Flujo: Catequil siembra los participantes del proyecto (config/proyecto.yaml) -> cada
uno recibe su link /r/<token> -> completa un formulario tipado (texto libre / opción
única / opción múltiple / booleano -- sin IA, cada pregunta es un campo real) a su
propio ritmo, con opción de grabar audio para las preguntas de texto libre o adjuntar
archivos -> Catequil hace la curaduría (marca reglas de negocio confirmadas a partir de
las respuestas) -> se envía el documento formal de aprobación al cliente vía el webhook
markdown-raifen."""
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import formato_empresa, render, settings, store, transcribe
from .auth_middleware import BasicAuthMiddleware

app = FastAPI(title="Raifen Discovery")
app.add_middleware(BasicAuthMiddleware)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["brand_name"] = settings.BRAND_NAME


@app.on_event("startup")
def _startup():
    store.init_db()


def _proyecto() -> dict:
    return settings.load_proyecto()


def _pregunta_por_id(proyecto: dict, pregunta_id: str) -> dict | None:
    for p in settings.preguntas_planas(proyecto):
        if p["id"] == pregunta_id:
            return p
    return None


# ---------- Admin ----------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    proyecto = _proyecto()
    total_preguntas = len(settings.preguntas_planas(proyecto))
    participantes = store.listar_participantes()
    for p in participantes:
        p["progreso"] = store.progreso(p, total_preguntas)
        p["link"] = f"{settings.PUBLIC_BASE_URL}/r/{p['token']}"
    reglas = store.listar_reglas()
    return templates.TemplateResponse(
        request, "home.html",
        {"proyecto": proyecto, "participantes": participantes, "reglas": reglas},
    )


@app.post("/admin/sembrar")
def sembrar():
    proyecto = _proyecto()
    store.sembrar_participantes(proyecto.get("participantes", []))
    return RedirectResponse(url="/", status_code=303)


@app.get("/admin/participantes/{pid}", response_class=HTMLResponse)
def ver_participante(request: Request, pid: str):
    p = store.obtener(pid)
    if not p:
        raise HTTPException(404, "participante no encontrado")
    proyecto = _proyecto()
    respuestas = store.get_respuestas(p)
    adjuntos = store.listar_adjuntos(pid)
    link = f"{settings.PUBLIC_BASE_URL}/r/{p['token']}"
    return templates.TemplateResponse(
        request, "admin_participante.html",
        {
            "p": p, "proyecto": proyecto, "respuestas": respuestas, "adjuntos": adjuntos, "link": link,
            "temas": proyecto.get("temas", []),
        },
    )


@app.post("/admin/reglas")
async def crear_regla(request: Request):
    form = await request.form()
    texto = (form.get("texto") or "").strip()
    participante_id = (form.get("participante_id") or "").strip() or None
    if not texto:
        raise HTTPException(400, "falta el texto de la regla")
    store.crear_regla(texto, participante_id)
    redirigir_a = f"/admin/participantes/{participante_id}" if participante_id else "/"
    return RedirectResponse(url=redirigir_a, status_code=303)


@app.post("/admin/reglas/{rid}/estado")
async def actualizar_regla(request: Request, rid: str):
    form = await request.form()
    estado = (form.get("estado") or "").strip()
    if estado not in ("borrador", "confirmada", "entregada_oscar", "validada_consume"):
        raise HTTPException(400, "estado inválido")
    store.actualizar_estado_regla(rid, estado)
    return RedirectResponse(url="/", status_code=303)


@app.post("/admin/aprobacion/enviar")
def enviar_aprobacion():
    proyecto = _proyecto()
    reglas = store.listar_reglas()
    participantes = {p["id"]: p for p in store.listar_participantes()}
    documento = render.documento_aprobacion(proyecto, reglas, participantes)
    ok, error = formato_empresa.enviar_para_aprobacion(
        markdown_text=documento,
        correo_referencia=proyecto.get("correo_aprobacion", ""),
        nombre_documento=f"Reglas de Negocio — {proyecto['cliente']}",
        nombre_proyecto=proyecto.get("proyecto", proyecto["cliente"]),
        nombre_cliente=proyecto["cliente"],
    )
    store.registrar_aprobacion(documento, proyecto.get("correo_aprobacion", ""), None if ok else error)
    return RedirectResponse(url="/", status_code=303)


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------- Publico (sin login admin -- la seguridad es el token) ----------

@app.get("/r/{token}", response_class=HTMLResponse)
def relevar(request: Request, token: str):
    p = store.obtener_por_token(token)
    if not p:
        raise HTTPException(404, "Este link no es válido.")
    proyecto = _proyecto()
    respuestas = store.get_respuestas(p)
    total_preguntas = len(settings.preguntas_planas(proyecto))
    return templates.TemplateResponse(
        request, "participante.html",
        {
            "p": p, "proyecto": proyecto, "respuestas": respuestas,
            "progreso": store.progreso(p, total_preguntas),
            "temas": proyecto.get("temas", []),
        },
    )


@app.post("/r/{token}/respuesta")
async def guardar_respuesta(request: Request, token: str):
    p = store.obtener_por_token(token)
    if not p:
        raise HTTPException(404, "link inválido")
    proyecto = _proyecto()
    body = await request.json()
    pregunta_id = body.get("pregunta_id")
    valor = body.get("valor")
    pregunta = _pregunta_por_id(proyecto, pregunta_id or "")
    if not pregunta:
        raise HTTPException(400, "pregunta inválida")
    store.guardar_respuesta(p["id"], pregunta_id, valor, fuente=body.get("fuente", "texto"))
    p_actualizado = store.obtener(p["id"])
    total_preguntas = len(settings.preguntas_planas(proyecto))
    return JSONResponse({"ok": True, "progreso": store.progreso(p_actualizado, total_preguntas)})


@app.post("/r/{token}/correcciones")
async def guardar_correcciones(request: Request, token: str):
    p = store.obtener_por_token(token)
    if not p:
        raise HTTPException(404, "link inválido")
    body = await request.json()
    store.guardar_correcciones(p["id"], (body.get("texto") or "").strip())
    return JSONResponse({"ok": True})


@app.post("/r/{token}/finalizar")
def finalizar(token: str):
    p = store.obtener_por_token(token)
    if not p:
        raise HTTPException(404, "link inválido")
    store.marcar_completado(p["id"])
    return RedirectResponse(url=f"/r/{token}", status_code=303)


@app.post("/r/{token}/audio/{pregunta_id}")
async def enviar_audio(token: str, pregunta_id: str, file: UploadFile):
    """Transcribe un audio y lo devuelve como texto sugerido para el campo de la
    pregunta -- el participante lo revisa/edita antes de guardarlo. No guarda la
    respuesta directamente: el guardado sigue pasando por /respuesta, como cualquier
    campo de texto."""
    p = store.obtener_por_token(token)
    if not p:
        raise HTTPException(404, "link inválido")
    proyecto = _proyecto()
    pregunta = _pregunta_por_id(proyecto, pregunta_id)
    if not pregunta or pregunta["tipo"] != "texto_libre":
        raise HTTPException(400, "esta pregunta no acepta audio")

    audio_bytes = await file.read()
    nombre = f"{uuid.uuid4().hex}_{file.filename or 'audio.webm'}"
    ruta = Path(settings.UPLOADS_DIR) / nombre
    ruta.write_bytes(audio_bytes)
    store.agregar_adjunto(p["id"], "audio", file.filename or nombre, str(ruta), pregunta_id=pregunta_id)

    texto = transcribe.transcribir(audio_bytes, file.filename or "audio.webm")
    if not texto:
        raise HTTPException(502, "no se pudo transcribir el audio -- probá escribir la respuesta")
    return JSONResponse({"texto": texto})


@app.post("/r/{token}/adjunto")
async def enviar_adjunto(token: str, file: UploadFile):
    p = store.obtener_por_token(token)
    if not p:
        raise HTTPException(404, "link inválido")
    contenido = await file.read()
    nombre = f"{uuid.uuid4().hex}_{file.filename or 'archivo'}"
    ruta = Path(settings.UPLOADS_DIR) / nombre
    ruta.write_bytes(contenido)
    store.agregar_adjunto(p["id"], "archivo", file.filename or nombre, str(ruta))
    return JSONResponse({"ok": True, "nombre_archivo": file.filename or nombre})
