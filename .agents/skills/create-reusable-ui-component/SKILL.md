# Skill: create-reusable-ui-component

## Objetivo unico
Crear un componente de UI reutilizable en React, alineado a la identidad visual y reglas de naming del proyecto.

## Cuándo usar esta skill
- Cuando se necesite construir un bloque reutilizable para `uis/website` o `uis/backoffice`.
- Cuando un componente deba ser compartido por multiples vistas dentro de una misma app.

## Inputs requeridos
1. `componentName`: nombre en PascalCase (ejemplo: `MetricCard`).
2. `componentType`: tipo de componente (`presentational` o `container`).
3. `targetPath`: ruta destino dentro del proyecto UI.
4. `propsSpec`: lista de props con tipo y descripcion.
5. `visualVariant` (opcional): variante visual esperada (`default`, `compact`, `highlight`, etc.).

## Proceso sugerido
1. Validar que `componentName` cumple PascalCase.
2. Crear archivo `ComponentName.tsx` en `targetPath`.
3. Implementar tipado de props con TypeScript.
4. Aplicar estilo coherente con el sistema visual (Tailwind o CSS Module aprobado).
5. Exportar componente y documentar uso basico.
6. Agregar prueba unitaria minima o test de render cuando el stack de testing exista.

## Criterios de aceptacion (verificables)
- [ ] El componente compila y renderiza sin errores en el navegador.
- [ ] El archivo y la funcion del componente usan PascalCase.
- [ ] Incluye tipado de props y JSDoc basico para el componente.
- [ ] Incluye ejemplo de uso en el mismo archivo o documentacion cercana.
- [ ] Si existe setup de tests, incluye al menos una prueba basica de render.

## Output esperado
- Componente funcional y reutilizable.
- Tipos/props documentados.
- Evidencia de validacion local (build o dev server sin errores).
