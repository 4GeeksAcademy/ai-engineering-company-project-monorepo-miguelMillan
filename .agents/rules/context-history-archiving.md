# Rule: context-history-archiving

## Objetivo
Evitar copias historicas redundantes de `CONTEXT.md` y conservar solo las revisiones que aportan cambios.

## Alcance
- Estado: Siempre activa.
- Aplica a: tareas que actualicen contexto o progreso en `memory-bank/`.

## Reglas
1. Al cerrar una tarea, actualiza `memory-bank/progress.md` aunque el contexto no haya cambiado.
2. Antes de archivar `CONTEXT.md`, localiza la copia historica mas reciente correspondiente al mismo contexto activo y comparala byte por byte con `cmp -s`.
3. Si ambas copias son identicas, no crees otro archivo historico. Deja constancia en `progress.md` de que el contexto no cambio y que se reutiliza el archivo existente.
4. Si el contenido cambio, crea un archivo historico nuevo con nombre identificable; nunca sobrescribas una revision anterior y verifica la copia con `cmp`.
5. Un nuevo hito o tarea, por si solo, no justifica duplicar un `CONTEXT.md` sin cambios. Esta regla prevalece sobre pautas anteriores que exijan archivarlo en cada tarea aunque sea identico.

## Checklist rapido
- [ ] Se comparo el contexto activo con su archivo historico correspondiente.
- [ ] Solo se creo una copia si el contenido difiere.
- [ ] `memory-bank/progress.md` registra el resultado de la comparacion.
