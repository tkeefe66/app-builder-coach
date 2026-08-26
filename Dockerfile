FROM node:22-slim AS frontend
WORKDIR /build
COPY apps/coach_web/frontend/package.json apps/coach_web/frontend/package-lock.json ./
RUN npm ci
COPY apps/coach_web/frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared/ shared/
COPY apps/ apps/
COPY taxonomy.yaml rubric.yaml apps.yaml .
COPY --from=frontend /build/dist apps/coach_web/frontend/dist
CMD alembic -c apps/coach_web/alembic.ini upgrade head && uvicorn apps.coach_web.main:app --host 0.0.0.0 --port $PORT
