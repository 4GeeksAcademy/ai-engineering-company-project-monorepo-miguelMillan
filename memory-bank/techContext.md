# Technical Context — TrackFlow

## Stack actual identificado en el monorepo
- Lenguaje principal para logica base: TypeScript.
- Configuracion: `tsconfig.json` en raiz con validacion estricta.
- Script disponible en raiz: `npm run typecheck`.
- Frontend: aun no existe una estructura estandar para `uis/website` y `uis/backoffice` en este repo (solo hay esqueleto y documentacion).
- Backend: carpeta `services/` existente como contenedor de APIs/workers, sin servicio base funcional aun.

## Decision de arquitectura para este hito
Para cumplir la inicializacion de interfaces y mantener separacion clara por producto:
- Crear `uis/website` como app independiente (cara publica).
- Crear `uis/backoffice` como app independiente (uso interno).
- Mantener desacople de layouts y rutas entre ambas.
- Crear `services/api` como servicio backend minimo con endpoint de salud.

## Restricciones tecnicas relevantes
- Monorepo sin orquestador global configurado (no hay turbo/nx/workspaces declarados en raiz).
- Se debe respetar la estructura por carpetas del template y documentar ejecucion por proyecto.
- Evitar modificar archivos sensibles o estructura base sin confirmacion.
- Mantener consistencia con el contexto de negocio TrackFlow (EE.UU./Espana, almacenes Los Angeles/Zaragoza, carriers, devoluciones).

## Convenciones operativas propuestas
- Cada app dentro de `uis/` debe incluir su propio `README.md` con comandos.
- Antes de commit: revisar diff, ejecutar pruebas/typecheck/build relevantes, validar reglas de agente y actualizar `memory-bank/progress.md`.
- `memory-bank/` se usa como referencia de sesion para conservar continuidad tecnica y de negocio.
