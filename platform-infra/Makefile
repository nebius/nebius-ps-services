MODULES := $(sort $(wildcard modules/*))
EXAMPLE_ROOTS := $(sort $(dir $(wildcard modules/*/examples/*/versions.tf)))

.PHONY: fmt validate test test-all

fmt:
	terraform fmt -recursive

validate:
	@for m in $(MODULES); do \
		if [ -d "$$m" ]; then \
			echo "==> validate $$m"; \
			terraform -chdir=$$m init -backend=false -upgrade=false >/dev/null; \
			terraform -chdir=$$m validate; \
			find $$m -maxdepth 1 -name '.terraform.lock.hcl' -delete; \
		fi; \
	done
	@for root in $(EXAMPLE_ROOTS); do \
		echo "==> validate $$root"; \
		terraform -chdir=$$root init -backend=false -upgrade=false -lockfile=readonly >/dev/null; \
		terraform -chdir=$$root validate; \
	done

test:
	@echo "Run 'make test-all' to execute terraform test for modules that contain tests/."

test-all:
	@for m in $(MODULES); do \
		if [ -d "$$m/tests" ]; then \
			echo "==> test $$m"; \
			terraform -chdir=$$m test; \
		fi; \
	done
