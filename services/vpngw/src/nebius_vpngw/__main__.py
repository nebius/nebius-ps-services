import os

from nebius_vpngw.cli import main as cli_main


def main() -> None:
    # Suppress gRPC debug logging
    os.environ.setdefault("GRPC_VERBOSITY", "ERROR")
    os.environ.setdefault("GRPC_TRACE", "")
    request = os.environ.pop("VPNGW_PAIR_CHILD_REQUEST", None)
    if request is not None:
        from pathlib import Path

        from nebius_vpngw.pair_worker import main as pair_child

        try:
            pair_child(Path(request))
        except Exception:
            # Invalid private requests and report-write failures may contain
            # provider or credential details. Valid children publish recovery
            # before their own terminal exit and always release the parent lock.
            import sys

            print("Pair child could not complete. Inspect the parent operation and validated child configuration before retrying.", file=sys.stderr)
            raise SystemExit(1) from None
        return
    cli_main()


if __name__ == "__main__":
    main()
