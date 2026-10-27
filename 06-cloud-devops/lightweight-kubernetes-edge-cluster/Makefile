# Makefile for the Lightweight Kubernetes Edge Cluster project.
#
# Most targets operate on a real cluster and need sudo + k3s. The `lint`
# target is fully self-contained and is the gate that runs in this repo's
# build environment (no k3s/kubectl/helm/docker needed).

SHELL := /bin/bash
.DEFAULT_GOAL := help

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))

# All shell scripts that must pass `bash -n`.
SH_SCRIPTS := $(wildcard bootstrap/*.sh) $(wildcard systemd/scripts/*.sh)

.DEFAULT:
	@echo "unknown target: $@ (try 'make help')"
	@exit 2

.PHONY: help lint syntax validate bootstrap-server join-server join-agent \
        verify backup smoke deploy-chart destroy-chart clean

help: ## Show this help.
	@echo "Lightweight Kubernetes Edge Cluster"
	@echo
	@echo "Usage: make <target>"
	@echo
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | sort \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'
	@echo
	@echo "The 'lint' target is the offline gate (no cluster required)."
	@echo "Targets touching a cluster require sudo and k3s on the target node."

# ---------------------------------------------------------------------------
# Lint / validation gate
# ---------------------------------------------------------------------------
lint: syntax validate ## Run the full offline gate (bash -n + structural validation).
	@echo
	@echo "lint: all checks passed"

syntax: ## Syntax-check every shell script with bash -n.
	@echo "== bash -n on $(words $(SH_SCRIPTS)) script(s) =="
	@fail=0; \
	for f in $(SH_SCRIPTS); do \
	  if bash -n "$$f"; then \
	    echo "  [ ok ] $$f"; \
	  else \
	    echo "  [FAIL] $$f"; fail=1; \
	  fi; \
	done; \
	if [ $$fail -ne 0 ]; then echo "syntax: FAILED"; exit 1; fi; \
	echo "syntax: ok"

validate: ## Run tools/validate.py structural checks.
	@echo
	@echo "== python3 tools/validate.py =="
	@python3 tools/validate.py

# ---------------------------------------------------------------------------
# Cluster provisioning (require sudo on the target node)
# ---------------------------------------------------------------------------
bootstrap-server: ## Bootstrap the first control-plane node (needs sudo, --tls-san).
	@echo "Run on the FIRST server node, e.g.:"
	@echo "  sudo ./bootstrap/00-preflight.sh --role server"
	@echo "  sudo ./bootstrap/10-install-server.sh --tls-san k3s-api.edge.local --tls-san 10.0.0.10"
	sudo ./bootstrap/00-preflight.sh --role server
	sudo ./bootstrap/10-install-server.sh

join-server: ## Join an additional control-plane node (needs --server + token file).
	@echo "Run on the 2nd/3rd server node with the token file present, e.g.:"
	@echo "  sudo ./bootstrap/11-install-server-join.sh --server https://10.0.0.10:6443 --token-file ./secrets/node-token"
	@if [ -z "$(SERVER)" ]; then echo "set SERVER=https://IP:6443 make join-server"; exit 2; fi
	sudo ./bootstrap/11-install-server-join.sh --server "$(SERVER)" --token-file "$(or $(TOKEN_FILE),./secrets/node-token)"

join-agent: ## Join a worker node (set SERVER=... and CLASS=pi|vm).
	@if [ -z "$(SERVER)" ]; then echo "set SERVER=https://IP:6443 CLASS=pi make join-agent"; exit 2; fi
	sudo ./bootstrap/00-preflight.sh --role agent
	sudo ./bootstrap/20-install-agent.sh --server "$(SERVER)" --token-file "$(or $(TOKEN_FILE),./secrets/node-token)" --node-class "$(or $(CLASS),vm)"

verify: ## Verify cluster health (nodes Ready, local-path, etcd).
	./bootstrap/40-verify-cluster.sh --etcd

# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------
backup: ## Trigger an etcd snapshot now (on a server node).
	sudo systemctl start k3s-backup.service
	@echo "snapshots:"
	@sudo ls -1t /var/lib/rancher/k3s/server/db/snapshots 2>/dev/null | head -n 5 || true

# ---------------------------------------------------------------------------
# Chart + demo manifests
# ---------------------------------------------------------------------------
deploy-chart: ## Install/upgrade the edge-stateful Helm chart.
	@command -v helm >/dev/null 2>&1 || { echo "helm not found; install helm first"; exit 2; }
	helm upgrade --install edge-stateful ./helm/edge-stateful --namespace edge --create-namespace

destroy-chart: ## Uninstall the chart (PVCs are left behind on purpose).
	helm uninstall edge-stateful --namespace edge

# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------
smoke: ## Print a safe dry-run plan, or run it if a cluster is reachable.
	@if command -v kubectl >/dev/null 2>&1 || command -v k3s >/dev/null 2>&1; then \
	  echo "== cluster detected: running the hello-edge rollout/rollback demo =="; \
	  KUBECTL="$$(command -v kubectl || echo 'k3s kubectl')"; \
	  $$KUBECTL apply -f manifests/hello-edge/deployment.yaml; \
	  $$KUBECTL -n edge-demo rollout status deployment/hello-edge --timeout=120s; \
	  $$KUBECTL -n edge-demo set image deployment/hello-edge app=nginx:1.27-alpine; \
	  $$KUBECTL -n edge-demo rollout status deployment/hello-edge --timeout=120s; \
	  $$KUBECTL -n edge-demo rollout undo deployment/hello-edge; \
	  $$KUBECTL -n edge-demo rollout status deployment/hello-edge --timeout=120s; \
	else \
	  echo "== no cluster here (no kubectl/k3s). Safe dry-run plan: =="; \
	  echo; \
	  echo "  1. sudo ./bootstrap/00-preflight.sh --role server"; \
	  echo "  2. sudo ./bootstrap/10-install-server.sh --tls-san k3s-api.edge.local"; \
	  echo "  3. sudo ./bootstrap/30-token-helper.sh show > ./secrets/node-token"; \
	  echo "  4. sudo ./bootstrap/11-install-server-join.sh --server https://<ip>:6443 --token-file ./secrets/node-token  # x2 servers"; \
	  echo "  5. sudo ./bootstrap/20-install-agent.sh --server https://<ip>:6443 --token-file ./secrets/node-token --node-class pi"; \
	  echo "  6. ./bootstrap/40-verify-cluster.sh --etcd"; \
	  echo "  7. helm upgrade --install edge-stateful ./helm/edge-stateful -n edge --create-namespace"; \
	  echo "  8. kubectl apply -f manifests/hello-edge/ && kubectl -n edge-demo rollout status deploy/hello-edge"; \
	  echo "  9. kubectl -n edge-demo rollout undo deploy/hello-edge   # demonstrate rollback"; \
	  echo; \
	  echo "  Nothing was changed."; \
	fi

clean: ## Remove local scratch files (never touches a cluster).
	rm -f /tmp/k3s-install.sh /tmp/k3s-install-agent.sh 2>/dev/null || true
	@echo "cleaned local scratch files"
