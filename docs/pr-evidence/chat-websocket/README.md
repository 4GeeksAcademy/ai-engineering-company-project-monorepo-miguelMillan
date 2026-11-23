# Evidencia: chat por WebSocket con el agente real

Ejecución real del 2026-11-23 de `scripts/chat_live_smoke.cjs`: Supabase, Qdrant, Ollama (`qwen2.5:1.5b`), el build de la UI y un proxy TCP para cortar la red.

| Paso | Resultado |
|---|---|
| Streaming | 91 `token_chunk` en 14,7 s; 88 longitudes distintas vistas en pantalla |
| Interrumpir a mitad | `generation_interrupted` 86 ms después del clic. Ningún token de la original después. El parcial («¡Hola! Gracias por tu pregunta. Aquí») sigue visible y marcado |
| El modelo se detiene | Ollama cierra esa petición a los 42,9 s, en el instante de la interrupción, con 36 caracteres generados |
| Respuesta redirigida | Mensaje nuevo, sobre devoluciones, que es lo que pedía `new_input` |
| Red cortada a mitad | 4 intentos con backoff; la conversación sigue visible. Al volver, la respuesta continúa (de 19 a 552 caracteres) sin repetir y sin recargar |
| Recargar | El hilo completo, con la interrumpida marcada |
| API muerto (`kill -9`) a mitad | El parcial (121 caracteres) se conserva como interrumpido y la conversación sigue en la misma sesión |
| JWT | En ninguna de las URL ni en los logs |

- `verificacion.json`: cada comprobación y su instante. `frames.json`: muestra de frames reales alrededor de la interrupción.
- `01` a `05`: capturas.

## Interrupción medida en el modelo

Con `npm run dev`, el grafo real y un modelo simulado que cuenta lo que produce (400 tokens previstos). Cuatro ejecuciones:

| | Resultado |
|---|---|
| Del clic a `generation_interrupted` | 50 a 57 ms |
| Tokens de la original después | 0 |
| El modelo | Petición cerrada tras 55 y 81 tokens de 400: uno más que los mostrados |
| Parcial | Intacto y marcado; la respuesta nueva, sobre devoluciones, es otro mensaje |
| Recargar | El mismo hilo |

El primer token tarda entre 20 y 60 s: es el modelo en CPU leyendo el prompt, igual que antes. Después fluye.
