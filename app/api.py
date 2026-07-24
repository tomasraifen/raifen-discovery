"""Raifen Discovery -- FastAPI.

Flujo: Catequil siembra los participantes del proyecto (config/proyecto.yaml) -> cada
uno recibe su link /r/<token> -> completa su módulo (chat adaptativo, con opción de
grabar audio o adjuntar archivos) -> al cerrar se genera un MD crudo por participante ->
Catequil hace la curaduría (marca reglas de negocio confirmadas) -> se envía el
documento formal de aprobación al cliente vía el webhook markdown-raifen."""
import json
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from . import formato_empresa, interview, render, settings, store, transcribe
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


def _total_temas(proyecto: dict) -> int:
    return len(proyecto.get("preguntas", []))


# ---------- Admin ----------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    proyecto = _proyecto()
    participantes = store.listar_participantes()
    total_temas = _total_temas(proyecto)
    for p in participantes:
        conv = json.loads(store.obtener(p["id"])["conversacion_json"])
        cubiertos = len([t for t in conv if t["rol"] == "participante"])
        p["progreso"] = {"cubiertos": min(cubiertos, total_temas), "total": total_temas}
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
    conversacion = store.get_conversacion(p)
    adjuntos = store.listar_adjuntos(pid)
    link = f"{settings.PUBLIC_BASE_URL}/r/{p['token']}"
    return templates.TemplateResponse(
        request, "admin_participante.html",
        {"p": p, "proyecto": proyecto, "conversacion": conversacion, "adjuntos": adjuntos, "link": link},
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

def _preparar_apertura(p: dict, proyecto: dict) -> tuple[dict, list[dict]]:
    conversacion = store.get_conversacion(p)
    if p["estado"] == "pendiente":
        primer_mensaje = interview.iniciar(proyecto, p)
        conversacion = [{"rol": "agente", "texto": primer_mensaje}]
        store.append_turno(p["id"], conversacion)
        store.marcar_en_progreso(p["id"])
        p = store.obtener(p["id"])
    return p, conversacion


@app.get("/r/{token}", response_class=HTMLResponse)
def relevar(request: Request, token: str):
    p = store.obtener_por_token(token)
    if not p:
        raise HTTPException(404, "Este link no es válido.")
    proyecto = _proyecto()
    p, conversacion = _preparar_apertura(p, proyecto)
    return templates.TemplateResponse(
        request, "participante.html",
        {"p": p, "proyecto": proyecto, "conversacion": conversacion, "cerrado": p["estado"] == "completado"},
    )


async def _procesar_mensaje(p: dict, proyecto: dict, texto: str) -> dict:
    conversacion = store.get_conversacion(p)
    conversacion.append({"rol": "participante", "texto": texto})
    total_temas = _total_temas(proyecto)

    if p["turnos"] + 1 >= store.MAX_TURNOS:
        paso = {
            "accion": "cerrar",
            "mensaje": "¡Gracias por todo lo que me contó! Con esto tenemos una base sólida — el equipo de Raifen va a seguir trabajando con esta información.",
            "temas_cubiertos": [],
        }
    else:
        try:
            paso = interview.siguiente_paso(proyecto, p, conversacion)
        except Exception as e:
            raise HTTPException(502, f"error del agente: {e}")

    conversacion.append({"rol": "agente", "texto": paso["mensaje"]})
    progreso = {"cubiertos": min(len(paso.get("temas_cubiertos") or []), total_temas), "total": total_temas}

    cerrar_de_verdad = paso["accion"] == "cerrar" and not interview.es_invitacion_a_responder(paso["mensaje"])
    if cerrar_de_verdad:
        store.marcar_completado(p["id"], conversacion)
        return {"mensaje": paso["mensaje"], "cerrado": True, "progreso": progreso}

    store.append_turno(p["id"], conversacion)
    return {"mensaje": paso["mensaje"], "cerrado": False, "progreso": progreso}


@app.post("/r/{token}/mensaje")
async def enviar_mensaje(request: Request, token: str):
    p = store.obtener_por_token(token)
    if not p:
        raise HTTPException(404, "link inválido")
    if p["estado"] not in ("en_progreso",):
        raise HTTPException(400, "esta conversación ya terminó")
    body = await request.json()
    texto = (body.get("texto") or "").strip()
    if not texto:
        raise HTTPException(400, "mensaje vacío")
    resultado = await _procesar_mensaje(p, _proyecto(), texto)
    return JSONResponse(resultado)


@app.post("/r/{token}/mensaje-stream")
async def enviar_mensaje_stream(request: Request, token: str):
    p = store.obtener_por_token(token)
    if not p:
        raise HTTPException(404, "link inválido")
    if p["estado"] not in ("en_progreso",):
        raise HTTPException(400, "esta conversación ya terminó")
    body = await request.json()
    texto = (body.get("texto") or "").strip()
    if not texto:
        raise HTTPException(400, "mensaje vacío")

    proyecto = _proyecto()
    conversacion = store.get_conversacion(p)
    conversacion.append({"rol": "participante", "texto": texto})
    total_temas = _total_temas(proyecto)
    forzar_cierre = p["turnos"] + 1 >= store.MAX_TURNOS

    def event_stream():
        mensaje_completo = ""
        try:
            if forzar_cierre:
                mensaje_completo = "¡Gracias por todo lo que me contó! Con esto tenemos una base sólida — el equipo de Raifen va a seguir trabajando con esta información."
                yield f"data: {json.dumps({'delta': mensaje_completo})}\n\n"
                clasif = {"accion": "cerrar", "temas_cubiertos": []}
            else:
                for delta in interview.siguiente_paso_stream(proyecto, p, conversacion):
                    mensaje_completo += delta
                    yield f"data: {json.dumps({'delta': delta})}\n\n"
                mensaje_limpio = interview.limpiar_fuga_metadata(mensaje_completo)
                if mensaje_limpio != mensaje_completo:
                    mensaje_completo = mensaje_limpio
                    yield f"data: {json.dumps({'correccion': mensaje_completo})}\n\n"
                conversacion_con_msg = conversacion + [{"rol": "agente", "texto": mensaje_completo}]
                clasif = interview.clasificar_paso(proyecto, p, conversacion_con_msg)
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            return

        conversacion.append({"rol": "agente", "texto": mensaje_completo})
        progreso = {"cubiertos": min(len(clasif.get("temas_cubiertos") or []), total_temas), "total": total_temas}

        cerrar_de_verdad = clasif.get("accion") == "cerrar" and (
            forzar_cierre or not interview.es_invitacion_a_responder(mensaje_completo)
        )
        if cerrar_de_verdad:
            store.marcar_completado(p["id"], conversacion)
            yield f"data: {json.dumps({'done': True, 'cerrado': True, 'progreso': progreso})}\n\n"
        else:
            store.append_turno(p["id"], conversacion)
            yield f"data: {json.dumps({'done': True, 'cerrado': False, 'progreso': progreso})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/r/{token}/audio")
async def enviar_audio(token: str, file: UploadFile):
    p = store.obtener_por_token(token)
    if not p:
        raise HTTPException(404, "link inválido")
    if p["estado"] not in ("en_progreso",):
        raise HTTPException(400, "esta conversación ya terminó")

    audio_bytes = await file.read()
    nombre = f"{uuid.uuid4().hex}_{file.filename or 'audio.webm'}"
    ruta = Path(settings.UPLOADS_DIR) / nombre
    ruta.write_bytes(audio_bytes)
    store.agregar_adjunto(p["id"], "audio", file.filename or nombre, str(ruta), turno_indice=p["turnos"])

    texto = transcribe.transcribir(audio_bytes, file.filename or "audio.webm")
    if not texto:
        raise HTTPException(502, "no se pudo transcribir el audio -- probá escribir la respuesta")

    resultado = await _procesar_mensaje(p, _proyecto(), texto)
    resultado["transcripcion"] = texto
    return JSONResponse(resultado)


@app.post("/r/{token}/adjunto")
async def enviar_adjunto(token: str, file: UploadFile):
    """Sube un archivo/pantallazo sin que dispare un turno del agente -- queda asociado
    al turno actual como evidencia de soporte."""
    p = store.obtener_por_token(token)
    if not p:
        raise HTTPException(404, "link inválido")
    contenido = await file.read()
    nombre = f"{uuid.uuid4().hex}_{file.filename or 'archivo'}"
    ruta = Path(settings.UPLOADS_DIR) / nombre
    ruta.write_bytes(contenido)
    store.agregar_adjunto(p["id"], "archivo", file.filename or nombre, str(ruta), turno_indice=p["turnos"])
    return JSONResponse({"ok": True, "nombre_archivo": file.filename or nombre})
