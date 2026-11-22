Ticket — Generación y evaluación de respuestas a RFP

Contexto: La Parte 1 nos dice qué hay que responder en cada RFP, pero armar el borrador de la propuesta económica sigue siendo manual y lento. Necesito que el sistema genere un primer borrador por departamento y que ese borrador se autoevalúe antes de llegar a un humano.

Qué necesito que construyas:

Un agente generador por departamento que reciba los metadatos y el resumen producidos en la Parte 1, y redacte la sección correspondiente de la propuesta económica.
Varios agentes evaluadores corriendo en paralelo sobre cada sección generada: legibilidad (de nuevo, py-readability-metrics te sirve para esto), pertinencia respecto a lo que pide la RFP, y cumplimiento de nuestros lineamientos de empresa.
Si una sección falla la evaluación, que vuelva al generador correspondiente con feedback concreto sobre qué corregir — no que se quede atascada ni que se descarte el ticket completo.
Un límite de iteraciones para ese ciclo generador-evaluador, para que no se repita indefinidamente si un generador no logra pasar la evaluación. Si una sección agota ese límite sin pasar, conserva el último borrador y su resultado de evaluación, márcala needs_human_review, e inclúyela igual en el handoff a la Parte 3 — nunca descartes el ticket completo.
Opcional: si ya tienes montada la base de conocimiento semántica de la empresa, dale acceso al generador — que redacte con nuestras políticas y tono reales en lugar de improvisarlos ayuda mucho a que pase la evaluación de cumplimiento a la primera. No es indispensable para esta parte, pero si la tienes disponible, úsala.
Acceptance criteria: El handoff hacia la Parte 3 debe incluir, por cada departamento, tanto el contenido generado como un resultado de evaluación estructurado (ver schema abajo). Las secciones que agotaron el límite de iteraciones van con needs_human_review — no se eliminan.

— Tu tech lead

Parte del flujo de clasificación y enrutamiento que construiste en la Parte 1 — no lo reescribas desde cero.
Consume el handoff de enrutamiento de la Parte 1 — la Parte 2 debe arrancar desde tickets que la Parte 1 marcó listos (estado analisis_completo más el flag de cola / campo DB / contrato de handoff que definiste). Entrada = ticket_id + payload del synthesizer (key_aspects / estructura de workstreams). No vuelvas a parsear el PDF ni inventes un resumen paralelo que ignore ese contrato.
Instala cualquier dependencia nueva con uv add.
Revisa nuevamente RFP-INTAKE-CONTEXT-2026-11-22.md: allí están los lineamientos concretos contra los que deben validar tus agentes evaluadores.