import os

from nebius_vpngw.cli import app


def main() -> None:
    # Suppress gRPC debug logging
    os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
    os.environ.setdefault("GRPC_TRACE", "")
    request = os.environ.pop("VPNGW_PAIR_CHILD_REQUEST", None)
    if request is not None:
        from pathlib import Path

        from nebius_vpngw.pair_worker import main as pair_child

        pair_child(Path(request))
        return
    app()


if __name__ == "__main__":
    main()
