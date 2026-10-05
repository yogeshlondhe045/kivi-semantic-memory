"""Command-line client: move an AGV to a named warehouse location.

    ros2 run uco_fleet move_agv A03            # -> A-03-R01
    ros2 run uco_fleet move_agv B-05-R02 --ns /agv_01
    ros2 run uco_fleet move_agv charger
"""
import argparse
import sys

import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node

from uco_interfaces.action import NavigateToLocation


def main(argv=None):
    argv = rclpy.utilities.remove_ros_args(sys.argv if argv is None else argv)[1:]
    ap = argparse.ArgumentParser(description='Move an AGV to a named location')
    ap.add_argument('location')
    ap.add_argument('--ns', default='/agv_01', help='AGV action namespace')
    ap.add_argument('--timeout', type=float, default=600.0)
    a = ap.parse_args(argv)
    rclpy.init()
    node = Node('move_agv')
    client = ActionClient(node, NavigateToLocation, f'{a.ns.rstrip("/")}/navigate_to_location')
    if not client.wait_for_server(timeout_sec=15.0):
        print('AGV action server not available', file=sys.stderr)
        return 2
    goal = NavigateToLocation.Goal()
    goal.location_id = a.location
    last = [None]

    def on_fb(fb):
        d = round(fb.feedback.distance_remaining, 1)
        if d != last[0]:
            last[0] = d
            print(f'  {fb.feedback.state:12s} distance remaining {d:5.1f} m', flush=True)

    fut = client.send_goal_async(goal, feedback_callback=on_fb)
    rclpy.spin_until_future_complete(node, fut, timeout_sec=15.0)
    handle = fut.result()
    if handle is None or not handle.accepted:
        print('goal rejected (AGV busy, charging or stopped by safety system)')
        return 1
    print(f'moving to {a.location} ...', flush=True)
    rfut = handle.get_result_async()
    rclpy.spin_until_future_complete(node, rfut, timeout_sec=a.timeout)
    if not rfut.done():
        handle.cancel_goal_async()
        print('timeout')
        return 1
    res = rfut.result().result
    print(('OK: ' if res.success else 'FAILED: ') + res.message + f' ({res.travel_time_s:.0f} s)')
    node.destroy_node()
    rclpy.shutdown()
    return 0 if res.success else 1


if __name__ == '__main__':
    sys.exit(main())
