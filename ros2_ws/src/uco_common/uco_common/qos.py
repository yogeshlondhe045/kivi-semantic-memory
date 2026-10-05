"""Shared QoS profiles."""
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy

# State topics: late joiners (dashboard, metrics) receive the last value immediately.
LATCHED = QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST,
                     reliability=ReliabilityPolicy.RELIABLE,
                     durability=DurabilityPolicy.TRANSIENT_LOCAL)

# Event streams: keep a backlog so short bursts are not lost.
EVENTS = QoSProfile(depth=100, history=HistoryPolicy.KEEP_LAST,
                    reliability=ReliabilityPolicy.RELIABLE,
                    durability=DurabilityPolicy.VOLATILE)

# Fault events must reach nodes that start after the injection too.
FAULTS = QoSProfile(depth=50, history=HistoryPolicy.KEEP_LAST,
                    reliability=ReliabilityPolicy.RELIABLE,
                    durability=DurabilityPolicy.TRANSIENT_LOCAL)
