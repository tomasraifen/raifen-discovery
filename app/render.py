"""Arma el markdown del documento de discovery -- para revision interna de Raifen (via
formato_empresa.py). Regla dura: este documento NUNCA se manda directo al cliente --
llega siempre a un correo de Raifen para revision humana, y recien despues se reenvia al
cliente a mano si corresponde.

Las secciones a incluir son configurables (Tom decide que mandar cada vez, no siempre
todo) -- ver Fase 4/enviar_aprobacion en api.py."""
from datetime import datetime, timezone

SECCIONES_VALIDAS = {"lo_que_sabemos", "stakeholders", "reglas", "progreso"}


def documento_aprobacion(
    proyecto: dict,
    reglas: list[dict],
    participantes_por_id: dict[str, dict],
    *,
    secciones: set[str],
    participantes: list[dict] | None = None,
    resumen_temas: list[dict] | None = None,
) -> str:
    fecha = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    correo_cliente = proyecto.get("correo_aprobacion", "(sin definir en proyecto.yaml)")

    lineas = [
        f"# Discovery — {proyecto['cliente']} | {fecha}",
        "",
        f"⚠️ **Borrador para revisión interna — todavía NO se envió al cliente.** "
        f"Destinatario final previsto (una vez aprobado acá): `{correo_cliente}`.",
        "",
        f"Documento armado a mano por Catequil desde el proyecto "
        f"**{proyecto.get('proyecto', proyecto['cliente'])}** — incluye solo las secciones elegidas al enviar.",
    ]

    if "lo_que_sabemos" in secciones and (proyecto.get("lo_que_ya_sabemos") or "").strip():
        lineas += ["", "## Lo que ya sabemos del negocio", "", proyecto["lo_que_ya_sabemos"].strip()]

    if "stakeholders" in secciones and participantes:
        lineas += ["", "## Stakeholders", "", "| Nombre | Cargo | Correo | Formulario | Estado |", "|---|---|---|---|---|"]
        for p in participantes:
            lineas.append(
                f"| {p['nombre']} | {p.get('cargo') or '—'} | {p.get('email') or '—'} | "
                f"{p.get('formulario_nombre') or '—'} | {p['estado']} |"
            )

    if "progreso" in secciones and resumen_temas:
        lineas += ["", "## Progreso de respuestas por área", "", "| Área | Cobertura |", "|---|---|"]
        for t in resumen_temas:
            lineas.append(f"| {t['titulo']} | {t['cubiertas']}/{t['total']} |")

    if "reglas" in secciones:
        confirmadas = [r for r in reglas if r["estado"] in ("confirmada", "entregada_oscar", "validada_consume")]
        lineas += ["", "## Reglas de negocio confirmadas", "", "| # | Regla | Confirmada por | Estado |", "|---|---|---|---|"]
        for i, r in enumerate(confirmadas, start=1):
            participante = participantes_por_id.get(r.get("participante_id") or "", {})
            fuente = participante.get("nombre", "—")
            lineas.append(f"| {i} | {r['texto']} | {fuente} | {r['estado']} |")
        if not confirmadas:
            lineas.append("| — | *Sin reglas confirmadas todavía* | — | — |")

    lineas += [
        "",
        "---",
        f"*Revisar. Si está listo para el cliente, reenviar manualmente a "
        f"`{correo_cliente}` — este documento no sale de Raifen automáticamente.*",
    ]
    return "\n".join(lineas)
