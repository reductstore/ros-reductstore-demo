# Jupyter + ROS 2 Jazzy Kernel Setup

This project uses a Python virtual environment together with ROS 2 Jazzy. Follow these steps to make Jupyter notebooks run with ROS libraries (`rclpy`, `rosbag2_py`, etc.) inside VS Code or JupyterLab.

## 1. Create and activate the venv

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## 2. Source ROS 2

```bash
source /opt/ros/jazzy/setup.bash
```

## 3. Install Jupyter support in the venv

```bash
pip install --upgrade pip ipykernel jupyterlab
```

## 4. Register a new Jupyter kernel

```bash
python -m ipykernel install --user --name=ros2_jazzy --display-name "Python (ROS 2 Jazzy)"
```

## 5. Edit the kernel spec to source ROS

Edit the file:

```
~/.local/share/jupyter/kernels/ros2_jazzy/kernel.json
```

Replace `argv` with:

```json
{
  "argv": [
    "/bin/bash",
    "-lc",
    "source /opt/ros/jazzy/setup.bash && /home/anthony/git/ros-reductstore-demo/.venv/bin/python -m ipykernel -f {connection_file}"
  ],
  "display_name": "Python (ROS 2 Jazzy)",
  "language": "python"
}
```

👉 Adjust the Python path if your venv is elsewhere.

## 6. Launch JupyterLab

```bash
jupyter lab
```

## 7. Select the kernel

In JupyterLab or VS Code:

* Open your notebook
* Switch kernel to **Python (ROS 2 Jazzy)**

## 8. Verify

Run in a notebook cell:

```python
import os, sys
print("Python:", sys.executable)
print("ROS_DISTRO:", os.environ.get("ROS_DISTRO"))

import rclpy, rosbag2_py, rosidl_runtime_py
print("ROS imports OK")
```

You should see `ROS_DISTRO: jazzy` and no import errors.
