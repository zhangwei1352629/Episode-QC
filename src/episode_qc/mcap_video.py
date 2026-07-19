from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from mcap.reader import make_reader

from episode_qc.compressed_image import decode_compressed_image


@dataclass(frozen=True)
class ImageTopic:
    name: str
    channel_id: int
    message_count: int
    message_encoding: str
    schema_name: str


@dataclass(frozen=True)
class VideoFrame:
    topic: str
    index: int
    log_time_ns: int
    publish_time_ns: int
    sequence: int
    timestamp_ns: int | None
    frame_id: str
    format: str
    jpeg: bytes
    source_interval_ns: int | None = None
    source_frame_gap_ratio: float | None = None
    source_sequence_gap: int | None = None


def list_image_topics(mcap_path: str | Path) -> list[ImageTopic]:
    path = Path(mcap_path)
    with path.open("rb") as stream:
        reader = make_reader(stream)
        summary = reader.get_summary()

        counts = summary.statistics.channel_message_counts if summary.statistics else {}
        topics: list[ImageTopic] = []
        for channel_id, channel in sorted(summary.channels.items()):
            schema = summary.schemas.get(channel.schema_id)
            schema_name = schema.name if schema else ""
            if not _is_compressed_image_channel(channel.topic, channel.message_encoding, schema_name):
                continue
            topics.append(
                ImageTopic(
                    name=channel.topic,
                    channel_id=channel_id,
                    message_count=counts.get(channel_id, 0),
                    message_encoding=channel.message_encoding,
                    schema_name=schema_name,
                )
            )

    return topics


def iter_video_frames(
    mcap_path: str | Path,
    topics: Iterable[str] | None = None,
    max_frames_per_topic: int | None = None,
) -> Iterator[VideoFrame]:
    path = Path(mcap_path)
    selected_topics = list(topics) if topics else [topic.name for topic in list_image_topics(path)]
    topic_set = set(selected_topics)
    topic_counts: dict[str, int] = defaultdict(int)

    with path.open("rb") as stream:
        reader = make_reader(stream)
        for _schema, channel, message in reader.iter_messages(topics=selected_topics):
            if channel.topic not in topic_set:
                continue
            if max_frames_per_topic is not None and topic_counts[channel.topic] >= max_frames_per_topic:
                if _all_topic_limits_reached(selected_topics, topic_counts, max_frames_per_topic):
                    break
                continue

            compressed = decode_compressed_image(message.data)
            frame_index = topic_counts[channel.topic]
            topic_counts[channel.topic] += 1
            timestamp_ns = _timestamp_to_ns(
                compressed.timestamp_seconds,
                compressed.timestamp_nanos,
            )

            yield VideoFrame(
                topic=channel.topic,
                index=frame_index,
                log_time_ns=message.log_time,
                publish_time_ns=message.publish_time,
                sequence=message.sequence,
                timestamp_ns=timestamp_ns,
                frame_id=compressed.frame_id,
                format=compressed.format,
                jpeg=compressed.data,
            )


def _is_compressed_image_channel(topic: str, message_encoding: str, schema_name: str) -> bool:
    return (
        message_encoding == "protobuf"
        and schema_name == "foxglove.CompressedImage"
        and topic.endswith("/image/jpeg")
    )


def _timestamp_to_ns(seconds: int | None, nanos: int | None) -> int | None:
    if seconds is None and nanos is None:
        return None
    return (seconds or 0) * 1_000_000_000 + (nanos or 0)


def _all_topic_limits_reached(
    topics: list[str],
    topic_counts: dict[str, int],
    max_frames_per_topic: int,
) -> bool:
    return all(topic_counts[topic] >= max_frames_per_topic for topic in topics)
