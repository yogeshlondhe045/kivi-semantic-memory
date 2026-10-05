"""dashboard_server: web monitoring interface for the UCO warehouse (Python std-lib HTTP + SSE).

    http://localhost:8080/            dashboard
    GET  /api/layout                  static facility layout (zones, obstacles, locations)
    GET  /api/state                   current system state (JSON)
    GET  /api/stream                  Server-Sent Events, one state snapshot per second
    POST /api/delivery   {"supplier": "...", "containers": [["DRUM_200L", 180], ...]}
    POST /api/processing {"count": 2}
    POST /api/move       {"location": "A03"}
    POST /api/estop      {"active": true}
    POST /api/fault      {"type": "LOW_BATTERY", "active": true, "target": "", "value": 18, "duration": 0}
"""
from __future__ import annotations

import collections
import json
import math
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import rclpy
from ament_index_python.packages import get_package_share_directory
from rclpy.action import ActionClient
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from sensor_msgs.msg import BatteryState
from std_msgs.msg import Float64
from std_srvs.srv import SetBool

from uco_common.layout import load_layout
from uco_common.qos import EVENTS, LATCHED
from uco_interfaces.action import NavigateToLocation
from uco_interfaces.msg import AgvState, Alert, Inventory, SafetyStatus, TaskList
from uco_interfaces.srv import InjectFault, ReceiveDelivery, RequestDispatch

SEVERITY = ['INFO', 'WARN', 'ERROR', 'ALARM']


def layout_json(layout) -> dict:
    return {
        'name': layout.name, 'size': layout.size,
        'zones': [{'id': z.id, 'name': z.name, 'kind': z.kind, 'rect': [z.rect.xmin, z.rect.ymin, z.rect.xmax, z.rect.ymax],
                   'speed_limit': z.speed_limit, 'color': z.color} for z in layout.zones],
        'obstacles': [{'id': o.id, 'shape': o.shape, 'center': o.center, 'size': o.size, 'radius': o.radius,
                       'color': o.color, 'collision': o.collision} for o in layout.obstacles],
        'doors': [{'id': d.id, 'wall': d.wall, 'span': d.span} for d in layout.doors],
        'locations': [{'id': loc.id, 'kind': loc.kind, 'x': loc.slot.x, 'y': loc.slot.y,
                       'access': [loc.access.x, loc.access.y, loc.access.yaw] if loc.access else None}
                      for loc in layout.locations.values()],
        'container_types': list(layout.container_types),
    }


class Dashboard(Node):
    def __init__(self):
        super().__init__('dashboard_server')
        self.declare_parameter('layout_file', '')
        self.declare_parameter('port', 8080)
        self.declare_parameter('host', '0.0.0.0')
        self.layout = load_layout(self.get_parameter('layout_file').value or None)
        self.layout_json = layout_json(self.layout)
        self.lock = threading.Lock()
        self.inv = self.tasks = self.agv = self.safety = self.battery = None
        self.energy_wh = 0.0
        self.alerts = collections.deque(maxlen=200)
        self.trail = collections.deque(maxlen=600)
        self.last_seen = {}
        self.start = time.time()
        cb = ReentrantCallbackGroup()
        sub = lambda t, topic, attr, qos=10: self.create_subscription(  # noqa: E731
            t, topic, lambda m, a=attr, tp=topic: self._store(a, tp, m), qos, callback_group=cb)
        sub(Inventory, '/warehouse/inventory', 'inv', LATCHED)
        sub(TaskList, '/warehouse/tasks', 'tasks', LATCHED)
        sub(AgvState, '/agv/state', 'agv')
        sub(SafetyStatus, '/safety/status', 'safety', LATCHED)
        sub(BatteryState, '/agv/battery', 'battery')
        self.create_subscription(Float64, '/agv/battery/consumed_wh', lambda m: setattr(self, 'energy_wh', m.data), 10)
        self.create_subscription(Alert, '/warehouse/alerts', self._on_alert, EVENTS, callback_group=cb)
        self.cli = {
            'delivery': self.create_client(ReceiveDelivery, '/stations/receive_delivery', callback_group=cb),
            'processing': self.create_client(RequestDispatch, '/stations/request_processing', callback_group=cb),
            'fault': self.create_client(InjectFault, '/faults/inject', callback_group=cb),
            'estop': self.create_client(SetBool, '/safety/emergency_stop', callback_group=cb),
        }
        self.move_ac = ActionClient(self, NavigateToLocation, '/agv_01/navigate_to_location', callback_group=cb)
        share = get_package_share_directory('uco_dashboard')
        self.static_dir = os.path.join(share, 'static')
        port = int(self.get_parameter('port').value)
        self.httpd = ThreadingHTTPServer((self.get_parameter('host').value, port), self._handler_class())
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.get_logger().info(f'[INFO] Dashboard at http://localhost:{port}/')

    # ------------------------------------------------------------------ ROS side
    def _store(self, attr, topic, msg):
        with self.lock:
            setattr(self, attr, msg)
            self.last_seen[topic] = time.time()
            if attr == 'agv' and msg.pose.x == msg.pose.x and (not self.trail or math.hypot(self.trail[-1][0] - msg.pose.x,
                                                                self.trail[-1][1] - msg.pose.y) > 0.2):
                self.trail.append((round(msg.pose.x, 2), round(msg.pose.y, 2)))

    def _on_alert(self, a: Alert):
        with self.lock:
            self.alerts.appendleft({'t': round(a.stamp.sec + a.stamp.nanosec * 1e-9, 1),
                                    'severity': SEVERITY[min(a.severity, 3)], 'source': a.source, 'code': a.code,
                                    'message': a.message})

    def _call(self, name, req, timeout=15.0):
        cli = self.cli[name]
        if not cli.wait_for_service(timeout_sec=3.0):
            return {'ok': False, 'message': f'{cli.srv_name} not available'}
        fut = cli.call_async(req)
        ev = threading.Event()
        fut.add_done_callback(lambda _: ev.set())
        if not ev.wait(timeout):
            return {'ok': False, 'message': 'timeout'}
        r = fut.result()
        return {'ok': bool(getattr(r, 'success', True)), 'message': getattr(r, 'message', '')}

    def state(self) -> dict:
        with self.lock:
            inv, tasks, agv, saf, bat = self.inv, self.tasks, self.agv, self.safety, self.battery
            alerts = list(self.alerts)[:60]
            trail = list(self.trail)[-300:]
            seen = dict(self.last_seen)
        now = time.time()
        health = {t: round(now - s, 1) for t, s in seen.items()}
        out = {'wall_time': now, 'health': health, 'alerts': alerts, 'trail': trail, 'energy_wh': round(self.energy_wh, 1)}
        if agv:
            out['agv'] = {'id': agv.agv_id, 'state': agv.state, 'task': agv.current_task,
                          'carrying': agv.carrying_container, 'goal': agv.goal_location, 'location': agv.current_location,
                          'x': agv.pose.x if agv.pose.x == agv.pose.x else None,
                          'y': agv.pose.y if agv.pose.y == agv.pose.y else None,
                          'theta': agv.pose.theta, 'speed': round(agv.linear_speed, 2),
                          'battery': round(agv.battery_percent, 1), 'charging': agv.charging, 'available': agv.available,
                          'distance_m': round(agv.distance_travelled_m, 1),
                          'utilization': round(agv.busy_time_s / agv.uptime_s, 3) if agv.uptime_s > 0 else 0.0,
                          'online': now - seen.get('/agv/state', 0) < 3.0}
        if bat:
            out['battery'] = {'percent': round(bat.percentage * 100, 1), 'voltage': round(bat.voltage, 2),
                              'current': round(bat.current, 2), 'status': bat.power_supply_status}
        if saf:
            out['safety'] = {'state': saf.state, 'estop': saf.estop_active, 'motion_allowed': saf.motion_allowed,
                             'speed_limit': round(saf.speed_limit, 2), 'zone': saf.zone,
                             'conditions': list(saf.active_conditions)}
        if inv:
            out['inventory'] = {
                'capacity': inv.storage_capacity, 'occupied': inv.storage_occupied,
                'occupancy': round(inv.storage_occupancy, 3), 'volume_l': round(inv.stored_volume_l, 1),
                'received': inv.containers_received_total, 'dispatched': inv.containers_dispatched_total,
                'containers': [{'id': c.id, 'type': c.container_type, 'status': c.status, 'quality': c.quality_status,
                                'location': c.location, 'destination': c.destination, 'volume_l': round(
                                    c.measured_volume_l or c.declared_volume_l, 1), 'weight_kg': round(c.weight_kg, 1),
                                'supplier': c.supplier, 'arrival': round(c.arrival_time, 1)} for c in inv.containers],
                'slots': [{'id': s.id, 'kind': s.kind, 'container': s.container_id, 'reserved': s.reserved,
                           'enabled': s.enabled} for s in inv.slots],
            }
        if tasks:
            active = [t for t in tasks.tasks if t.status in ('PENDING', 'ASSIGNED', 'IN_PROGRESS')]
            done = [t for t in tasks.tasks if t.status not in ('PENDING', 'ASSIGNED', 'IN_PROGRESS')]
            conv = lambda t: {'id': t.id, 'type': t.task_type, 'container': t.container_id, 'source': t.source,  # noqa
                              'destination': t.destination, 'status': t.status, 'phase': t.phase,
                              'agv': t.assigned_agv, 'attempts': t.attempts,
                              'duration': round((t.completed_time or 0) - t.created_time, 1) if t.completed_time else None,
                              'reason': t.failure_reason}
            durations = [t.completed_time - t.created_time for t in done if t.status == 'COMPLETED']
            out['tasks'] = {'active': [conv(t) for t in active], 'recent': [conv(t) for t in done[:15]],
                            'completed': tasks.completed_total, 'failed': tasks.failed_total,
                            'cancelled': tasks.cancelled_total,
                            'avg_duration_s': round(sum(durations) / len(durations), 1) if durations else None}
            uptime_h = (agv.uptime_s / 3600.0) if agv and agv.uptime_s > 0 else 0
            out['throughput_per_h'] = round(tasks.completed_total / uptime_h, 1) if uptime_h > 0.01 else None
        status = 'OK'
        if saf and saf.state in ('ALARM', 'EMERGENCY_STOP'):
            status = saf.state
        elif (saf and saf.state == 'WARNING') or not out.get('agv', {}).get('online', False):
            status = 'WARNING'
        out['system_status'] = status
        return out

    # ------------------------------------------------------------------ HTTP side
    def _handler_class(self):
        node = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):   # keep the ROS log clean
                pass

            def _send(self, code, body, ctype='application/json'):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header('Content-Type', ctype)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                path = self.path.split('?')[0]
                if path == '/api/state':
                    return self._send(200, node.state())
                if path == '/api/layout':
                    return self._send(200, node.layout_json)
                if path == '/api/stream':
                    self.send_response(200)
                    self.send_header('Content-Type', 'text/event-stream')
                    self.send_header('Cache-Control', 'no-cache')
                    self.end_headers()
                    try:
                        while rclpy.ok():
                            self.wfile.write(f'data: {json.dumps(node.state())}\n\n'.encode())
                            self.wfile.flush()
                            time.sleep(1.0)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    return
                name = 'index.html' if path in ('/', '/index.html') else path.lstrip('/')
                full = os.path.normpath(os.path.join(node.static_dir, name))
                if not full.startswith(node.static_dir) or not os.path.isfile(full):
                    return self._send(404, {'error': 'not found'})
                ctype = {'.html': 'text/html', '.js': 'application/javascript', '.css': 'text/css'}.get(
                    os.path.splitext(full)[1], 'application/octet-stream')
                with open(full, 'rb') as f:
                    return self._send(200, f.read(), ctype + '; charset=utf-8')

            def do_POST(self):
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0)) or 0) or b'{}')
                except json.JSONDecodeError:
                    return self._send(400, {'ok': False, 'message': 'invalid JSON'})
                path = self.path.split('?')[0]
                try:
                    if path == '/api/delivery':
                        req = ReceiveDelivery.Request()
                        req.supplier = str(body.get('supplier', 'Restaurant'))
                        cs = body.get('containers', [['DRUM_200L', 180]])
                        req.container_types = [str(c[0]) for c in cs]
                        req.declared_volumes_l = [float(c[1]) for c in cs]
                        return self._send(200, node._call('delivery', req))
                    if path == '/api/processing':
                        req = RequestDispatch.Request()
                        req.count = int(body.get('count', 1))
                        return self._send(200, node._call('processing', req))
                    if path == '/api/estop':
                        return self._send(200, node._call('estop', SetBool.Request(data=bool(body.get('active', True)))))
                    if path == '/api/fault':
                        req = InjectFault.Request()
                        req.fault_type = str(body.get('type', ''))
                        req.active = bool(body.get('active', True))
                        req.target = str(body.get('target', ''))
                        req.value = float(body.get('value', 0) or 0)
                        req.duration_s = float(body.get('duration', 0) or 0)
                        return self._send(200, node._call('fault', req))
                    if path == '/api/move':
                        if not node.move_ac.wait_for_server(timeout_sec=3.0):
                            return self._send(200, {'ok': False, 'message': 'AGV not available'})
                        goal = NavigateToLocation.Goal()
                        goal.location_id = str(body.get('location', ''))
                        fut = node.move_ac.send_goal_async(goal)
                        ev = threading.Event()
                        fut.add_done_callback(lambda _: ev.set())
                        ev.wait(10.0)
                        h = fut.result() if fut.done() else None
                        ok = bool(h and h.accepted)
                        return self._send(200, {'ok': ok, 'message': f'moving to {goal.location_id}' if ok else
                                                'rejected (AGV busy, charging or stopped)'})
                except (ValueError, TypeError, IndexError) as e:
                    return self._send(400, {'ok': False, 'message': str(e)})
                return self._send(404, {'ok': False, 'message': 'unknown endpoint'})
        return Handler


def main(args=None):
    rclpy.init(args=args)
    node = Dashboard()
    try:
        rclpy.spin(node)   # HTTP handler threads wait on futures completed by this executor
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.httpd.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
