# Runbook

Operational procedures for the K3s edge cluster. Commands that change cluster
state are marked and should be rehearsed on a lab cluster first.

Conventions:
- Run server-node commands as `root` (use `sudo`).
- `k3s kubectl` is used when plain `kubectl` is not installed on a node.
- `<SERVER_IP>` is the address of the first server (or the API VIP).

---

## 1. Join a new node

### 1a. Join an additional control-plane server (2nd / 3rd)

On any existing server, export the token to a file readable only by you:

```bash
sudo ./bootstrap/30-token-helper.sh show > ./secrets/node-token
chmod 600 ./secrets/node-token
```

Copy `./secrets/node-token` to the new server, then on the **new** server:

```bash
sudo ./bootstrap/00-preflight.sh --role server
sudo ./bootstrap/11-install-server-join.sh \
  --server https://<SERVER_IP>:6443 \
  --token-file ./secrets/node-token \
  --tls-san k3s-api.edge.local
```

Confirm quorum from any server:

```bash
sudo ./bootstrap/40-verify-cluster.sh --etcd
sudo k3s kubectl get nodes -o wide
```

> Keep the server count odd. Going from 1 to 2 servers does **not** add fault
> tolerance; go straight to 3.

### 1b. Join a new agent (worker)

On the **new** agent:

```bash
sudo ./bootstrap/00-preflight.sh --role agent
sudo ./bootstrap/20-install-agent.sh \
  --server https://<SERVER_IP>:6443 \
  --token-file ./secrets/node-token \
  --node-class pi        # or vm
```

Verify:

```bash
sudo k3s kubectl get nodes -l node-class=pi -o wide
```

---

## 2. Replace a failed node

### 2a. Replace a failed server

1. If the cluster still has quorum (2 of 3 servers up), remove the dead member
   from the etcd cluster **from a surviving server** (destructive):
   ```bash
   sudo k3s kubectl get nodes                 # note the dead node name
   sudo k3s kubectl delete node <dead-server>  # deletes the Node object
   ```
   K3s reconciles embedded etcd membership on restart; if the member lingers
   in `etcdctl member list`, remove it there with
   `etcdctl member remove <id>`.
2. Provision a replacement host with a **different hostname** and follow
   procedure **1a** to join it back as a server.
3. Confirm quorum (`40-verify-cluster.sh --etcd`) and that etcd is healthy.

### 2b. Replace a failed agent

1. Note which pods were on the failed node:
   ```bash
   sudo k3s kubectl get pods -A -o wide --field-selector spec.nodeName=<dead-agent>
   ```
2. Remove it (destructive — the Node object only; local-path data on the dead
   host is gone with the host):
   ```bash
   sudo k3s kubectl drain <dead-agent> --ignore-daemonsets --delete-emptydir-data --force
   sudo k3s kubectl delete node <dead-agent>
   ```
3. Join the replacement with the **same `node-class`** (procedure **1b**).
4. StatefulSet pods that used node-local storage will come back but with
   **empty volumes**: the old PV is bound to the dead node. If the data
   mattered, restore the application-level backup onto the new volume (see
   section 6). This is the cost of local-path storage — plan for it.

---

## 3. Restore from an etcd snapshot

Snapshots live on each server under
`/var/lib/rancher/k3s/server/db/snapshots/` (or whatever `SNAPSHOT_DIR` you set).

### 3a. Restore in place (single node) — destructive

```bash
sudo systemctl stop k3s
sudo cp -a /var/lib/rancher/k3s/server/db/snapshots/<snapshot> /tmp/restore.db
sudo k3s server \
  --cluster-reset \
  --cluster-reset-restore-path=/tmp/restore.db
```

`--cluster-reset` rewrites the datastore from the snapshot and then exits.
Start K3s normally afterwards:

```bash
sudo systemctl start k3s
sudo ./bootstrap/40-verify-cluster.sh --etcd
```

### 3b. Restore a multi-server cluster

1. Stop K3s on **all** servers: `sudo systemctl stop k3s`
2. On **one** server run the restore in 3a (`--cluster-reset` +
   `--cluster-reset-restore-path`). This server becomes the new etcd
   bootstrap node.
3. On each of the **other** servers:
   ```bash
   sudo rm -rf /var/lib/rancher/k3s/server/db/etcd   # destructive
   sudo systemctl start k3s                          # they rejoin the reset cluster
   ```
4. Start K3s on the first server and verify quorum.

> After any restore, take a fresh snapshot immediately and confirm the control
> plane is healthy before rejoining agents.

---

## 4. Rotate the node-token

Rotating invalidates the old token; existing agents keep running until they
reconnect, then need the new token.

On a server (destructive — restarts k3s):

```bash
sudo ./bootstrap/30-token-helper.sh rotate --yes
sudo ./bootstrap/30-token-helper.sh show > ./secrets/node-token
```

Then re-join any node that needs it (procedures 1a / 1b) or restart the agent
service so it re-reads the token file you distribute.

---

## 5. Upgrade K3s

K3s upgrades in place by re-running the installer with a pinned version.

On each node, one at a time, **servers last**:

```bash
# Pin the target version; never upgrade all nodes at once.
sudo INSTALL_K3S_VERSION=v1.29.6+k3s1 systemctl stop k3s || true

# Re-run the appropriate installer with the new version:
sudo ./bootstrap/20-install-agent.sh --server https://<SERVER_IP>:6443 \
  --token-file ./secrets/node-token --node-class pi
```

For servers, use the join/serve installer with the same version. Practical
order:

1. Drain and upgrade one **agent**; verify `40-verify-cluster.sh`.
2. Repeat for other agents.
3. Upgrade **servers** one at a time, waiting for quorum to recover between
   each. Never reboot two servers at the same moment.

Always take an etcd snapshot (`make backup`) immediately before upgrading the
first server.

---

## 6. Drain a node for maintenance

```bash
# Cordon + drain (safe drain; pods reschedule if capacity allows).
sudo ./bootstrap/40-verify-cluster.sh --drain <node>

# ... do maintenance ...

# Make it schedulable again.
sudo ./bootstrap/40-verify-cluster.sh --uncordon <node>
```

Notes:

- local-path pods can only schedule back onto their original node, so a drain
  of a node whose local-path pods have no PDB will block. The chart ships a
  `PodDisruptionBudget`; with a single replica it will protect the pod and the
  drain will wait — reduce replicas to 0 for that workload if you truly need
  the node empty.
- `--delete-emptydir-data` is included; it does not delete local-path PVCs.

---

## 7. Back up application data (beyond etcd)

etcd snapshots do not contain local-path volume contents. Back up app data at
the source:

- **Postgres** (from inside the cluster):
  ```bash
  kubectl -n edge exec edge-stateful-edge-stateful-postgres-0 -- \
    pg_dump -U edgeuser edgeapp | gzip > /backups/edgeapp-$(date +%F).sql.gz
  ```
- **Cache**: it is a cache; treat it as disposable, or `BGSAVE` and copy
  `/data` if it is authoritative for something.
- Copy all backups **off-box**. A snapshot on the same disk as the data is not
  a backup.

---

## 8. Uninstall a node

```bash
# Dry-run first (prints the plan, changes nothing):
sudo ./bootstrap/99-uninstall.sh --role agent

# Actually remove:
sudo ./bootstrap/99-uninstall.sh --role agent --yes
```

Never uninstall more than one **server** at a time without confirming quorum
afterwards — removing servers shrinks the etcd cluster.
