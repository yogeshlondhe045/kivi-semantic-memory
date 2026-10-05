"""Alert publishing helper: logs with a severity prefix and publishes on /warehouse/alerts."""
from __future__ import annotations

import time
from typing import Dict

from uco_interfaces.msg import Alert

from .qos import EVENTS

ALERT_TOPIC = '/warehouse/alerts'
_PREFIX = {Alert.INFO: '[INFO]', Alert.WARN: '[WARN]', Alert.ERROR: '[ERROR]', Alert.ALARM: '[ALARM]'}


class AlertPublisher:
    """Severity-tagged alerts with optional de-duplication of repeated codes."""

    def __init__(self, node, source: str = '', repeat_period_s: float = 5.0):
        self._node = node
        self._source = source or node.get_name()
        self._pub = node.create_publisher(Alert, ALERT_TOPIC, EVENTS)
        self._repeat_period = repeat_period_s
        self._last: Dict[str, float] = {}

    def publish(self, severity: int, code: str, message: str, dedup: bool = False) -> None:
        now = time.monotonic()
        key = f'{severity}:{code}:{message}' if dedup else ''
        if dedup and now - self._last.get(key, -1e9) < self._repeat_period:
            return
        if dedup:
            self._last[key] = now
        text = f'{_PREFIX.get(severity, "[INFO]")} {message}'
        logger = self._node.get_logger()
        if severity == Alert.INFO:
            logger.info(text)
        elif severity == Alert.WARN:
            logger.warning(text)
        else:
            logger.error(text)
        msg = Alert()
        msg.stamp = self._node.get_clock().now().to_msg()
        msg.severity = severity
        msg.source = self._source
        msg.code = code
        msg.message = message
        self._pub.publish(msg)

    def info(self, code: str, message: str, dedup: bool = False) -> None:
        self.publish(Alert.INFO, code, message, dedup)

    def warn(self, code: str, message: str, dedup: bool = False) -> None:
        self.publish(Alert.WARN, code, message, dedup)

    def error(self, code: str, message: str, dedup: bool = False) -> None:
        self.publish(Alert.ERROR, code, message, dedup)

    def alarm(self, code: str, message: str, dedup: bool = False) -> None:
        self.publish(Alert.ALARM, code, message, dedup)
