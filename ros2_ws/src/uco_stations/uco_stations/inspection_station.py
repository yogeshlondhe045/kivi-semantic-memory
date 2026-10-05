"""inspection_station: quality test model, service /stations/inspect (uco_interfaces/Inspect)."""
import random

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node

from uco_common.alerts import AlertPublisher
from uco_common.qos import FAULTS
from uco_interfaces.msg import Fault
from uco_interfaces.srv import Inspect

from .station_models import AcceptanceRules, classify, measure_quality


class InspectionStation(Node):
    def __init__(self):
        super().__init__('inspection_station')
        for k, v in (('pass_max_ffa', 5.0), ('pass_max_water', 1.0), ('marginal_max_ffa', 15.0),
                     ('marginal_max_water', 3.0), ('ffa_noise_sd', 0.15), ('water_noise_sd', 0.05),
                     ('contamination_detection_p', 0.95), ('seed', 23)):
            self.declare_parameter(k, v)
        p = lambda n: self.get_parameter(n).value  # noqa: E731
        self.rules = AcceptanceRules(p('pass_max_ffa'), p('pass_max_water'), p('marginal_max_ffa'),
                                     p('marginal_max_water'))
        self.rng = random.Random(p('seed'))
        self.unavailable = False
        self.alerts = AlertPublisher(self, 'INSPECT')
        self.create_service(Inspect, '/stations/inspect', self._inspect)
        self.create_subscription(Fault, '/warehouse/faults', self._on_fault, FAULTS)

    def _on_fault(self, f: Fault) -> None:
        if f.fault_type == Fault.STATION_UNAVAILABLE and f.target in ('', 'INSPECT'):
            self.unavailable = f.active
            (self.alerts.warn if f.active else self.alerts.info)(
                'STATION_UNAVAILABLE' if f.active else 'STATION_AVAILABLE',
                'Inspection station ' + ('unavailable (simulated fault)' if f.active else 'back in service'))

    def _inspect(self, req, res):
        if self.unavailable:
            res.success, res.message = False, 'inspection station unavailable'
            return res
        p = lambda n: self.get_parameter(n).value  # noqa: E731
        ffa, water, detected = measure_quality(req.simulated_ffa_percent, req.simulated_water_percent,
                                               req.simulated_contaminated, self.rng, p('ffa_noise_sd'),
                                               p('water_noise_sd'), p('contamination_detection_p'))
        res.quality_status = classify(ffa, water, detected, self.rules)
        res.ffa_percent, res.water_percent, res.contamination_detected = ffa, water, detected
        res.success = True
        res.message = (f'{req.container_id}: FFA {ffa:.1f} %, water {water:.2f} %, '
                       f'{"contaminated, " if detected else ""}-> {res.quality_status}')
        (self.alerts.info if res.quality_status == 'PASS' else self.alerts.warn)('INSPECTED', res.message)
        return res


def main(args=None):
    rclpy.init(args=args)
    node = InspectionStation()
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
