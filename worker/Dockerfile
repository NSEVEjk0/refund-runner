FROM python:3.12-slim-bookworm@sha256:623674a14ef93d052bb9b0de4dbf5f28eb578a35263838941cc8aa43213c1487
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pypdf==7.0.0
WORKDIR /worker
COPY documents.py media.py main.py /worker/
RUN chmod -R a-w /worker
USER 65532:65532
ENV PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1
ENTRYPOINT ["python", "-I", "-B", "/worker/main.py"]
