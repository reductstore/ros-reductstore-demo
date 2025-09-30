#!/usr/bin/env python3
import os
import uuid
import tempfile
import asyncio
import shutil
import time
import logging
import rosbag2_py
from reduct import Client
from reduct.error import ReductError
from rclpy.serialization import deserialize_message
from rosidl_runtime_py.utilities import get_message

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("mcap_to_reduct")

MCAP_INPUT_PATH = "../data/example-010-amr.mcap"
REDUCT_URL = "http://192.168.178.94/cos-robotics-model-reductstore"
API_TOKEN = "reductstore"
BUCKET = "autonomous_mobile_robot"
EPISODE_SECONDS = 10
CONTENT_TYPE_MCAP = "application/mcap"

def get_entry_name(topic: str) -> str:
    safe_name = topic.lstrip("/").replace("/", "_")
    if safe_name.endswith("_restamped"):
        safe_name = safe_name[: -len("_restamped")]
    return safe_name or "root"

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
        meta = rosbag2_py.TopicMetadata(0, tname, ttype, "cdr")
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
    return "application/octet-stream", {}

async def write_raw(bucket, entry, payload, ts_ns, labels, content_type):
    try:
        await bucket.write(entry, payload, ts_ns // 1_000, labels=labels, content_type=content_type)
    except ReductError as e:
        if getattr(e, "status_code", None) in (409,) or getattr(e, "status", None) in (409,):
            log.info("[raw] duplicate entry=%s ts_ns=%d", entry, ts_ns)
            return
        raise

async def write_episode_blob(bucket, blob, ts_ns, labels):
    try:
        await bucket.write("lightweight_telemetry", blob, ts_ns // 1_000, labels=labels, content_type=CONTENT_TYPE_MCAP)
    except ReductError as e:
        if getattr(e, "status_code", None) in (409,) or getattr(e, "status", None) in (409,):
            log.info("[episode] duplicate ts_ns=%d", ts_ns)
            return
        raise

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
    t0 = time.time()
    async with Client(REDUCT_URL, api_token=API_TOKEN) as client:
        log.info("[reduct] connect url=%s", REDUCT_URL)
        bucket = await client.create_bucket(BUCKET, exist_ok=True)
        log.info("[reduct] bucket ready name=%s", BUCKET)
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
                wrote_raw = False
                if not is_downsampled:
                    if hasattr(msg, "format") and hasattr(msg, "data"):
                        img_bytes = bytes(msg.data)
                        content_type, extra = infer_image_content_type(getattr(msg, "format", None), img_bytes)
                        labels = {"topic": topic, "type": topic_type, "serialization": "cdr", **extra}
                        await write_raw(bucket, get_entry_name(topic), img_bytes, t_ns, labels, content_type)
                        img_count += 1
                        wrote_raw = True
                        if img_count % 50 == 0:
                            log.info("[raw] images written=%d", img_count)
                    elif topic_type.endswith("PointCloud2") and hasattr(msg, "data"):
                        pc_bytes = bytes(msg.data)
                        labels = {
                            "topic": topic,
                            "type": topic_type,
                            "serialization": "cdr",
                            "kind": "pointcloud2",
                            "height": str(getattr(msg, "height", 0)),
                            "width": str(getattr(msg, "width", 0)),
                            "point_step": str(getattr(msg, "point_step", 0)),
                            "row_step": str(getattr(msg, "row_step", 0)),
                            "is_dense": str(getattr(msg, "is_dense", False)),
                        }
                        await write_raw(bucket, get_entry_name(topic), pc_bytes, t_ns, labels, "application/octet-stream")
                        pc_count += 1
                        wrote_raw = True
                        if pc_count % 10 == 0:
                            log.info("[raw] pointcloud2 written=%d", pc_count)
                if not wrote_raw:
                    episode_writer.write(topic, cdr_bytes, t_ns)
                    ep_topics[topic.split("/")[-1] or topic] = str(topic_type)
                    ep_msgs += 1
                if window_end_ns is not None and t_ns >= window_end_ns:
                    prev_start = window_start_ns
                    topics_list = sorted(ep_topics)
                    mcap_path = close_episode_return_file(episode_dir, episode_writer)
                    episode_writer = None
                    if mcap_path and os.path.exists(mcap_path):
                        with open(mcap_path, "rb") as f:
                            blob = f.read()
                        mb = len(blob) / (1024 * 1024)
                        log.info("[episode] upload size_mb=%.1f ts_ns=%d index=%d topics=%d msgs=%d", mb, prev_start, episode_index, len(topics_list), ep_msgs)
                        labels = {
                            "window_s": EPISODE_SECONDS,
                            "file_bytes": len(blob),
                            "topics_count": len(topics_list),
                            "messages": ep_msgs,
                            "serialization": "cdr",
                        }
                        labels.update(ep_topics)
                        await write_episode_blob(bucket, blob, prev_start, labels)
                    episode_index += 1
                    ep_topics = {}
                    ep_msgs = 0
                    episode_dir, episode_writer = start_episode(tmp_root, topics)
                    window_start_ns = t_ns
                    window_end_ns = window_start_ns + int(EPISODE_SECONDS * 1e9)
                    log.info("[episode] next window start_ns=%d end_ns=%d index=%d", window_start_ns, window_end_ns, episode_index)
                msg_count += 1
                if msg_count % 1000 == 0:
                    log.info("[progress] messages=%d", msg_count)
            if episode_writer is not None:
                topics_list = sorted(ep_topics)
                mcap_path = close_episode_return_file(episode_dir, episode_writer)
                episode_writer = None
                if mcap_path and os.path.exists(mcap_path) and window_start_ns is not None:
                    with open(mcap_path, "rb") as f:
                        blob = f.read()
                    mb = len(blob) / (1024 * 1024)
                    log.info("[episode] final upload size_mb=%.1f ts_ns=%d index=%d topics=%d msgs=%d", mb, window_start_ns, episode_index, len(topics_list), ep_msgs)
                    labels = {
                        "window_s": EPISODE_SECONDS,
                        "file_bytes": len(blob),
                        "topics_count": len(topics_list),
                        "messages": ep_msgs,
                        "serialization": "cdr",
                    }
                    labels.update(ep_topics)
                    await write_episode_blob(bucket, blob, window_start_ns, labels)
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
    log.info("[done] messages=%d duration_s=%.3f wall_s=%.2f topics=%d images=%d pointcloud2=%d", msg_count, dur, dt, len(topics), img_count, pc_count)

if __name__ == "__main__":
    asyncio.run(main())
