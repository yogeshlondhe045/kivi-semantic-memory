"""Dashboard HTTP API test: static page, layout, state, command validation (no other nodes needed)."""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

import pytest


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


@pytest.fixture(scope='module')
def server():
    port = free_port()
    env = dict(os.environ, ROS_DOMAIN_ID=str(150 + os.getpid() % 50))
    proc = subprocess.Popen([sys.executable, '-m', 'uco_dashboard.dashboard_server', '--ros-args', '-p',
                             f'port:={port}'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f'http://127.0.0.1:{port}'
    for _ in range(100):
        try:
            urllib.request.urlopen(base + '/api/layout', timeout=1)
            break
        except OSError:
            time.sleep(0.2)
    yield base
    proc.terminate()
    proc.wait(timeout=10)


def get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.headers.get('Content-Type'), r.read()


def post(url, body):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST')
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def test_index_and_static(server):
    code, ctype, body = get(server + '/')
    assert code == 200 and 'text/html' in ctype and b'UCO Warehouse Digital Twin' in body
    assert get(server + '/app.js')[0] == 200


def test_layout_api(server):
    lay = json.loads(get(server + '/api/layout')[2])
    assert lay['size'] == [36.0, 24.0]
    assert sum(1 for loc in lay['locations'] if loc['kind'] == 'STORAGE') == 32


def test_state_without_system(server):
    st = json.loads(get(server + '/api/state')[2])
    assert st['system_status'] == 'WARNING'          # no AGV heartbeat yet
    assert st['alerts'] == []


def test_commands_report_missing_services(server):
    r = post(server + '/api/processing', {'count': 1})
    assert r['ok'] is False and 'not available' in r['message']


def test_path_traversal_blocked(server):
    with pytest.raises(urllib.error.HTTPError) as e:
        get(server + '/../../setup.py')
    assert e.value.code == 404
