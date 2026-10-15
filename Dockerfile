FROM node:22-alpine AS frontend
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY index.html vite.config.js ./
COPY src ./src
RUN npm run build
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY server ./server
COPY data ./data
# The service reads its corpus from a store built here (server/store.py), so it never holds the whole
# corpus in its 256 MB; ASK_STORE=prebuilt keeps it from building one itself.
RUN python -m server.build_store
COPY --from=frontend /app/dist ./dist
ENV PORT=8000 PYTHONUNBUFFERED=1 ASK_STORE=prebuilt
EXPOSE 8000
CMD ["sh", "-c", "exec uvicorn server.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --no-access-log"]
