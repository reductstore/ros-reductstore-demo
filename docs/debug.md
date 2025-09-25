## Restarting MicroK8s + Juju after Reboot

1. **Check MicroK8s status**

   ```bash
   microk8s status --wait-ready
   ```

   * If all services are running → continue.
   * If `dqlite` is down, remove stale lock:

     ```bash
     sudo systemctl stop snap.microk8s.daemon-k8s-dqlite
     sudo rm -f /var/snap/microk8s/current/var/kubernetes/backend/lock
     sudo systemctl start snap.microk8s.daemon-k8s-dqlite
     ```

2. **Verify Kubernetes cluster**

   ```bash
   microk8s kubectl get pods -A
   ```

   All pods from Juju workloads should start automatically.

3. **Check Juju services**

   ```bash
   juju status
   ```

   * `active/idle` = everything is good.
   * If something is stuck, Juju will usually resolve it automatically; otherwise run:

     ```bash
     juju debug-log
     ```

4. **(Optional) Restart all MicroK8s services**

   ```bash
   sudo snap restart microk8s
   ```
  