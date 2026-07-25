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
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import formato_empresa, render, settings, store, transcribe
from .auth_middleware import BasicAuthMiddleware

app = FastAPI(title="Raifen Discovery")
app.add_middleware(BasicAuthMiddleware)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["brand_name"] = settings.BRAND_NAME
templates.env.globals["admin_review_email"] = settings.ADMIN_REVIEW_EMAIL


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
    resumen_temas = store.resumen_por_tema(proyecto, participantes)
    return templates.TemplateResponse(
        request, "home.html",
        {"proyecto": proyecto, "participantes": participantes, "reglas": reglas, "resumen_temas": resumen_temas},
    )


@app.post("/admin/sembrar")
def sembrar():
    proyecto = _proyecto()
    store.sembrar_participantes(proyecto.get("participantes", []))
    return RedirectResponse(url="/", status_code=303)


@app.post("/admin/lo-que-sabemos")
async def actualizar_lo_que_sabemos(request: Request):
    form = await request.form()
    texto = (form.get("texto") or "").strip()
    settings.actualizar_lo_que_sabemos(_proyecto(), texto)
    return RedirectResponse(url="/", status_code=303)


# ---------- Editor de banco de preguntas y participantes ----------

@app.post("/admin/temas")
async def crear_tema(request: Request):
    form = await request.form()
    titulo = (form.get("titulo") or "").strip()
    if not titulo:
        raise HTTPException(400, "falta el título del tema")
    settings.agregar_tema(_proyecto(), titulo)
    return RedirectResponse(url="/admin/editor", status_code=303)


@app.post("/admin/temas/{tema_id}/preguntas")
async def crear_pregunta(request: Request, tema_id: str):
    form = await request.form()
    texto = (form.get("texto") or "").strip()
    tipo = (form.get("tipo") or "").strip()
    if not texto or tipo not in ("texto_libre", "opcion_unica", "opcion_multiple", "booleano"):
        raise HTTPException(400, "faltan datos de la pregunta")
    opciones_raw = (form.get("opciones") or "").strip()
    opciones = [o.strip() for o in opciones_raw.splitlines() if o.strip()] if opciones_raw else None
    if tipo in ("opcion_unica", "opcion_multiple") and not opciones:
        raise HTTPException(400, "este tipo de pregunta necesita al menos una opción")
    ayuda = (form.get("ayuda") or "").strip() or None
    settings.agregar_pregunta(_proyecto(), tema_id, texto, tipo, opciones, ayuda)
    return RedirectResponse(url="/admin/editor", status_code=303)


@app.post("/admin/temas/{tema_id}/preguntas/{pregunta_id}/eliminar")
def eliminar_pregunta(tema_id: str, pregunta_id: str):
    settings.eliminar_pregunta(_proyecto(), tema_id, pregunta_id)
    return RedirectResponse(url="/admin/editor", status_code=303)


@app.post("/admin/participantes")
async def crear_participante(request: Request):
    form = await request.form()
    nombre = (form.get("nombre") or "").strip()
    cargo = (form.get("cargo") or "").strip()
    email = (form.get("email") or "").strip()
    if not nombre:
        raise HTTPException(400, "falta el nombre del participante")
    settings.agregar_participante_a_yaml(_proyecto(), nombre, cargo, email)
    store.sembrar_participantes([{"nombre": nombre, "cargo": cargo, "email": email}])
    return RedirectResponse(url="/", status_code=303)


@app.get("/admin/api/respuestas")
def api_respuestas():
    """Export estructurado de todo lo relevado -- pensado para que Catequil (u otro
    agente/script) lo consuma directo en vez de raspar HTML, al armar el resumen de
    "lo que sabemos" o la curaduria de reglas de negocio. Mismo auth basico que el
    resto de /admin."""
    proyecto = _proyecto()
    participantes = store.listar_participantes()
    data = []
    for p in participantes:
        completo = store.obtener(p["id"])
        data.append({
            "id": p["id"], "nombre": p["nombre"], "cargo": p["cargo"], "email": p["email"],
            "estado": p["estado"], "correcciones_ya_sabemos": completo.get("correcciones_ya_sabemos"),
            "respuestas": store.get_respuestas(completo),
        })
    return JSONResponse({
        "proyecto": {"cliente": proyecto["cliente"], "proyecto": proyecto.get("proyecto"), "vertical": proyecto.get("vertical")},
        "lo_que_ya_sabemos": proyecto.get("lo_que_ya_sabemos"),
        "temas": proyecto.get("temas", []),
        "participantes": data,
        "reglas_negocio": store.listar_reglas(),
    })


@app.get("/admin/editor", response_class=HTMLResponse)
def editor(request: Request):
    proyecto = _proyecto()
    return templates.TemplateResponse(
        request, "editor.html",
        {"proyecto": proyecto, "temas": proyecto.get("temas", [])},
    )


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


@app.post("/admin/participantes/{pid}/respuestas/{pregunta_id}")
async def editar_respuesta_admin(request: Request, pid: str, pregunta_id: str):
    """Correccion manual desde el admin -- ej. para arreglar una respuesta de prueba
    cargada por error. Mismo mecanismo de guardado que usa el participante."""
    form = await request.form()
    valor = (form.get("valor") or "").strip()
    if valor:
        pregunta = _pregunta_por_id(_proyecto(), pregunta_id)
        if pregunta and pregunta["tipo"] == "opcion_multiple":
            valor = [v.strip() for v in valor.split(",") if v.strip()]
        store.guardar_respuesta(pid, pregunta_id, valor, fuente="editado_por_admin")
    return RedirectResponse(url=f"/admin/participantes/{pid}", status_code=303)


@app.post("/admin/participantes/{pid}/respuestas/{pregunta_id}/eliminar")
def eliminar_respuesta_admin(pid: str, pregunta_id: str):
    store.eliminar_respuesta(pid, pregunta_id)
    return RedirectResponse(url=f"/admin/participantes/{pid}", status_code=303)


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
        correo_referencia=settings.ADMIN_REVIEW_EMAIL,  # NUNCA el cliente -- siempre revisión interna primero
        nombre_documento=f"Reglas de Negocio — {proyecto['cliente']}",
        nombre_proyecto=proyecto.get("proyecto", proyecto["cliente"]),
        nombre_cliente=proyecto["cliente"],
    )
    store.registrar_aprobacion(documento, settings.ADMIN_REVIEW_EMAIL, None if ok else error)
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
    return JSONResponse({"ok": True, "siguiente": f"/r/{token}/gracias"})


@app.get("/r/{token}/gracias", response_class=HTMLResponse)
def gracias(request: Request, token: str):
    p = store.obtener_por_token(token)
    if not p:
        raise HTTPException(404, "Este link no es válido.")
    proyecto = _proyecto()
    return templates.TemplateResponse(request, "gracias.html", {"p": p, "proyecto": proyecto})


@app.get("/r/{token}/panel", response_class=HTMLResponse)
def panel_participante(request: Request, token: str):
    """Vista de solo lectura del progreso general del proyecto -- mismo tipo de info que
    ve el admin (quienes son los stakeholders, quien respondio, que areas estan
    cubiertas), pero sin nada de curaduria ni edicion de preguntas. Cualquier
    participante con un link valido puede verla -- es informacion del propio proyecto
    del cliente, no datos internos de Raifen."""
    p = store.obtener_por_token(token)
    if not p:
        raise HTTPException(404, "Este link no es válido.")
    proyecto = _proyecto()
    total_preguntas = len(settings.preguntas_planas(proyecto))
    participantes = store.listar_participantes()
    for part in participantes:
        part["progreso"] = store.progreso(part, total_preguntas)
    resumen_temas = store.resumen_por_tema(proyecto, participantes)
    return templates.TemplateResponse(
        request, "panel_participante.html",
        {"p": p, "proyecto": proyecto, "participantes": participantes, "resumen_temas": resumen_temas},
    )


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
        raise HTTPException(502, "no se pudo transcribir el audio -- escriba la respuesta directamente")
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
