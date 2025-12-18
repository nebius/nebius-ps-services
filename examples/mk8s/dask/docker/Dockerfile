FROM ghcr.io/dask/dask:dev-py3.10

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir MDAnalysis dask_kubernetes ipywidgets
