# Troubleshooting

Symptom-first guide for the common edge-cluster failures. Start with the two
diagnostic commands that answer most questions:

```bash
journalctl -u k3s -n 200 --no-pager          # servers
journalctl -u k3s-agent -n 200 --no-pager    # agents
sudo ./bootstrap/40-verify-cluster.sh --etcd
```

---

## Node never becomes Ready

**Check the runtime is even running:**

```bash
systemctl status k3s.service        # or k3s-agent.service
```

**Agent cannot reach the server (most common):**

```bash
# On the agent:
grep K3S_URL /etc/systemd/system/k3s-agent.service.env 2>/dev/null
curl -k https://<SERVER_IP>:6443/readyz   # should return "ok"
```

If the curl hangs or is refused, a firewall or route is blocking 6443. See the
firewall section below.

**Token is wrong or stale:**

```bash
sudo journalctl -u k3s-agent | grep -i "token\|k10\|401\|403"
```

Re-fetch the token on a server (`sudo ./bootstrap/30-token-helper.sh show`)
into `./secrets/node-token` and re-run `20-install-agent.sh`.

**Hostname collision:** two nodes with the same hostname fight over one Node
object and never both become Ready. Set unique hostnames:

```bash
hostnamectl set-hostname edge-agent-03
# and make it permanent in /etc/hosts so it resolves
```

`00-preflight.sh` warns about this when `EXPECTED_UNIQUE_HOSTNAMES` is set.

---

## cgroup v2 / kubelet will not start

K3s containers need a working cgroup hierarchy. Verify:

```bash
mount | grep cgroup
# cgroup v2 (good): cgroup2 on /sys/fs/cgroup type cgroup2
# cgroup v1 (works): multiple cgroup mounts under /sys/fs/cgroup
ls /sys/fs/cgroup/cgroup.controllers   # present on v2
```

- **Raspberry Pi OS / Debian:** enable memory cgroups by adding to
  `/boot/firmware/cmdline.txt` (Pi OS Bookworm) or `/boot/cmdline.txt`
  (older): `cgroup_enable=memory cgroup_memory=1` then reboot.
- **No systemd / minimal containers:** K3s's installer assumes systemd. Use the
  manual binary install and run `k3s` under your own supervisor instead.
- **"failed to create cgroup" in the journal:** reboot after changing kernel
  command-line options; the setting is not live.

---

## Networking: iptables, bridge, and pod-to-pod traffic

K3s sets these up itself, but a host firewall can undo them. Confirm the
sysctls and modules:

```bash
sysctl net.ipv4.ip_forward net.bridge.bridge-nf-call-iptables
lsmod | grep -E 'br_netfilter|overlay'
```

Both sysctls should be `1`. If not:

```bash
sudo modprobe br_netfilter overlay
sudo sysctl -w net.ipv4.ip_forward=1
sudo sysctl -w net.bridge.bridge-nf-call-iptables=1
# persist them under /etc/sysctl.d/90-k3s.conf
```

**Pods resolve names but cannot connect cross-node:** 8472/udp (flannel VXLAN)
is blocked. Allow it between all nodes.

**Legacy iptables on Debian/Ubuntu:** if the journal shows nftables/iptables
conflicts, install `iptables` and switch the alternative:

```bash
sudo update-alternatives --set iptables /usr/sbin/iptables-legacy
sudo update-alternatives --set ip6tables /usr/sbin/ip6tables-legacy
sudo systemctl restart k3s
```

**Host firewall (firewalld/ufw):** open the cluster ports between nodes only:

```bash
# firewalld example
sudo firewall-cmd --permanent --add-port=6443/tcp
sudo firewall-cmd --permanent --add-port=2379-2380/tcp
sudo firewall-cmd --permanent --add-port=8472/udp
sudo firewall-cmd --permanent --add-port=10250/tcp
sudo firewall-cmd --reload
```

---

## Swap is enabled

Kubelet refuses to run (or behaves badly) with swap on:

```bash
sudo swapoff -a
sudo sed -i '/[[:space:]]swap[[:space:]]/ s/^/#/' /etc/fstab
free -h   # Swap should be 0B
```

---

## Image pull failures

```bash
sudo k3s crictl images | head            # what is cached
journalctl -u k3s-agent | grep -i pull
```

- **Slow or metered edge link:** pull once, then rely on `IfNotPresent` (the
  chart sets this). Pre-load images with `k3s ctr images import` if needed.
- **Architecture mismatch:** on a Pi the image must publish an arm64 variant.
  The chart's images (postgres/redis/nginx alpine tags) are multi-arch.
- **DNS inside the cluster:** if pulls fail with name resolution errors, check
  CoreDNS:
  ```bash
  sudo k3s kubectl -n kube-system get pods -l k8s-app=kube-dns
  sudo k3s kubectl -n kube-system logs -l k8s-app=kube-dns --tail=50
  ```

---

## PVC stuck in Pending

```bash
sudo k3s kubectl get pvc -A
sudo k3s kubectl describe pvc <name> -n <ns>
```

- `WaitForFirstConsumer` is **normal**: the PVC stays Pending until a pod
  using it is scheduled. If no pod is using it, that is expected.
- If a pod **is** waiting and the PVC is still Pending, check the provisioner:
  ```bash
  sudo k3s kubectl -n kube-system get deploy local-path-provisioner
  sudo k3s kubectl -n kube-system logs deploy/local-path-provisioner --tail=100
  ```
- "no space left on device" in those logs means the node's disk is full; free
  space (old images via `k3s crictl rmi --prune`, logs) and retry.

---

## Pods stuck after a node came back

A node that rejoined with a **new hostname or IP** leaves orphaned pods. Remove
the old Node object and let the StatefulSet recreate pods:

```bash
sudo k3s kubectl get nodes
sudo k3s kubectl delete node <old-node-name>   # destructive
```

Local-path StatefulSet pods reschedule only onto the node their PV is bound to.
If that node is gone, delete the old PV/PVC pair to let a fresh volume be
provisioned on the new node — this **destroys the old data** (restore from
backup if it mattered):

```bash
sudo k3s kubectl -n edge get pvc
sudo k3s kubectl -n edge delete pvc data-edge-stateful-edge-stateful-postgres-0
```

---

## etcd quorum lost

Symptoms: API returns errors, `40-verify-cluster.sh --etcd` fails, writes
rejected.

1. Confirm how many servers are actually up: `systemctl status k3s` on each.
2. If **two of three** are down and cannot be recovered, quorum is gone —
   restore from the newest snapshot (RUNBOOK section 3).
3. If a **single** server is down, do not panic: bring it back or replace it
   (RUNBOOK section 2a). Do not delete other servers while degraded.

Never run `k3s server --cluster-reset` on a healthy member to "fix" a
one-node outage; that resets the datastore.

---

## Health check timer is restarting k3s repeatedly

```bash
journalctl -u k3s-healthcheck -n 100 --no-pager
cat /run/k3s-healthcheck.fails 2>/dev/null
```

If the probe keeps flipping healthy/unhealthy, the threshold is too low or the
node is genuinely overloaded. Raise `HEALTH_FAIL_THRESHOLD` in
`systemd/k3s-healthcheck.service` (drop-in) and check node pressure:

```bash
sudo k3s kubectl describe node <node> | grep -A5 Conditions
```

If the node is under memory pressure, lower the workload limits in the chart
values rather than disabling the health check.
