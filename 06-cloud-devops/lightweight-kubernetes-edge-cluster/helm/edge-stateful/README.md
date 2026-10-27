# edge-stateful Helm chart

An original Helm chart for running a small **stateful** workload on a
lightweight K3s edge cluster. It deploys a Postgres StatefulSet and a
Redis-compatible cache StatefulSet, both backed by the cluster's
`local-path` StorageClass, with edge-aware scheduling and disruption budgets.

This chart is part of the
[Lightweight Kubernetes Edge Cluster](../../README.md) project. It is not a
wrapper around any upstream chart.

## What it creates

| Kind | Notes |
| --- | --- |
| `StatefulSet` (postgres) | 1 replica by default; `volumeClaimTemplates` from `local-path` |
| `StatefulSet` (cache) | Redis-compatible, AOF persistence on `local-path` |
| `Service` (headless, x2) | stable per-pod DNS for stateful discovery |
| `Service` (ClusterIP, x2) | ordinary client access on 5432 / 6379 |
| `PodDisruptionBudget` (x2) | `minAvailable` guard for drains and upgrades |
| `NetworkPolicy` | default-deny plus intra-release allow (see caveat below) |
| `ServiceAccount` | least-privilege identity for the workloads |
| `Secret` | random Postgres password generated at install, never committed |
| test hook | `helm test` runs `pg_isready` against the client Service |

## Install

```bash
# x86_64 VMs
helm upgrade --install edge-stateful ./helm/edge-stateful --namespace edge --create-namespace

# Raspberry Pi (arm64, low memory)
helm upgrade --install edge-stateful ./helm/edge-stateful --namespace edge --create-namespace \
  -f ./helm/edge-stateful/values.pi.yaml
```

> This chart was **not rendered here** — no Helm binary is available in the
> build environment. The templates, values and structure are validated by
> `tools/validate.py` (see the repo README's Verification section) and are
> ready to `helm upgrade --install` on a real cluster.

## Key values

| Key | Default | Purpose |
| --- | --- | --- |
| `postgres.enabled` | `true` | deploy the Postgres StatefulSet |
| `postgres.replicas` | `1` | number of Postgres pods |
| `postgres.storage.className` | `local-path` | must stay `local-path` for edge storage |
| `postgres.storage.size` | `5Gi` | PVC size per pod |
| `postgres.existingSecret` | `""` | use your own Secret instead of the generated one |
| `cache.enabled` | `true` | deploy the cache StatefulSet |
| `placement.nodeClasses` | `[vm, pi]` | which `node-class` labels may host pods |
| `placement.topologyKey` | `kubernetes.io/hostname` | spread across hosts |
| `podDisruptionBudget.minAvailable` | `1` | availability during drains |
| `networkPolicy.enabled` | `true` | apply the default-deny policies |

## Storage model (read this)

`local-path` binds a PersistentVolume to the **node** that provisioned it.
That makes it cheap and fast, and means:

- a pod's data does not follow it to another node;
- losing a node loses its local volumes unless you have copied them off-box;
- StatefulSet pods are effectively pinned to their original node.

For a true HA Postgres you would need replication and a network storage class,
which is out of scope for a cheap edge cluster. The pattern here is
**single-writer stateful pods + off-box backups**, which is what the
`k3s-backup.timer` and the runbook's restore procedure support.

## NetworkPolicy caveat

K3s ships flannel, which **does not enforce** `NetworkPolicy` on its own.
To make these policies effective you need a policy-capable CNI (for example
Calico) or K3s's network-policy controller. The manifest is still applied and
harmless without enforcement; see `manifests/networkpolicy.example.yaml`.

## Testing the release

```bash
helm test edge-stateful -n edge
kubectl -n edge get statefulset,pvc,pdb
kubectl -n edge rollout status statefulset/edge-stateful-edge-stateful-postgres
```

## Uninstall

```bash
helm uninstall edge-stateful -n edge
# PVCs created from volumeClaimTemplates are NOT removed by helm uninstall:
kubectl -n edge get pvc
kubectl -n edge delete pvc -l app.kubernetes.io/instance=edge-stateful   # data-destroying
```
