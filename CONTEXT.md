Necesitamos desacoplar las operaciones pesadas de la API. Cuando un endpoint reciba una solicitud que implique trabajo de larga duración, debe encolar la tarea y devolver inmediatamente un task_id al cliente. Un worker independiente procesa la cola. El cliente puede consultar el estado de su tarea en cualquier momento.

Criterios de aceptación:

Redis corre como broker en Docker. La API y los workers se conectan al mismo broker.
Al menos una operación existente en tu API —la que más tarde en responder— se convierte en tarea asíncrona mediante Celery.
El endpoint que recibe la solicitud devuelve 202 Accepted con un task_id inmediatamente, sin esperar a que la tarea termine.
Existe un endpoint GET /tasks/{task_id} que devuelve el estado actual de la tarea (pending, started, success, failure) y el resultado cuando esté disponible.
Si una tarea falla hasta tres veces, pasa a una Dead Letter Queue (DLQ). El fallo queda registrado con task_id, número de intento y mensaje de error.
Los workers corren como procesos separados, no dentro del proceso de FastAPI.
Flower está levantado y accesible para monitorear la cola durante el desarrollo.


Añade Redis a tu docker-compose.yml como servicio broker. Usa la imagen oficial y exponla en el puerto estándar.
Instala las dependencias necesarias con uv add celery redis flower.
Crea el módulo Celery en tu monorepo (services/celery_app.py o similar) y configúralo para apuntar a Redis como broker y backend de resultados.
Identifica en tu API el endpoint o la operación que más tarda en responder — ese es el candidato a convertir en tarea asíncrona.
Levanta un worker con celery -A services.celery_app worker y verifica que se conecta al broker antes de modificar ningún endpoint.