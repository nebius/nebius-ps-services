MODULES := $(sort $(wildcard modules/*))

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

test:
	@echo "Run 'make test-all' to execute terraform test for modules that contain tests/."

test-all:
	@for m in $(MODULES); do \
		if [ -d "$$m/tests" ]; then \
			echo "==> test $$m"; \
			terraform -chdir=$$m test; \
		fi; \
	done
