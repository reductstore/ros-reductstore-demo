#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import uuid
import asyncio
import logging
import json
import random
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional
from datetime import datetime, timedelta, timezone
from io import BytesIO
from collections import defaultdict

import rosbag2_py
from rclpy.serialization import deserialize_message
from rosidl_runtime_py.utilities import get_message
from tqdm import tqdm

from reduct import Client

# For image rotation/re-encode
try:
    from PIL import Image
except ImportError as e:
    raise SystemExit("Please install Pillow: pip install pillow") from e

# ---------------------- Logging ----------------------
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("robot_seed")


# ---------------------- CONFIG ----------------------
@dataclass
class Config:
    # Input clip (≈30s)
    MCAP_INPUT_PATH: str = "./data/example-010-amr.mcap"

    # Target robot endpoint & bucket
    REDUCT_URL: str = "http://orion.field.demo"  # e.g. http://orion.field.demo
    API_TOKEN: str = "reductstore"
    BUCKET: str = "orion"  # bucket equals robot name

    # Session plan: 10-min sessions, spaced out, covering past→future window
    CLIP_SECONDS: float = 30.0
    SESSION_DURATION_SECONDS: int = 10 * 60
    SESSION_INTERVAL_SECONDS: int = 18 * 60 * 60  # start a session every 18 hours
    START_OFFSET: timedelta = timedelta(days=-1)  # ~1 month ago
    END_OFFSET: timedelta = timedelta(days=+0)  # ~0.5 month ahead

    # Save rules
    SAVE_IMAGES: bool = True  # keep JPEG/PNG only, rotated -90°
    SAVE_POINTCLOUD2: bool = True  # keep as raw bytes
    SAVE_OTHER_AS_JSON: bool = True  # interesting structured topics -> JSON
    SAVE_TF: bool = False  # never save TF

    # Topic filters - only save data from these specific topics
    ALLOWED_IMAGE_TOPICS: List[str] = field(
        default_factory=lambda: [
            "/rsense/color/image_raw/compressed_restamped_downsampled"
        ]
    )
    ALLOWED_POINTCLOUD_TOPICS: List[str] = field(
        default_factory=lambda: [
            "/os_node/segmented_point_cloud_no_destagger_restamped"
        ]
    )
    ALLOWED_CAMERA_INFO_TOPICS: List[str] = field(
        default_factory=lambda: ["/rsense/color/camera_info_restamped"]
    )

    # Label probabilities (per-record sprinkle)
    P_EVENT: float = 0.10
    P_VISION: float = 0.06
    P_LIDAR_ALERT: float = 0.05

    # Random seed (None => non-deterministic)
    RANDOM_SEED: Optional[int] = 42

    # Throttling settings
    TARGET_IMAGE_HZ: float = 1.0  # Target frequency for images
    TARGET_POINTCLOUD_HZ: float = 0.01  # Target frequency for point clouds

    # JSON batching settings
    JSON_BATCH_SIZE: int = 1000  # Number of JSON rows to accumulate before writing


CFG = Config()

# ---------------------- Entry names ----------------------
IMAGE_ENTRY = "image"
POINTCLOUD_ENTRY = "point_cloud"

# ---------------------- Content types ----------------------
CONTENT_TYPE_JSON = "application/json"
CONTENT_TYPE_OCTET = "application/octet-stream"
CONTENT_TYPE_JPEG = "image/jpeg"
CONTENT_TYPE_PNG = "image/png"


# ---------------------- Timestamp allocator (avoid 409) ----------------------
class TsAllocator:
    """Ensures strictly-increasing microsecond timestamps per entry."""

    def __init__(self):
        self._last_us = defaultdict(lambda: -1)

    def alloc_us(self, entry: str, ts_ns: int) -> int:
        cand = ts_ns // 1_000  # ns -> µs
        last = self._last_us[entry]
        if cand <= last:
            cand = last + 1
        self._last_us[entry] = cand
        return cand


ts_alloc = TsAllocator()


# ---------------------- Bag I/O ----------------------
def open_reader(path: str):
    r = rosbag2_py.SequentialReader()
    r.open(
        rosbag2_py.StorageOptions(uri=path, storage_id="mcap"),
        rosbag2_py.ConverterOptions(
            input_serialization_format="cdr", output_serialization_format="cdr"
        ),
    )
    return r


# ---------------------- Helpers ----------------------
def is_tf_type(topic_type: str) -> bool:
    return topic_type.endswith("tf2_msgs/msg/TFMessage")


def infer_image_content_type(fmt: Optional[str], data: bytes):
    f = (fmt or "").lower()
    if "jpeg" in f or "jpg" in f or data.startswith(b"\xff\xd8\xff"):
        return CONTENT_TYPE_JPEG
    if "png" in f or data.startswith(b"\x89PNG\r\n\x1a\n"):
        return CONTENT_TYPE_PNG
    return None  # unknown/unsupported


def base_labels(topic: str, topic_type: str, **extra):
    labels = {"topic": topic, "type": topic_type, "serialization": "cdr"}
    labels.update({k: v for k, v in extra.items() if v is not None})
    return labels


def get_json_entry_name(topic: str) -> str:
    """Map topic names to simplified entry names for JSON data."""
    topic_mapping = {
        "/rsense/color/camera_info_restamped": "camera_info",
        "/vectornav/Mag_restamped": "magnetic_field",
        "/vectornav/Pres_restamped": "pressure",
        "/vectornav/Temp_restamped": "temperature",
        "/vectornav/IMU_restamped": "imu",
    }

    # Return mapped name if exists, otherwise use default format
    if topic in topic_mapping:
        return topic_mapping[topic]
    else:
        return "json__" + topic.lstrip("/").replace("/", "_")


def flatten_row(topic: str, topic_type: str, msg, t_ns: int):
    # Only "interesting" structured data -> JSON.
    ts = t_ns
    if (
        topic_type.endswith("sensor_msgs/msg/CameraInfo")
        and topic in CFG.ALLOWED_CAMERA_INFO_TOPICS
    ):
        return {
            "ts_ns": ts,
            "frame_id": getattr(msg.header, "frame_id", ""),
            "width": getattr(msg, "width", None),
            "height": getattr(msg, "height", None),
            "distortion_model": getattr(msg, "distortion_model", None),
        }
    if (
        topic_type.endswith("sensor_msgs/msg/Imu")
        or topic == "/vectornav/IMU_restamped"
    ):
        ori = getattr(msg, "orientation", None)
        ang = getattr(msg, "angular_velocity", None)
        lin = getattr(msg, "linear_acceleration", None)
        return {
            "ts_ns": ts,
            "frame_id": getattr(msg.header, "frame_id", ""),
            "orientation": {
                "x": getattr(ori, "x", None),
                "y": getattr(ori, "y", None),
                "z": getattr(ori, "z", None),
                "w": getattr(ori, "w", None),
            },
            "angular_velocity": {
                "x": getattr(ang, "x", None),
                "y": getattr(ang, "y", None),
                "z": getattr(ang, "z", None),
            },
            "linear_acceleration": {
                "x": getattr(lin, "x", None),
                "y": getattr(lin, "y", None),
                "z": getattr(lin, "z", None),
            },
        }
    if (
        topic_type.endswith("sensor_msgs/msg/MagneticField")
        or topic == "/vectornav/Mag_restamped"
    ):
        mf = getattr(msg, "magnetic_field", None)
        return {
            "ts_ns": ts,
            "frame_id": getattr(msg.header, "frame_id", ""),
            "magnetic_field": {
                "x": getattr(mf, "x", None),
                "y": getattr(mf, "y", None),
                "z": getattr(mf, "z", None),
            },
        }
    if (
        topic_type.endswith("sensor_msgs/msg/FluidPressure")
        or topic == "/vectornav/Pres_restamped"
    ):
        return {
            "ts_ns": ts,
            "frame_id": getattr(msg.header, "frame_id", ""),
            "pressure": getattr(msg, "fluid_pressure", None),
            "variance": getattr(msg, "variance", None),
        }
    if (
        topic_type.endswith("sensor_msgs/msg/Temperature")
        or topic == "/vectornav/Temp_restamped"
    ):
        return {
            "ts_ns": ts,
            "frame_id": getattr(msg.header, "frame_id", ""),
            "temperature": getattr(msg, "temperature", None),
            "variance": getattr(msg, "variance", None),
        }
    # Extend with more message types if needed.
    return None


def write_json_blob(rows: List[dict]) -> bytes:
    return json.dumps(rows, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


async def write_json_batch(
    entry_name: str, rows: List[dict], session_ctx: Dict[str, str]
):
    """Write a batch of JSON rows to the store."""
    if not rows:
        return

    payload = write_json_blob(rows)
    # Use the timestamp of the last row for the batch
    last_ts_ns = rows[-1]["ts_ns"]

    labels = {
        "rows": str(len(rows)),
        "topic": rows[0].get("topic", "unknown"),
        "type": "json_batch",
        **session_ctx,
    }
    labels = sprinkle_incidents(labels)
    ts_us = ts_alloc.alloc_us(entry_name, last_ts_ns)
    await write_record_us(entry_name, payload, ts_us, labels, CONTENT_TYPE_JSON)


# ---------------------- Synthetic labels ----------------------
# Numeric ranges for different metrics (designed for Grafana visualization)
BATTERY_RANGE = (15, 100)  # Battery percentage: 15-100%
CPU_TEMP_RANGE = (45, 85)  # CPU temperature: 45-85°C
MEMORY_USAGE_RANGE = (20, 95)  # Memory usage: 20-95%
NETWORK_LATENCY_RANGE = (1, 150)  # Network latency: 1-150ms
VIBRATION_RANGE = (0, 25)  # Vibration level: 0-25 units
SAFETY_SCORE_RANGE = (60, 100)  # Safety compliance: 60-100%
OBSTACLE_DISTANCE_RANGE = (0, 500)  # Nearest obstacle: 0-500cm
SPEED_RANGE = (0, 180)  # Current speed: 0-1.8 m/s (scaled to 0-180)
CONFIDENCE_RANGE = (70, 99)  # AI confidence: 70-99%
SIGNAL_STRENGTH_RANGE = (-90, -30)  # WiFi signal: -90 to -30 dBm

# Site and shift mappings (keeping some categorical for context)
SITES = ["alpha-plant", "beta-yard", "charlie-warehouse"]
SHIFTS = ["day", "swing", "night"]

# Zone types with numeric risk levels
ZONE_RISK_MAPPING = {
    "safe_zone": (0, 20),
    "caution_zone": (20, 60),
    "restricted_zone": (60, 100),
}


def session_context(robot_name: str) -> Dict[str, str]:
    return {
        "robot": robot_name,
        "run_id": uuid.uuid4().hex[:8],
        "state_id": f"state-{random.randint(1,5)}",
        "mission_id": f"mission-{random.randint(1000,9999)}",
        "operator_id": f"op-{random.randint(10,99)}",
        "site": random.choice(SITES),
        "shift": random.choice(SHIFTS),
    }


def sprinkle_incidents(labels: Dict[str, str]) -> Dict[str, str]:
    """Add synthetic numeric metrics for rich Grafana visualization."""

    # Core performance metrics (always present)
    labels["battery_pct"] = str(random.randint(*BATTERY_RANGE))
    labels["cpu_temp_c"] = str(random.randint(*CPU_TEMP_RANGE))
    labels["memory_pct"] = str(random.randint(*MEMORY_USAGE_RANGE))
    labels["net_latency_ms"] = str(random.randint(*NETWORK_LATENCY_RANGE))

    # Environmental & safety metrics (conditional)
    if random.random() < 0.7:  # 70% chance
        labels["vibration_level"] = str(random.randint(*VIBRATION_RANGE))

    if random.random() < 0.8:  # 80% chance
        labels["safety_score"] = str(random.randint(*SAFETY_SCORE_RANGE))

    # Navigation & perception metrics
    if random.random() < 0.6:  # 60% chance
        labels["obstacle_dist_cm"] = str(random.randint(*OBSTACLE_DISTANCE_RANGE))

    if random.random() < 0.9:  # 90% chance
        labels["speed_scaled"] = str(random.randint(*SPEED_RANGE))

    if random.random() < 0.75:  # 75% chance
        labels["ai_confidence"] = str(random.randint(*CONFIDENCE_RANGE))

    # Communication metrics
    if random.random() < 0.85:  # 85% chance
        labels["wifi_dbm"] = str(random.randint(*SIGNAL_STRENGTH_RANGE))

    # Zone-based risk assessment
    if random.random() < 0.4:  # 40% chance
        zone_type = random.choice(list(ZONE_RISK_MAPPING.keys()))
        risk_range = ZONE_RISK_MAPPING[zone_type]
        labels["zone_risk"] = str(random.randint(*risk_range))
        labels["zone_type"] = zone_type

    # Event severity levels (numeric instead of categorical)
    if random.random() < CFG.P_EVENT:
        labels["event_severity"] = str(random.randint(1, 10))  # 1=minor, 10=critical

    if random.random() < CFG.P_VISION:
        labels["vision_confidence"] = str(
            random.randint(50, 95)
        )  # Vision detection confidence

    if random.random() < CFG.P_LIDAR_ALERT:
        labels["lidar_quality"] = str(random.randint(70, 100))  # LiDAR data quality

    return labels


# ---------------------- Reduct helpers ----------------------
async def write_record_us(
    entry: str, payload: bytes, ts_us: int, labels: Dict[str, str], content_type: str
):
    async with Client(CFG.REDUCT_URL, api_token=CFG.API_TOKEN, timeout=600) as client:
        bucket = await client.create_bucket(CFG.BUCKET, exist_ok=True)
        await bucket.write(
            entry, payload, ts_us, labels=labels, content_type=content_type
        )


async def clear_bucket():
    async with Client(CFG.REDUCT_URL, api_token=CFG.API_TOKEN, timeout=600) as client:
        bucket = await client.create_bucket(CFG.BUCKET, exist_ok=True)
        for e in await bucket.get_entry_list():
            await bucket.remove_entry(e.name)


# ---------------------- Time utilities ----------------------
def unix_ns(dt: datetime) -> int:
    return int(dt.timestamp() * 1e9)


def build_session_starts(
    now: datetime, start_off: timedelta, end_off: timedelta, interval_s: int
) -> List[int]:
    start_dt = (now + start_off).replace(tzinfo=timezone.utc)
    end_dt = (now + end_off).replace(tzinfo=timezone.utc)
    t = start_dt
    out = []
    while t <= end_dt:
        out.append(unix_ns(t))
        t += timedelta(seconds=interval_s)
    return out


# ---------------------- Main ----------------------
async def main():
    if CFG.RANDOM_SEED is not None:
        random.seed(CFG.RANDOM_SEED)

    # Load clip
    log.info("[init] opening mcap=%s", CFG.MCAP_INPUT_PATH)
    reader = open_reader(CFG.MCAP_INPUT_PATH)
    topic_types = reader.get_all_topics_and_types()
    topics: Dict[str, str] = {tt.name: tt.type for tt in topic_types}
    msgs: List[Tuple[str, bytes, int, str]] = []
    first_ts_ns = None
    last_ts_ns = None

    try:
        while reader.has_next():
            topic, cdr_bytes, t_ns = reader.read_next()
            topic_type = topics.get(topic, "")
            if first_ts_ns is None:
                first_ts_ns = t_ns
            last_ts_ns = t_ns
            msgs.append((topic, cdr_bytes, t_ns, topic_type))
    finally:
        try:
            del reader
        except Exception:
            pass

    if not msgs:
        log.error("No messages in MCAP.")
        return

    clip_dur_ns = (
        (last_ts_ns - first_ts_ns)
        if (first_ts_ns and last_ts_ns)
        else int(CFG.CLIP_SECONDS * 1e9)
    )

    # Calculate message frequencies for throttling
    topic_msg_counts = {}
    for topic, _, _, topic_type in msgs:
        # Only count messages from allowed topics
        is_allowed_image = (
            topic_type.endswith("sensor_msgs/msg/Image")
            or topic_type.endswith("sensor_msgs/msg/CompressedImage")
        ) and topic in CFG.ALLOWED_IMAGE_TOPICS
        is_allowed_pointcloud = (
            topic_type.endswith("sensor_msgs/msg/PointCloud2")
            and topic in CFG.ALLOWED_POINTCLOUD_TOPICS
        )

        if is_allowed_image or is_allowed_pointcloud:
            topic_msg_counts[topic] = topic_msg_counts.get(topic, 0) + 1

    clip_duration_s = clip_dur_ns / 1e9
    log.info("Clip duration: %.2f seconds", clip_duration_s)

    # Calculate throttling ratios (how many messages to skip to achieve target Hz)
    throttle_ratios = {}
    for topic, count in topic_msg_counts.items():
        original_hz = count / clip_duration_s
        # Determine target frequency based on message type
        topic_type = topics.get(topic, "")
        if topic_type.endswith("sensor_msgs/msg/PointCloud2"):
            target_hz = CFG.TARGET_POINTCLOUD_HZ
        else:  # Images (regular or compressed)
            target_hz = CFG.TARGET_IMAGE_HZ

        if original_hz > target_hz:
            # Keep every Nth message to achieve target Hz
            throttle_ratios[topic] = max(1, int(original_hz / target_hz))
            log.info(
                "Topic %s: %.1fHz -> %.1fHz (keep 1 every %d msgs)",
                topic,
                original_hz,
                target_hz,
                throttle_ratios[topic],
            )
        else:
            throttle_ratios[topic] = 1  # Keep all messages
            log.info("Topic %s: %.1fHz (keeping all)", topic, original_hz)

    # Schedule sessions
    now = datetime.now(timezone.utc)
    session_starts_ns = build_session_starts(
        now, CFG.START_OFFSET, CFG.END_OFFSET, CFG.SESSION_INTERVAL_SECONDS
    )
    loops_per_session = max(1, int(CFG.SESSION_DURATION_SECONDS / CFG.CLIP_SECONDS))

    log.info(
        "sessions=%d, session_len=%ds, loops_per_session=%d",
        len(session_starts_ns),
        CFG.SESSION_DURATION_SECONDS,
        loops_per_session,
    )
    log.info("clearing bucket '%s' at %s", CFG.BUCKET, CFG.REDUCT_URL)
    await clear_bucket()

    robot_name = os.environ.get("ROBOT_NAME", CFG.BUCKET)

    total_images = 0
    total_pc = 0
    total_json_rows = 0

    # Throttling counters (process every Nth message)
    image_msg_counters = {}  # per topic counter
    pc_msg_counters = {}  # per topic counter

    # Progress tracking
    total_sessions = len(session_starts_ns)
    total_messages_per_session = len(msgs) * loops_per_session

    # Session progress bar
    session_pbar = tqdm(
        total=total_sessions, desc="Processing sessions", unit="session", position=0
    )

    for s_idx, s_start in enumerate(session_starts_ns):
        session_ctx = session_context(robot_name)
        session_end_ns = s_start + CFG.SESSION_DURATION_SECONDS * 1_000_000_000

        # JSON batching per session - accumulate by entry name
        json_batches = {}  # entry_name -> list of rows

        log.info(
            "[session %d] start_ns=%d end_ns=%d ctx=%s",
            s_idx,
            s_start,
            session_end_ns,
            session_ctx,
        )

        # Message progress bar for this session
        msg_pbar = tqdm(
            total=total_messages_per_session,
            desc=f"Session {s_idx+1}/{total_sessions}",
            unit="msg",
            position=1,
            leave=False,
        )

        for loop_i in range(loops_per_session):
            loop_off_ns = loop_i * clip_dur_ns

            for topic, cdr_bytes, t_ns, topic_type in msgs:
                # remap source ts into session timeline
                rel_ns = t_ns - first_ts_ns
                ts_out_ns = s_start + loop_off_ns + rel_ns
                if ts_out_ns > session_end_ns:
                    break

                # Skip TF entirely
                if not CFG.SAVE_TF and is_tf_type(topic_type):
                    msg_pbar.update(1)
                    continue

                # Initialize counters for this session if needed
                if topic not in image_msg_counters:
                    image_msg_counters[topic] = 0
                if topic not in pc_msg_counters:
                    pc_msg_counters[topic] = 0

                # Try to decode (best-effort)
                msg_obj = None
                try:
                    msg_obj = deserialize_message(cdr_bytes, get_message(topic_type))
                except Exception:
                    pass

                # 1) Other interesting topics -> JSON batching (processed first)
                if CFG.SAVE_OTHER_AS_JSON and msg_obj is not None:
                    row = flatten_row(topic, topic_type, msg_obj, ts_out_ns)
                    if row is not None:
                        # Add topic info to row for later reference
                        row["topic"] = topic
                        row["type"] = topic_type

                        # Get the entry name for this topic
                        entry_name = get_json_entry_name(topic)

                        # Initialize batch if needed
                        if entry_name not in json_batches:
                            json_batches[entry_name] = []

                        # Add to batch
                        json_batches[entry_name].append(row)
                        total_json_rows += 1

                        # Write batch if it reaches the limit
                        if len(json_batches[entry_name]) >= CFG.JSON_BATCH_SIZE:
                            await write_json_batch(
                                entry_name, json_batches[entry_name], session_ctx
                            )
                            json_batches[entry_name] = []  # Clear the batch

                        msg_pbar.update(1)
                        continue  # Don't process as image/pointcloud if saved as JSON

                # 2) PointCloud2 -> single entry "point_cloud" (processed second)
                if (
                    CFG.SAVE_POINTCLOUD2
                    and topic_type.endswith("sensor_msgs/msg/PointCloud2")
                    and topic in CFG.ALLOWED_POINTCLOUD_TOPICS
                    and msg_obj is not None
                    and hasattr(msg_obj, "data")
                ):
                    # Throttling for point clouds
                    pc_msg_counters[topic] += 1
                    throttle_ratio = throttle_ratios.get(topic, 1)
                    if pc_msg_counters[topic] % throttle_ratio != 0:
                        msg_pbar.update(1)
                        continue  # Skip this message

                    pc_bytes = bytes(getattr(msg_obj, "data", b"") or b"")
                    labels = base_labels(
                        topic,
                        topic_type,
                        **session_ctx,
                        kind="pointcloud2",
                        height=str(getattr(msg_obj, "height", 0)),
                        width=str(getattr(msg_obj, "width", 0)),
                        point_step=str(getattr(msg_obj, "point_step", 0)),
                        row_step=str(getattr(msg_obj, "row_step", 0)),
                        is_dense=str(getattr(msg_obj, "is_dense", False)),
                    )
                    labels = sprinkle_incidents(labels)
                    ts_us = ts_alloc.alloc_us(POINTCLOUD_ENTRY, ts_out_ns)
                    await write_record_us(
                        POINTCLOUD_ENTRY, pc_bytes, ts_us, labels, CONTENT_TYPE_OCTET
                    )
                    total_pc += 1
                    msg_pbar.update(1)
                    continue

                # 3) Images -> single entry "image" (processed last)
                is_image_msg = (
                    CFG.SAVE_IMAGES
                    and topic in CFG.ALLOWED_IMAGE_TOPICS
                    and msg_obj is not None
                    and (
                        (
                            hasattr(msg_obj, "format") and hasattr(msg_obj, "data")
                        )  # sensor_msgs/Image
                        or (
                            topic_type.endswith("sensor_msgs/msg/CompressedImage")
                            and hasattr(msg_obj, "data")
                        )  # CompressedImage
                    )
                )
                if is_image_msg:
                    # Throttling for images
                    image_msg_counters[topic] += 1
                    throttle_ratio = throttle_ratios.get(topic, 1)
                    if image_msg_counters[topic] % throttle_ratio != 0:
                        msg_pbar.update(1)
                        continue  # Skip this message

                    raw = bytes(getattr(msg_obj, "data", b"") or b"")
                    ctype = infer_image_content_type(
                        getattr(msg_obj, "format", None), raw
                    )
                    if ctype in (CONTENT_TYPE_JPEG, CONTENT_TYPE_PNG):
                        with Image.open(BytesIO(raw)) as im:
                            im = im.rotate(-90, expand=True)  # clockwise 90°
                            buf = BytesIO()
                            if ctype == CONTENT_TYPE_PNG:
                                im.save(buf, format="PNG")
                            else:
                                im.save(buf, format="JPEG", quality=90)
                            payload = buf.getvalue()
                        labels = base_labels(topic, topic_type, **session_ctx)
                        labels = sprinkle_incidents(labels)
                        ts_us = ts_alloc.alloc_us(IMAGE_ENTRY, ts_out_ns)
                        await write_record_us(
                            IMAGE_ENTRY, payload, ts_us, labels, ctype
                        )
                        total_images += 1
                        msg_pbar.update(1)
                        continue

                # Update progress bar for each message processed
                msg_pbar.update(1)

        # Write any remaining JSON batches at the end of the session
        for entry_name, remaining_rows in json_batches.items():
            if remaining_rows:
                await write_json_batch(entry_name, remaining_rows, session_ctx)

        # Close message progress bar and update session progress
        msg_pbar.close()
        session_pbar.update(1)

    # Close session progress bar
    session_pbar.close()

    log.info(
        "[done] images=%d pointcloud=%d json_rows=%d",
        total_images,
        total_pc,
        total_json_rows,
    )


# ---------------------- Entrypoint ----------------------
if __name__ == "__main__":
    asyncio.run(main())
