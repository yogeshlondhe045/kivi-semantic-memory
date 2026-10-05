"""weighing_station: load-cell model, service /stations/weigh (uco_interfaces/Weigh)."""
import random

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node

from uco_common.alerts import AlertPublisher
from uco_common.layout import load_layout
from uco_common.qos import FAULTS
from uco_interfaces.msg import Fault
from uco_interfaces.srv import Weigh

from .station_models import measure_weight


class WeighingStation(Node):
    def __init__(self):
        super().__init__('weighing_station')
        for k, v in (('layout_file', ''), ('noise_sd_kg', 0.3), ('resolution_kg', 0.5), ('calibration_offset_kg', 0.0),
                     ('seed', 11)):
            self.declare_parameter(k, v)
        p = lambda n: self.get_parameter(n).value  # noqa: E731
        self.layout = load_layout(p('layout_file') or None)
        self.rng = random.Random(p('seed'))
        self.unavailable = False
        self.alerts = AlertPublisher(self, 'WEIGH')
        self.create_service(Weigh, '/stations/weigh', self._weigh)
        self.create_subscription(Fault, '/warehouse/faults', self._on_fault, FAULTS)

    def _on_fault(self, f: Fault) -> None:
        if f.fault_type == Fault.STATION_UNAVAILABLE and f.target == 'WEIGH':
            self.unavailable = f.active

    def _weigh(self, req, res):
        if self.unavailable:
            res.success, res.message = False, 'weighing station unavailable'
            return res
        ctype = self.layout.container_types.get(req.container_type)
        if ctype is None:
            res.success, res.message = False, f'unknown container type {req.container_type}'
            return res
        p = lambda n: self.get_parameter(n).value  # noqa: E731
        gross = measure_weight(req.simulated_gross_kg, self.rng, p('noise_sd_kg'), p('resolution_kg'),
                               p('calibration_offset_kg'))
        res.gross_kg = gross
        res.net_kg = max(0.0, gross - ctype.tare_kg)
        res.volume_l = res.net_kg / self.layout.density_kg_per_l
        res.success, res.message = True, f'{req.container_id}: {gross:.1f} kg gross, {res.volume_l:.0f} L'
        self.alerts.info('WEIGHED', f'{req.container_id} weighed: {gross:.1f} kg gross / {res.net_kg:.1f} kg net '
                         f'= {res.volume_l:.0f} L')
        return res


def main(args=None):
    rclpy.init(args=args)
    node = WeighingStation()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
