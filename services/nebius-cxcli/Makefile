.DEFAULT_GOAL := all

VENV ?= .venv
PYTHON ?= python3
UV ?= uv
VENV_LOCKER := scripts/run_with_file_lock.py
OFFLINE_TEST_ENV := NEBIUS_IAM_TOKEN=offline-test-token
DIFF_BASE ?=
BASE_REF_ARG = $(if $(strip $(DIFF_BASE)),--base-ref "$(DIFF_BASE)",)
UV_ENV = VIRTUAL_ENV="$(abspath $(VENV))" UV_PROJECT_ENVIRONMENT="$(abspath $(VENV))"
UV_RUN = $(UV_ENV) "$(UV)" run --locked --no-sync --no-python-downloads --python "$(PYTHON)"

.PHONY: all uv-check _validate-env-path lock-check env _env-sync fmt format-check lint typecheck quality-baseline-check architecture-check diff-check test test-unit test-integration coverage coverage-check ci-python ci-quality build verify-wheel-cli verify-wheel-cli-dist check clean

all: env
	$(MAKE) -j2 check verify-wheel-cli

uv-check:
	@command -v "$(UV)" >/dev/null 2>&1 || { \
		echo "uv is required for repository development; install uv 0.12.9 or a compatible 0.12.x release." >&2; \
		exit 1; \
	}

_validate-env-path:
	@set -eu; \
		if "$(PYTHON)" -c 'import sys; raise SystemExit(0 if any(character.isspace() for character in sys.argv[1]) else 1)' "$(VENV)"; then \
			echo "Refusing a VENV path containing whitespace." >&2; \
			exit 1; \
		fi; \
		if ! "$(PYTHON)" -c 'from pathlib import Path; import sys; venv = Path(sys.argv[1]).resolve(); project = Path(sys.argv[2]).resolve(); raise SystemExit(venv == Path(venv.anchor) or venv == project or venv in project.parents)' "$(VENV)" "$(CURDIR)"; then \
			echo "Refusing an unsafe VENV path." >&2; \
			exit 1; \
		fi; \
		if [ -L "$(VENV)" ]; then \
			echo "Refusing a symlinked VENV path." >&2; \
			exit 1; \
		fi; \
		if [ -e "$(VENV)" ] && { [ ! -d "$(VENV)" ] || [ ! -f "$(VENV)/pyvenv.cfg" ]; }; then \
			echo "Refusing an unrecognized VENV path." >&2; \
			exit 1; \
		fi

lock-check: uv-check _validate-env-path
	@$(UV_ENV) "$(UV)" lock --check --python "$(PYTHON)" --no-python-downloads

env: lock-check
	@"$(PYTHON)" "$(VENV_LOCKER)" --resource "$(VENV)" -- \
		"$(MAKE)" --no-print-directory _env-sync

_env-sync:
	@$(UV_ENV) "$(UV)" sync --locked --python "$(PYTHON)" --no-python-downloads
	@$(UV_RUN) python -c 'from pathlib import Path; import nebius_cxcli, sys; project = Path(sys.argv[1]).resolve(); venv = Path(sys.argv[2]).resolve(); module = Path(nebius_cxcli.__file__).resolve(); raise SystemExit(Path(sys.prefix).resolve() != venv or not module.is_relative_to(project / "src"))' "$(CURDIR)" "$(abspath $(VENV))"
	@$(UV_RUN) nebius-cxcli --version >/dev/null

fmt: env
	$(UV_RUN) ruff format src tests scripts

format-check: env
	$(UV_RUN) python scripts/check_quality_ratchets.py format \
		--project-root $(CURDIR) \
		--baseline $(CURDIR)/scripts/quality-baseline.json

quality-baseline-check: env
	$(UV_RUN) python scripts/check_quality_ratchets.py baseline \
		--project-root $(CURDIR) \
		--baseline $(CURDIR)/scripts/quality-baseline.json \
		$(BASE_REF_ARG)

architecture-check: env
	$(UV_RUN) python scripts/check_cli_architecture.py check \
		--project-root $(CURDIR) \
		--source $(CURDIR)/src/nebius_cxcli/cli.py \
		--baseline $(CURDIR)/scripts/cli-architecture-baseline.json \
		$(BASE_REF_ARG)

lint: env
	$(UV_RUN) ruff check src tests scripts

typecheck: env
	$(UV_RUN) python scripts/check_quality_ratchets.py mypy \
		--project-root $(CURDIR) \
		--baseline $(CURDIR)/scripts/quality-baseline.json

diff-check:
	@if [ -n "$(DIFF_BASE)" ]; then \
		git diff --check "$${DIFF_BASE}...HEAD"; \
	else \
		git diff --check; \
	fi

test: test-unit

test-unit: env
	$(OFFLINE_TEST_ENV) $(UV_RUN) pytest -m "not integration"

test-integration: env
	@if [ -d tests/integration ] && find tests/integration -type f \( -name 'test_*.py' -o -name '*_test.py' \) | grep -q .; then \
		$(UV_RUN) pytest -m integration tests/integration; \
	else \
		echo "No integration tests under tests/integration"; \
	fi

coverage: coverage-check

coverage-check: env
	$(OFFLINE_TEST_ENV) $(UV_RUN) pytest --cov=nebius_cxcli --cov-branch \
		--cov-report=term-missing --cov-report=json:.coverage.json -m "not integration"
	$(UV_RUN) python scripts/check_coverage.py .coverage.json \
		--baseline scripts/coverage-baseline.json \
		--project-root $(CURDIR) \
		$(BASE_REF_ARG)

ci-python: test-unit

ci-quality: lint quality-baseline-check architecture-check format-check typecheck coverage-check diff-check

build: env
	rm -rf build dist
	@set -eu; \
		temp_parent="$${TMPDIR:-/tmp}"; \
		temp_parent="$${temp_parent%/}"; \
		[ -n "$$temp_parent" ] || temp_parent=/tmp; \
		temp_root="$$(mktemp -d "$$temp_parent/nebius-cxcli-build.XXXXXX")"; \
		case "$$temp_root" in "$$temp_parent"/nebius-cxcli-build.*) ;; *) exit 1 ;; esac; \
		trap 'find "$$temp_root" -depth -delete' EXIT; \
		build_constraints="$$temp_root/build-constraints.txt"; \
		$(UV_ENV) "$(UV)" export --locked --all-groups --no-emit-project \
			--no-python-downloads --output-file "$$build_constraints" >/dev/null; \
		$(UV_ENV) "$(UV)" build --wheel --python "$(PYTHON)" --no-python-downloads \
			--build-constraints "$$build_constraints" --require-hashes

verify-wheel-cli: build
	$(MAKE) --no-print-directory verify-wheel-cli-dist

verify-wheel-cli-dist: env
	@set -eu; \
		temp_parent="$${TMPDIR:-/tmp}"; \
		temp_parent="$${temp_parent%/}"; \
		[ -n "$$temp_parent" ] || temp_parent=/tmp; \
		temp_root="$$(mktemp -d "$$temp_parent/nebius-cxcli-wheel.XXXXXX")"; \
		case "$$temp_root" in "$$temp_parent"/nebius-cxcli-wheel.*) ;; *) exit 1 ;; esac; \
		trap 'find "$$temp_root" -depth -delete' EXIT; \
		wheel_count="$$(find dist -maxdepth 1 -name '*.whl' -print | wc -l | tr -d ' ')"; \
		[ "$$wheel_count" -eq 1 ] || { echo "Expected exactly one wheel in dist/, found $$wheel_count." >&2; exit 1; }; \
		wheel="$$(find dist -maxdepth 1 -name '*.whl' -print)"; \
		wheel_venv="$$temp_root/venv"; \
		runtime_requirements="$$temp_root/runtime-requirements.txt"; \
		"$(UV)" venv --python "$(PYTHON)" --no-python-downloads "$$wheel_venv" >/dev/null; \
		"$(UV)" export --locked --no-dev --no-emit-project --no-python-downloads \
			--output-file "$$runtime_requirements" >/dev/null; \
		"$(UV)" pip install --python "$$wheel_venv/bin/python" --no-python-downloads \
			--requirement "$$runtime_requirements" >/dev/null; \
		"$(UV)" pip install --python "$$wheel_venv/bin/python" --no-python-downloads \
			--no-deps "$$wheel" >/dev/null; \
		"$(UV)" pip check --python "$$wheel_venv/bin/python" >/dev/null; \
		site_packages="$$(PYTHONNOUSERSITE=1 "$$wheel_venv/bin/python" -c 'from pathlib import Path; import nebius_cxcli; print(Path(nebius_cxcli.__file__).resolve().parent.parent)')"; \
		PYTHONNOUSERSITE=1 "$$wheel_venv/bin/python" scripts/verify_wheel_cli.py \
			--contract tests/fixtures/cli_contract.json \
			--package-root "$$site_packages" \
			--console-script "$$wheel_venv/bin/nebius-cxcli"

check: lint test

clean:
	rm -rf build dist .pytest_cache .ruff_cache .coverage .coverage.json coverage.xml
