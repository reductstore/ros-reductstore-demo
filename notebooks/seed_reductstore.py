#!/usr/bin/env python3
import os
import uuid
import tempfile
import asyncio
import shutil
import time
import logging
import csv
import json
from io import StringIO

import rosbag2_py
from reduct import Client
from reduct.error import ReductError
from rclpy.serialization import deserialize_message
from rosidl_runtime_py.utilities import get_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mcap_to_reduct")

# ---------------------- Config ----------------------
MCAP_INPUT_PATH = "../data/example-010-amr.mcap"
REDUCT_URL = "http://192.168.178.94/cos-robotics-model-reductstore"
API_TOKEN = "reductstore"
BUCKET = "autonomous_mobile_robot"

EPISODE_SECONDS = 10

# Content types
CONTENT_TYPE_MCAP = "application/mcap"
CONTENT_TYPE_JSON = "application/json"
CONTENT_TYPE_CSV = "text/csv"
CONTENT_TYPE_OCTET = "application/octet-stream"

# Entry naming helpers
def get_entry_name(topic: str) -> str:
    safe_name = topic.lstrip("/").replace("/", "_")
    if safe_name.endswith("_restamped"):
        safe_name = safe_name[: -len("_restamped")]
    return safe_name or "root"

def entry_for_raw(topic: str) -> str:
    return f"raw__{get_entry_name(topic)}"

def entry_for_csv(topic: str) -> str:
    return f"csv__{get_entry_name(topic)}"

def entry_for_json(topic: str) -> str:
    return f"json__{get_entry_name(topic)}" 

# ---------------------- ROS2 I/O ----------------------
def open_reader(path: str):
    r = rosbag2_py.SequentialReader()
    r.open(
        rosbag2_py.StorageOptions(uri=path, storage_id="mcap"),
        rosbag2_py.ConverterOptions(input_serialization_format="cdr", output_serialization_format="cdr"),
    )
    return r

def start_episode(root_dir: str, topics_map: dict):
    d = os.path.join(root_dir, f"episode_{uuid.uuid4().hex[:8]}")
    log.info("[episode] start dir=%s", d)
    w = rosbag2_py.SequentialWriter()
    w.open(
        rosbag2_py.StorageOptions(uri=d, storage_id="mcap"),
        rosbag2_py.ConverterOptions(input_serialization_format="cdr", output_serialization_format="cdr"),
    )
    for tname, ttype in topics_map.items():
        meta = rosbag2_py.TopicMetadata(0, tname, ttype, "cdr")  # keep parity with your current code
        w.create_topic(meta)
    return d, w

def close_episode_return_file(episode_dir: str, writer):
    if writer is not None:
        del writer
    if os.path.isdir(episode_dir):
        for fn in os.listdir(episode_dir):
            if fn.endswith(".mcap"):
                fp = os.path.join(episode_dir, fn)
                log.info("[episode] close file=%s size_bytes=%d", fp, os.path.getsize(fp))
                return fp
    return None

# ---------------------- Helpers ----------------------
def infer_image_content_type(fmt: str | None, data: bytes):
    f = (fmt or "").lower()
    if "jpeg" in f or "jpg" in f:
        enc = f.split(";")[0].strip() or None
        labels = {"compression": "jpeg"}
        if enc and enc not in ("jpeg", "jpg", "png"):
            labels["source_encoding"] = enc
        return "image/jpeg", labels
    if "png" in f:
        enc = f.split(";")[0].strip() or None
        labels = {"compression": "png"}
        if enc and enc not in ("jpeg", "jpg", "png"):
            labels["source_encoding"] = enc
        return "image/png", labels
    if data.startswith(b"\xFF\xD8\xFF"):
        return "image/jpeg", {"compression": "jpeg"}
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", {"compression": "png"}
    return CONTENT_TYPE_OCTET, {}

def base_labels(topic: str, topic_type: str, **extra):
    # Include schema hints when cheap
    labels = {"topic": topic, "type": topic_type, "serialization": "cdr"}
    labels.update({k: v for k, v in extra.items() if v is not None})
    return labels

def flatten_row(topic: str, topic_type: str, msg, t_ns: int):
    """Return a dict that is CSV/JSON friendly for 'possible' topics.
       If a topic is not supported, return None (will skip CSV/JSON)."""
    ts = t_ns  # keep ns to avoid rounding error
    # CameraInfo
    if topic_type.endswith("sensor_msgs/msg/CameraInfo"):
        return {
            "ts_ns": ts,
            "frame_id": getattr(msg.header, "frame_id", ""),
            "width": getattr(msg, "width", None),
            "height": getattr(msg, "height", None),
            "distortion_model": getattr(msg, "distortion_model", None),
        }
    # CompressedImage
    if topic_type.endswith("sensor_msgs/msg/CompressedImage"):
        size_bytes = len(getattr(msg, "data", b"") or b"")
        return {
            "ts_ns": ts,
            "frame_id": getattr(msg.header, "frame_id", ""),
            "format": getattr(msg, "format", None),
            "size_bytes": size_bytes,
        }
    # PointCloud2
    if topic_type.endswith("sensor_msgs/msg/PointCloud2"):
        return {
            "ts_ns": ts,
            "frame_id": getattr(msg.header, "frame_id", ""),
            "height": getattr(msg, "height", None),
            "width": getattr(msg, "width", None),
            "point_step": getattr(msg, "point_step", None),
            "row_step": getattr(msg, "row_step", None),
            "is_dense": getattr(msg, "is_dense", None),
        }
    # TFMessage
    if topic_type.endswith("tf2_msgs/msg/TFMessage"):
        count = len(getattr(msg, "transforms", []) or [])
        return {
            "ts_ns": ts,
            "transforms_count": count,
        }
    # IMU
    if topic_type.endswith("sensor_msgs/msg/Imu"):
        ori = getattr(msg, "orientation", None)
        ang = getattr(msg, "angular_velocity", None)
        lin = getattr(msg, "linear_acceleration", None)
        return {
            "ts_ns": ts,
            "frame_id": getattr(msg.header, "frame_id", ""),
            "orientation_x": getattr(ori, "x", None),
            "orientation_y": getattr(ori, "y", None),
            "orientation_z": getattr(ori, "z", None),
            "orientation_w": getattr(ori, "w", None),
            "angular_velocity_x": getattr(ang, "x", None),
            "angular_velocity_y": getattr(ang, "y", None),
            "angular_velocity_z": getattr(ang, "z", None),
            "linear_acceleration_x": getattr(lin, "x", None),
            "linear_acceleration_y": getattr(lin, "y", None),
            "linear_acceleration_z": getattr(lin, "z", None),
        }
    # MagneticField
    if topic_type.endswith("sensor_msgs/msg/MagneticField"):
        mf = getattr(msg, "magnetic_field", None)
        return {
            "ts_ns": ts,
            "frame_id": getattr(msg.header, "frame_id", ""),
            "mag_x": getattr(mf, "x", None),
            "mag_y": getattr(mf, "y", None),
            "mag_z": getattr(mf, "z", None),
        }
    # FluidPressure
    if topic_type.endswith("sensor_msgs/msg/FluidPressure"):
        return {
            "ts_ns": ts,
            "frame_id": getattr(msg.header, "frame_id", ""),
            "pressure": getattr(msg, "fluid_pressure", None),
            "variance": getattr(msg, "variance", None),
        }
    # Temperature
    if topic_type.endswith("sensor_msgs/msg/Temperature"):
        return {
            "ts_ns": ts,
            "frame_id": getattr(msg.header, "frame_id", ""),
            "temperature": getattr(msg, "temperature", None),
            "variance": getattr(msg, "variance", None),
        }
    # Diagnostics and other complex types are skipped for CSV/JSON (still in MCAP and raw)
    return None

def write_csv_blob(rows: list[dict]) -> bytes:
    if not rows:
        return b""
    # stable column order: union of keys
    cols = []
    seen = set()
    for r in rows:
        for k in r.keys():
            if k not in seen:
                seen.add(k)
                cols.append(k)
    buf = StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8")

# ---------------------- Reduct helpers ----------------------
async def write_record(bucket, entry, payload, ts_ns, labels, content_type):
    try:
        await bucket.write(entry, payload, ts_ns // 1_000, labels=labels, content_type=content_type)
    except ReductError as e:
        if getattr(e, "status_code", None) in (409,) or getattr(e, "status", None) in (409,):
            log.info("[dup] entry=%s ts_ns=%d", entry, ts_ns)
            return
        raise

# ---------------------- Main ----------------------
async def main():
    log.info("[init] opening mcap=%s", MCAP_INPUT_PATH)
    reader = open_reader(MCAP_INPUT_PATH)
    topic_types = reader.get_all_topics_and_types()
    topics = {tt.name: tt.type for tt in topic_types}
    log.info("[init] topics=%d", len(topics))
    for k, v in topics.items():
        log.info("[init] topic %s -> %s", k, v)

    tmp_root = tempfile.mkdtemp(prefix="mcap_episodes_")
    log.info("[init] tmp_root=%s", tmp_root)

    first_ts_ns = None
    last_ts_ns = None
    msg_count = 0
    img_count = 0
    pc_count = 0

    episode_dir = None
    episode_writer = None
    window_start_ns = None
    window_end_ns = None
    episode_index = 0
    ep_topics = {}
    ep_msgs = 0

    # Per-episode per-topic row accumulator for CSV/JSON
    per_topic_rows: dict[str, list[dict]] = {}

    t0 = time.time()
    async with Client(REDUCT_URL, api_token=API_TOKEN) as client:
        log.info("[reduct] connect url=%s", REDUCT_URL)
        bucket = await client.create_bucket(BUCKET, exist_ok=True)
        log.info("[reduct] bucket ready name=%s", BUCKET)

        # WARNING: clearing existing entries (kept from your original)
        log.info("[reduct] clearing existing entries")
        for e in await bucket.get_entry_list():
            log.info("[reduct] delete entry=%s", e.name)
            await bucket.remove_entry(e.name)

        try:
            while reader.has_next():
                topic, cdr_bytes, t_ns = reader.read_next()
                topic_type = topics.get(topic, "")
                msg_type = get_message(topic_type)
                msg = deserialize_message(cdr_bytes, msg_type)

                if first_ts_ns is None:
                    first_ts_ns = t_ns
                    episode_dir, episode_writer = start_episode(tmp_root, topics)
                    window_start_ns = t_ns
                    window_end_ns = window_start_ns + int(EPISODE_SECONDS * 1e9)
                    log.info("[episode] window start_ns=%d end_ns=%d index=%d", window_start_ns, window_end_ns, episode_index)

                last_ts_ns = t_ns
                is_downsampled = "downsampled" in topic.lower()

                # ---------- (1) Always write per-topic raw record ----------
                # Prefer native bytes for images/pointclouds; fallback to CDR
                wrote_native = False
                if hasattr(msg, "format") and hasattr(msg, "data"):  # CompressedImage
                    img_bytes = bytes(msg.data)
                    content_type, extra = infer_image_content_type(getattr(msg, "format", None), img_bytes)
                    labels = base_labels(topic, topic_type, **extra)
                    await write_record(bucket, entry_for_raw(topic), img_bytes, t_ns, labels, content_type)
                    img_count += 1
                    wrote_native = True
                    if img_count % 50 == 0:
                        log.info("[raw] images written=%d", img_count)
                elif topic_type.endswith("PointCloud2") and hasattr(msg, "data"):
                    pc_bytes = bytes(msg.data)
                    labels = base_labels(
                        topic,
                        topic_type,
                        kind="pointcloud2",
                        height=str(getattr(msg, "height", 0)),
                        width=str(getattr(msg, "width", 0)),
                        point_step=str(getattr(msg, "point_step", 0)),
                        row_step=str(getattr(msg, "row_step", 0)),
                        is_dense=str(getattr(msg, "is_dense", False)),
                    )
                    await write_record(bucket, entry_for_raw(topic), pc_bytes, t_ns, labels, CONTENT_TYPE_OCTET)
                    pc_count += 1
                    wrote_native = True
                    if pc_count % 10 == 0:
                        log.info("[raw] pointcloud2 written=%d", pc_count)

                if not wrote_native:
                    # Store the CDR bytes for all topics so "data of each topic" lives in distinct entries
                    labels = base_labels(topic, topic_type)
                    await write_record(bucket, entry_for_raw(topic), cdr_bytes, t_ns, labels, CONTENT_TYPE_OCTET)

                # ---------- (2) Keep building MCAP episode (skip only if aggressively downsampled?) ----------
                # We still include ALL messages in MCAP so episodes are complete.
                episode_writer.write(topic, cdr_bytes, t_ns)
                ep_topics[topic.split("/")[-1] or topic] = str(topic_type)
                ep_msgs += 1

                # ---------- (3) Accumulate CSV/JSON-friendly rows when feasible ----------
                row = flatten_row(topic, topic_type, msg, t_ns)
                if row is not None:
                    per_topic_rows.setdefault(topic, []).append(row)

                # ---------- (4) Close window & upload artifacts ----------
                if window_end_ns is not None and t_ns >= window_end_ns:
                    prev_start = window_start_ns
                    topics_list = sorted(ep_topics)
                    mcap_path = close_episode_return_file(episode_dir, episode_writer)
                    episode_writer = None

                    # Upload MCAP as "mcap" entry (renamed)
                    if mcap_path and os.path.exists(mcap_path):
                        with open(mcap_path, "rb") as f:
                            blob = f.read()
                        mb = len(blob) / (1024 * 1024)
                        log.info("[episode] upload mcap size_mb=%.1f ts_ns=%d index=%d topics=%d msgs=%d",
                                 mb, prev_start, episode_index, len(topics_list), ep_msgs)
                        labels = {
                            "window_s": EPISODE_SECONDS,
                            "file_bytes": len(blob),
                            "topics_count": len(topics_list),
                            "messages": ep_msgs,
                            "serialization": "cdr",
                        }
                        labels.update(ep_topics)
                        await write_record(bucket, "mcap", blob, prev_start, labels, CONTENT_TYPE_MCAP)

                    # Upload per-topic CSV/JSON duplicates (if any rows gathered)
                    for t, rows in per_topic_rows.items():
                        if not rows:
                            continue
                        # CSV
                        csv_blob = write_csv_blob(rows)
                        if csv_blob:
                            csv_labels = {
                                "window_s": EPISODE_SECONDS,
                                "rows": len(rows),
                                "topic": t,
                                "type": topics.get(t, ""),
                                "schema_hint": ",".join(sorted(rows[0].keys())),
                            }
                            await write_record(bucket, entry_for_csv(t), csv_blob, prev_start, csv_labels, CONTENT_TYPE_CSV)
                        # JSON
                        json_blob = json.dumps(rows, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                        json_labels = {
                            "window_s": EPISODE_SECONDS,
                            "rows": len(rows),
                            "topic": t,
                            "type": topics.get(t, ""),
                        }
                        await write_record(bucket, entry_for_json(t), json_blob, prev_start, json_labels, CONTENT_TYPE_JSON)

                    # Reset episode accumulators
                    episode_index += 1
                    ep_topics = {}
                    ep_msgs = 0
                    per_topic_rows = {}

                    # Start next episode window
                    episode_dir, episode_writer = start_episode(tmp_root, topics)
                    window_start_ns = t_ns
                    window_end_ns = window_start_ns + int(EPISODE_SECONDS * 1e9)
                    log.info("[episode] next window start_ns=%d end_ns=%d index=%d",
                             window_start_ns, window_end_ns, episode_index)

                msg_count += 1
                if msg_count % 1000 == 0:
                    log.info("[progress] messages=%d", msg_count)

            # ---------- Final flush ----------
            if episode_writer is not None:
                topics_list = sorted(ep_topics)
                mcap_path = close_episode_return_file(episode_dir, episode_writer)
                episode_writer = None
                if mcap_path and os.path.exists(mcap_path) and window_start_ns is not None:
                    with open(mcap_path, "rb") as f:
                        blob = f.read()
                    mb = len(blob) / (1024 * 1024)
                    log.info("[episode] final mcap size_mb=%.1f ts_ns=%d index=%d topics=%d msgs=%d",
                             mb, window_start_ns, episode_index, len(topics_list), ep_msgs)
                    labels = {
                        "window_s": EPISODE_SECONDS,
                        "file_bytes": len(blob),
                        "topics_count": len(topics_list),
                        "messages": ep_msgs,
                        "serialization": "cdr",
                    }
                    labels.update(ep_topics)
                    await write_record(bucket, "mcap", blob, window_start_ns, labels, CONTENT_TYPE_MCAP)

                # per-topic CSV/JSON at tail
                for t, rows in per_topic_rows.items():
                    if not rows:
                        continue
                    csv_blob = write_csv_blob(rows)
                    if csv_blob:
                        csv_labels = {
                            "window_s": EPISODE_SECONDS,
                            "rows": len(rows),
                            "topic": t,
                            "type": topics.get(t, ""),
                            "schema_hint": ",".join(sorted(rows[0].keys())),
                        }
                        await write_record(bucket, entry_for_csv(t), csv_blob, window_start_ns, csv_labels, CONTENT_TYPE_CSV)
                    json_blob = json.dumps(rows, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
                    json_labels = {
                        "window_s": EPISODE_SECONDS,
                        "rows": len(rows),
                        "topic": t,
                        "type": topics.get(t, ""),
                    }
                    await write_record(bucket, entry_for_json(t), json_blob, window_start_ns, json_labels, CONTENT_TYPE_JSON)

        finally:
            try:
                del reader
            except Exception:
                pass
            try:
                shutil.rmtree(tmp_root, ignore_errors=True)
                log.info("[cleanup] removed %s", tmp_root)
            except Exception:
                pass

    dur = ((last_ts_ns - first_ts_ns) / 1e9) if (first_ts_ns and last_ts_ns) else 0.0
    dt = time.time() - t0
    log.info("[done] messages=%d duration_s=%.3f wall_s=%.2f topics=%d images=%d pointcloud2=%d",
             msg_count, dur, dt, len(topics), img_count, pc_count)

if __name__ == "__main__":
    asyncio.run(main())
