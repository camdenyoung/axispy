import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from axispy import Camera, Controller, STOP
from axispy_web import Dashboard, LiveLogs


@pytest.fixture
def web_config(monkeypatch):
    configs = [dict(name=f'Camera {i}', url=f'http://192.168.1.{i}', username='operator', password_env=f'TEST_WEB_PASSWORD_{i}', channel=i) for i in range(1, 5)]
    for config in configs:
        monkeypatch.setenv(config['password_env'], 'private-example-password')
    return configs


def response(body='pan=0\ntilt=0\nzoom=1\nautofocus=on', status=200):
    result = requests.Response()
    result.status_code = status
    result._content = body.encode()
    return result


def test_status_probe_is_read_only_and_reports_focus(web_config):
    dashboard = Dashboard(web_config, Controller([Mock() for _ in range(4)]))
    session = Mock()
    session.get.return_value = response()
    dashboard.probe(2, session)
    camera = dashboard.snapshot()['cameras'][2]
    assert camera['online'] is True
    assert camera['focus'] == 'auto'
    assert camera['focus_source'] == 'camera'
    assert session.get.call_args.kwargs['params'] == {'camera': 3, 'query': 'position'}
    assert session.get.call_args.kwargs['allow_redirects'] is False


@pytest.mark.parametrize('result,reason', [(response(status=401), 'HTTP 401'), (response(status=503), 'HTTP 503'), (response('<html>Login</html>'), 'VAPIX position'), (response('Error: not permitted'), 'VAPIX position')])
def test_bad_probe_is_unavailable_with_reason(web_config, result, reason):
    dashboard = Dashboard(web_config, Controller([Mock() for _ in range(4)]))
    session = Mock()
    session.get.return_value = result
    dashboard.probe(0, session)
    state = dashboard.snapshot()['cameras'][0]
    assert state['online'] is False
    assert reason in state['error']
    assert state['focus'] == 'unknown'


def test_probe_without_focus_does_not_invent_auto_mode(web_config):
    dashboard = Dashboard(web_config, Controller([Mock() for _ in range(4)]))
    session = Mock()
    session.get.return_value = response('pan=0\ntilt=0')
    dashboard.probe(0, session)
    assert dashboard.snapshot()['cameras'][0]['focus'] == 'unknown'
    dashboard.command_result(0, True, 'manual')
    dashboard.probe(0, session)
    assert dashboard.snapshot()['cameras'][0]['focus'] == 'manual'
    assert dashboard.snapshot()['cameras'][0]['focus_source'] == 'command'


def test_stale_probe_cannot_override_newer_control_state(web_config):
    dashboard = Dashboard(web_config, Controller([Mock() for _ in range(4)]))
    session = Mock()
    def delayed_reply(*_, **__):
        dashboard.command_result(0, True, 'manual')
        return response()  # Autofocus was on when this request started.
    session.get.side_effect = delayed_reply
    dashboard.probe(0, session)
    assert dashboard.snapshot()['cameras'][0]['focus'] == 'manual'
    def delayed_failure(*_, **__):
        dashboard.command_result(0, True)
        raise requests.Timeout('sensitive request URL')
    session.get.side_effect = delayed_failure
    dashboard.probe(0, session)
    assert dashboard.snapshot()['cameras'][0]['online'] is True


def test_live_logs_are_bounded_incremental_and_redacted():
    logs = LiveLogs(['private/#pass'], capacity=2)
    for message in ['old', 'secret private/#pass', 'encoded private%2F%23pass']:
        logs.emit(logging.LogRecord('axispy', logging.INFO, '', 0, message, (), None))
    result = logs.since(0)
    assert len(result['entries']) == 2
    assert all('[redacted]' in entry['message'] for entry in result['entries'])
    assert logs.since(2)['entries'][0]['id'] == 3
    assert len(logs.since(999)['entries']) == 2


@pytest.fixture
def running_dashboard(web_config):
    modes = {i: 'on' for i in range(1, 5)}
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            params = parse_qs(urlsplit(self.path).query)
            received.append(params)
            channel = int(params['camera'][0])
            if 'autofocus' in params:
                modes[channel] = params['autofocus'][0]
            body = f'pan=0\ntilt=0\nzoom=1\nautofocus={modes[channel]}' if params.get('query') == ['position'] else ''
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body.encode())
        def log_message(self, *_):
            pass
    camera_server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    camera_thread = threading.Thread(target=camera_server.serve_forever, daemon=True)
    camera_thread.start()
    configs = [dict(config, url=f'http://127.0.0.1:{camera_server.server_port}') for config in web_config]
    cameras = [Camera(config, .5, 2) for config in configs]
    controller = Controller(cameras)
    dashboard = Dashboard(configs, controller, port=0, interval=.05)
    for index, camera in enumerate(cameras):
        camera.dashboard, camera.dashboard_index = dashboard, index
    dashboard.start()
    try:
        yield dashboard, controller, received
    finally:
        dashboard.stop()
        for camera in cameras:
            camera.close()
        camera_server.shutdown()
        camera_server.server_close()
        camera_thread.join()


def get(dashboard, path):
    # Local test HTTP requests should never use ambient network proxies.
    with requests.Session() as session:
        session.trust_env = False
        return session.get(f'http://127.0.0.1:{dashboard.port}{path}', timeout=2)


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.02)
    raise AssertionError('Status did not converge')


def test_http_dashboard_selection_focus_joystick_and_no_credentials(running_dashboard):
    dashboard, controller, _ = running_dashboard
    page = get(dashboard, '/')
    assert page.status_code == 200
    assert 'Debug logs' in page.text
    wait_for(lambda: all(camera['online'] for camera in dashboard.snapshot()['cameras']))
    controller.select(2)
    controller.send(STOP)
    controller.send((0, 0, 0, 30))
    dashboard.set_joystick('AXIS T8311')
    data = get(dashboard, '/api/status').json()
    assert data['selected'] == 2
    assert data['cameras'][2]['selected'] is True
    assert data['cameras'][2]['focus'] == 'manual'
    assert data['joystick'] == 'AXIS T8311'
    assert 'private-example-password' not in json.dumps(data)
    assert 'password_env' not in json.dumps(data)
    assert controller.enable_autofocus(2)
    wait_for(lambda: dashboard.snapshot()['cameras'][2]['focus'] == 'auto')
    dashboard.set_joystick(None)
    assert get(dashboard, '/api/status').json()['joystick'] is None
    assert get(dashboard, '/api/status').json()['armed'] is False


def test_http_live_logs_and_routes(running_dashboard, caplog):
    dashboard, _, _ = running_dashboard
    with caplog.at_level(logging.INFO, logger='axispy'):
        logging.getLogger('axispy').info('Camera ready private-example-password')
    data = get(dashboard, '/api/logs').json()
    assert any('Camera ready [redacted]' in entry['message'] for entry in data['entries'])
    assert 'private-example-password' not in json.dumps(data)
    assert get(dashboard, f'/api/logs?after={data["cursor"]}').json()['entries'] == []
    assert get(dashboard, '/api/logs?after=invalid').status_code == 400
    assert get(dashboard, '/api/logs?after=-1').status_code == 400
    assert get(dashboard, '/.env').status_code == 404
    assert get(dashboard, '/api/status').headers['Cache-Control'] == 'no-store'
