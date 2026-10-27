# Lightweight Kubernetes Edge Cluster

A fault-tolerant K3s cluster for cheap VMs and Raspberry Pis, with local-path stateful storage and automated node joins.

> Provision and operate a small, self-hosted HA Kubernetes cluster on edge hardware: idempotent bootstrap scripts, systemd health and backup timers, a stateful Helm chart, and a real offline validation gate.

## Overview

An original, self-contained implementation of the **Lightweight Kubernetes Edge Cluster** concept, built for the Foundev portfolio. It provides the full provisioning-and-operations toolkit described in the brief: numbered idempotent bootstrap scripts, systemd units for self-healing and etcd backups, a stateful Helm chart with edge-aware placement, plain manifests for a rollout/rollback demo, and the runbook and troubleshooting docs an operator actually needs. The architecture is kept small enough to read end to end: three control-plane nodes for etcd quorum, N agents labelled by hardware class, and node-local storage backed by off-box snapshots.

## Features

- Idempotent, `bash -n`-clean bootstrap scripts (`00` → `99`) with a real preflight check
- Three-server HA control plane on embedded etcd (`--cluster-init` + token joins)
- Worker joins via `K3S_URL` + `K3S_TOKEN`, auto-labelled `node-class=pi|vm`
- Token helper (`show` / `rotate`) that never prints secrets into logs
- systemd health-probe timer (auto-restart) and etcd snapshot timer (with retention)
- Original `edge-stateful` Helm chart: Postgres + cache StatefulSets on `local-path`, headless Services, PodDisruptionBudgets, topology spread, core NetworkPolicy
- `values.pi.yaml` arm64 / low-memory overrides for Raspberry Pi agents
- `hello-edge` sample Deployment used to demonstrate `kubectl rollout` / `undo`
- Offline validation gate: YAML parse, chart structure, storage checks, secret scan, dangling-reference and destructive-command guards

## Stack

`K3s; VMs or Raspberry Pi; Docker; systemd; Helm`

The CSV stack is K3s + VMs/Raspberry Pi + Docker + systemd + Helm. This is an infrastructure-as-code repository (scripts, units, charts, docs); there is no application runtime to compile. No k3s, kubectl, helm or docker is installed in the environment this was built in, so nothing was provisioned here — see **Verification** for what *was* run, and **Limitations** for what that means.

## Architecture

- **Control plane:** three K3s servers with embedded etcd. The first is installed with `--cluster-init`; the other two join the same datastore, giving quorum that tolerates one server failing. The API is reached through a stable name added as a `tls-san`.
- **Agents:** workers join with a node token, never run etcd, and are labelled `node-class=pi|vm` so the same manifests schedule correctly on a mixed ARM/x86 fleet.
- **Storage:** K3s's `local-path` StorageClass provisions node-local PersistentVolumes via `volumeClaimTemplates`. Volumes are node-bound; durability comes from etcd snapshots plus off-box application backups, not replication.
- **Resilience:** a systemd health-probe timer restarts a wedged runtime; a snapshot timer protects cluster state.

Full diagrams, ports and the failure model are in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Hardware bill of materials

A minimum fault-tolerant cluster is 3 servers + 2 agents. Scale agents to match your workload.

| Role | Count | Spec | Suggested hardware |
| --- | --- | --- | --- |
| Server (control plane + etcd) | 3 | 2 vCPU, 2 GB RAM, 40 GB disk | cheap cloud VM (e.g. 2 GB droplet) |
| Agent (worker) | 2–N | 2–4 vCPU, 2–4 GB RAM, 32 GB+ disk | Raspberry Pi 4/5 (4–8 GB) or small VMs |
| Load balancer / API VIP | 1 | — | keepalived VIP, or a TCP LB in front of the 3 servers |

One server is enough to try it out; **three** is the smallest quorum-safe size. Always keep the server count odd.

## Quickstart

```bash
# 1. First control-plane node (run on the first server)
sudo ./bootstrap/00-preflight.sh --role server
sudo ./bootstrap/10-install-server.sh \
  --tls-san k3s-api.edge.local --tls-san 10.0.0.10

# 2. Export the join token to a file (never printed to the terminal log)
sudo ./bootstrap/30-token-helper.sh show > ./secrets/node-token
chmod 600 ./secrets/node-token

# 3. Join two more servers (run on server 2 and server 3)
sudo ./bootstrap/11-install-server-join.sh \
  --server https://10.0.0.10:6443 --token-file ./secrets/node-token

# 4. Join agents (run on each worker; --node-class pi|vm)
sudo ./bootstrap/20-install-agent.sh \
  --server https://10.0.0.10:6443 --token-file ./secrets/node-token --node-class pi

# 5. Deploy the stateful chart
helm upgrade --install edge-stateful ./helm/edge-stateful \
  --namespace edge --create-namespace
#    on Raspberry Pi, add: -f ./helm/edge-stateful/values.pi.yaml

# 6. Verify the cluster and demonstrate a rollout/rollback
./bootstrap/40-verify-cluster.sh --etcd
kubectl apply -f manifests/hello-edge/deployment.yaml
kubectl -n edge-demo rollout status deployment/hello-edge
kubectl -n edge-demo rollout undo    deployment/hello-edge
```

No cluster available? `make smoke` prints a safe dry-run plan and changes nothing.

## Structure

```text
lightweight-kubernetes-edge-cluster/
├── bootstrap/                     numbered, idempotent provisioning scripts
│   ├── 00-preflight.sh            arch/mem/cgroup/ports/hostname/swap checks
│   ├── 10-install-server.sh       first control plane (--cluster-init etcd)
│   ├── 11-install-server-join.sh  additional servers (HA quorum)
│   ├── 20-install-agent.sh        worker join, labelled node-class=pi|vm
│   ├── 30-token-helper.sh         node-token show|rotate (sudo)
│   ├── 40-verify-cluster.sh       node/etcd/local-path checks + drain helper
│   └── 99-uninstall.sh            server/agent uninstall (gated by --yes)
├── systemd/
│   ├── k3s.service.d/override.conf        server drop-in (restart, limits)
│   ├── k3s-agent.service.d/override.conf  agent drop-in
│   ├── k3s-healthcheck.service + .timer   probe + auto-restart
│   ├── k3s-backup.service + .timer        etcd snapshot + retention
│   └── scripts/k3s-healthcheck.sh, k3s-backup.sh  installed to /usr/local/bin
├── k3s/
│   ├── config.yaml                server config (datastore, tls-san, labels)
│   └── config-agent.yaml          agent config (kubelet args, log caps)
├── helm/edge-stateful/            original stateful chart (local-path)
│   ├── Chart.yaml, values.yaml, values.pi.yaml
│   ├── templates/                 StatefulSets, Services, PDB, NetworkPolicy
│   └── README.md                  chart usage and storage caveats
├── manifests/
│   ├── local-path.md              StorageClass notes
│   ├── hello-edge/                rollout/rollback demo (Deployment+Service+PDB)
│   └── networkpolicy.example.yaml core NetworkPolicy example + caveat
├── docs/
│   ├── ARCHITECTURE.md            topology, ports, failure model
│   ├── RUNBOOK.md                 join/replace/restore/rotate/upgrade/drain
│   └── TROUBLESHOOTING.md         cgroup/iptables/hostname/pull fixes
├── tools/validate.py              offline structural validation gate
├── Makefile                       help, lint, bootstrap-server, verify, smoke, ...
├── LICENSE                        Apache-2.0
└── README.md
```

## Verification

The environment this was built in has **no k3s, kubectl, helm or docker**, so no
cluster was provisioned and the chart was not rendered. What *was* run is a
real offline gate driven by `make lint`:

1. `bash -n` on every shell script (`bootstrap/*.sh`, `systemd/scripts/*.sh`).
2. `tools/validate.py` — parses every YAML under `helm/`, `manifests/`, `k3s/`,
   `systemd/`; validates the Helm chart structure; checks `local-path` storage
   and `volumeClaimTemplates`; scans for committed secrets; verifies referenced
   files exist; and guards against unguarded destructive commands.

`make lint` output (exit code 0):

```text
== bash -n on 9 script(s) ==
  [ ok ] bootstrap/00-preflight.sh
  [ ok ] bootstrap/10-install-server.sh
  [ ok ] bootstrap/11-install-server-join.sh
  [ ok ] bootstrap/20-install-agent.sh
  [ ok ] bootstrap/30-token-helper.sh
  [ ok ] bootstrap/40-verify-cluster.sh
  [ ok ] bootstrap/99-uninstall.sh
  [ ok ] systemd/scripts/k3s-backup.sh
  [ ok ] systemd/scripts/k3s-healthcheck.sh
syntax: ok

== python3 tools/validate.py ==
======================================================================
Lightweight Kubernetes Edge Cluster - validation gate
======================================================================
repo root: /home/aziz/foundev/projects/lightweight-kubernetes-edge-cluster

  [✓] layout              all 7 bootstrap scripts present
  [✓] yaml-parse          17 YAML file(s) parsed cleanly
  [✓] chart-structure     Chart.yaml valid, 12 template doc(s), kinds=['NetworkPolicy', 'PodDisruptionBudget', 'Secret', 'Service', 'ServiceAccount', 'StatefulSet']
  [✓] local-path-storage  2 StatefulSet(s), 2 volumeClaimTemplate(s), all use local-path
  [✓] secret-scan         scanned 41 file(s); no committed secrets detected
  [✓] referenced-files    13 referenced bootstrap/systemd path(s) all exist
  [✓] destructive-guard   9 script(s) scanned; destructive ops gated behind --yes

======================================================================
RESULT: PASS - all 7 checks passed
======================================================================

lint: all checks passed
```

### The gate has teeth (negative tests)

Injecting a fake token or a literal password makes it fail, then removing them
restores PASS:

```text
injected-secret   -> FAIL (expected FAIL)
literal-password  -> FAIL (expected FAIL)
clean             -> PASS (expected PASS)
```

### What the gate does and does not prove

| Check | Proves |
| --- | --- |
| `bash -n` | every script is syntactically valid bash |
| `yaml-parse` | every YAML file parses (Helm templating stripped for the skeleton check) |
| `chart-structure` | `Chart.yaml` is a valid v2 chart with values and the required kinds |
| `local-path-storage` | stateful workloads use `local-path` and declare `volumeClaimTemplates` |
| `secret-scan` | no private keys, AWS keys, literal node-tokens or literal passwords are committed |
| `referenced-files` | every path under `bootstrap/` or `systemd/` cited in docs/Makefile exists |
| `destructive-guard` | no unguarded `rm -rf /`, `mkfs`, `dd of=/dev/...`; uninstall is gated |

It does **not** render Helm, apply manifests, or talk to a cluster. Correctness
on a real cluster is the job of `40-verify-cluster.sh` and the runbook.

## Operations

Common tasks are documented in [`docs/RUNBOOK.md`](docs/RUNBOOK.md): joining a
node, replacing a failed node, restoring an etcd snapshot, rotating the token,
upgrading K3s, and draining for maintenance. Diagnostics live in
[`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md).

Useful Make targets:

```bash
make help             # list targets
make lint             # the offline gate (no cluster needed)
make bootstrap-server # preflight + install first server (needs sudo)
make verify           # node/etcd/local-path health (needs a cluster)
make backup           # trigger an etcd snapshot now (needs a cluster)
make smoke            # dry-run plan, or the rollout demo if a cluster exists
```

## Limitations

- **Nothing was provisioned here.** There is no k3s/kubectl/helm/docker in the
  build environment, so the scripts, units and chart were not executed against
  a live cluster. The docs describe the real procedure; the gate proves
  structure, not runtime behaviour.
- **local-path is not replicated.** Node-local volumes are pinned to their node,
  so losing a node loses its volume contents. The design compensates with etcd
  snapshots and off-box app backups, which is the honest trade-off of cheap
  edge storage. True HA Postgres would need replication plus network storage,
  which is out of scope.
- **NetworkPolicy needs a policy-capable CNI.** K3s's default flannel does not
  enforce `NetworkPolicy`. The objects are applied and ready, but are inert
  until you install Calico (or K3s's network-policy controller).
- **No ingress / load balancer.** Traefik and `servicelb` are disabled by
  design; expose workloads with a NodePort or your own reverse proxy.
- **The API VIP is not provisioned.** The scripts add `tls-san` values for a
  stable name, but keepalived/the load balancer itself is left to you.
- **Single-writer stateful pods.** Defaults are 1 replica each for simplicity on
  small hardware; the chart can scale, but the storage model still pins pods to
  nodes.

## License

Apache-2.0. See `LICENSE`.
