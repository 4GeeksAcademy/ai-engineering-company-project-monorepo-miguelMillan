# Auditoría de rendimiento y duplicación

## Alcance y condiciones

Se revisaron `uis/website` y `uis/backoffice` (React/Vite) y se consultaron las skills instaladas `core-web-vitals`, `performance` y `web-perf`.

Comparación principal: Lighthouse CLI 13.6.0, Chrome for Testing 156, perfil móvil predeterminado, build local de producción, tres navegaciones por URL. Se mantuvieron exactamente las mismas URLs antes/después:

- Website: `http://127.0.0.1:4173/`
- Backoffice: `http://127.0.0.1:4174/login`

CrUX no proporciona datos para estas URLs locales. Lighthouse en una navegación sin interacción no mide INP; TBT se registra como métrica de laboratorio distinta, no como sustituto de INP.

Las capturas anteriores aportadas desde los dominios reenviados `*.app.github.dev` corresponden a Vite/túnel y a otro estado del backoffice. Muestran puntuaciones móvil website `56/100/100/58` y backoffice `55/100/100/45`, con LCP de `18,4 s` y `46,3 s`; las de escritorio muestran website `68/100/100/58` y backoffice `56/100/100/45`. Lighthouse CLI desde Codespaces redirige esos dominios al inicio de sesión del túnel, así que no se pudo repetir allí la misma sesión/URL. Esas capturas se conservan, pero no se mezclan con la comparación local controlada ni se atribuye su latencia a una causa de código.

## Línea base Lighthouse

Mediana y rango de tres corridas antes de las correcciones:

| Frontend y URL | Performance | Accessibility | Best Practices | SEO | TTFB | LCP | CLS | INP | TBT |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |
| Website `/` | 99 (83–100) | 100 | 100 | 92 | 12 ms (6–14) | 1,544 ms (1,533–1,546) | 0,000929 | No medido | 14,5 ms (8,5–646) |
| Backoffice `/login` | 99 (97–99) | 100 | 100 | 82 | 8 ms (5–16) | 1,550 ms (1,543–1,673) | 0 | No medido | 65 ms (46–192) |

En la línea base de builds locales, TTFB, LCP y CLS cumplen los umbrales de laboratorio de las skills. La variación de Performance coincide con variación de TBT en corridas aisladas; no había evidencia para una optimización de rendimiento dirigida antes de corregir SEO. No se reclama una mejora de campo.

## Hallazgos SEO y causa raíz

### `robots.txt` devolvía HTML en ambos frontends

Lighthouse encontró `robots-txt` inválido en website y backoffice. No existía un archivo estático `robots.txt`; Vite aplicaba el fallback SPA y respondía `200 text/html` con el contenido de `index.html` para `/robots.txt`. Se confirmó con `curl` en ambos previews.

### Meta description ausente en el acceso al backoffice

`uis/backoffice/index.html` no tenía `<meta name="description">`; Lighthouse lo reportó en `/login`. El documento de entrada es compartido por las rutas del SPA, así que la corrección se aplica allí.

### Indexación del backoffice

El backoffice es una herramienta interna; `uis/backoffice/public/robots.txt` usa `Disallow: /` para no pedir a los buscadores que indexen sus rutas. Lighthouse reporta `is-crawlable` como fallo y reduce la puntuación SEO. Es una consecuencia intencional de privacidad/descubribilidad, no un fallo del archivo robots ni un problema de autenticación. Robots no sustituye al control de acceso.

## Hallazgos de código

### 1. Ciclo de carga, cancelación y reintento repetido

**Dónde aparece:**

- `uis/backoffice/src/app/inventory/InventoryPages.tsx`: `useProducts`.
- `uis/backoffice/src/app/suppliers/SupplierDirectory.tsx`: carga filtrada del directorio.
- `uis/backoffice/src/app/incidents/IncidentDirectory.tsx`: carga del listado.
- `uis/backoffice/src/app/incidents/IncidentSummaryPanel.tsx`: carga del resumen.
- `uis/backoffice/src/app/account/AccountProfile.tsx`: carga del perfil.

**Por qué es candidato:** cada módulo crea un `AbortController`, inicia una petición en `useEffect`, evita actualizar después de cancelar y administra `loading`, `error` y refresco/reintento. Los detalles de endpoint y filtros cambian, pero la orquestación asíncrona se repite y una corrección futura debe conservarse en varios sitios.

**Abstracción propuesta:** un hook tipado `useAsyncResource<T>` que reciba una función de carga, dependencias y política de refresco; que centralice cancelación, resultado/error y `retry`. Los llamadores conservarían filtros, mensajes de dominio y reglas para retener o limpiar resultados previos. Antes de extraerlo hay que comparar las diferencias de estado entre listado, resumen y perfil para no cambiar su comportamiento.

### 2. Estado de error con acción de reintento repetido

**Dónde aparece:**

- `uis/backoffice/src/app/inventory/InventoryPages.tsx`: componente local `LoadError`.
- `uis/backoffice/src/app/suppliers/SupplierDirectory.tsx`: bloque `role="alert"` y botón Actualizar/Reintentar.
- `uis/backoffice/src/app/incidents/IncidentDirectory.tsx` y `IncidentSummaryPanel.tsx`: mensaje de error y botón Reintentar.
- `uis/backoffice/src/App.tsx`: error de sesión con acción Reintentar.

**Por qué es candidato:** se repiten el anuncio accesible del error, el espaciado y un control para recuperar la vista; parte del marcado y estilos ya diverge entre pantallas. La duplicación puede hacer que estados equivalentes tengan accesibilidad o interacción inconsistentes.

**Abstracción propuesta:** un componente `AsyncErrorState` con `message`, `onRetry`, `retryLabel` y `busy`, permitiendo composición/estilo de variante donde la pantalla lo necesite. Mantener errores por fila y errores de validación de formularios fuera de este componente.

### 3. Campo de formulario con error accesible

**Dónde aparece:**

- `uis/website/src/components/ContactForm.tsx`: campos controlados con `aria-invalid` y `aria-describedby`.
- `uis/backoffice/src/components/AuthPage.tsx`: campos de acceso y errores por campo.
- `uis/backoffice/src/app/incidents/IncidentRegistration.tsx`: campos con IDs de error y `role="alert"`.

**Por qué es candidato:** la asociación label/control/mensaje y el estado inválido se implementan campo por campo. Una futura modificación debe mantener sincronizados el ID, `aria-invalid` y `aria-describedby`.

**Abstracción propuesta:** un `FormField` pequeño por aplicación, que acepte `id`, `label`, `error` y un control como `children`, genere el ID de ayuda y aplique `aria-invalid`/`aria-describedby`. Los formularios tienen estilos y reglas de negocio distintos, así que no se recomienda un paquete visual compartido entre ambas apps hasta demostrar que sus contratos coinciden.

## Evidencia

- Informes Lighthouse de línea base: `audit/before/*-prod-run-*.report.{html,json}`.
- Informes posteriores a cada commit: `audit/after/*-run-*.report.{html,json}`.
- Capturas comparables de resumen Lighthouse: `audit/before/website-lighthouse-baseline.png`, `audit/before/backoffice-lighthouse-baseline.png`, `audit/after/website-lighthouse-after.png` y `audit/after/backoffice-lighthouse-after.png`.
- Se conservan además las capturas reenviadas originales, identificadas aparte por sus nombres `Lighthouse*.png`.
