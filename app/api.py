"""Raifen Discovery -- FastAPI.

Flujo: Catequil siembra los participantes del proyecto (config/proyecto.yaml) -> cada
uno recibe su link /r/<token>, asignado a UN formulario del proyecto (un proyecto puede
tener varios formularios -- ej. uno por area/rol) -> completa un formulario tipado
(texto libre / opción única / opción múltiple / booleano -- sin IA, cada pregunta es un
campo real) a su propio ritmo, con opción de grabar audio para las preguntas de texto
libre o adjuntar archivos -> Catequil hace la curaduría (marca reglas de negocio
confirmadas a partir de las respuestas) -> se envía el documento formal de aprobación
para revisión interna vía el webhook markdown-raifen."""
import uuid
from pathlib import Path

import markdown as markdown_lib
from fastapi import FastAPI, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup

from . import formato_empresa, mailer, render, settings, store, transcribe
from .auth_middleware import BasicAuthMiddleware

app = FastAPI(title="Raifen Discovery")
app.add_middleware(BasicAuthMiddleware)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["brand_name"] = settings.BRAND_NAME
templates.env.globals["admin_review_email"] = settings.ADMIN_REVIEW_EMAIL


def _render_markdown(texto: str | None) -> Markup:
    """"Lo que ya sabemos del negocio" se edita como texto plano (textarea) pero se
    muestra a participantes con formato -- admin puede usar ## subtitulos, listas, etc.
    Contenido siempre editado por el admin (auth basica), nunca por un participante, asi
    que renderizarlo como HTML sin escapar es seguro."""
    return Markup(markdown_lib.markdown(texto or "", extensions=["nl2br"]))


templates.env.filters["markdown"] = _render_markdown


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


def _formulario_de(proyecto: dict, formulario_id: str) -> dict | None:
    """Formulario a mostrar en /r/<token>?formulario=<id> -- la landing sin query param
    ya no resuelve un formulario directo, siempre manda al panel primero (ver relevar()),
    asi que este helper solo se usa con un id explicito."""
    return settings.formulario_por_id(proyecto, formulario_id) or (proyecto["formularios"][0] if proyecto.get("formularios") else None)


def _ids_preguntas_de_formularios(proyecto: dict, formulario_ids: list[str]) -> set[str]:
    ids: set[str] = set()
    for fid in formulario_ids:
        ids |= {q["id"] for q in settings.preguntas_planas(proyecto, formulario_id=fid)}
    return ids


def _participantes_con_progreso(proyecto: dict) -> list[dict]:
    """Para las tablas "Quienes participan" (panel de admin y de cada participante):
    progreso agregado de cada persona + la lista de sus formularios asignados (para el
    dropdown de "ir directo a lo que le toca a X")."""
    participantes = store.listar_participantes()
    for part in participantes:
        ids_asignados = store.formularios_de_participante(part["id"])
        part["formularios_asignados_lista"] = [
            {"id": fid, "nombre": (settings.formulario_por_id(proyecto, fid) or {}).get("nombre", fid)}
            for fid in ids_asignados
        ]
        ids_preguntas = _ids_preguntas_de_formularios(proyecto, ids_asignados)
        part["progreso"] = store.progreso_formulario(part, ids_preguntas)
    return participantes


# ---------- Admin ----------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    proyecto = _proyecto()
    stakeholders = store.listar_participantes()  # ya es 1 fila por PERSONA, no por asignacion
    for p in stakeholders:
        p["link"] = f"{settings.PUBLIC_BASE_URL}/r/{p['token']}"

    # "Participantes por formulario" -- una fila por asignacion (persona x formulario).
    # Quien no tiene ningun formulario asignado sale igual, con formulario_id None (es
    # solo un stakeholder del proyecto).
    asignaciones = store.formularios_por_participante_bulk()
    participantes_por_formulario = []
    for p in stakeholders:
        fids = asignaciones.get(p["id"], [])
        if not fids:
            participantes_por_formulario.append({**p, "formulario_id_resuelto": None, "formulario_nombre": None, "progreso": {"cubiertos": 0, "total": 0}})
            continue
        for fid in fids:
            formulario = settings.formulario_por_id(proyecto, fid)
            ids_preguntas = {q["id"] for q in settings.preguntas_planas(proyecto, formulario_id=fid)}
            participantes_por_formulario.append({
                **p,
                "formulario_id_resuelto": fid,
                "formulario_nombre": formulario["nombre"] if formulario else fid,
                "progreso": store.progreso_formulario(p, ids_preguntas),
            })

    reglas = store.listar_reglas()
    resumen_temas = store.resumen_por_tema(proyecto, stakeholders)

    return templates.TemplateResponse(
        request, "home.html",
        {
            "proyecto": proyecto, "participantes": participantes_por_formulario, "stakeholders": stakeholders,
            "reglas": reglas, "resumen_temas": resumen_temas, "formularios": proyecto.get("formularios", []),
        },
    )


@app.get("/admin/panel", response_class=HTMLResponse)
def admin_panel(request: Request):
    """Vista de admin para previsualizar el panel general tal como lo ve cualquier
    participante -- sin atarse al token de uno puntual (a diferencia de
    /r/<token>/panel, que sí es el de una persona real)."""
    proyecto = _proyecto()
    participantes = _participantes_con_progreso(proyecto)
    resumen_temas = store.resumen_por_tema(proyecto, participantes)
    reglas_confirmadas = [r for r in store.listar_reglas() if r["estado"] in ("confirmada", "entregada_oscar", "validada_consume")]
    return templates.TemplateResponse(
        request, "panel_participante.html",
        {
            "p": None, "proyecto": proyecto, "participantes": participantes, "resumen_temas": resumen_temas,
            "reglas_confirmadas": reglas_confirmadas,
        },
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


@app.post("/admin/proyecto")
async def actualizar_proyecto(request: Request):
    form = await request.form()
    cliente = (form.get("cliente") or "").strip()
    nombre_proyecto = (form.get("proyecto") or "").strip()
    vertical = (form.get("vertical") or "").strip()
    logo_url = (form.get("logo_url") or "").strip()
    if not cliente:
        raise HTTPException(400, "falta el nombre del cliente")
    settings.actualizar_datos_proyecto(_proyecto(), cliente, nombre_proyecto, vertical, logo_url)
    return RedirectResponse(url="/", status_code=303)


# ---------- Editor de formularios, temas, preguntas y participantes ----------

@app.post("/admin/formularios")
async def crear_formulario(request: Request):
    form = await request.form()
    nombre = (form.get("nombre") or "").strip()
    if not nombre:
        raise HTTPException(400, "falta el nombre del formulario")
    settings.agregar_formulario(_proyecto(), nombre)
    return RedirectResponse(url="/admin/editor", status_code=303)


@app.post("/admin/formularios/{formulario_id}/temas")
async def crear_tema(request: Request, formulario_id: str):
    form = await request.form()
    titulo = (form.get("titulo") or "").strip()
    if not titulo:
        raise HTTPException(400, "falta el título del tema")
    settings.agregar_tema(_proyecto(), formulario_id, titulo)
    return RedirectResponse(url="/admin/editor", status_code=303)


@app.post("/admin/formularios/{formulario_id}/temas/{tema_id}/preguntas")
async def crear_pregunta(request: Request, formulario_id: str, tema_id: str):
    form = await request.form()
    texto = (form.get("texto") or "").strip()
    tipo = (form.get("tipo") or "").strip()
    if not texto or tipo not in settings.TIPOS_PREGUNTA:
        raise HTTPException(400, "faltan datos de la pregunta")
    opciones_raw = (form.get("opciones") or "").strip()
    opciones = [o.strip() for o in opciones_raw.splitlines() if o.strip()] if opciones_raw else None
    if tipo in ("opcion_unica", "opcion_multiple") and not opciones:
        raise HTTPException(400, "este tipo de pregunta necesita al menos una opción")
    ayuda = (form.get("ayuda") or "").strip() or None
    settings.agregar_pregunta(_proyecto(), formulario_id, tema_id, texto, tipo, opciones, ayuda)
    return RedirectResponse(url="/admin/editor", status_code=303)


@app.post("/admin/formularios/{formulario_id}/temas/{tema_id}/preguntas/{pregunta_id}/eliminar")
def eliminar_pregunta(formulario_id: str, tema_id: str, pregunta_id: str):
    settings.eliminar_pregunta(_proyecto(), formulario_id, tema_id, pregunta_id)
    return RedirectResponse(url="/admin/editor", status_code=303)


@app.post("/admin/participantes")
async def crear_participante(request: Request):
    form = await request.form()
    nombre = (form.get("nombre") or "").strip()
    cargo = (form.get("cargo") or "").strip()
    email = (form.get("email") or "").strip()
    formulario_ids = [f.strip() for f in form.getlist("formulario_ids") if f.strip()]  # vacio = solo stakeholder
    if not nombre:
        raise HTTPException(400, "falta el nombre del participante")
    settings.agregar_participante_a_yaml(_proyecto(), nombre, cargo, email, formulario_ids)
    store.sembrar_participantes([{"nombre": nombre, "cargo": cargo, "email": email, "formulario_ids": formulario_ids}])
    return RedirectResponse(url="/", status_code=303)


@app.post("/admin/participantes/{pid}/formulario")
async def reasignar_formulario(request: Request, pid: str):
    form = await request.form()
    formulario_ids = [f.strip() for f in form.getlist("formulario_ids") if f.strip()]  # vacio = pasa a ser solo stakeholder
    store.asignar_formularios(pid, formulario_ids)
    return RedirectResponse(url=f"/admin/participantes/{pid}", status_code=303)


@app.post("/admin/participantes/{pid}/datos")
async def actualizar_datos_participante(request: Request, pid: str):
    form = await request.form()
    nombre = (form.get("nombre") or "").strip()
    cargo = (form.get("cargo") or "").strip()
    email = (form.get("email") or "").strip()
    if not nombre:
        raise HTTPException(400, "falta el nombre del participante")
    store.actualizar_datos_participante(pid, nombre, cargo, email)
    return RedirectResponse(url=f"/admin/participantes/{pid}", status_code=303)


@app.post("/admin/participantes/{pid}/eliminar")
def eliminar_participante(pid: str):
    store.eliminar_participante(pid)
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
            "estado": p["estado"], "formulario_ids": store.formularios_de_participante(p["id"]),
            "correcciones_ya_sabemos": completo.get("correcciones_ya_sabemos"),
            "respuestas": store.get_respuestas(completo),
        })
    return JSONResponse({
        "proyecto": {"cliente": proyecto["cliente"], "proyecto": proyecto.get("proyecto"), "vertical": proyecto.get("vertical")},
        "lo_que_ya_sabemos": proyecto.get("lo_que_ya_sabemos"),
        "formularios": proyecto.get("formularios", []),
        "participantes": data,
        "reglas_negocio": store.listar_reglas(),
    })


# ---------- Alta en bloque (JSON) -- para que Catequil suba contenido ya acordado con
# Tom en una sola llamada, en vez de clickear campo a campo en el editor HTML ----------

@app.post("/admin/api/formularios")
async def api_crear_formulario(request: Request):
    """Body: {"nombre": str, "temas": [{"titulo": str, "preguntas": [{"texto","tipo","opciones"?,"ayuda"?}]}]}"""
    body = await request.json()
    nombre = (body.get("nombre") or "").strip()
    if not nombre:
        raise HTTPException(400, "falta el nombre del formulario")
    try:
        settings.crear_formulario_completo(_proyecto(), nombre, body.get("temas", []))
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    return JSONResponse({"ok": True})


@app.post("/admin/api/formularios/{formulario_id}/temas")
async def api_agregar_temas(request: Request, formulario_id: str):
    """Body: {"temas": [{"titulo": str, "preguntas": [...]}]}"""
    body = await request.json()
    try:
        settings.agregar_temas_a_formulario(_proyecto(), formulario_id, body.get("temas", []))
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    return JSONResponse({"ok": True})


@app.post("/admin/api/formularios/{formulario_id}/temas/{tema_id}/preguntas")
async def api_agregar_preguntas(request: Request, formulario_id: str, tema_id: str):
    """Body: {"preguntas": [{"texto","tipo","opciones"?,"ayuda"?}]}"""
    body = await request.json()
    try:
        settings.agregar_preguntas_a_tema(_proyecto(), formulario_id, tema_id, body.get("preguntas", []))
    except (KeyError, ValueError) as e:
        raise HTTPException(400, str(e))
    return JSONResponse({"ok": True})


@app.get("/admin/editor", response_class=HTMLResponse)
def editor(request: Request):
    proyecto = _proyecto()
    return templates.TemplateResponse(
        request, "editor.html",
        {"proyecto": proyecto, "formularios": proyecto.get("formularios", [])},
    )


@app.get("/admin/participantes/{pid}", response_class=HTMLResponse)
def ver_participante(request: Request, pid: str):
    p = store.obtener(pid)
    if not p:
        raise HTTPException(404, "participante no encontrado")
    proyecto = _proyecto()
    formulario_ids_asignados = store.formularios_de_participante(pid)
    formularios_asignados = [
        f for fid in formulario_ids_asignados if (f := settings.formulario_por_id(proyecto, fid))
    ]
    respuestas = store.get_respuestas(p)
    # Solo adjuntos de preguntas de los formularios asignados (o sin pregunta asociada,
    # ej. un adjunto general) -- no de cualquier formulario que el participante hubiera
    # tenido asignado alguna vez.
    ids_preguntas_asignadas = {
        pr["id"] for f in formularios_asignados for t in f.get("temas", []) for pr in t.get("preguntas", [])
    }
    adjuntos = [
        a for a in store.listar_adjuntos(pid)
        if not a.get("pregunta_id") or a["pregunta_id"] in ids_preguntas_asignadas
    ]
    link = f"{settings.PUBLIC_BASE_URL}/r/{p['token']}"
    return templates.TemplateResponse(
        request, "admin_participante.html",
        {
            "p": p, "proyecto": proyecto, "respuestas": respuestas, "adjuntos": adjuntos, "link": link,
            "formularios_asignados": formularios_asignados, "formularios_asignados_ids": formulario_ids_asignados,
            "formularios": proyecto.get("formularios", []),
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
async def enviar_aprobacion(request: Request):
    form = await request.form()
    secciones = {s for s in render.SECCIONES_VALIDAS if form.get(f"incluir_{s}")}
    # Preseteado a ADMIN_REVIEW_EMAIL pero editable -- pedido explicito de Tom para
    # poder mandarselo a otro empleado de Raifen o reenviarlo el mismo a quien
    # corresponda. El default sigue siendo la revision interna; a quien se lo termine
    # mandando queda a criterio de quien lo envia desde acá, no hardcodeado.
    correo_destino = (form.get("correo") or "").strip() or settings.ADMIN_REVIEW_EMAIL

    proyecto = _proyecto()
    reglas = store.listar_reglas()
    stakeholders = store.listar_participantes()
    for p in stakeholders:
        p["link"] = f"{settings.PUBLIC_BASE_URL}/r/{p['token']}"
    participantes_por_id = {p["id"]: p for p in stakeholders}
    resumen_temas = store.resumen_por_tema(proyecto, stakeholders)

    asignaciones = store.formularios_por_participante_bulk()
    participantes_por_formulario = []
    for p in stakeholders:
        fids = asignaciones.get(p["id"], [])
        if not fids:
            participantes_por_formulario.append({**p, "formulario_nombre": "— sin asignar —"})
            continue
        for fid in fids:
            formulario = settings.formulario_por_id(proyecto, fid)
            participantes_por_formulario.append({**p, "formulario_nombre": formulario["nombre"] if formulario else fid})

    documento = render.documento_aprobacion(
        proyecto, reglas, participantes_por_id,
        secciones=secciones, stakeholders=stakeholders,
        participantes_por_formulario=participantes_por_formulario, resumen_temas=resumen_temas,
    )
    ok, error = formato_empresa.enviar_para_aprobacion(
        markdown_text=documento,
        correo_referencia=correo_destino,
        nombre_documento=f"Discovery — {proyecto['cliente']}",
        nombre_proyecto=proyecto.get("proyecto", proyecto["cliente"]),
        nombre_cliente=proyecto["cliente"],
    )
    store.registrar_aprobacion(documento, correo_destino, None if ok else error)
    return RedirectResponse(url="/", status_code=303)


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------- Publico (sin login admin -- la seguridad es el token) ----------

@app.get("/r/{token}", response_class=HTMLResponse)
def relevar(request: Request, token: str, formulario: str | None = None):
    p = store.obtener_por_token(token)
    if not p:
        raise HTTPException(404, "Este link no es válido.")
    proyecto = _proyecto()
    if not formulario:
        # Sin pedir un formulario puntual, siempre entra primero al panel -- ahi ve la
        # tabla "Tus formularios" y elige cual completar. Landing uniforme para todos,
        # sin importar cuantos formularios tenga asignados (antes dependia de eso).
        return RedirectResponse(url=f"/r/{token}/panel", status_code=303)
    formulario_actual = _formulario_de(proyecto, formulario)
    if not formulario_actual:
        return RedirectResponse(url=f"/r/{token}/panel", status_code=303)
    respuestas = store.get_respuestas(p)
    ids_preguntas = {q["id"] for q in settings.preguntas_planas(proyecto, formulario_id=formulario_actual["id"])}
    return templates.TemplateResponse(
        request, "participante.html",
        {
            "p": p, "proyecto": proyecto, "respuestas": respuestas,
            "progreso": store.progreso_formulario(p, ids_preguntas),
            "temas": formulario_actual.get("temas", []), "formulario": formulario_actual,
            "formularios": proyecto.get("formularios", []),
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
    # El progreso se calcula sobre el formulario AL QUE PERTENECE la pregunta que se
    # acaba de responder (puede no ser uno de los asignados por default, si esta viendo
    # otro formulario del proyecto via ?formulario=).
    ids_preguntas = {q["id"] for q in settings.preguntas_planas(proyecto, formulario_id=pregunta["formulario_id"])}
    return JSONResponse({"ok": True, "progreso": store.progreso_formulario(p_actualizado, ids_preguntas)})


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
    proyecto = _proyecto()
    try:
        mailer.notificar_completado(p["nombre"], proyecto["cliente"], f"{settings.PUBLIC_BASE_URL}/admin/participantes/{p['id']}")
    except Exception:
        pass  # el aviso es best-effort, nunca bloquea el flujo del participante
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
    ids_asignados = set(store.formularios_de_participante(p["id"]))
    p["formularios_asignados"] = ids_asignados
    # Tabla "Tus formularios": TODOS los del proyecto (no solo los asignados por
    # default), marcando cual es cual -- cualquier participante puede completar
    # cualquier formulario del proyecto desde su mismo link, no solo el suyo.
    p["mis_formularios"] = []
    for formulario in proyecto.get("formularios", []):
        ids_preguntas = {q["id"] for q in settings.preguntas_planas(proyecto, formulario_id=formulario["id"])}
        p["mis_formularios"].append({
            "id": formulario["id"], "nombre": formulario["nombre"], "asignado": formulario["id"] in ids_asignados,
            "progreso": store.progreso_formulario(p, ids_preguntas),
        })
    participantes = _participantes_con_progreso(proyecto)
    resumen_temas = store.resumen_por_tema(proyecto, participantes)
    reglas_confirmadas = [r for r in store.listar_reglas() if r["estado"] in ("confirmada", "entregada_oscar", "validada_consume")]
    return templates.TemplateResponse(
        request, "panel_participante.html",
        {
            "p": p, "proyecto": proyecto, "participantes": participantes, "resumen_temas": resumen_temas,
            "reglas_confirmadas": reglas_confirmadas,
        },
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
async def enviar_adjunto(token: str, file: UploadFile, pregunta_id: str | None = Form(None)):
    """Adjunto general (sin pregunta_id, boton "Adjuntar archivo o pantallazo") o
    respuesta a una pregunta tipo "archivo" (con pregunta_id) -- en ese caso ademas
    guarda una respuesta con el nombre del archivo, para que cuente en el progreso
    igual que cualquier otra pregunta respondida."""
    p = store.obtener_por_token(token)
    if not p:
        raise HTTPException(404, "link inválido")
    contenido = await file.read()
    nombre = f"{uuid.uuid4().hex}_{file.filename or 'archivo'}"
    ruta = Path(settings.UPLOADS_DIR) / nombre
    ruta.write_bytes(contenido)
    nombre_archivo = file.filename or nombre
    store.agregar_adjunto(p["id"], "archivo", nombre_archivo, str(ruta), pregunta_id=pregunta_id)
    respuesta = {"ok": True, "nombre_archivo": nombre_archivo}
    if pregunta_id:
        proyecto = _proyecto()
        pregunta = _pregunta_por_id(proyecto, pregunta_id)
        if pregunta and pregunta["tipo"] == "archivo":
            store.guardar_respuesta(p["id"], pregunta_id, nombre_archivo, fuente="archivo")
            p_actualizado = store.obtener(p["id"])
            ids_preguntas = {q["id"] for q in settings.preguntas_planas(proyecto, formulario_id=pregunta["formulario_id"])}
            respuesta["progreso"] = store.progreso_formulario(p_actualizado, ids_preguntas)
    return JSONResponse(respuesta)
