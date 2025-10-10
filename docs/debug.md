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

## Managing applications with Juju

You can manage your deployed applications using Juju commands. Here are some common tasks:

- **Check application status:**

  ```bash
  juju status
  ```

- **Debug logs:**

   First, the application needs to be configured to log at `DEBUG` level:

   ```bash
   juju config reductstore log-level=debug
   ```

   Check config:

   ```bash
   juju config reductstore log-level
   ```

   Then, to view real-time logs for all applications, use:

   ```bash
   juju debug-log
   ```

   for one application, use:

   ```bash
   juju debug-log --debug --include reductstore

   juju debug-log --debug --include-module unit.reductstore/0.juju-log --replay
   ```

   Or directly access the logs of a specific unit:

   ```bash
   microk8s kubectl logs -c reductstore reductstore-0 -n cos-robotics-model -f
   ```

- **Scale an application:**

  ```bash
   juju add-unit <application-name> --num-units <number-of-units>
   ```

- **Remove an application:**

   ```bash
    juju remove-application <application-name>
    ```

- **Access application catalogue endpoint:**

   ```bash
   juju show-unit catalogue/0 --endpoint catalogue
   ```

- **Access application ingress endpoint:**

   ```bash
   juju show-unit reductstore/0 --endpoint ingress --app --format yaml
   ```

- **Check relations:**

   ```bash
   juju status --relations
   ```

   Remove the existing relation:

   ```bash
   juju remove-relation reductstore:catalogue catalogue:catalogue
   ```

   Re-add it:

   ```bash
   juju relate reductstore:catalogue catalogue:catalogue
   ```

- **Scale to zero units:**

   ```bash
   juju scale-application traefik 0
   ```
