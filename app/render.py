"""Arma el markdown consolidado de reglas de negocio confirmadas, para mandar a
aprobacion formal del cliente (via formato_empresa.py). Solo incluye lo que Catequil ya
curo y marco como 'confirmada' -- nunca manda borradores sin revisar."""
from datetime import datetime, timezone


def documento_aprobacion(proyecto: dict, reglas: list[dict], participantes_por_id: dict[str, dict]) -> str:
    confirmadas = [r for r in reglas if r["estado"] in ("confirmada", "entregada_oscar", "validada_consume")]
    fecha = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    lineas = [
        f"# Reglas de Negocio — {proyecto['cliente']} | {fecha}",
        "",
        f"Documento de reglas de negocio relevadas durante el discovery del proyecto "
        f"**{proyecto.get('proyecto', proyecto['cliente'])}**. Este documento se envía para "
        f"aprobación explícita antes de que el equipo de datos lo tome como insumo de modelado.",
        "",
        "## Reglas de negocio",
        "",
        "| # | Regla | Confirmada por |",
        "|---|---|---|",
    ]
    for i, r in enumerate(confirmadas, start=1):
        participante = participantes_por_id.get(r.get("participante_id") or "", {})
        fuente = participante.get("nombre", "—")
        lineas.append(f"| {i} | {r['texto']} | {fuente} |")

    if not confirmadas:
        lineas.append("| — | *Sin reglas confirmadas todavía* | — |")

    lineas += [
        "",
        "---",
        "*Por favor confirmar por este mismo correo si las reglas listadas reflejan correctamente "
        "cómo opera el negocio, o indicar los ajustes necesarios antes de que avancemos con el modelado.*",
    ]
    return "\n".join(lineas)
