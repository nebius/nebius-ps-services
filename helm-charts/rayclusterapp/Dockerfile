FROM rayproject/ray:nightly-py39-cu128

# Install required packages (you're root during build)
RUN apt update && \
    apt install -y kmod infiniband-diags ibverbs-utils libibverbs-dev perftest net-tools && \
    rm -rf /var/lib/apt/lists/*

# Add training script and set working directory
COPY train.py /app/train.py
WORKDIR /app

# Install PyTorch with CUDA 12.8 support
RUN pip install torch --index-url https://download.pytorch.org/whl/cu128

# Create a non-root user and switch to it
RUN useradd -m rayuser && chown -R rayuser:rayuser /app
USER rayuser

# Keeps the container running to receive Ray tasks
CMD ["sleep", "infinity"]
