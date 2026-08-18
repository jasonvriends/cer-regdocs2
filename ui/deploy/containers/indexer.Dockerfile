FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY regdocs_atlas/requirements-deploy.txt ./requirements-deploy.txt
RUN python -m pip install --no-cache-dir -r requirements-deploy.txt

COPY VERSION pipeline.py ./
COPY regdocs_atlas/ ./regdocs_atlas/
COPY tools/publish_hybrid_index.py tools/publish_regulatory_records.py tools/run_cloud_indexer.py tools/run_cloud_intelligence.py ./tools/

RUN useradd --create-home --uid 10001 regdocs \
    && mkdir -p /work/normalize /work/index /work/enrich/model \
    && chown -R regdocs:regdocs /work /app

USER regdocs
CMD ["python", "tools/run_cloud_indexer.py"]
