Optimización de rendimiento: Caching
La plataforma de tu empresa está creciendo. Lo que antes gestionaba un puñado de peticiones al día ahora soporta carga real: más usuarios, más llamadas a la API, interacciones de UI más complejas. El tech lead ha detectado un patrón recurrente en la telemetría: algunos endpoints reciben decenas de peticiones por minuto con consultas idénticas, y algunos componentes se están re-renderizando mucho más de lo necesario.

El encargo es claro: antes del próximo sprint de funcionalidades, el equipo debe analizar la aplicación existente, identificar las oportunidades de caching con mayor impacto y aplicarlas. No se trata de cachear todo — sino de tomar decisiones deliberadas y justificadas sobre qué cachear, dónde hacerlo y durante cuánto tiempo.

Trabajarás directamente sobre el monorepo de tu proyecto transversal. El resultado no es solo código funcional: es un informe técnico estructurado que explica tu razonamiento. Las decisiones de ingeniería sin documentación son solo intuiciones — el equipo necesita entender por qué se tomó cada decisión.

1. Middleware de timing (sin dependencias)
Añade esto a tu main.py:

import time
import logging
from fastapi import Request

logger = logging.getLogger("api.timing")

@app.middleware("http")
async def timing_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration = (time.perf_counter() - start) * 1000  # ms

    logger.info(
        f"{request.method} {request.url.path} → {response.status_code} | {duration:.1f}ms"
    )
    return response

Obtienes una línea de log por petición:

GET /users → 200 | 312.4ms
POST /orders → 201 | 48.2ms
GET /products → 200 | 891.7ms  ← 🐢
¿Qué buscar en los logs?

Señal en el log	Pregunta que responde	¿Candidato a caché?
Latencia alta (>100–200 ms de forma consistente)	¿Cuánto cuesta la operación? (eje coste)	Sí, si se repite
Misma ruta muchas veces en poco tiempo	¿Con qué frecuencia se llama? (eje frecuencia)	Sí, si la respuesta es la misma
Mismo path + mismo status + tiempos similares en lecturas	¿Los datos subyacentes cambian poco? (eje estabilidad)	Sí, con TTL acorde
Un endpoint que aparece lento y se invoca en ráfagas con los mismos parámetros (p. ej. GET /products?category=electronics) es un candidato fuerte. Un POST que escribe datos o un GET con respuesta distinta por usuario no lo es — o solo con clave de caché acotada al usuario.

Complementa con tráfico real:

Navega tu frontend o lanza peticiones repetidas (misma URL, mismos query params).
Ordena mentalmente los logs: los paths con más líneas y mayor ms van primero en tu lista de candidatos.
Cruza con el checklist del informe: documenta en CACHING_REPORT.md el tiempo medido antes de cachear y el estimado después.
😉 Carga realista en la base de datos:

Con pocos registros, casi todo el API responde rápido y los logs de timing no revelan dónde el caching aporta valor. Antes de fiarte del middleware, aumenta el volumen en las tablas que alimentan tus lecturas más pesadas (catálogo, pedidos, usuarios con relaciones, etc.).

Pide a tu agente de código un seeder (script de tu stack: Alembic, seed de Prisma, comando de gestión en Django, etc.) o un script SQL que inserte muchos registros; revísalo y ejecútalo en local.
Prioriza la calidad de los datos, no solo la cantidad: nombres, categorías, fechas, precios y claves foráneas variadas y coherentes para que filtros, joins, ordenaciones y agregaciones cuesten trabajo de verdad — no quinientas filas idénticas "test".
Vuelve a ejecutar el middleware de timing tras el seed. En CACHING_REPORT.md, indica volumen aproximado de filas antes y después y cómo cambió la latencia en los endpoints elegidos.
En el frontend:

React DevTools → Profiler: componentes que se re-renderizan sin cambio real de props son candidatos a useMemo o a dividir estado.
Lazy Loading: rutas o modales que no se usan en la carga inicial pero pesan en el bundle (pestaña Network: JS grande que solo se pide al entrar en esa vista).
⚠️ No implementes caché en todo lo lento: primero mide, luego prioriza los casos donde coste × frecuencia × estabilidad justifican el intercambio frescura/rendimiento.