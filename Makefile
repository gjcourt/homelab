SHELL := /usr/bin/env bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

ROOT_YAML := $(wildcard *.yaml *.yml)

YAMLLINT_IMAGE ?= cytopia/yamllint:latest
YAML_FMT_IMAGE ?= ghcr.io/google/yamlfmt:latest
KUBECONFORM_IMAGE ?= ghcr.io/yannh/kubeconform:v0.6.7
SHELLCHECK_IMAGE ?= koalaman/shellcheck:stable

KUBECTL ?= kubectl
SYNO_NS ?= synology-csi

IMAGE_PREFIX ?= homelab
TAG ?= dev
PLATFORMS ?=
PUSH ?= 0

.PHONY: help
help: ## Show available targets
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z0-9_.-]+:.*##/ {printf "%-22s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

.PHONY: lint
lint: lint-yaml lint-shell ## Run repo linters

.PHONY: lint-yaml
lint-yaml: ## Lint YAML (indentation, tabs, etc.)
	docker run --rm -v "$(PWD)":/work -w /work $(YAMLLINT_IMAGE) -c .yamllint .

.PHONY: fmt-yaml
fmt-yaml: ## Format YAML to repo standard (2-space indentation)
	docker run --rm -v "$(PWD)":/work -w /work $(YAML_FMT_IMAGE) -conf .yamlfmt.yaml -dstar "**/*.yaml" "**/*.yml"

.PHONY: fmt-yaml-check
fmt-yaml-check: ## Check YAML formatting (no changes)
	docker run --rm -v "$(PWD)":/work -w /work $(YAML_FMT_IMAGE) -conf .yamlfmt.yaml -dstar -lint "**/*.yaml" "**/*.yml"

.PHONY: format
format: fmt-yaml ## Alias for fmt-yaml

.PHONY: lint-shell
lint-shell: ## Run shellcheck on scripts
	docker run --rm -v "$(PWD)":/work -w /work $(SHELLCHECK_IMAGE) scripts/*.sh

.PHONY: test
test: test-kustomize test-kubeconform fmt-yaml-check ## Render + validate manifests

.PHONY: test-kustomize
test-kustomize: ## Render key overlays with kustomize (via kubectl)
	kubectl kustomize apps/staging >/tmp/kustomize-staging.yaml
	kubectl kustomize apps/production >/tmp/kustomize-production.yaml
	kubectl kustomize infra/configs >/tmp/kustomize-infra-configs.yaml
	kubectl kustomize infra/controllers >/tmp/kustomize-infra-controllers.yaml

.PHONY: test-kubeconform
test-kubeconform: ## Validate rendered manifests with kubeconform (best-effort)
	@for f in /tmp/kustomize-staging.yaml /tmp/kustomize-production.yaml /tmp/kustomize-infra-configs.yaml /tmp/kustomize-infra-controllers.yaml; do \
		echo "Validating $$f"; \
		docker run --rm -i $(KUBECONFORM_IMAGE) -strict -ignore-missing-schemas -skip Secret < "$$f"; \
	done

.PHONY: kubectl-context
kubectl-context: ## Show current kubectl context and cluster-info
	@command -v "$(KUBECTL)" >/dev/null 2>&1 || { echo "ERROR: kubectl not found (set KUBECTL=... or install kubectl)." >&2; exit 2; }
	@echo "Context: $$($(KUBECTL) config current-context 2>/dev/null || echo '<none>')"
	@$(KUBECTL) cluster-info || true

.PHONY: kubectl-check
kubectl-check: ## Verify kubectl can reach the cluster
	@command -v "$(KUBECTL)" >/dev/null 2>&1 || { echo "ERROR: kubectl not found (set KUBECTL=... or install kubectl)." >&2; exit 2; }
	@$(KUBECTL) version >/dev/null 2>&1 || { \
		echo "ERROR: kubectl cannot reach a cluster (check context/kubeconfig/VPN)." >&2; \
		echo "Try: $(KUBECTL) config current-context" >&2; \
		echo "Try: $(KUBECTL) config get-contexts" >&2; \
		echo "Try: make kubectl-context" >&2; \
		exit 2; \
	}

.PHONY: synology-diag
synology-diag: kubectl-check ## Diagnose Synology-backed storage read-only issues (requires cluster access)
	KUBECTL="$(KUBECTL)" SYNO_NS="$(SYNO_NS)" scripts/synology-diag.sh

.PHONY: synology-csi-restart
synology-csi-restart: kubectl-check ## Restart Synology CSI workloads to force remounts (requires cluster access)
	@deploys="$$( $(KUBECTL) -n "$(SYNO_NS)" get deployment -o name 2>/dev/null || true )"; \
	if [[ -n "$$deploys" ]]; then \
		$(KUBECTL) -n "$(SYNO_NS)" rollout restart $$deploys; \
		for d in $$deploys; do $(KUBECTL) -n "$(SYNO_NS)" rollout status "$$d" --timeout=5m || true; done; \
	else \
		echo "No deployments found in namespace $(SYNO_NS)"; \
	fi
	@dss="$$( $(KUBECTL) -n "$(SYNO_NS)" get daemonset -o name 2>/dev/null || true )"; \
	if [[ -n "$$dss" ]]; then \
		$(KUBECTL) -n "$(SYNO_NS)" rollout restart $$dss; \
		for ds in $$dss; do $(KUBECTL) -n "$(SYNO_NS)" rollout status "$$ds" --timeout=5m || true; done; \
	else \
		echo "No daemonsets found in namespace $(SYNO_NS)"; \
	fi
	@stss="$$( $(KUBECTL) -n "$(SYNO_NS)" get statefulset -o name 2>/dev/null || true )"; \
	if [[ -n "$$stss" ]]; then \
		$(KUBECTL) -n "$(SYNO_NS)" rollout restart $$stss; \
		for s in $$stss; do $(KUBECTL) -n "$(SYNO_NS)" rollout status "$$s" --timeout=10m || true; done; \
	else \
		echo "No statefulsets found in namespace $(SYNO_NS)"; \
	fi

.PHONY: synology-speedtest-nfs

.PHONY: synology-speedtest
synology-speedtest: kubectl-check ## Run a quick CSI write/read test (iSCSI/RWO) (requires cluster access)
	KUBECTL="$(KUBECTL)" SYNO_NS="$(SYNO_NS)" scripts/synology-speedtest.sh

synology-speedtest-nfs: synology-speedtest ## Alias for synology-speedtest (historical name)

.PHONY: images
images: ## List buildable images under images/
	@find images -mindepth 1 -maxdepth 1 -type d -print | sed 's|^images/||' | sort

.PHONY: build-images
build-images: ## Build all Dockerfiles under images/
	@for img in $$(find images -mindepth 1 -maxdepth 1 -type d -print | sed 's|^images/||'); do \
		$(MAKE) build-image IMAGE="$$img"; \
	done

.PHONY: build-image
build-image: ## Build a single image: make build-image IMAGE=snapcast
	@if [[ -z "${IMAGE:-}" ]]; then echo "IMAGE is required (e.g. IMAGE=snapcast)" >&2; exit 2; fi
	@context="images/$(IMAGE)"; \
	if [[ ! -f "$$context/Dockerfile" ]]; then echo "No Dockerfile at $$context/Dockerfile" >&2; exit 2; fi; \
	tag="$(IMAGE_PREFIX)/$(IMAGE):$(TAG)"; \
	echo "Building $$tag from $$context"; \
	args=(docker buildx build -t "$$tag" "$$context"); \
	if [[ -n "$(PLATFORMS)" ]]; then args+=(--platform "$(PLATFORMS)"); fi; \
	if [[ "$(PUSH)" == "1" ]]; then args+=(--push); else args+=(--load); fi; \
	"${args[@]}"

.PHONY: immich-init-db
immich-init-db: ## Initialize Immich DB helper. Usage: make immich-init-db ENV=staging|production
	@if [ -z "$(ENV)" ]; then echo "Error: ENV variable is required. usage: make immich-init-db ENV=staging|production"; exit 1; fi
	@case "$(ENV)" in \
		staging) NAMESPACE="immich-stage"; CLUSTER="immich-db-staging-cnpg-v1";; \
		production) NAMESPACE="immich-prod"; CLUSTER="immich-db-prod-cnpg-v1";; \
		*) echo "Error: valid ENV values are 'staging' or 'production'"; exit 1;; \
	esac; \
	echo "Waiting for cnpg cluster $$CLUSTER in $$NAMESPACE..."; \
	POD=$$(kubectl get pods -n $$NAMESPACE -l cnpg.io/cluster=$$CLUSTER,role=primary -o jsonpath='{.items[0].metadata.name}'); \
	if [ -z "$$POD" ]; then echo "Error: Could not find primary pod for cluster $$CLUSTER"; exit 1; fi; \
	echo "Found primary pod: $$POD"; \
	echo "Initialize extensions..."; \
	kubectl exec -n $$NAMESPACE -it $$POD -- psql -U postgres -d immich -c "CREATE EXTENSION IF NOT EXISTS vectors CASCADE;"; \
	kubectl exec -n $$NAMESPACE -it $$POD -- psql -U postgres -d immich -c "CREATE EXTENSION IF NOT EXISTS earthdistance CASCADE;"; \	kubectl exec -n $$NAMESPACE -it $$POD -- psql -U postgres -d immich -c "UPDATE pg_extension SET extowner = (SELECT oid FROM pg_roles WHERE rolname = 'immich') WHERE extname = 'vectors';"
	kubectl exec -n $$NAMESPACE -it $$POD -- psql -U postgres -d immich -c "UPDATE pg_extension SET extowner = (SELECT oid FROM pg_roles WHERE rolname = 'immich') WHERE extname = 'earthdistance';"	echo "Grant permissions..."; \
	kubectl exec -n $$NAMESPACE -it $$POD -- psql -U postgres -d immich -c "GRANT ALL ON SCHEMA vectors TO immich;"; \
	kubectl exec -n $$NAMESPACE -it $$POD -- psql -U postgres -d immich -c "GRANT ALL ON ALL TABLES IN SCHEMA vectors TO immich;"; \
	kubectl exec -n $$NAMESPACE -it $$POD -- psql -U postgres -d immich -c "ALTER DEFAULT PRIVILEGES IN SCHEMA vectors GRANT ALL ON TABLES TO immich;"

