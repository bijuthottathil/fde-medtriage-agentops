# ---- build stage ----
FROM python:3.12-slim AS build
WORKDIR /w
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---- runtime stage (small, non-root) ----
FROM python:3.12-slim
ARG GIT_SHA=local
ENV APP_VERSION=$GIT_SHA PYTHONUNBUFFERED=1
WORKDIR /srv
COPY --from=build /install /usr/local
COPY app/ app/
COPY prompts/ prompts/
COPY data/patients.json data/patients.json
COPY data/protocols/ data/protocols/
RUN useradd -m -u 10001 appuser
USER 10001
# Bake Chroma's embedding model (all-MiniLM-L6-v2, ~80MB) into the image: no runtime download,
# works with readOnlyRootFilesystem and in private subnets without internet egress.
ENV HOME=/home/appuser ANONYMIZED_TELEMETRY=False
RUN python -c "from chromadb.utils.embedding_functions import DefaultEmbeddingFunction as E; E()(['warmup'])"
EXPOSE 8080
HEALTHCHECK CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8080/healthz')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2"]
