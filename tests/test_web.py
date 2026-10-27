import json
import errno
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


@pytest.mark.parametrize('value,expected', [(None, 1000), ('', 1000), ('  ', 1000), ('250', 250), ('100', 100), ('60000', 60000)])
def test_browser_poll_interval_environment(web_config, monkeypatch, value, expected):
    monkeypatch.delenv('AXIS_WEB_POLL_INTERVAL_MS', raising=False)
    if value is not None:
        monkeypatch.setenv('AXIS_WEB_POLL_INTERVAL_MS', value)
    dashboard = Dashboard(web_config, Controller([Mock() for _ in range(4)]))
    assert dashboard.snapshot()['poll_interval_ms'] == expected
    assert dashboard.interval == 5  # Browser polling must not change camera probes.


@pytest.mark.parametrize('value', ['0', '-1', '99', '60001', '250.5', 'fast'])
def test_invalid_browser_poll_interval(web_config, monkeypatch, value):
    monkeypatch.setenv('AXIS_WEB_POLL_INTERVAL_MS', value)
    with pytest.raises(ValueError, match='AXIS_WEB_POLL_INTERVAL_MS'):
        Dashboard(web_config, Controller([Mock() for _ in range(4)]))


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
def test_api_error_stays_online_with_reason(web_config, result, reason):
    dashboard = Dashboard(web_config, Controller([Mock() for _ in range(4)]))
    session = Mock()
    session.get.return_value = result
    dashboard.probe(0, session)
    state = dashboard.snapshot()['cameras'][0]
    assert state['online'] is True
    assert state['consecutive_failures'] == 0
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


def test_ptz_activity_tracks_commands_stops_selection_and_failures(running_dashboard):
    dashboard, controller, _ = running_dashboard

    def activity(index=0):
        return get(dashboard, '/api/status').json()['cameras'][index]['ptz_activity']

    assert activity() == 'unknown'
    controller.send(STOP)  # Initial safety stop.
    controller.send(STOP)  # Arm locally.
    assert activity() == 'idle'
    for motion in [(25, 0, 0, 0), (0, -30, 0, 0), (0, 0, 20, 0)]:
        controller.send(motion)
        assert activity() == 'active'
    # A read-only probe must not turn an active command into Idle.
    with requests.Session() as session:
        session.trust_env = False
        dashboard.probe(0, session)
    assert activity() == 'active'
    controller.send(STOP)
    assert activity() == 'idle'
    controller.send((0, 0, 0, 30))
    assert activity() == 'idle'  # Focus alone is not PTZ.
    controller.select(1)
    assert activity(0) == activity(1) == 'idle'
    controller.send(STOP)
    controller.send((10, 0, 0, 0))
    assert activity(1) == 'active'
    camera = controller.cameras[1]
    original_move = camera.move
    camera.move = Mock(side_effect=requests.ConnectionError())
    try:
        controller.send(STOP)
        assert activity(1) == 'unknown'  # Failed stop is not Idle.
        dashboard.command_result(1, True, 'auto')
        assert activity(1) == 'unknown'  # Focus success doesn't confirm a stop.
        with requests.Session() as session:
            session.trust_env = False
            dashboard.probe(1, session)
        assert activity(1) == 'unknown'
    finally:
        camera.move = original_move
    controller.stop()
    assert activity(1) == 'idle'


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



def test_single_probe_timeout_does_not_mark_healthy_camera_offline(web_config):
    now = [0.]
    dashboard = Dashboard(web_config, Controller([Mock() for _ in range(4)]), clock=lambda: now[0])
    session = Mock()
    session.get.return_value = response()
    dashboard.probe(0, session)
    now[0] = 5
    session.get.side_effect = requests.ReadTimeout()
    dashboard.probe(0, session)
    state = dashboard.snapshot()['cameras'][0]
    assert state['online'] is True
    assert state['consecutive_failures'] == 1
    assert 'confirming connectivity' in state['status_note']
    session.get.side_effect = None
    now[0] = 10
    dashboard.probe(0, session)
    state = dashboard.snapshot()['cameras'][0]
    assert state['online'] is True
    assert state['consecutive_failures'] == 0
    assert state['status_note'] is None
    assert state['error'] is None


def test_persistent_outage_becomes_offline_and_recovers(web_config):
    now = [0.]
    dashboard = Dashboard(web_config, Controller([Mock() for _ in range(4)]), clock=lambda: now[0])
    dashboard.command_result(0, True, 'manual')
    session = Mock()
    session.get.side_effect = requests.ConnectionError()
    for moment in (5, 10):
        now[0] = moment
        dashboard.probe(0, session)
        assert dashboard.snapshot()['cameras'][0]['online'] is True
    now[0] = 15
    dashboard.probe(0, session)
    state = dashboard.snapshot()['cameras'][0]
    assert state['online'] is False
    assert state['consecutive_failures'] == 3
    assert state['focus'] == 'manual'
    session.get.side_effect = None
    session.get.return_value = response()
    now[0] = 20
    dashboard.probe(0, session)
    assert dashboard.snapshot()['cameras'][0]['online'] is True


def test_control_failure_does_not_toggle_connectivity(web_config):
    dashboard = Dashboard(web_config, Controller([Mock() for _ in range(4)]))
    dashboard.command_result(0, True)
    for _ in range(10):
        dashboard.command_result(0, False)
    state = dashboard.snapshot()['cameras'][0]
    assert state['online'] is True
    assert state['consecutive_failures'] == 0
    assert 'Control request failed' in state['control_error']
    dashboard.command_result(0, True)
    assert dashboard.snapshot()['cameras'][0]['control_error'] is None


def test_recent_success_prevents_burst_failures_from_claiming_offline(web_config):
    now = [0.]
    dashboard = Dashboard(web_config, Controller([Mock() for _ in range(4)]), clock=lambda: now[0])
    dashboard.command_result(0, True)
    session = Mock()
    session.get.side_effect = requests.Timeout()
    for moment in (1, 2, 3):
        now[0] = moment
        dashboard.probe(0, session)
    assert dashboard.snapshot()['cameras'][0]['online'] is True
    now[0] = 15
    dashboard.probe(0, session)
    assert dashboard.snapshot()['cameras'][0]['online'] is False


def test_initial_connectivity_is_unknown_until_outage_confirmed(web_config):
    dashboard = Dashboard(web_config, Controller([Mock() for _ in range(4)]))
    session = Mock()
    session.get.side_effect = requests.Timeout()
    for _ in range(2):
        dashboard.probe(0, session)
        assert dashboard.snapshot()['cameras'][0]['online'] is None
    dashboard.probe(0, session)
    assert dashboard.snapshot()['cameras'][0]['online'] is False


def test_failed_commands_do_not_suppress_background_outage_evidence(web_config):
    now = [0.]
    dashboard = Dashboard(web_config, Controller([Mock() for _ in range(4)]), clock=lambda: now[0])
    dashboard.command_result(0, True)
    session = Mock()
    def failed_request(*_, **__):
        dashboard.command_result(0, False)
        raise requests.Timeout()
    session.get.side_effect = failed_request
    for moment in (5, 10, 15):
        now[0] = moment
        dashboard.probe(0, session)
    assert dashboard.snapshot()['cameras'][0]['online'] is False


def test_health_failure_logs_reason_duration_and_recovery_without_spam(web_config, caplog):
    now = [0.]
    dashboard = Dashboard(web_config, Controller([Mock() for _ in range(4)]), clock=lambda: now[0])
    session = Mock()
    def timeout(*_, **__):
        now[0] += 2
        raise requests.ReadTimeout('private-example-password must never be printed')
    session.get.side_effect = timeout
    with caplog.at_level(logging.INFO, logger='axispy'):
        dashboard.probe(0, session)
        dashboard.probe(0, session)
        assert caplog.text.count('health check failed') == 1
        assert 'ReadTimeout after 2.00s' in caplog.text
        assert 'private-example-password' not in caplog.text
        assert 'ReadTimeout' in dashboard.snapshot()['cameras'][0]['status_note']
        now[0] = 65
        dashboard.probe(0, session)
        assert caplog.text.count('health check failed') == 2
        session.get.side_effect = None
        session.get.return_value = response()
        dashboard.probe(0, session)
        dashboard.probe(0, session)
        assert caplog.text.count('health check recovered') == 1


def test_superseded_probe_failure_is_still_logged(web_config, caplog):
    dashboard = Dashboard(web_config, Controller([Mock() for _ in range(4)]))
    session = Mock()
    def timeout(*_, **__):
        dashboard.command_result(0, True)
        raise requests.ReadTimeout()
    session.get.side_effect = timeout
    dashboard.probe(0, session)
    assert dashboard.snapshot()['cameras'][0]['online'] is True
    assert 'health check failed: ReadTimeout' in caplog.text


@pytest.mark.parametrize('number,label', [(errno.ECONNREFUSED, 'connection refused'), (errno.ECONNRESET, 'connection reset'), (errno.ENETUNREACH, 'network unreachable'), (errno.EHOSTUNREACH, 'host unreachable')])
def test_connection_error_reports_nested_errno_without_raw_messages(number, label):
    from axispy_web import request_failure_reason
    from urllib3.exceptions import MaxRetryError, NewConnectionError
    cause = OSError(number, 'private-example-password in sensitive host')
    wrapped = NewConnectionError(None, 'private-example-password in URL')
    wrapped.__cause__ = cause
    error = requests.ConnectionError(MaxRetryError(None, '/private-example-password', wrapped))
    reason = request_failure_reason(error)
    assert label in reason
    assert f'errno {number}' in reason
    assert 'private-example-password' not in reason


def test_connection_closed_and_dns_causes_are_identified():
    from axispy_web import request_failure_reason
    from http.client import RemoteDisconnected
    import socket
    assert 'before HTTP response' in request_failure_reason(requests.ConnectionError(RemoteDisconnected('secret')))
    assert 'DNS resolution failed' in request_failure_reason(requests.ConnectionError(socket.gaierror(-2, 'secret')))
    assert request_failure_reason(requests.ReadTimeout('secret')) == 'ReadTimeout'
    assert request_failure_reason(requests.exceptions.SSLError('secret')) == 'SSLError'


def test_health_checks_use_fresh_tcp_connections_without_changing_cadence(web_config):
    received = []
    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def do_GET(self):
            received.append((self.client_address[1], self.headers.get('Connection'), parse_qs(urlsplit(self.path).query)))
            body = b'pan=0\ntilt=0\nautofocus=on'
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = dict(web_config[0], url=f'http://127.0.0.1:{server.server_port}')
    dashboard = Dashboard([config], Controller([Mock()]))
    dashboard.finished = Mock()
    dashboard.finished.is_set.side_effect = [False, False, True]
    try:
        dashboard._monitor(0)
        assert len(received) == 2
        assert received[0][0] != received[1][0]
        assert all(item[1] == 'close' for item in received)
        assert all(item[2] == {'camera': ['1'], 'query': ['position']} for item in received)
        assert dashboard.finished.wait.call_count == 2
        dashboard.finished.wait.assert_called_with(5)
        assert dashboard.snapshot()['cameras'][0]['online'] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
