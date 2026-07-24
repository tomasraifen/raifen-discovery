# Raifen Discovery

Plataforma de relevamiento de negocio para proyectos de datalake — reemplaza el
documento estático (versionados horribles, ediciones sueltas) por un portal online que
el cliente completa a su ritmo, módulo por stakeholder, con transparencia total sobre lo
que Raifen ya sabe de su negocio.

**Arquitectura: una instancia por cliente.** Cada proyecto nuevo es un deploy nuevo en
Coolify (subdominio propio, DB propia — SQLite, no hace falta Postgres porque cada
instancia es single-tenant). No es una plataforma multi-cliente compartida.

Operada por **Catequil** (`.claude/commands/agentes/catequil.md` en `Raifen_Claude_System`)
— es quien arma `config/proyecto.yaml` de cada cliente (a partir del knowledge de
`knowledge/proyectos/verticales_negocio/` y `knowledge/proyectos/templates/relevamiento_negocio/`)
y hace la curaduría de las respuestas a medida que entran.

Base técnica adaptada de `raifen-smart-blueprint` (mismo patrón: FastAPI + SQLite +
Gemini para el motor conversacional adaptativo) — pero para discovery post-venta de un
proyecto activo, no diagnóstico pre-venta de un prospecto. Ver diferencias en el diseño
original (sesión de Catequil, 2026-07-24).

## Qué hace distinto de Smart Blueprint

- **Multi-stakeholder**: cada persona del cliente tiene su propio link/token y completa
  su propio módulo — no es una sola conversación por empresa.
- **"Lo que ya sabemos"**: bloque de contexto previo que se le muestra al cliente de
  entrada, transparente — puede corregirlo si algo no aplica.
- **Multi-sesión**: no es una charla de una sola pasada. El cliente puede volver,
  retomar, y Catequil sigue completando/curando durante todo el discovery del proyecto.
- **Audio + adjuntos**: el stakeholder puede grabar una respuesta de voz (se transcribe
  vía `transcribe.raifen.ai`, servicio propio de Raifen) o adjuntar archivos/pantallazos.
- **Curaduría, no informe automático**: el output no es un PDF de venta generado por IA
  sin supervisión — es una tabla de reglas de negocio que Catequil revisa y confirma
  antes de mandarlas a aprobación formal del cliente (vía `/herramientas/formato-empresa`,
  Google Doc con formato Raifen, por correo).
- El portal es **tránsito**. El respaldo formal de cada cliente sigue viviendo en Drive
  (`protocolo_drive.md` de `Raifen_Claude_System`), no en la base de datos de esta app.

## Setup local

```bash
python -m venv .venv
python -m pip --python "$(pwd)/.venv/Scripts/python.exe" install -r requirements.txt   # Windows sin pip en venv
cp .env.example .env   # completar GEMINI_API_KEY, TRANSCRIBE_API_KEY, credenciales admin
cp config/proyecto.example.yaml config/proyecto.yaml   # editar con el cliente real
python cli.py init-db
python cli.py serve    # http://127.0.0.1:8020
```

## Deploy (Coolify, una instancia por cliente)

1. Nuevo servicio en Coolify a partir de este repo (mismo patrón que `raifen-smart-blueprint`)
2. Subdominio propio para el proyecto (ej. `<cliente>.discovery.raifen.ai`)
3. `config/proyecto.yaml` con los datos reales del cliente antes de buildear
4. Variables de entorno: `GEMINI_API_KEY`, `TRANSCRIBE_API_KEY`, `BASIC_AUTH_USER/PASS`,
   `N8N_MARKDOWN_WEBHOOK` (para el envío del documento formal de aprobación)
5. Volumen persistente para la DB SQLite + adjuntos (mismo patrón que `blueprint-data`)

**Estado (2026-07-24): scaffold inicial, sin deploy todavía.** Falta: probar el flujo
completo local, definir con Oscar el procedimiento repetible de deploy en Coolify (hoy
es manual, calcado del de Smart Blueprint), y decidir si Catequil necesita acceso propio
a la API de Coolify o si cada deploy lo dispara Tom/Oscar.

## Estructura

```
app/
  settings.py       — carga .env + config/proyecto.yaml
  store.py          — SQLite: participantes, conversaciones, adjuntos, reglas de negocio
  interview.py       — motor conversacional adaptativo (Gemini) por participante
  transcribe.py      — cliente de transcribe.raifen.ai (audio -> texto)
  formato_empresa.py — envío del documento de aprobación vía webhook n8n markdown-raifen
  render.py          — arma el markdown consolidado de reglas de negocio para aprobación
  api.py             — rutas FastAPI (admin con basic auth + público por token)
  templates/         — UI (home admin, chat del participante, curaduría)
config/
  proyecto.example.yaml — plantilla: cliente, vertical, "lo que ya sabemos", banco de
                           preguntas (genérico + vertical, ya fusionado), participantes
```
