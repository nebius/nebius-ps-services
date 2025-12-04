import os
from .cli import app


def main() -> None:
    # Suppress gRPC debug logging
    os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
    os.environ.setdefault("GRPC_TRACE", "")
    app()


if __name__ == "__main__":
    main()
