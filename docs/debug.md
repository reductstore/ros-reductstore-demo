# 🧩 MicroK8s + Juju Offline Setup (Standalone, No Router)

Your MicroK8s + Juju environment runs fully **offline**, using a **dummy interface** (`dummy0`) to keep Kubernetes stable even without internet.
This document summarizes setup details, recovery steps, and debugging procedures.

---

## Network Overview

| Component       | Purpose                    | Interface                 | Address        |
| --------------- | -------------------------- | ------------------------- | -------------- |
| Host Node       | MicroK8s + Juju controller | `enp1s0` (wired)          | 192.168.178.94 |
| Dummy Interface | Fallback when offline      | `dummy0`                  | 192.168.178.94 |
| Host Alias      | Local DNS name             | `/etc/hosts → demo.local` |                |
| API Server      | Kubernetes control plane   | Port 16443                |                |
| Kubelet         | Node agent                 | Port 10250                |                |

---

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

   * All Juju workloads should show as `Running`.

3. **Check Juju controller and workloads**

   ```bash
   juju status
   ```

   * `active/idle` = everything is good.
   * If it hangs or fails with `no route to host`, reapply the dummy route:

     ```bash
     sudo ip route replace default dev dummy0 metric 500
     ```

4. **Restart MicroK8s if needed**

   ```bash
   sudo snap restart microk8s
   ```

---

## Recovering Network Connectivity

### When switching **from Ethernet to Wi-Fi**

If you see:

```
dial tcp 192.168.178.94:10250: connect: no route to host
```

Re-add a route for the cluster interface:

```bash
sudo ip addr add 192.168.178.94/24 dev dummy0
sudo ip route replace default dev dummy0 metric 500
```

Kubernetes will immediately reconnect and Juju should recover.

### When Ethernet (enp1s0) is unplugged

Keep your dummy interface up — it provides a logical “anchor” for K8s to bind to:

```bash
ip link show dummy0
```

If it’s missing:

```bash
sudo systemctl restart dummy-net.service
```

### When internet doesn’t work while online

Check that your **dummy route has higher metric** (lower priority):

```bash
ip route show default
# Good example:
# default via 192.168.178.1 dev enp1s0 proto dhcp metric 100
# default dev dummy0 scope link metric 500
```

If not, fix it:

```bash
sudo ip route replace default dev dummy0 metric 500
```

---

## Debugging Common Issues

| Symptom                                                            | Likely Cause                  | Fix                                                   |              |
| ------------------------------------------------------------------ | ----------------------------- | ----------------------------------------------------- | ------------ |
| `no route to host`                                                 | Lost default route            | `sudo ip route replace default dev dummy0 metric 500` |              |
| `tls: certificate is valid for 192.168.178.94, not 192.168.178.95` | Switched network              | Use `demo.local` or reissue certs with new SANs       |              |                                   |
| Internet gone when plugging cable                                  | Dummy route priority too high | Add `metric 500` to dummy route                       |              |
| Dummy interface missing after reboot                               | Service not enabled           | `sudo systemctl enable --now dummy-net.service`       |              |

---

## Managing Applications with Juju

* **List deployed applications**

  ```bash
  juju status
  ```

* **View Juju logs**

  ```bash
  juju debug-log
  ```

  Filter for one application:

  ```bash
  juju debug-log --debug --include reductstore
  ```

* **Access container logs**

  ```bash
  microk8s kubectl logs -n cos-robotics-model -l app.kubernetes.io/name=reductstore -f
  ```

* **Scale an application**

  ```bash
  juju add-unit <app-name> --num-units <n>
  ```

* **Remove an application**

  ```bash
  juju remove-application <app-name>
  ```

* **Inspect relations**

  ```bash
  juju status --relations
  juju remove-relation reductstore:catalogue catalogue:catalogue
  juju relate reductstore:catalogue catalogue:catalogue
  ```

* **Scale to zero**

  ```bash
  juju scale-application traefik 0
  ```

---

## Dummy Interface Reference

Show dummy interface details:

```bash
ip addr show dummy0
ip route show default
```

### Create manually

```bash
sudo modprobe dummy
sudo ip link add dummy0 type dummy
sudo ip addr add 192.168.178.94/24 dev dummy0
sudo ip link set dummy0 up
# sudo ip route replace default dev dummy0 metric 500
# sudo ip route del default dev dummy0
```

## Verifying Everything

```bash
# Check network routes
ip route | grep default

# Check cluster health
microk8s status --wait-ready

# Check kubelet and apiserver ports
sudo ss -lntp | grep -E ':16443|:10250'

# Check workloads
juju status
```
