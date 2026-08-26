# AGENTS Guide

## Archivos obligatorios a leer al iniciar cada sesion
El agente debe cargar estos archivos antes de proponer cambios:
1. `memory-bank/projectbrief.md`
2. `memory-bank/techContext.md`
3. `memory-bank/progress.md`

## Flujo obligatorio antes de cada commit
1. Revisar cambios locales con `git status` y `git diff --stat` + `git diff`.
2. Ejecutar validaciones tecnicas de los proyectos modificados (por ejemplo: `npm run typecheck`, `npm run build`, `npm test` donde exista).
3. Validar reglas de `.agents/rules/` con checklist:
   - Naming y estructura segun regla activa.
   - Separacion de layouts entre website/backoffice.
   - Contenido alineado al contexto de TrackFlow.
4. Actualizar `memory-bank/progress.md` con lo completado, estado actual y bloqueos.
5. Confirmar que no se tocaron rutas protegidas sin permiso.
6. Anadir archivos (`git add ...`) y crear commit descriptivo.

## Rutas protegidas: no modificar sin confirmacion explicita del desarrollador
- `.agents/`
- `memory-bank/`
- `.env`
- `.env.*`
- `node_modules/`
- Cualquier secreto o credencial en archivos de configuracion (`*.pem`, `*.key`, `*.p12`, `*.crt`)
- Configuraciones sensibles de infraestructura (`infra/`), salvo indicacion explicita.

## Notas de operacion
- Evitar cambios no solicitados en archivos fuera del alcance del ticket.
- Si una regla entra en conflicto con el requerimiento del desarrollador, priorizar el requerimiento explicito y dejar registro en `memory-bank/progress.md`.
