"""scenario_runner: executes a scripted scenario against the running digital twin and checks results.

Scenarios live in config/scenarios.yaml. Each step is a one-key mapping:

  log: "text"                                   print a narrative line
  delivery: {supplier, containers: [[TYPE, litres], ...]}
  processing_request: {count: N}
  move_agv: {location: A03, wait: true}
  fault: {type: LOW_BATTERY, active: true, target: '', value: 18, duration: 0}
  estop: true | false
  sleep: seconds (simulation time)
  wait_for: {<condition>: <value>, ..., timeout: s}   all conditions must hold (fails on timeout)
  expect: {<condition>: <value>, ...}                  checked once
  snapshot: {name: file_stem, view: overview|receiving|storage|dispatch}

Conditions: received, stored, dispatched, tasks_completed, tasks_failed, tasks_active, holding,
  agv_state, safety_state, motion_allowed, speed_limit, agv_speed_below, agv_speed_above, agv_at,
  battery_below, battery_above,
  alert (code seen since scenario start), alert_since_step (since the last action step), task_phase,
  container_status
Numeric values: plain number = ">=", or a string with an operator: "==3", "<=0", "<1".

Writes <results_dir>/<run>/scenario_report.json and exits 0 only if every check passed.
"""
from __future__ import annotations

import json
import math
import operator
import os
import subprocess
import sys
import time
from typing import Any, Dict, List

import rclpy
import yaml
from rclpy.action import ActionClient
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from std_srvs.srv import SetBool

from uco_common.layout import load_layout
from uco_common.qos import EVENTS, LATCHED
from uco_interfaces.action import NavigateToLocation
from uco_interfaces.msg import AgvState, Alert, Inventory, SafetyStatus, TaskList
from uco_interfaces.srv import InjectFault, ReceiveDelivery, RequestDispatch

OPS = {'>=': operator.ge, '<=': operator.le, '==': operator.eq, '>': operator.gt, '<': operator.lt,
       '!=': operator.ne}
ACTION_STEPS = ('delivery', 'processing_request', 'move_agv', 'fault', 'estop')
VIEWS = {   # x y z roll pitch yaw, horizontal fov
    'overview': ([18.0, -7.0, 20.0, 0.0, 0.85, 1.5708], 1.3),
    'receiving': ([6.0, 9.5, 7.5, 0.0, 0.62, 1.5708], 1.2),
    'storage': ([18.6, -2.5, 9.0, 0.0, 0.62, 1.5708], 1.25),
    'dispatch': ([27.0, 13.5, 6.5, 0.0, 0.55, 0.0], 1.2),
}


def _cmp(actual, spec) -> bool:
    if isinstance(spec, str):
        for sym in ('>=', '<=', '==', '!=', '>', '<'):
            if spec.startswith(sym):
                return OPS[sym](actual, float(spec[len(sym):]))
        return actual == spec
    if isinstance(spec, bool):
        return bool(actual) == spec
    return actual >= spec


class ScenarioRunner(Node):
    def __init__(self):
        super().__init__('scenario_runner')
        self.declare_parameter('scenario_file', '')
        self.declare_parameter('scenario', 'demo')
        self.declare_parameter('layout_file', '')
        self.declare_parameter('results_dir', os.path.join(os.getcwd(), 'runtime', 'results'))
        self.declare_parameter('run_name', '')
        self.layout = load_layout(self.get_parameter('layout_file').value or None)
        self.inv = None
        self.tasks = None
        self.agv = None
        self.safety = None
        self.alerts: List[Alert] = []
        self.create_subscription(Inventory, '/warehouse/inventory', lambda m: setattr(self, 'inv', m), LATCHED)
        self.create_subscription(TaskList, '/warehouse/tasks', lambda m: setattr(self, 'tasks', m), LATCHED)
        self.create_subscription(AgvState, '/agv/state', lambda m: setattr(self, 'agv', m), 10)
        self.create_subscription(SafetyStatus, '/safety/status', lambda m: setattr(self, 'safety', m), LATCHED)
        self.create_subscription(Alert, '/warehouse/alerts', lambda m: self.alerts.append(m), EVENTS)
        self.delivery_cli = self.create_client(ReceiveDelivery, '/stations/receive_delivery')
        self.processing_cli = self.create_client(RequestDispatch, '/stations/request_processing')
        self.fault_cli = self.create_client(InjectFault, '/faults/inject')
        self.estop_cli = self.create_client(SetBool, '/safety/emergency_stop')
        self.move_ac = ActionClient(self, NavigateToLocation, '/agv_01/navigate_to_location')
        self.step_start_alert = 0
        self.report: Dict[str, Any] = {}

    # ------------------------------------------------------------------ utilities
    def now(self) -> float:
        return self.get_clock().now().nanoseconds * 1e-9

    def spin_for(self, seconds: float) -> None:
        end = self.now() + seconds
        while rclpy.ok() and self.now() < end:
            rclpy.spin_once(self, timeout_sec=0.1)

    def call(self, client, req, timeout=30.0):
        if not client.wait_for_service(timeout_sec=timeout):
            raise RuntimeError(f'service {client.srv_name} not available')
        fut = client.call_async(req)
        rclpy.spin_until_future_complete(self, fut, timeout_sec=timeout)
        if not fut.done():
            raise RuntimeError(f'service {client.srv_name} timed out')
        return fut.result()

    def say(self, text: str) -> None:
        self.get_logger().info(f'[SCENARIO] {text}')

    # ------------------------------------------------------------------ conditions
    def value(self, key: str, spec):
        inv, tasks, agv, saf = self.inv, self.tasks, self.agv, self.safety
        if key == 'received':
            return inv.containers_received_total if inv else 0
        if key == 'stored':
            return inv.storage_occupied if inv else 0
        if key == 'dispatched':
            return inv.containers_dispatched_total if inv else 0
        if key == 'holding':
            return sum(1 for s in inv.slots if s.kind == 'HOLDING' and s.container_id) if inv else 0
        if key == 'tasks_completed':
            return tasks.completed_total if tasks else 0
        if key == 'tasks_failed':
            return tasks.failed_total if tasks else 0
        if key == 'tasks_active':
            return sum(1 for t in tasks.tasks if t.status in ('PENDING', 'ASSIGNED', 'IN_PROGRESS')) if tasks else 0
        if key == 'agv_state':
            return agv.state if agv else ''
        if key == 'task_phase':
            return next((t.phase for t in tasks.tasks if t.status == 'IN_PROGRESS'), '') if tasks else ''
        if key == 'safety_state':
            return saf.state if saf else ''
        if key == 'motion_allowed':
            return saf.motion_allowed if saf else True
        if key in ('agv_speed_below', 'agv_speed_above'):
            return abs(agv.linear_speed) if agv else (99.0 if key == 'agv_speed_below' else 0.0)
        if key == 'speed_limit':
            return round(saf.speed_limit, 2) if saf else 0.0
        if key == 'battery_below' or key == 'battery_above':
            return agv.battery_percent if agv else -1.0
        if key == 'agv_at':
            if not agv:
                return ''
            loc = self.layout.location(spec)
            return spec if math.hypot(agv.pose.x - loc.access.x, agv.pose.y - loc.access.y) < 0.5 else agv.current_location
        if key == 'alert':
            return spec if any(a.code == spec for a in self.alerts) else ''
        if key == 'alert_since_step':
            return spec if any(a.code == spec for a in self.alerts[self.step_start_alert:]) else ''
        if key == 'container_status':
            return {cid: next((c.status for c in inv.containers if c.id == cid), 'GONE') for cid in spec} if inv else {}
        raise ValueError(f'unknown condition {key}')

    def check(self, key: str, spec) -> bool:
        v = self.value(key, spec)
        if key == 'agv_speed_below':
            return v < float(spec)
        if key == 'agv_speed_above':
            return v > float(spec)
        if key == 'battery_below':
            return v < float(spec)
        if key == 'battery_above':
            return v > float(spec)
        if key in ('agv_state', 'safety_state', 'task_phase'):
            return v in (spec if isinstance(spec, list) else [spec])
        if key in ('alert', 'alert_since_step', 'agv_at'):
            return v == spec
        if key == 'container_status':
            return all(v.get(cid) == st for cid, st in spec.items())
        return _cmp(v, spec)

    def describe(self, conds: Dict) -> Dict:
        return {k: self.value(k, v) for k, v in conds.items()}

    # ------------------------------------------------------------------ steps
    def run_step(self, step: Dict, index: int) -> Dict:
        (kind, arg), = step.items()
        rec = {'index': index, 'step': kind, 'arg': arg, 'sim_time': round(self.now(), 1), 'ok': True}
        if kind in ACTION_STEPS:
            # 'alert_since_step' looks at alerts raised since the most recent action step
            self.step_start_alert = len(self.alerts)
        if kind == 'log':
            self.say(str(arg))
        elif kind == 'sleep':
            self.spin_for(float(arg))
        elif kind == 'delivery':
            req = ReceiveDelivery.Request()
            req.supplier = arg.get('supplier', 'Restaurant')
            req.container_types = [c[0] for c in arg['containers']]
            req.declared_volumes_l = [float(c[1]) for c in arg['containers']]
            r = self.call(self.delivery_cli, req)
            rec['ok'], rec['message'] = r.success, r.message
            self.say(f'Delivery: {r.message}')
        elif kind == 'processing_request':
            req = RequestDispatch.Request()
            req.count = int(arg.get('count', 1))
            r = self.call(self.processing_cli, req)
            rec['ok'], rec['message'] = bool(r.success), r.message
            rec['containers'] = list(r.container_ids)
            self.say(f'Processing request: {r.message} {list(r.container_ids)}')
        elif kind == 'fault':
            req = InjectFault.Request()
            req.fault_type, req.active = arg['type'], bool(arg.get('active', True))
            req.target, req.value = str(arg.get('target', '')), float(arg.get('value', 0.0))
            req.duration_s = float(arg.get('duration', 0.0))
            r = self.call(self.fault_cli, req)
            rec['ok'], rec['message'] = r.success, r.message
            self.say(f'Fault: {r.message}')
        elif kind == 'estop':
            r = self.call(self.estop_cli, SetBool.Request(data=bool(arg)))
            rec['ok'], rec['message'] = r.success, r.message
            self.say(f'E-stop: {r.message}')
        elif kind == 'move_agv':
            if not self.move_ac.wait_for_server(timeout_sec=30.0):
                raise RuntimeError('AGV action server not available')
            goal = NavigateToLocation.Goal()
            goal.location_id = arg['location']
            fut = self.move_ac.send_goal_async(goal)
            rclpy.spin_until_future_complete(self, fut, timeout_sec=30.0)
            handle = fut.result()
            rec['ok'] = bool(handle and handle.accepted)
            if rec['ok'] and arg.get('wait', True):
                rf = handle.get_result_async()
                rclpy.spin_until_future_complete(self, rf, timeout_sec=float(arg.get('timeout', 600)))
                res = rf.result().result if rf.done() else None
                rec['ok'] = bool(res and res.success)
                rec['message'] = res.message if res else 'timeout'
            self.say(f'Move AGV to {arg["location"]}: {rec.get("message", "accepted" if rec["ok"] else "rejected")}')
        elif kind == 'wait_for':
            conds = {k: v for k, v in arg.items() if k != 'timeout'}
            timeout = float(arg.get('timeout', 300))
            t0 = self.now()
            while rclpy.ok() and not all(self.check(k, v) for k, v in conds.items()):
                if self.now() - t0 > timeout:
                    rec['ok'] = False
                    break
                rclpy.spin_once(self, timeout_sec=0.2)
            rec['waited_s'] = round(self.now() - t0, 1)
            rec['observed'] = self.describe(conds)
            self.say(f'wait_for {conds}: {"OK" if rec["ok"] else "TIMEOUT"} after {rec["waited_s"]} s '
                     f'(observed {rec["observed"]})')
        elif kind == 'expect':
            self.spin_for(0.5)
            rec['observed'] = self.describe(arg)
            failed = [k for k, v in arg.items() if not self.check(k, v)]
            rec['ok'], rec['failed'] = not failed, failed
            self.say(f'expect {arg}: {"PASS" if not failed else "FAIL " + str(failed)} (observed {rec["observed"]})')
        elif kind == 'snapshot':
            rec['file'] = self.snapshot(arg['name'], arg.get('view', 'overview'))
            rec['ok'] = rec['file'] is not None
        else:
            raise ValueError(f'unknown step {kind}')
        return rec

    def snapshot(self, name: str, view: str):
        pose, fov = VIEWS[view]
        out_dir = os.path.join(self.out_dir, 'figures')
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f'{name}.png')
        try:
            from ament_index_python.packages import get_package_prefix
            tool = os.path.join(get_package_prefix('uco_simulation'), 'lib', 'uco_simulation', 'snapshot.py')
            subprocess.run([sys.executable, tool, '--world', self.layout.world_name, '--out', path, '--fov', str(fov),
                            '--pose', *[str(v) for v in pose]], check=True, timeout=90, capture_output=True)
            self.say(f'snapshot {path}')
            return path
        except Exception as e:  # noqa: BLE001 - figures are optional, never fail the scenario for them
            self.get_logger().warning(f'[WARN] snapshot failed: {e}')
            return None

    # ------------------------------------------------------------------ main
    def run(self) -> int:
        path = self.get_parameter('scenario_file').value
        name = self.get_parameter('scenario').value
        with open(path, encoding='utf-8') as f:
            spec = yaml.safe_load(f)['scenarios'][name]
        run = self.get_parameter('run_name').value or f'{name}_{time.strftime("%Y%m%d_%H%M%S")}'
        self.out_dir = os.path.join(self.get_parameter('results_dir').value, run)
        os.makedirs(self.out_dir, exist_ok=True)
        self.say(f'=== Scenario "{name}": {spec.get("description", "")} ===')
        # wait until the system is up (AGV heartbeat and WMS inventory)
        t0 = time.time()
        while rclpy.ok() and (self.agv is None or self.inv is None) and time.time() - t0 < 240:
            rclpy.spin_once(self, timeout_sec=0.2)
        if self.agv is None or self.inv is None:
            self.get_logger().error('[ERROR] system not ready (no AGV state / inventory)')
            return 3
        steps, ok = [], True
        started = self.now()
        for i, step in enumerate(spec['steps']):
            try:
                rec = self.run_step(step, i)
            except Exception as e:  # noqa: BLE001 - record and stop the scenario
                rec = {'index': i, 'step': list(step)[0], 'ok': False, 'error': str(e)}
                self.get_logger().error(f'[ERROR] step {i} failed: {e}')
            steps.append(rec)
            if not rec['ok']:
                ok = False
                if spec.get('stop_on_failure', True):
                    break
        alerts = [{'t': round(a.stamp.sec + a.stamp.nanosec * 1e-9, 1), 'severity': a.severity, 'source': a.source,
                   'code': a.code, 'message': a.message} for a in self.alerts]
        self.report = {'scenario': name, 'description': spec.get('description', ''), 'passed': ok,
                       'sim_duration_s': round(self.now() - started, 1), 'steps': steps, 'alerts': alerts}
        with open(os.path.join(self.out_dir, 'scenario_report.json'), 'w', encoding='utf-8') as f:
            json.dump(self.report, f, indent=2, default=str)
        checks = [s for s in steps if s['step'] in ('expect', 'wait_for')]
        self.say(f'=== Scenario "{name}" {"PASSED" if ok else "FAILED"}: {sum(s["ok"] for s in checks)}/{len(checks)} '
                 f'checks, {self.report["sim_duration_s"]} s simulated; report {self.out_dir} ===')
        return 0 if ok else 1


def main(args=None):
    rclpy.init(args=args)
    node = ScenarioRunner()
    code = 1
    try:
        code = node.run()
    except (KeyboardInterrupt, ExternalShutdownException):
        code = 130
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
    sys.exit(code)


if __name__ == '__main__':
    main()
