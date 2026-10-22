import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
import requests

from axispy import Camera, Controller, Settings, STOP, read_motion, speed


@pytest.mark.parametrize('value,expected', [(0, 0), (.1, 0), (.12, 0), (1, 70), (-1, -70), (.56, 35), (float('nan'), 0)])
def test_speed(value, expected):
    assert speed(value, .12, 70, False) == expected


def test_inverted_axis():
    assert speed(1, .12, 50, True) == -50


def test_deadman():
    joystick = Mock()
    joystick.get_button.return_value = False
    assert read_motion(joystick, {'deadman_button': 4}) == STOP
    joystick.get_axis.assert_not_called()


def test_camera_switch_stops_old_and_requires_neutral():
    cameras = [Mock() for _ in range(4)]
    controller = Controller(cameras)
    controller.send(STOP)
    controller.send(STOP)
    controller.send((50, 0, 0))
    cameras[0].move.assert_called_with((50, 0, 0))
    controller.select(3)
    cameras[0].move.assert_called_with(STOP)
    controller.send((50, 0, 0))
    cameras[3].move.assert_called_with(STOP)
    controller.send(STOP)
    controller.send((0, -50, 20))
    cameras[3].move.assert_called_with((0, -50, 20))


def test_failed_stop_prevents_selection_when_motion_is_uncertain():
    cameras = [Mock() for _ in range(4)]
    controller = Controller(cameras)
    controller.send(STOP)
    controller.send(STOP)
    controller.send((50, 0, 0))
    cameras[0].move.side_effect = requests.Timeout()
    controller.select(1)
    assert controller.selected == 0
    assert not controller.armed
    cameras[1].move.assert_not_called()


def test_shutdown_retries_stop():
    camera = Mock()
    camera.move.side_effect = [requests.Timeout(), None]
    controller = Controller([camera])
    controller.stop()
    assert camera.move.call_count == 2
    camera.move.assert_called_with(STOP)


@pytest.fixture
def http_camera():
    received = []
    class Handler(BaseHTTPRequestHandler):
        body = b''
        status = 200
        location = None
        def do_GET(self):
            received.append(urlsplit(self.path))
            self.send_response(self.status)
            if self.location:
                self.send_header("Location", self.location)
            self.end_headers()
            self.wfile.write(self.body)
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', received, Handler
    server.shutdown()
    server.server_close()
    thread.join()


def test_vapix_request_and_stop(http_camera, monkeypatch):
    url, received, _ = http_camera
    monkeypatch.setenv('TEST_CAMERA_PASSWORD', 'local-test-only')
    camera = Camera(dict(name='test', url=url, username='operator', password_env='TEST_CAMERA_PASSWORD', channel=2), .5, 2)
    try:
        camera.move((25, -30, 50))
        camera.move(STOP)
    finally:
        camera.close()
    assert received[0].path == '/axis-cgi/com/ptz.cgi'
    assert parse_qs(received[0].query) == dict(camera=['2'], continuouspantiltmove=['25,-30'], continuouszoommove=['50'], continuoustimeout=['2'])
    assert parse_qs(received[1].query)['continuouspantiltmove'] == ['0,0']
    assert parse_qs(received[1].query)['continuouszoommove'] == ['0']


def test_vapix_error_in_http_200(http_camera, monkeypatch):
    url, _, handler = http_camera
    handler.body = b'Error: PTZ not available'
    monkeypatch.setenv('TEST_CAMERA_PASSWORD', 'local-test-only')
    camera = Camera(dict(name='test', url=url, username='operator', password_env='TEST_CAMERA_PASSWORD'), .5, 2)
    try:
        with pytest.raises(RuntimeError, match='rejected'):
            camera.move((1, 2, 3))
    finally:
        camera.close()


def test_example_config():
    settings = Settings.load('config.example.toml')
    assert len(settings.cameras) == 4
    assert settings.joystick['camera_buttons'] == [0, 1, 2, 3]


def test_missing_credential(monkeypatch):
    monkeypatch.delenv('TEST_CAMERA_PASSWORD', raising=False)
    with pytest.raises(ValueError, match='TEST_CAMERA_PASSWORD'):
        Camera(dict(name='test', password_env='TEST_CAMERA_PASSWORD'), .5, 2)


@pytest.mark.parametrize('old,new', [('deadzone = 0.18', 'deadzone = 1.0'), ('camera_buttons = [0, 1, 2, 3]', 'camera_buttons = [0, 0, 2, 3]'), ('pan_axis = 0', 'pan_axis = -1'), ('continuous_timeout = 2', 'continuous_timeout = 0')])
def test_invalid_config(tmp_path, old, new):
    from pathlib import Path
    config = tmp_path / 'config.toml'
    config.write_text(Path('config.example.toml').read_text().replace(old, new))
    with pytest.raises(ValueError):
        Settings.load(str(config))


@pytest.mark.parametrize('index', range(4))
def test_each_camera_button_selects_corresponding_camera(index):
    settings = Settings.load('config.example.toml')
    cameras = [Mock() for _ in range(4)]
    controller = Controller(cameras)
    button = settings.joystick['camera_buttons'][index]
    controller.select(settings.joystick['camera_buttons'].index(button))
    assert controller.selected == index


def test_axis_mapping_and_inversion():
    joystick = Mock()
    joystick.get_axis.side_effect = lambda index: {0: 1., 1: .59, 2: -1.}[index]
    config = Settings.load('config.example.toml').joystick
    assert read_motion(joystick, config) == (70, -35, -50)


@pytest.mark.parametrize('axes,expected', [
    ((-.929, .004, .004), (-64, 0, 0)),  # Left
    ((.976, .004, .122), (68, 0, 0)),    # Right with incidental twist
    ((.153, -.961, .027), (0, 67, 0)),  # Up with incidental pan
    ((.129, .906, .004), (0, -62, 0)),  # Down with incidental pan
    ((.004, .004, -.922), (0, 0, -45)), # Twist left
    ((.004, .004, 1.), (0, 0, 50)),     # Twist right
    ((.004, .004, .004), STOP),          # Released
])
def test_reported_t8311_samples(axes, expected):
    joystick = Mock()
    joystick.get_axis.side_effect = lambda index: axes[index]
    config = Settings.load('config.example.toml').joystick
    assert read_motion(joystick, config) == expected


@pytest.fixture
def camera_environment(monkeypatch):
    for i in range(1, 5):
        monkeypatch.setenv(f'AXIS_J{i}_URL', f'https://192.168.10.{i}')
        monkeypatch.setenv(f'AXIS_J{i}_USERNAME', f'operator{i}')
        monkeypatch.setenv(f'AXIS_J{i}_PASSWORD', 'local-test-only')


def test_environment_only_configuration(camera_environment):
    settings = Settings.load()
    assert settings.joystick['camera_buttons'] == [0, 1, 2, 3]
    assert settings.joystick['invert_tilt'] is True
    assert settings.cameras[3]['url'] == 'https://192.168.10.4'
    assert settings.cameras[0]['username'] == 'operator1'
    assert settings.cameras[0]['password_env'] == 'AXIS_J1_PASSWORD'


def test_environment_overrides_toml(camera_environment, monkeypatch):
    monkeypatch.setenv('AXIS_CAMERA_BUTTONS', '3,2,1,0')
    monkeypatch.setenv('AXIS_INVERT_ZOOM', 'true')
    monkeypatch.setenv('AXIS_MAX_PAN', '30')
    monkeypatch.setenv('AXIS_RATE_HZ', '5')
    monkeypatch.setenv('AXIS_J2_CHANNEL', '2')
    monkeypatch.setenv('AXIS_J2_CA_BUNDLE', '/certs/camera.pem')
    settings = Settings.load('config.example.toml')
    assert settings.joystick['camera_buttons'] == [3, 2, 1, 0]
    assert settings.joystick['invert_zoom'] is True
    assert settings.joystick['max_pan'] == 30
    assert settings.rate == 5
    assert settings.cameras[1]['channel'] == 2
    assert settings.cameras[1]['ca_bundle'] == '/certs/camera.pem'


@pytest.mark.parametrize('key,value', [('AXIS_INVERT_TILT', 'maybe'), ('AXIS_DEVICE', '-1'), ('AXIS_DEADZONE', '1'), ('AXIS_CAMERA_BUTTONS', '0,0,1,2'), ('AXIS_RATE_HZ', '0')])
def test_invalid_environment(camera_environment, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        Settings.load()


def test_dotenv_preserves_existing_environment(tmp_path, monkeypatch):
    from dotenv import load_dotenv
    path = tmp_path / '.env'
    path.write_text("AXIS_MAX_PAN=25\nAXIS_J1_PASSWORD='value with # and $'\n")
    monkeypatch.setenv('AXIS_MAX_PAN', '40')
    monkeypatch.delenv('AXIS_J1_PASSWORD', raising=False)
    load_dotenv(path, override=False)
    import os
    assert os.environ['AXIS_MAX_PAN'] == '40'
    assert os.environ['AXIS_J1_PASSWORD'] == 'value with # and $'


def test_no_argument_launch_loads_dotenv(tmp_path, monkeypatch):
    import axispy
    import os
    import sys
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv('AXISPY_ENV_FILE', raising=False)
    monkeypatch.delenv('AXISPY_CONFIG', raising=False)
    monkeypatch.delenv('AXIS_J1_URL', raising=False)
    (tmp_path / '.env').write_text('AXIS_J1_URL=https://192.168.20.1\n')
    monkeypatch.setattr(sys, 'argv', ['axispy'])
    observed = []
    monkeypatch.setattr(axispy, 'run', lambda args: observed.append((args.config, os.environ['AXIS_J1_URL'])))
    assert axispy.main() == 0
    assert observed == [(None, 'https://192.168.20.1')]


def test_executable_loads_configuration_beside_binary(tmp_path, monkeypatch):
    import axispy
    import sys
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(tmp_path / 'axispy'))
    monkeypatch.setattr(sys, 'argv', ['axispy'])
    monkeypatch.delenv('AXISPY_CONFIG', raising=False)
    monkeypatch.delenv('AXISPY_ENV_FILE', raising=False)
    (tmp_path / 'config.toml').write_text('')
    observed = []
    monkeypatch.setattr(axispy, 'run', lambda args: observed.append(args.config))
    assert axispy.main() == 0
    assert observed == [str(tmp_path / 'config.toml')]



def test_offline_camera_retries_at_interval_and_recovers_safely():
    now = [0.]
    cameras = [Mock() for _ in range(4)]
    cameras[0].move.side_effect = [requests.ConnectionError(), None, None, None, None]
    controller = Controller(cameras, clock=lambda: now[0])
    controller.send(STOP)
    for _ in range(100):
        controller.send((50, 0, 0))
    assert cameras[0].move.call_count == 1
    now[0] = 5
    controller.send((50, 0, 0))
    cameras[0].move.assert_called_with(STOP)
    assert not controller.armed
    controller.send((50, 0, 0))
    cameras[0].move.assert_called_with(STOP)
    controller.send(STOP)
    controller.send((50, 0, 0))
    cameras[0].move.assert_called_with((50, 0, 0))


def test_offline_idle_camera_does_not_block_other_cameras():
    cameras = [Mock() for _ in range(4)]
    cameras[0].move.side_effect = requests.Timeout()
    controller = Controller(cameras)
    controller.send(STOP)
    controller.select(1)
    assert controller.selected == 1
    controller.send(STOP)
    controller.send((0, 25, 0))
    cameras[1].move.assert_called_with((0, 25, 0))


def test_failed_movement_response_requires_stop_before_any_more_motion():
    now = [0.]
    cameras = [Mock() for _ in range(4)]
    controller = Controller(cameras, clock=lambda: now[0])
    controller.send(STOP)
    controller.send(STOP)
    cameras[0].move.side_effect = requests.Timeout()
    controller.send((30, 0, 0))
    assert 0 in controller.may_be_moving
    assert not controller.armed
    controller.select(1)
    assert controller.selected == 0
    cameras[0].move.side_effect = None
    now[0] = 5
    controller.send((30, 0, 0))
    cameras[0].move.assert_called_with(STOP)
    assert 0 not in controller.may_be_moving
    assert not controller.armed
    controller.select(1)
    assert controller.selected == 1


def test_missing_joystick_stop_retry_does_not_arm_or_spam(caplog):
    now = [0.]
    camera = Mock()
    camera.move.side_effect = requests.Timeout()
    controller = Controller([camera], clock=lambda: now[0])
    for _ in range(100):
        controller.maintain_stop()
    assert camera.move.call_count == 1
    assert len(caplog.records) == 1
    now[0] = 5
    camera.move.side_effect = None
    controller.maintain_stop()
    assert camera.move.call_count == 2
    assert not controller.armed
    controller.maintain_stop()
    assert camera.move.call_count == 2


@pytest.mark.parametrize('disconnect_via_read', [False, True])
def test_usb_wait_connect_disconnect_and_reconnect(camera_environment, monkeypatch, caplog, disconnect_via_read):
    import axispy
    import signal
    import sys
    from types import SimpleNamespace
    clock = [0.]
    frame = [0]
    fake = Mock()
    fake.error = type('SDLError', (Exception,), {})
    fake.JOYDEVICEREMOVED = 1
    fake.JOYBUTTONDOWN = 2
    fake.JOYAXISMOTION = 3
    device = Mock()
    device.get_numaxes.return_value = 3
    device.get_numbuttons.return_value = 6
    device.get_instance_id.return_value = 42
    def axis_value(_):
        if disconnect_via_read and frame[0] == 6:
            raise fake.error('Device disconnected')
        return 0. if frame[0] <= 4 else .8
    device.get_axis.side_effect = axis_value
    fake.joystick.Joystick.return_value = device
    fake.joystick.get_count.side_effect = lambda: int(clock[0] >= 2)
    handlers = {}
    monkeypatch.setattr(signal, 'signal', lambda sig, handler: handlers.setdefault(sig, handler))
    def events():
        frame[0] += 1
        if frame[0] == 4:
            return [SimpleNamespace(type=999)]  # Unrelated SDL events have no instance ID.
        if frame[0] == 6 and not disconnect_via_read:
            return [SimpleNamespace(type=1, instance_id=42)]
        if frame[0] == 10:
            handlers[signal.SIGTERM]()
        return []
    fake.event.get.side_effect = events
    monkeypatch.setitem(sys.modules, 'pygame', fake)
    monkeypatch.setattr(axispy.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(axispy.time, 'sleep', lambda _: clock.__setitem__(0, clock[0] + 1))
    cameras = [Mock() for _ in range(4)]
    monkeypatch.setattr(axispy, 'Camera', Mock(side_effect=cameras))
    axispy.run(SimpleNamespace(list=False, inspect=False, config=None, device=0))
    assert fake.joystick.Joystick.call_count == 2
    assert device.quit.call_count == 1
    assert 'waiting for USB' in caplog.text
    for camera in cameras:
        camera.close.assert_called_once()
    sent = [call.args[0] for call in cameras[0].move.call_args_list]
    moving = [i for i, motion in enumerate(sent) if motion != STOP]
    assert len(moving) == 1  # Movement before unplug; held stick must not resume afterward.
    assert all(motion == STOP for motion in sent[moving[0] + 1:])


def test_camera_outage_logged_without_request_credentials(caplog):
    camera = Mock()
    camera.move.side_effect = requests.HTTPError('do not log secret URL', response=SimpleResponse(401))
    controller = Controller([camera])
    controller.send(STOP)
    assert 'HTTP 401' in caplog.text
    assert 'secret URL' not in caplog.text


class SimpleResponse:
    def __init__(self, status_code):
        self.status_code = status_code


@pytest.mark.parametrize('status,body,location,expected', [
    (301, b'', 'https://camera.example/axis-cgi/com/ptz.cgi', 'redirect to HTTPS'),
    (302, b'', '/login', 'check camera URL'),
    (204, b'', None, 'Unexpected HTTP 204'),
    (200, b'Error: unknown parameter continuoustimeout', None, 'unknown parameter continuoustimeout'),
    (200, b'<html>Login</html>', None, 'HTML page'),
])
def test_camera_response_reason_reaches_logs(http_camera, monkeypatch, caplog, status, body, location, expected):
    url, received, handler = http_camera
    handler.status, handler.body, handler.location = status, body, location
    monkeypatch.setenv('TEST_CAMERA_PASSWORD', 'local-test-only')
    camera = Camera(dict(name='J1', url=url, username='operator', password_env='TEST_CAMERA_PASSWORD'), .5, 2)
    try:
        controller = Controller([camera])
        controller.send(STOP)
        assert expected in caplog.text
        assert not controller.armed
        assert len(received) == 1  # Redirects must not silently change transport.
    finally:
        camera.close()


def test_camera_error_redacts_password_and_control_characters(http_camera, monkeypatch, caplog):
    url, _, handler = http_camera
    password = 'private-test-password'
    handler.body = f'Error: {password}\n\x1b[31m unavailable'.encode()
    monkeypatch.setenv('TEST_CAMERA_PASSWORD', password)
    camera = Camera(dict(name='J1', url=url, username='operator', password_env='TEST_CAMERA_PASSWORD'), .5, 2)
    try:
        Controller([camera]).send(STOP)
        assert password not in caplog.text
        assert '[redacted]' in caplog.text
        assert '\x1b' not in caplog.text
    finally:
        camera.close()
