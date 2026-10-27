# Architecture

The cluster is a small, self-hosted K3s fleet sized for cheap VMs or
Raspberry Pis. It favours **simplicity and fault tolerance over scale**: a
three-node control plane for etcd quorum, a handful of workers running
stateful pods on node-local storage, and off-box backups for durability.

## Topology

```text
                         clients / operators
                                 |
                    kubectl, helm, 40-verify-cluster.sh
                                 |
                 +---------------+----------------+
                 |        Kubernetes API (VIP)    |
                 |   https://k3s-api.edge.local   |
                 +---------------+----------------+
                                 |
        +------------------------+------------------------+
        |                        |                        |
  +-----v------+          +------v-----+          +-------v----+
  | server-1   |          | server-2   |          | server-3   |
  | control    |<-------->| control    |<-------->| control    |
  | plane      |  etcd    | plane      |  etcd    | plane      |
  | + etcd     |  peer    | + etcd     |  peer    | + etcd     |
  | (cluster-  |  2380    |            |  2380    |            |
  |  init)     |          |            |          |            |
  +-----+------+          +-----+------+          +------+-----+
        |                       |                        |
        |   flannel VXLAN (8472/udp) + kubelet (10250)   |
        +-----------------------+------------------------+
                                |
          +---------------------+---------------------+
          |                     |                     |
    +-----v-----+         +-----v-----+         +-----v-----+
    | agent-pi1 |         | agent-pi2 |         |  agent-vm |
    | node-     |         | node-     |         |  node-    |
    | class=pi  |         | class=pi  |         |  class=vm |
    |           |         |           |         |           |
    | local-    |         | local-    |         | local-    |
    | path PV   |         | path PV   |         | path PV   |
    | (edge-    |         | (edge-    |         | (edge-    |
    |  stateful)|         |  stateful)|         |  stateful)|
    +-----------+         +-----------+         +-----------+
```

## Control plane (HA with embedded etcd)

- The **first** server is installed with `--cluster-init`
  (`bootstrap/10-install-server.sh`). This bootstraps the embedded etcd
  datastore and the Kubernetes control plane on one host.
- The **2nd and 3rd** servers join the same datastore with
  `bootstrap/11-install-server-join.sh`, each adding an etcd member.
- Three servers is the smallest **quorum-safe** size: etcd needs a majority
  (2 of 3) to accept writes, so the cluster tolerates one server failing.
  Two servers is *worse* than one, because losing either blocks writes. Always
  keep an odd number.
- The API is exposed through a stable name (`k3s-api.edge.local`) added as a
  `tls-san` so certificates are valid for how clients reach it. In production
  put a TCP load balancer or keepalived VIP in front of the three servers.

## Data plane (agents)

- Agents join with `K3S_URL` + `K3S_TOKEN` (`bootstrap/20-install-agent.sh`);
  they never run etcd.
- Each agent is labelled `node-class=pi` or `node-class=vm` at join time.
  Workloads use `nodeAffinity` on that label, so the same manifests work for
  a mixed x86 + ARM fleet.
- Agents run the bundled containerd. Docker is not required — the K3s
  installer ships its own runtime, which keeps the edge images small.

## Storage (local-path)

- K3s installs a `local-path` StorageClass backed by
  `local-path-provisioner` in `kube-system`.
- `volumeClaimTemplates` in the `edge-stateful` chart provision
  node-local PersistentVolumes. The binding mode is `WaitForFirstConsumer`,
  so the volume is created on whatever node the scheduler chose.
- **Consequence:** a local-path PV is pinned to its node via
  `nodeAffinity`. State does not follow a pod to a new node. The design
  compensates with backups, not replication — this is the honest trade-off of
  cheap edge storage. See `docs/RUNBOOK.md`.

## Stateful workload

`helm/edge-stateful/` deploys:

- a Postgres StatefulSet (single writer) with a headless Service for stable
  per-pod DNS and a ClusterIP Service for clients;
- a Redis-compatible cache StatefulSet with AOF persistence;
- a `PodDisruptionBudget` per component so a drain cannot silently take the
  only replica;
- `topologySpreadConstraints` to spread replicas across hosts when room
  allows;
- a core `NetworkPolicy` (default-deny + intra-release allow).

## Backup and recovery

```text
  k3s-backup.timer  --6h-->  k3s-backup.service --> k3s etcd-snapshot save
                                                        |
                                          /var/lib/rancher/k3s/
                                          server/db/snapshots/
                                                        |
                                     (copy off-box: rsync / object store)
```

- Embedded etcd is snapshotted on a timer (`systemd/k3s-backup.{service,timer}`)
  with retention pruning.
- `systemd/k3s-healthcheck.{service,timer}` probes the runtime every minute
  and restarts it after N consecutive failures, so a wedged node self-heals.
- Recovery from a snapshot is documented in `docs/RUNBOOK.md`.

## Networking

| Port | Proto | Purpose |
| --- | --- | --- |
| 6443 | TCP | Kubernetes API server |
| 2379 | TCP | etcd client (servers only) |
| 2380 | TCP | etcd peer (servers only) |
| 8472 | UDP | flannel VXLAN pod network |
| 10250 | TCP | kubelet API |

Open these between nodes and nothing else is needed for the cluster to
function. `bootstrap/00-preflight.sh` checks that they are free and warns about
host firewalls.

## Failure model

| Failure | Effect | Mitigation |
| --- | --- | --- |
| 1 of 3 servers down | Cluster keeps serving; no new etcd member | Replace with `11-install-server-join.sh` |
| 2 of 3 servers down | Control plane loses quorum; API read-mostly | Restore from etcd snapshot (RUNBOOK) |
| 1 agent down | Its local-path pods stall until rescheduled | Drain + replace node; restore app data |
| Agent disk full | Pod eviction, image pull failures | Disk check in preflight; log caps in config-agent.yaml |
| Token leak | Unauthorised node could join | Rotate with `30-token-helper.sh rotate --yes` |
