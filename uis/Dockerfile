FROM node:22-alpine

WORKDIR /app/uis
COPY website/package.json website/package-lock.json ./website/
COPY backoffice/package.json backoffice/package-lock.json ./backoffice/
RUN npm --prefix website ci && npm --prefix backoffice ci
COPY website/ ./website/
COPY backoffice/ ./backoffice/
COPY start.sh ./start.sh

EXPOSE 3000 3001
CMD ["sh", "/app/uis/start.sh"]