"""Arma el markdown consolidado de reglas de negocio confirmadas -- para revision
interna de Raifen (via formato_empresa.py). Solo incluye lo que Catequil ya curo y marco
como 'confirmada'. Regla dura: este documento NUNCA se manda directo al cliente -- llega
siempre a un correo de Raifen para revision humana, y recien despues se reenvia al
cliente a mano si corresponde."""
from datetime import datetime, timezone


def documento_aprobacion(proyecto: dict, reglas: list[dict], participantes_por_id: dict[str, dict]) -> str:
    confirmadas = [r for r in reglas if r["estado"] in ("confirmada", "entregada_oscar", "validada_consume")]
    fecha = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    correo_cliente = proyecto.get("correo_aprobacion", "(sin definir en proyecto.yaml)")

    lineas = [
        f"# Reglas de Negocio — {proyecto['cliente']} | {fecha}",
        "",
        f"⚠️ **Borrador para revisión interna — todavía NO se envió al cliente.** "
        f"Destinatario final previsto (una vez aprobado acá): `{correo_cliente}`.",
        "",
        f"Documento de reglas de negocio relevadas durante el discovery del proyecto "
        f"**{proyecto.get('proyecto', proyecto['cliente'])}**.",
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
        f"*Revisar esta lista. Si está lista para el cliente, reenviarla manualmente a "
        f"`{correo_cliente}` — este documento no sale de Raifen automáticamente.*",
    ]
    return "\n".join(lineas)
