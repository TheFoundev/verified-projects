# manifests/local-path.md
#
# Notes on the local-path StorageClass that K3s installs by default.
# This file is documentation, not a manifest; see hello-edge/ for YAML.
#
# ---
#
# ## What it is
#
# K3s ships a StorageClass named `local-path` backed by the
# `local-path-provisioner` in kube-system. It dynamically provisions
# PersistentVolumes from a directory on the node that hosts the pod
# (default /var/lib/rancher/k3s/storage).
#
# ## Key properties
#
#   * VolumeBindingMode is WaitForFirstConsumer: the PVC stays Pending until a
#     pod that uses it is scheduled, so the volume lands on whatever node the
#     scheduler picked.
#   * reclaimPolicy is Delete: deleting a PVC deletes the underlying directory.
#   * The PV has a nodeAffinity to the node that created it, so a pod using it
#     can only be scheduled back onto that same node.
#
# ## Inspect it on a live cluster
#
#   kubectl get storageclass local-path -o yaml
#   kubectl -n kube-system get deploy local-path-provisioner
#   kubectl get pvc -A
#   kubectl get pv -o custom-columns=NAME:.metadata.name,NODE:.spec.nodeAffinity
#
# ## Make it the default
#
# On a fresh K3s cluster local-path is already annotated as the default
# StorageClass. To confirm:
#
#   kubectl get storageclass
#   # NAME         PROVISIONER             RECLAIMPOLICY   VOLUMEBINDINGMODE
#   # local-path   rancher.io/local-path   Delete          WaitForFirstConsumer
#
# ## Back up local volumes
#
# Because volumes are node-bound, back up at two levels:
#   1. cluster state -> k3s etcd snapshots (systemd/k3s-backup.timer)
#   2. application data -> your own dump/copy off-box (see docs/RUNBOOK.md)
