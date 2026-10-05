#!/usr/bin/env python3
"""Render a still image of the running Gazebo world (used for documentation figures).

Spawns a temporary camera model through the world's UserCommands `create` service,
waits for one rendered frame, writes it as PNG and removes the camera again.

    snapshot.py --out overview.png --pose 18 -7 20 0 0.85 1.5708
"""
import argparse
import sys
import threading
import time

import numpy as np
from gz.msgs10.boolean_pb2 import Boolean
from gz.msgs10.entity_factory_pb2 import EntityFactory
from gz.msgs10.entity_pb2 import Entity
from gz.msgs10.image_pb2 import Image
from gz.transport13 import Node


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--world', default='uco_warehouse')
    p.add_argument('--out', required=True)
    p.add_argument('--pose', nargs=6, type=float, default=[18.0, -7.0, 20.0, 0.0, 0.85, 1.5708],
                   metavar=('X', 'Y', 'Z', 'ROLL', 'PITCH', 'YAW'))
    p.add_argument('--width', type=int, default=1600)
    p.add_argument('--height', type=int, default=1000)
    p.add_argument('--fov', type=float, default=1.3, help='horizontal field of view [rad]')
    p.add_argument('--timeout', type=float, default=60.0)
    a = p.parse_args()

    name = f'snapshot_cam_{int(time.time() * 1000) % 100000}'
    topic = f'/{name}/image'
    sdf = f'''<?xml version="1.0"?><sdf version="1.9"><model name="{name}"><static>true</static>
      <pose>{" ".join(str(v) for v in a.pose)}</pose><link name="link">
      <sensor name="cam" type="camera"><topic>{topic}</topic><update_rate>2</update_rate><always_on>1</always_on>
      <camera><horizontal_fov>{a.fov}</horizontal_fov><image><width>{a.width}</width><height>{a.height}</height>
      <format>R8G8B8</format></image><clip><near>0.1</near><far>200</far></clip></camera></sensor>
      </link></model></sdf>'''

    node = Node()
    frames = []
    got = threading.Event()

    def on_image(msg: Image):
        frames.append(msg)
        if len(frames) >= 3:      # skip the first frames while materials load
            got.set()

    node.subscribe(Image, topic, on_image)
    req = EntityFactory()
    req.sdf = sdf
    ok, rep = node.request(f'/world/{a.world}/create', req, EntityFactory, Boolean, 5000)
    if not ok or not rep.data:
        print('failed to spawn snapshot camera (is the simulation running?)', file=sys.stderr)
        return 1
    try:
        if not got.wait(a.timeout):
            print('timed out waiting for camera frames', file=sys.stderr)
            return 1
        msg = frames[-1]
        img = np.frombuffer(msg.data, dtype=np.uint8).reshape(msg.height, msg.width, 3)
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        plt.imsave(a.out, img)
        print(f'wrote {a.out} ({msg.width}x{msg.height})')
        return 0
    finally:
        rm = Entity()
        rm.name = name
        rm.type = Entity.MODEL
        node.request(f'/world/{a.world}/remove', rm, Entity, Boolean, 5000)


if __name__ == '__main__':
    sys.exit(main())
