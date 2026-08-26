# Rule: component-naming-convention

## Objetivo
Asegurar consistencia de nombres y estructura en componentes de UI para facilitar mantenimiento en las apps de TrackFlow.

## Alcance
- Estado: Siempre activa.
- Aplica a: `uis/website/**` y `uis/backoffice/**`.

## Reglas
1. Todo archivo de componente React debe usar PascalCase (ejemplo: `ServiceCard.tsx`).
2. Toda funcion de componente React exportada debe usar PascalCase y coincidir con el nombre del archivo.
3. Los estilos deben mantenerse en un sistema de diseno consistente:
   - Preferencia: clases utilitarias de Tailwind.
   - Si se necesita CSS dedicado, usar `*.module.css` y evitar estilos globales arbitrarios.
4. Evitar logica de negocio compleja en componentes de presentacion; extraer funciones a utilidades cuando sea necesario.

## Checklist rapido de validacion
- [ ] Nombres de componentes y archivos en PascalCase.
- [ ] No hay componentes en `kebab-case.tsx` o `snake_case.tsx`.
- [ ] Estilos en Tailwind o CSS Modules, sin mezcla desordenada.
- [ ] Componentes renderizan sin errores de compilacion.
