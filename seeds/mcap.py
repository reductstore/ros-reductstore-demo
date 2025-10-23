#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import uuid
import asyncio
import logging
import json
import random
import tempfile
import shutil
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional
from datetime import datetime, timedelta, timezone
from io import BytesIO
from collections import defaultdict

import rosbag2_py
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
    # REDUCT_URL: str = "http://cloud.reduct.demo"
    REDUCT_URL: str = f"http://orion.field.demo"
    API_TOKEN: str = "reductstore"
    BUCKET: str = "mcap"

    # Session plan: 10-min sessions, spaced out, covering past→future window
    CLIP_SECONDS: float = 30.0
    SESSION_DURATION_SECONDS: int = 10 * 60
    SESSION_INTERVAL_SECONDS: int = 24 * 60 * 60  # start a session every 24 hours
    START_OFFSET: timedelta = timedelta(days=-1)
    END_OFFSET: timedelta = timedelta(days=+8)

    # MCAP Episode settings
    EPISODE_DURATION_SECONDS: float = 30.0  # Duration of each MCAP episode
    ENTRY_NAME: str = "episodes"  # Single entry name for all MCAP files

    # Keep all topics but with downsampling
    SAVE_ALL_TOPICS: bool = True

    # Topic filters - only keep these specific topics
    ALLOWED_IMAGE_TOPICS: List[str] = field(
        default_factory=lambda: [
            "/camera/image_color/compressed_restamped_downsampled"  # Only keep this image topic
        ]
    )
    ALLOWED_CAMERA_INFO_TOPICS: List[str] = field(
        default_factory=lambda: [
            "/camera/camera_info_restamped"  # Camera info for the image topic
        ]
    )
    ALLOWED_POINTCLOUD_TOPICS: List[str] = field(
        default_factory=lambda: [
            # Skip all point cloud topics for now due to heavy data
        ]
    )

    # Downsampling settings - different rates for different data types
    TARGET_IMAGE_HZ: float = 10.0
    TARGET_POINTCLOUD_HZ: float = 0.0  # Skip point clouds entirely
    # Label probabilities (per-episode sprinkle) - removed LIDAR_ALERT
    P_EVENT: float = 0.10
    P_VISION: float = 0.06

    # Random seed (None => non-deterministic)
    RANDOM_SEED: Optional[int] = 42


CFG = Config()

# ---------------------- Content types ----------------------
CONTENT_TYPE_MCAP = "application/mcap"


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


def create_writer(path: str):
    w = rosbag2_py.SequentialWriter()

    # Configure storage options with MCAP chunk compression
    storage_options = rosbag2_py.StorageOptions(uri=path, storage_id="mcap")

    # Enable chunk compression for MCAP files
    # This reduces file size significantly for robotics data
    storage_options.storage_config_uri = ""
    storage_options.max_bagfile_size = 0  # No size limit per file
    storage_options.max_bagfile_duration = 0  # No duration limit per file
    storage_options.max_cache_size = 100000  # Cache size for better performance

    # Configure storage-specific options for MCAP compression
    # Use LZ4 compression which is fast and provides good compression ratio
    storage_options.storage_preset_profile = ""
    storage_options.storage_config_uri = ""

    converter_options = rosbag2_py.ConverterOptions(
        input_serialization_format="cdr", output_serialization_format="cdr"
    )

    w.open(storage_options, converter_options)
    return w


# ---------------------- MCAP Episode Statistics ----------------------
def calculate_episode_stats(
    msgs: List[Tuple[str, bytes, int, str]], topic_types: Dict[str, str]
) -> Dict[str, str]:
    """Calculate aggregated statistics for an MCAP episode."""
    stats = {}

    # Basic counts
    stats["total_messages"] = str(len(msgs))
    stats["total_topics"] = str(len(set(msg[0] for msg in msgs)))

    # Topic type distribution
    topic_type_counts = defaultdict(int)
    topic_counts = defaultdict(int)
    topic_sizes = defaultdict(int)

    min_timestamp = float("inf")
    max_timestamp = 0

    for topic, cdr_bytes, t_ns, topic_type in msgs:
        topic_type_counts[topic_type] += 1
        topic_counts[topic] += 1
        topic_sizes[topic] += len(cdr_bytes)
        min_timestamp = min(min_timestamp, t_ns)
        max_timestamp = max(max_timestamp, t_ns)

    # Duration
    duration_ns = max_timestamp - min_timestamp if max_timestamp > min_timestamp else 0
    stats["duration_seconds"] = str(duration_ns / 1e9)

    # Max values for aggregated labels
    stats["max_messages_per_topic"] = str(
        max(topic_counts.values()) if topic_counts else 0
    )
    stats["max_bytes_per_topic"] = str(max(topic_sizes.values()) if topic_sizes else 0)
    stats["max_topic_frequency_hz"] = str(
        max(topic_counts.values()) / (duration_ns / 1e9) if duration_ns > 0 else 0
    )

    # Most frequent topic type
    if topic_type_counts:
        most_frequent_type = max(topic_type_counts.items(), key=lambda x: x[1])
        stats["max_topic_type"] = most_frequent_type[0].split("/")[
            -1
        ]  # Just the message name
        stats["max_topic_type_count"] = str(most_frequent_type[1])

    # Data size statistics
    total_bytes = sum(topic_sizes.values())
    stats["total_bytes"] = str(total_bytes)
    stats["max_bytes"] = str(total_bytes)  # For compatibility with aggregation naming
    stats["avg_message_size"] = str(total_bytes // len(msgs) if msgs else 0)

    return stats


# ---------------------- Helpers ----------------------
def should_include_topic(topic: str, topic_type: str) -> bool:
    """Check if a topic should be included in the MCAP episode."""
    # Skip heavy point cloud data entirely (LiDAR)
    if topic_type.endswith("sensor_msgs/msg/PointCloud2"):
        return False  # Remove all LiDAR/point cloud data

    # Skip any LiDAR-related topics by name pattern
    if any(
        lidar_pattern in topic.lower()
        for lidar_pattern in [
            "lidar",
            "laser",
            "scan",
            "os_node",
            "ouster",
            "velodyne",
            "sick",
            "hokuyo",
        ]
    ):
        return False

    # Only keep the specific image topic we want
    if topic_type.endswith("sensor_msgs/msg/Image") or topic_type.endswith(
        "sensor_msgs/msg/CompressedImage"
    ):
        return topic in CFG.ALLOWED_IMAGE_TOPICS

    # Only keep the specific camera info topic we want
    if topic_type.endswith("sensor_msgs/msg/CameraInfo"):
        return topic in CFG.ALLOWED_CAMERA_INFO_TOPICS

    # Keep essential robot data (IMU, TF, navigation, etc.) but exclude LiDAR
    if any(
        essential_pattern in topic_type
        for essential_pattern in [
            "sensor_msgs/msg/Imu",
            "sensor_msgs/msg/MagneticField",
            "sensor_msgs/msg/FluidPressure",
            "sensor_msgs/msg/Temperature",
            "tf2_msgs/msg/TFMessage",
            "nav_msgs/",
            "geometry_msgs/",
            "std_msgs/",
        ]
    ):
        return True

    # Skip everything else to keep file size manageable
    return False


def should_downsample_topic(topic: str, topic_type: str) -> bool:
    """Check if a topic should be downsampled based on type and configuration."""
    # Images - downsample to 2Hz
    if (
        topic_type.endswith("sensor_msgs/msg/Image")
        or topic_type.endswith("sensor_msgs/msg/CompressedImage")
    ) and topic in CFG.ALLOWED_IMAGE_TOPICS:
        return True

    # Point clouds - downsample heavily (but we're skipping them anyway)
    if (
        topic_type.endswith("sensor_msgs/msg/PointCloud2")
        and topic in CFG.ALLOWED_POINTCLOUD_TOPICS
    ):
        return True

    # Other topics - no downsampling needed, keep original frequency
    return False


def get_target_frequency(topic: str, topic_type: str) -> float:
    """Get the target frequency for a given topic."""
    if topic_type.endswith("sensor_msgs/msg/PointCloud2"):
        return CFG.TARGET_POINTCLOUD_HZ
    elif topic_type.endswith("sensor_msgs/msg/Image") or topic_type.endswith(
        "sensor_msgs/msg/CompressedImage"
    ):
        return CFG.TARGET_IMAGE_HZ
    else:
        return float("inf")  # No limit for other topics


def create_mcap_episode(
    msgs: List[Tuple[str, bytes, int, str]],
    topic_types: Dict[str, str],
    episode_start_ns: int,
    original_first_ns: int,
    throttle_ratios: Dict[str, int],
) -> bytes:
    """Create an MCAP file from a list of messages with adjusted timestamps."""

    # Create temporary directory path (but don't create the directory yet)
    tmp_dir = tempfile.mkdtemp()
    # Remove the directory since rosbag2 will create it
    os.rmdir(tmp_dir)

    try:
        writer = create_writer(tmp_dir)

        # Track which topics we've created
        created_topics = set()

        # Track message counters for throttling
        msg_counters = defaultdict(int)

        # Process messages
        for topic, cdr_bytes, original_ts_ns, topic_type in msgs:
            # Skip topics that shouldn't be included
            if not should_include_topic(topic, topic_type):
                continue

            # Adjust timestamp relative to episode start
            relative_ns = original_ts_ns - original_first_ns
            new_ts_ns = episode_start_ns + relative_ns

            # Apply throttling if configured for this topic
            should_throttle = should_downsample_topic(topic, topic_type)
            if should_throttle:
                msg_counters[topic] += 1
                throttle_ratio = throttle_ratios.get(topic, 1)
                if msg_counters[topic] % throttle_ratio != 0:
                    continue  # Skip this message

            # Create topic if not already created
            if topic not in created_topics:
                topic_info = rosbag2_py.TopicMetadata(0, topic, topic_type, "cdr")
                writer.create_topic(topic_info)
                created_topics.add(topic)

            # Write message
            writer.write(topic, cdr_bytes, new_ts_ns)

        # Close writer to flush data
        del writer

        # Find the generated MCAP file in the directory
        mcap_files = [f for f in os.listdir(tmp_dir) if f.endswith(".mcap")]
        if not mcap_files:
            raise RuntimeError("No MCAP file generated")

        mcap_path = os.path.join(tmp_dir, mcap_files[0])

        # Read the generated MCAP file
        with open(mcap_path, "rb") as f:
            mcap_data = f.read()

        return mcap_data

    finally:
        # Clean up temporary directory
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir)


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


def sprinkle_incidents_aggregated(labels: Dict[str, str]) -> Dict[str, str]:
    """Add synthetic numeric metrics for rich Grafana visualization with 'max_' prefix for aggregation."""

    # Core performance metrics (always present) - using max_ prefix for aggregation
    labels["max_battery_pct"] = str(random.randint(*BATTERY_RANGE))
    labels["max_cpu_temp_c"] = str(random.randint(*CPU_TEMP_RANGE))
    labels["max_memory_pct"] = str(random.randint(*MEMORY_USAGE_RANGE))
    labels["max_net_latency_ms"] = str(random.randint(*NETWORK_LATENCY_RANGE))

    # Environmental & safety metrics (conditional)
    if random.random() < 0.7:  # 70% chance
        labels["max_vibration_level"] = str(random.randint(*VIBRATION_RANGE))

    if random.random() < 0.8:  # 80% chance
        labels["max_safety_score"] = str(random.randint(*SAFETY_SCORE_RANGE))

    # Navigation & perception metrics
    if random.random() < 0.6:  # 60% chance
        labels["max_obstacle_dist_cm"] = str(random.randint(*OBSTACLE_DISTANCE_RANGE))

    if random.random() < 0.9:  # 90% chance
        labels["max_speed_scaled"] = str(random.randint(*SPEED_RANGE))

    if random.random() < 0.75:  # 75% chance
        labels["max_ai_confidence"] = str(random.randint(*CONFIDENCE_RANGE))

    # Communication metrics
    if random.random() < 0.85:  # 85% chance
        labels["max_wifi_dbm"] = str(random.randint(*SIGNAL_STRENGTH_RANGE))

    # Zone-based risk assessment
    if random.random() < 0.4:  # 40% chance
        zone_type = random.choice(list(ZONE_RISK_MAPPING.keys()))
        risk_range = ZONE_RISK_MAPPING[zone_type]
        labels["max_zone_risk"] = str(random.randint(*risk_range))
        labels["zone_type"] = zone_type

    # Event severity levels (numeric instead of categorical)
    if random.random() < CFG.P_EVENT:
        labels["max_event_severity"] = str(
            random.randint(1, 10)
        )  # 1=minor, 10=critical

    if random.random() < CFG.P_VISION:
        labels["max_vision_confidence"] = str(
            random.randint(50, 95)
        )  # Vision detection confidence

    # Removed LiDAR quality metrics since we're not including LiDAR data

    return labels


def sprinkle_incidents(labels: Dict[str, str]) -> Dict[str, str]:
    """Legacy function for backward compatibility - calls the aggregated version."""
    return sprinkle_incidents_aggregated(labels)


# ---------------------- Reduct helpers ----------------------
async def write_mcap_episode(
    mcap_data: bytes, episode_start_ns: int, labels: Dict[str, str]
):
    """Write an MCAP episode to ReductStore."""
    ts_us = ts_alloc.alloc_us(CFG.ENTRY_NAME, episode_start_ns)

    async with Client(CFG.REDUCT_URL, api_token=CFG.API_TOKEN, timeout=600) as client:
        bucket = await client.create_bucket(CFG.BUCKET, exist_ok=True)
        await bucket.write(
            CFG.ENTRY_NAME,
            mcap_data,
            ts_us,
            labels=labels,
            content_type=CONTENT_TYPE_MCAP,
        )


async def clear_bucket():
    async with Client(CFG.REDUCT_URL, api_token=CFG.API_TOKEN, timeout=600) as client:
        bucket = await client.create_bucket(CFG.BUCKET, exist_ok=True)
        for e in await bucket.get_entry_list():
            if e.name == CFG.ENTRY_NAME:
                log.info("Removing entry '%s'...", e.name)
                await bucket.remove_entry(CFG.ENTRY_NAME)


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

    # Calculate message frequencies for downsampling
    topic_msg_counts = {}
    for topic, _, _, topic_type in msgs:
        # Only count messages from topics that will be included and need downsampling
        if should_include_topic(topic, topic_type) and should_downsample_topic(
            topic, topic_type
        ):
            topic_msg_counts[topic] = topic_msg_counts.get(topic, 0) + 1

    clip_duration_s = clip_dur_ns / 1e9
    log.info("Clip duration: %.2f seconds", clip_duration_s)

    # Log which topics will be included/excluded
    all_topics = set(topics.keys())
    included_topics = {
        topic for topic in all_topics if should_include_topic(topic, topics[topic])
    }
    excluded_topics = all_topics - included_topics

    log.info("Topics to include: %d", len(included_topics))
    for topic in sorted(included_topics):
        log.info("  INCLUDE: %s (%s)", topic, topics[topic])

    log.info("Topics to exclude: %d", len(excluded_topics))
    for topic in sorted(excluded_topics):
        log.info("  EXCLUDE: %s (%s)", topic, topics[topic])

    # Calculate throttling ratios (how many messages to skip to achieve target Hz)
    throttle_ratios = {}
    for topic, count in topic_msg_counts.items():
        original_hz = count / clip_duration_s
        # Get target frequency for this topic type
        topic_type = topics.get(topic, "")
        target_hz = get_target_frequency(topic, topic_type)

        if target_hz > 0 and original_hz > target_hz:
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
            if target_hz > 0:
                log.info("Topic %s: %.1fHz (keeping all)", topic, original_hz)
            else:
                log.info("Topic %s: SKIPPED (target_hz=0)", topic)

    # Schedule sessions
    now = datetime.now(timezone.utc)
    session_starts_ns = build_session_starts(
        now, CFG.START_OFFSET, CFG.END_OFFSET, CFG.SESSION_INTERVAL_SECONDS
    )

    # Calculate how many episodes per session
    episodes_per_session = max(
        1, int(CFG.SESSION_DURATION_SECONDS / CFG.EPISODE_DURATION_SECONDS)
    )

    log.info(
        "sessions=%d, session_len=%ds, episodes_per_session=%d",
        len(session_starts_ns),
        CFG.SESSION_DURATION_SECONDS,
        episodes_per_session,
    )
    log.info("clearing bucket '%s' at %s", CFG.BUCKET, CFG.REDUCT_URL)
    await clear_bucket()

    robot_name = os.environ.get("ROBOT_NAME", CFG.BUCKET)

    total_episodes = 0

    # Progress tracking
    total_sessions = len(session_starts_ns)
    total_episodes_expected = total_sessions * episodes_per_session

    # Session progress bar
    session_pbar = tqdm(
        total=total_sessions, desc="Processing sessions", unit="session", position=0
    )

    for s_idx, s_start in enumerate(session_starts_ns):
        session_ctx = session_context(robot_name)
        session_end_ns = s_start + CFG.SESSION_DURATION_SECONDS * 1_000_000_000

        log.info(
            "[session %d] start_ns=%d end_ns=%d ctx=%s",
            s_idx,
            s_start,
            session_end_ns,
            session_ctx,
        )

        # Episode progress bar for this session
        episode_pbar = tqdm(
            total=episodes_per_session,
            desc=f"Session {s_idx+1}/{total_sessions} Episodes",
            unit="episode",
            position=1,
            leave=False,
        )

        for episode_i in range(episodes_per_session):
            episode_start_ns = s_start + episode_i * int(
                CFG.EPISODE_DURATION_SECONDS * 1e9
            )
            episode_end_ns = episode_start_ns + int(CFG.EPISODE_DURATION_SECONDS * 1e9)

            if episode_end_ns > session_end_ns:
                break

            # Create MCAP episode with all messages, applying downsampling where configured
            mcap_data = create_mcap_episode(
                msgs, topics, episode_start_ns, first_ts_ns, throttle_ratios
            )

            # Calculate episode statistics
            episode_stats = calculate_episode_stats(msgs, topics)

            # Create labels combining session context, episode stats, and synthetic metrics
            labels = {
                **session_ctx,
                **episode_stats,
                "episode_index": str(episode_i),
                "session_index": str(s_idx),
                "format": "mcap",
                "duration_target_seconds": str(CFG.EPISODE_DURATION_SECONDS),
            }

            # Add synthetic metrics with "max_" prefix for aggregation naming
            labels = sprinkle_incidents_aggregated(labels)

            # Write episode to ReductStore
            await write_mcap_episode(mcap_data, episode_start_ns, labels)

            total_episodes += 1
            episode_pbar.update(1)

        # Close episode progress bar and update session progress
        episode_pbar.close()
        session_pbar.update(1)

    # Close session progress bar
    session_pbar.close()

    log.info(
        "[done] episodes=%d",
        total_episodes,
    )


# ---------------------- Entrypoint ----------------------
if __name__ == "__main__":
    asyncio.run(main())
