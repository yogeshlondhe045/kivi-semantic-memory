"""The AGV xacro must expand to a valid URDF with the expected frames and sensors."""
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

XACRO = os.path.join(os.path.dirname(__file__), '..', 'urdf', 'agv.urdf.xacro')


def expand(*args):
    out = subprocess.run(['xacro', XACRO, *args], capture_output=True, text=True, check=True)
    return out.stdout


@pytest.mark.parametrize('camera', ['false', 'true'])
def test_expands_and_has_frames(camera):
    root = ET.fromstring(expand(f'camera:={camera}'))
    links = {link.get('name') for link in root.findall('link')}
    for required in ('base_footprint', 'base_link', 'deck_link', 'left_wheel_link', 'right_wheel_link',
                     'lidar_front_link', 'lidar_rear_link', 'imu_link', 'estop_link'):
        assert required in links
    assert ('camera_link' in links) == (camera == 'true')
    sensors = [s.get('type') for s in root.iter('sensor')]
    assert sensors.count('gpu_lidar') == 2 and 'imu' in sensors


def test_deck_height_matches_layout():
    root = ET.fromstring(expand())
    joints = {j.get('name'): j for j in root.findall('joint')}
    wheel_r = float(joints['base_footprint_joint'].find('origin').get('xyz').split()[2])
    deck_z = float(joints['deck_joint'].find('origin').get('xyz').split()[2])
    assert wheel_r + deck_z == pytest.approx(0.35)   # layout agv.deck_height


@pytest.mark.skipif(shutil.which('check_urdf') is None, reason='check_urdf not installed')
def test_check_urdf(tmp_path):
    path = tmp_path / 'agv.urdf'
    path.write_text(expand())
    result = subprocess.run(['check_urdf', str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
