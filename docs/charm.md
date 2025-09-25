# ReductStore Juju Charm

This repository contains the **Kubernetes charm** for [ReductStore](https://www.reduct.store), a time-series object store optimized for high-frequency unstructured data (images, logs, vibration data, etc.).

It allows deploying ReductStore into Kubernetes clusters with [Juju](https://juju.is) and integrating with ingress, monitoring, and other charms.

## ⚙️ Prerequisites

* [Juju](https://juju.is/docs/juju/installing) installed and bootstrapped (e.g. `juju bootstrap microk8s`)
* [Charmcraft](https://juju.is/docs/sdk/charmcraft) installed
* A Charmhub account (`charmcraft login`)


## 🏗️ Build the charm locally

```bash
cd reductstore-charm
charmcraft pack
```

This produces a file like:

```
reductstore_amd64.charm
```

## ☁️ Publish to Charmhub

### 1. Register the charm name

```bash
charmcraft register reductstore-k8s
```

### 2. Upload the charm

```bash
charmcraft upload reductstore-k8s_amd64.charm
```

### 3. Upload the OCI image resource

```bash
charmcraft upload-resource reductstore-k8s reductstore-image --image=docker://docker.io/reduct/store:latest
```

### 4. Release to a channel

```bash
charmcraft release reductstore-k8s --revision <REV> --channel edge --resource reductstore-image:<RES_REV>
```

Check available revisions:

```bash
charmcraft revisions reductstore-k8s
```

Check available ressources:

```bash
charmcraft resources reductstore-k8s
```

**ℹ️ Notes on revisions and channels**

- Every time you upload a charm, Charmhub assigns a new revision (1, 2, 3…).
- Revisions are immutable snapshots of your charm.
- Channels (edge, beta, candidate, stable) are movable pointers — you decide which revision gets released to which channel.
  - Example: revision 2 can be in edge while revision 1 is still in stable.

## 🔄 Managing releases

* **Promote a revision** to a stable channel:

  ```bash
  charmcraft release reductstore-k8s --revision <REV> --channel stable
  ```
* **Close a channel** (stop serving it):

  ```bash
  charmcraft close reductstore-k8s/edge
  ```
* **List releases**:

  ```bash
  charmcraft status reductstore-k8s
  ```

## 📦 Deploy from Charmhub

Once released, users can deploy with:

```bash
juju deploy reductstore-k8s --channel edge --trust
```
