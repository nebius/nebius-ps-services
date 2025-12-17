FROM ghcr.io/dask/dask:latest

RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir MDAnalysis dask_kubernetes ipywidgets
