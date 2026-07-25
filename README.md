# Raifen Discovery

Plataforma de relevamiento de negocio para proyectos de datalake — reemplaza el
documento estático (versionados horribles, ediciones sueltas) por un portal online que
el cliente completa a su ritmo, módulo por stakeholder, con transparencia total sobre lo
que Raifen ya sabe de su negocio.

**Sin IA en el flujo del participante.** Es un formulario tipado, no un chat: Catequil
define el banco de preguntas de cada proyecto con un tipo fijo por pregunta (texto
libre, opción única, opción múltiple, booleana) y el portal las renderiza como campos
reales. Nada decide qué preguntar ni cuándo cerrar — eso lo define Catequil de antemano,
y el participante completa a su ritmo. El único punto donde interviene un modelo es la
transcripción de audio (opcional, por pregunta de texto libre), un servicio propio de
Raifen (`transcribe.raifen.ai`) que solo convierte voz a texto — el participante revisa
y edita la transcripción antes de guardarla.

**Arquitectura: una instancia por cliente.** Cada proyecto nuevo es un deploy nuevo en
Coolify (subdominio propio, DB propia — SQLite, no hace falta Postgres porque cada
instancia es single-tenant). No es una plataforma multi-cliente compartida.

Operada por **Catequil** (`.claude/commands/agentes/catequil.md` en `Raifen_Claude_System`)
— es quien arma `config/proyecto.yaml` de cada cliente (a partir del knowledge de
`knowledge/proyectos/verticales_negocio/` y `knowledge/proyectos/templates/relevamiento_negocio/`)
y hace la curaduría de las respuestas a medida que entran.

Base técnica adaptada de `raifen-smart-blueprint` (FastAPI + SQLite) — pero para
discovery post-venta de un proyecto activo, no diagnóstico pre-venta de un prospecto, y
sin el motor conversacional de IA de Smart Blueprint (descartado a propósito, ver
diseño original — sesión de Catequil, 2026-07-24).

## Qué hace distinto de Smart Blueprint

- **Formulario tipado, no chat con IA**: preguntas con tipo fijo (texto libre / opción
  única / opción múltiple / booleana), sin motor conversacional decidiendo el flujo.
- **Multi-stakeholder**: cada persona del cliente tiene su propio link/token y completa
  su propio módulo — no es un solo formulario por empresa.
- **"Lo que ya sabemos"**: bloque de contexto previo que se le muestra al cliente de
  entrada, transparente — puede corregirlo si algo no aplica.
- **Multi-sesión**: el participante puede volver y retomar; guardado por pregunta, no
  hay que completar todo de una sentada. Catequil sigue curando durante todo el discovery.
- **Audio + adjuntos**: el stakeholder puede grabar una respuesta de voz para una
  pregunta de texto libre (se transcribe vía `transcribe.raifen.ai` y queda editable) o
  adjuntar archivos/pantallazos.
- **Curaduría manual**: el output no es un informe generado automáticamente — es una
  tabla de reglas de negocio que Catequil arma a mano a partir de las respuestas y
  confirma antes de mandarlas a aprobación formal del cliente (vía
  `/herramientas/formato-empresa`, Google Doc con formato Raifen, por correo).
- El portal es **tránsito**. El respaldo formal de cada cliente sigue viviendo en Drive
  (`protocolo_drive.md` de `Raifen_Claude_System`), no en la base de datos de esta app.

## Setup local

```bash
python -m venv .venv
python -m pip --python "$(pwd)/.venv/Scripts/python.exe" install -r requirements.txt   # Windows sin pip en venv
cp .env.example .env   # completar TRANSCRIBE_API_KEY, credenciales admin
cp config/proyecto.example.yaml config/proyecto.yaml   # editar con el cliente real
python cli.py init-db
python cli.py serve    # http://127.0.0.1:8020
```

## Deploy (Coolify, una instancia por cliente)

1. Nuevo servicio en Coolify a partir de este repo (mismo patrón que `raifen-smart-blueprint`)
2. Subdominio propio para el proyecto (ej. `<cliente>.discovery.raifen.ai`)
3. `config/proyecto.yaml` con los datos reales del cliente antes de buildear
4. Variables de entorno: `TRANSCRIBE_API_KEY`, `BASIC_AUTH_USER/PASS`,
   `N8N_MARKDOWN_WEBHOOK` (para el envío del documento formal de aprobación)
5. Volumen persistente para la DB SQLite + adjuntos (mismo patrón que `blueprint-data`)

**Estado (2026-07-24): scaffold inicial, sin deploy todavía.** Falta: probar el flujo
completo local, definir con Oscar el procedimiento repetible de deploy en Coolify (hoy
es manual, calcado del de Smart Blueprint), y decidir si Catequil necesita acceso propio
a la API de Coolify o si cada deploy lo dispara Tom/Oscar.

## Estructura

```
app/
  settings.py         — carga .env + config/proyecto.yaml (incluye preguntas_planas())
  store.py            — SQLite: participantes, respuestas por pregunta, adjuntos, reglas de negocio
  transcribe.py       — cliente de transcribe.raifen.ai (audio -> texto, por pregunta)
  formato_empresa.py  — envío del documento de aprobación vía webhook n8n markdown-raifen
  render.py           — arma el markdown consolidado de reglas de negocio para aprobación
  api.py              — rutas FastAPI (admin con basic auth + público por token)
  templates/          — UI (home admin, formulario del participante, curaduría)
config/
  proyecto.example.yaml — plantilla: cliente, vertical, "lo que ya sabemos", banco de
                           preguntas tipadas por tema (genérico + vertical, ya fusionado),
                           participantes
```
