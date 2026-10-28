# statseat: one container with the backend (python -m app.engine) and the PWA (task C-1, see infra/README.md).

# 1. The PWA (web/), built once; the backend serves web/dist on the same origin as /api.
FROM node:22-alpine AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
COPY app/contract/ /src/app/contract/
COPY synthetic/universe/universe.json /src/synthetic/universe/universe.json
RUN npm run build  # tsc also checks the tests, which read the contract's fake payloads and the universe (flags)

# 2. The backend: the match engine reads SYN-001 through the data pipeline (data_pipeline/ next to app/).
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOST=0.0.0.0 PORT=8000 MATCH_SOURCE=synthetic:SYN-001
WORKDIR /srv
COPY app/requirements.txt app/requirements.txt
RUN pip install --no-cache-dir -r app/requirements.txt
COPY app/ app/
COPY data_pipeline/ data_pipeline/
COPY calibration/ calibration/
COPY synthetic/ synthetic/
COPY reports/qc/SYN-001/ reports/qc/SYN-001/
COPY --from=web /src/web/dist web/dist
RUN useradd --create-home statseat && chown -R statseat /srv
USER statseat
EXPOSE 8000
CMD ["python", "-m", "app.engine"]
