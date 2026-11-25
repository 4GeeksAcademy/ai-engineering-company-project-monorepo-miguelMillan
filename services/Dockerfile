FROM node:22-bookworm-slim AS node
FROM python:3.12-slim-bookworm

COPY --from=node /usr/local/bin/node /usr/local/bin/node
WORKDIR /app/services/api
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir uv==0.9.9 \
    && uv pip install --system -r /tmp/requirements.txt
COPY api/ ./
COPY api/celery_app.py ./celery_app.py
COPY api/tasks.py ./tasks.py
COPY --from=telemetry_schema event-schemas.json /app/docs/telemetry/event-schemas.json
COPY --from=scripts incidents_analysis.py /app/scripts/incidents_analysis.py
COPY --from=shared incident_validation.py /app/packages/shared/incident_validation.py

# Usuario sin privilegios. El volumen nuevo hereda este dueño; uno ya creado por root: chown una vez (docs/security).
RUN groupadd --gid 1000 trackflow && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin trackflow \
    && install -d -o trackflow -g trackflow -m 0700 /var/lib/trackflow
USER trackflow

EXPOSE 8080 8081
CMD ["node", "src/start.js"]