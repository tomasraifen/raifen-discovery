"""Arma el markdown del documento de discovery -- para revision interna de Raifen (via
formato_empresa.py). Regla dura: este documento NUNCA se manda directo al cliente --
llega siempre a un correo de Raifen para revision humana, y recien despues se reenvia al
cliente a mano si corresponde.

Las secciones a incluir son configurables (Tom decide que mandar cada vez, no siempre
todo) -- ver Fase 4/enviar_aprobacion en api.py."""

SECCIONES_VALIDAS = {"lo_que_sabemos", "stakeholders", "participantes", "reglas", "progreso"}


def documento_aprobacion(
    proyecto: dict,
    reglas: list[dict],
    participantes_por_id: dict[str, dict],
    *,
    secciones: set[str],
    stakeholders: list[dict] | None = None,
    participantes_por_formulario: list[dict] | None = None,
    resumen_temas: list[dict] | None = None,
) -> str:
    lineas: list[str] = []

    if "lo_que_sabemos" in secciones and (proyecto.get("lo_que_ya_sabemos") or "").strip():
        lineas += ["# Lo que ya sabemos del negocio", "", proyecto["lo_que_ya_sabemos"].strip()]

    if "stakeholders" in secciones and stakeholders:
        # Matriz de PERSONAS unica -- una por persona, sin importar a cuantos
        # formularios este asignada (eso va en "Participantes por formulario"). Incluye
        # el link personal de cada uno -- pedido explicito de Tom, para poder copiar la
        # tabla del documento y mandarle a cada quien su link sin volver al panel.
        lineas += ["", "# Stakeholders", "", "| Nombre | Cargo | Correo | Link personal |", "|---|---|---|---|"]
        for p in stakeholders:
            lineas.append(f"| {p['nombre']} | {p.get('cargo') or '—'} | {p.get('email') or '—'} | {p.get('link') or '—'} |")

    if "participantes" in secciones and participantes_por_formulario:
        # Una fila por asignacion a formulario -- puede repetir a la misma persona si
        # completa mas de uno. Complementa a "Stakeholders", no lo reemplaza.
        lineas += ["", "# Participantes por formulario", "", "| Nombre | Cargo | Correo | Formulario | Estado |", "|---|---|---|---|---|"]
        for p in participantes_por_formulario:
            lineas.append(
                f"| {p['nombre']} | {p.get('cargo') or '—'} | {p.get('email') or '—'} | "
                f"{p.get('formulario_nombre') or '—'} | {p['estado']} |"
            )

    if "progreso" in secciones and resumen_temas:
        lineas += ["", "# Progreso de respuestas por área", "", "| Área | Cobertura |", "|---|---|"]
        for t in resumen_temas:
            lineas.append(f"| {t['titulo']} | {t['cubiertas']}/{t['total']} |")

    if "reglas" in secciones:
        confirmadas = [r for r in reglas if r["estado"] in ("confirmada", "entregada_oscar", "validada_consume")]
        lineas += ["", "# Reglas de negocio confirmadas", "", "| # | Regla | Confirmada por | Estado |", "|---|---|---|---|"]
        for i, r in enumerate(confirmadas, start=1):
            participante = participantes_por_id.get(r.get("participante_id") or "", {})
            fuente = participante.get("nombre", "—")
            lineas.append(f"| {i} | {r['texto']} | {fuente} | {r['estado']} |")
        if not confirmadas:
            lineas.append("| — | *Sin reglas confirmadas todavía* | — | — |")

    return "\n".join(lineas)
