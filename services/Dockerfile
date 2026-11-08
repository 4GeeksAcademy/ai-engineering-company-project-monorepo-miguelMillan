FROM node:22-bookworm-slim AS node
FROM python:3.12-slim-bookworm

COPY --from=node /usr/local/bin/node /usr/local/bin/node
WORKDIR /app/services/api
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir uv==0.9.9 \
    && uv pip install --system -r /tmp/requirements.txt
COPY api/ ./
COPY --from=scripts incidents_analysis.py /app/scripts/incidents_analysis.py
COPY --from=shared incident_validation.py /app/packages/shared/incident_validation.py

EXPOSE 8080 8081
CMD ["node", "src/start.js"]