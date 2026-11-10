# Informe de correcciones Lighthouse

## Método

Lighthouse CLI 13.6.0 con Chrome for Testing 156, perfil móvil predeterminado y tres navegaciones por URL para la comparación final. Antes y después se auditaron las mismas builds locales de producción:

- Website: `http://127.0.0.1:4173/`
- Backoffice: `http://127.0.0.1:4174/login`

Los valores son medianas con rango entre tres corridas; las puntuaciones de laboratorio no son datos de campo. INP no se obtiene de estas navegaciones sin interacción. TBT se presenta por separado y no equivale a INP.

## Comparativa principal

| Frontend | Performance antes → después | Accessibility | Best Practices | SEO antes → después | TTFB antes → después | LCP antes → después | CLS antes → después | TBT antes → después |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Website `/` | 99 (83–100) → 99 (97–99) | 100 → 100 | 100 → 100 | 92 → 100 | 12 ms (6–14) → 10 ms (6–14) | 1,544 ms (1,533–1,546) → 1,563 ms (1,561–1,567) | 0,000929 → 0,000932 | 14,5 ms (8,5–646) → 16,5 ms (8,5–162) |
| Backoffice `/login` | 99 (97–99) → 100 (100–100) | 100 → 100 | 100 → 100 | 82 → 63* | 8 ms (5–16) → 1 ms (1–6) | 1,550 ms (1,543–1,673) → 1,538 ms (1,532–1,544) | 0 → 0 | 65 ms (46–192) → 12 ms (5–23) |

`*` El backoffice es una herramienta interna. Al corregir su `robots.txt` se configuró `Disallow: /`, y Lighthouse marca `is-crawlable` como fallo. Es la consecuencia deliberada de no pedir indexación de rutas internas. La meta description faltante se corrigió y su auditoría pasa; no se recomienda permitir indexación solo para elevar el score SEO.

La diferencia de Performance del backoffice (+1 punto) no se atribuye al refactor: este afecta estados de error de otras vistas, no la ruta `/login`, y las puntuaciones de laboratorio tienen variación. LCP, CLS y TTFB permanecen bajos/estables en ambos frontends. No se encontró una corrección de rendimiento obligatoria respaldada por la línea base local.

## Puntuaciones de las capturas

Estas cifras transcriben las cuatro capturas de `audit/before/` y las cuatro de `audit/after/`. Son ejecuciones individuales tomadas mediante URLs reenviadas `*.app.github.dev`, no las medianas de tres corridas locales de la tabla anterior.

| Aplicación / dispositivo | Performance antes → después | Accessibility | Best Practices | SEO antes → después | Capturas |
| --- | ---: | ---: | ---: | ---: | --- |
| Website / móvil | 56 → 99 | 100 → 100 | 100 → 100 | 58 → 66 | [antes](audit/before/LighthouseAntes.png) · [después](audit/after/LighthouseDespuesMovil.png) |
| Back office / móvil | 55 → 55 | 100 → 100 | 100 → 100 | 45 → 63 | [antes](audit/before/LighthouseBOantes.png) · [después](audit/after/LighthouseDespuesBOmovil.png) |
| Website / escritorio | 68 → 100 | 100 → 100 | 100 → 100 | 58 → 66 | [antes](audit/before/LighthouseAntesOrdenador.png) · [después](audit/after/LighthouseDespuesOrdenador.png) |
| Back office / escritorio | 56 → 56 | 100 → 100 | 100 → 100 | 45 → 63 | [antes](audit/before/LighthouseBOantesordenador.png) · [después](audit/after/LighthouseDespuesBOOrdenador.png) |

La tabla de capturas muestra los resultados que se ven en pantalla, pero no es una comparación experimental idéntica: los dominios reenviados y las condiciones de ejecución difieren de los previews locales de la comparación principal. Por ello, no se atribuyen automáticamente sus cambios a un commit ni se mezclan ambos conjuntos de cifras.

## Cambios por commit

### `156754d` — servir robots válidos

- Website: añadido `uis/website/public/robots.txt` con `Allow: /`. Se corrigió el fallback que devolvía HTML en `/robots.txt`; Lighthouse confirma `robots-txt` válido. SEO pasó de 92 a 100 (+8 puntos), Performance permaneció en 99 en la comparación final.
- Back office: añadido `uis/backoffice/public/robots.txt` con `Disallow: /` para señalar que la herramienta interna no debe indexarse. `robots-txt` pasa; Lighthouse informa `is-crawlable` no aprobado. En la medición local controlada, SEO pasó de 82 a 54 inmediatamente después de esta política, una caída deliberada para reducir la exposición en buscadores por privacidad. Las capturas adjuntas muestran 45 antes y 63 después; no son la misma corrida local.

### `2a70b79` — añadir meta description del backoffice

- Añadida una descripción para la pantalla de acceso en `uis/backoffice/index.html`.
- Lighthouse cambió de SEO 54 a 63 (+9 puntos); `meta-description` pasa. `is-crawlable` sigue fallando por la política `Disallow: /`.

### `4b983ed` — reutilizar estado accesible de error

- Extraído `AsyncErrorState` y aplicado en inventario, directorio de proveedores y resumen de incidencias. Mantiene `role="alert"`, acción Reintentar, estado disabled y clases de acción configurables.
- La build pasa. No se atribuye mejora del Lighthouse al refactor; su objetivo es reducir duplicación y mantener coherentes accesibilidad/recuperación.

## Valoración

La mayor mejora SEO medible en la comparación local fue el `robots.txt` del website (+8 puntos). La descripción añadió otros 9 puntos al login del back office, pero la política de no indexación para una aplicación interna mantiene SEO en 63 en vez de permitir rastreo para perseguir 100. Esa puntuación menor es una decisión de privacidad/seguridad operativa, no un fallo que se deba corregir habilitando indexación. `robots.txt` no protege datos ni sustituye la autenticación; el acceso al back office debe seguir protegido por el login. Ningún KPI principal evidencia una regresión tras los cambios; las variaciones de milisegundos están dentro de la variabilidad observada.

## Evidencia y validaciones

- Las cuatro capturas anteriores y las cuatro posteriores están enlazadas en la tabla de puntuaciones. No se incluyen informes HTML/JSON en esas carpetas.
- `npm --prefix uis/website run build`: correcto.
- `npm --prefix uis/backoffice run build`: correcto tras robots, meta description y refactor.
- `npm --prefix uis/backoffice run lint`: termina con una advertencia preexistente `react(set-state-in-effect)` en `src/app/account/AccountProfile.tsx`; no afecta los archivos del refactor.
- Lighthouse Accessibility y Best Practices: 100 en las medianas antes y después.
