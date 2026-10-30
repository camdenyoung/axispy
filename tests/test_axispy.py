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
    joystick.get_button.return_value = False
    assert read_motion(joystick, {'deadman_button': 4}) == STOP
    joystick.get_axis.assert_not_called()


def test_camera_switch_stops_old_and_requires_neutral():
    cameras = [Mock() for _ in range(4)]
    controller = Controller(cameras)
    controller.send(STOP)
    controller.send(STOP)
    controller.send((50, 0, 0, 0))
    cameras[0].move.assert_called_with((50, 0, 0, 0))
    controller.select(3)
    cameras[0].move.assert_called_with(STOP)
    controller.send((50, 0, 0, 0))
    cameras[3].move.assert_called_with(STOP)
    controller.send(STOP)
    controller.send((0, -50, 20, 0))
    cameras[3].move.assert_called_with((0, -50, 20, 0))


def test_failed_stop_prevents_selection_when_motion_is_uncertain():
    cameras = [Mock() for _ in range(4)]
    controller = Controller(cameras)
    controller.send(STOP)
    controller.send(STOP)
    controller.send((50, 0, 0, 0))
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
        focus_delay = 0
        def do_GET(self):
            received.append(urlsplit(self.path))
            focus = parse_qs(urlsplit(self.path).query).get('continuousfocusmove', ['0'])[0]
            if self.focus_delay and focus != '0':
                import time
                time.sleep(self.focus_delay)
            self.send_response(self.status)
            if self.location:
                self.send_header("Location", self.location)
            self.end_headers()
            try:
                self.wfile.write(self.body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # A timeout test deliberately closes a delayed response.
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
        camera.move((25, -30, 50, 0))
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
            camera.move((1, 2, 3, 0))
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
    joystick.get_button.return_value = False
    joystick.get_axis.side_effect = lambda index: {0: 1., 1: .59, 2: -1.}[index]
    config = Settings.load('config.example.toml').joystick
    assert read_motion(joystick, config) == (70, -35, -50, 0)


@pytest.mark.parametrize('axes,expected', [
    ((-.929, .004, .004), (-64, 0, 0, 0)),  # Left
    ((.976, .004, .122), (68, 0, 0, 0)),    # Right with incidental twist
    ((.153, -.961, .027), (0, 67, 0, 0)),  # Up with incidental pan
    ((.129, .906, .004), (0, -62, 0, 0)),  # Down with incidental pan
    ((.004, .004, -.922), (0, 0, -45, 0)), # Twist left
    ((.004, .004, 1.), (0, 0, 50, 0)),     # Twist right
    ((.004, .004, .004), STOP),          # Released
])
def test_reported_t8311_samples(axes, expected):
    joystick = Mock()
    joystick.get_button.return_value = False
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
        controller.send((50, 0, 0, 0))
    assert cameras[0].move.call_count == 1
    now[0] = 5
    controller.send((50, 0, 0, 0))
    cameras[0].move.assert_called_with(STOP)
    assert not controller.armed
    controller.send((50, 0, 0, 0))
    cameras[0].move.assert_called_with(STOP)
    controller.send(STOP)
    controller.send((50, 0, 0, 0))
    cameras[0].move.assert_called_with((50, 0, 0, 0))


def test_offline_idle_camera_does_not_block_other_cameras():
    cameras = [Mock() for _ in range(4)]
    cameras[0].move.side_effect = requests.Timeout()
    controller = Controller(cameras)
    controller.send(STOP)
    controller.select(1)
    assert controller.selected == 1
    controller.send(STOP)
    controller.send((0, 25, 0, 0))
    cameras[1].move.assert_called_with((0, 25, 0, 0))


def test_failed_movement_response_requires_stop_before_any_more_motion():
    now = [0.]
    cameras = [Mock() for _ in range(4)]
    controller = Controller(cameras, clock=lambda: now[0])
    controller.send(STOP)
    controller.send(STOP)
    cameras[0].move.side_effect = requests.Timeout()
    controller.send((30, 0, 0, 0))
    assert 0 in controller.may_be_moving
    assert not controller.armed
    controller.select(1)
    assert controller.selected == 0
    cameras[0].move.side_effect = None
    now[0] = 5
    controller.send((30, 0, 0, 0))
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
    device.get_button.return_value = False
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
    (202, b'', None, 'Unexpected HTTP 202'),
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


def test_http_204_accepts_motion_stop_and_camera_switch(http_camera, monkeypatch, caplog):
    url, received, handler = http_camera
    handler.status = 204
    monkeypatch.setenv('TEST_CAMERA_PASSWORD', 'local-test-only')
    cameras = [Camera(dict(name=f'J{i}', url=url, username='operator', password_env='TEST_CAMERA_PASSWORD', channel=i), .5, 2) for i in (1, 2)]
    try:
        controller = Controller(cameras)
        controller.send(STOP)  # Initial stop handshake
        controller.send(STOP)  # Center to arm
        controller.send((25, -30, 50, 0))
        assert controller.armed
        assert controller.unavailable == set()
        assert parse_qs(received[-1].query)['continuouspantiltmove'] == ['25,-30']
        controller.select(1)
        assert controller.selected == 1
        assert not controller.armed
        assert parse_qs(received[-2].query)['camera'] == ['1']
        assert parse_qs(received[-1].query)['camera'] == ['2']
        assert all(parse_qs(request.query)['continuouspantiltmove'] == ['0,0'] for request in received[-2:])
        controller.stop()
        assert 'unavailable' not in caplog.text
    finally:
        for camera in cameras:
            camera.close()


@pytest.mark.parametrize('buttons,expected', [({4}, -30), ({5}, 30), (set(), 0), ({4, 5}, 0)])
def test_focus_buttons(buttons, expected):
    joystick = Mock()
    joystick.get_axis.return_value = 0.
    joystick.get_button.side_effect = lambda button: button in buttons
    config = Settings.load('config.example.toml').joystick
    assert read_motion(joystick, config) == (0, 0, 0, expected)


def test_focus_http_requests_and_release_while_panning(http_camera, monkeypatch):
    url, received, handler = http_camera
    handler.status = 204
    monkeypatch.setenv('TEST_CAMERA_PASSWORD', 'local-test-only')
    camera = Camera(dict(name='J1', url=url, username='operator', password_env='TEST_CAMERA_PASSWORD'), .5, 2)
    try:
        camera.move((20, 0, 0, -30))
        camera.move((20, 0, 0, 0))
        camera.move((20, 0, 0, 30))
        camera.move(STOP)
        camera.move(STOP)
        params = [parse_qs(request.query) for request in received]
        assert params[0]['continuousfocusmove'] == ['-30']
        assert params[0]['autofocus'] == ['off']
        assert params[1]['continuousfocusmove'] == ['0']
        assert params[1]['continuouspantiltmove'] == ['20,0']
        assert 'autofocus' not in params[1]
        assert params[2]['continuousfocusmove'] == ['30']
        assert params[3]['continuousfocusmove'] == ['0']
        assert 'continuousfocusmove' not in params[4]
    finally:
        camera.close()


def test_failed_focus_request_preserves_need_for_stop(http_camera, monkeypatch):
    url, received, handler = http_camera
    monkeypatch.setenv('TEST_CAMERA_PASSWORD', 'local-test-only')
    camera = Camera(dict(name='J1', url=url, username='operator', password_env='TEST_CAMERA_PASSWORD'), .5, 2)
    handler.body = b'Error: temporarily unavailable'
    try:
        with pytest.raises(RuntimeError):
            camera.move((0, 0, 0, -30))
        assert camera.focus_active
        handler.body = b''
        camera.move(STOP)
        assert parse_qs(received[-1].query)['continuousfocusmove'] == ['0']
        assert not camera.focus_active
    finally:
        camera.close()


def test_switch_stops_focus_and_requires_button_release():
    cameras = [Mock() for _ in range(4)]
    controller = Controller(cameras)
    controller.send(STOP)
    controller.send(STOP)
    controller.send((0, 0, 0, -30))
    controller.select(1)
    cameras[0].move.assert_called_with(STOP)
    controller.send((0, 0, 0, -30))
    cameras[1].move.assert_called_with(STOP)
    controller.send(STOP)
    controller.send((0, 0, 0, 30))
    cameras[1].move.assert_called_with((0, 0, 0, 30))
    controller.stop()
    cameras[1].move.assert_called_with(STOP)


@pytest.mark.parametrize('key,value', [('AXIS_FOCUS_NEAR_BUTTON', '-2'), ('AXIS_FOCUS_FAR_BUTTON', '4'), ('AXIS_FOCUS_SPEED', '0'), ('AXIS_FOCUS_SPEED', '101'), ('AXIS_DEADMAN_BUTTON', '4')])
def test_invalid_focus_settings(camera_environment, monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        Settings.load()


def test_focus_settings_from_environment(camera_environment, monkeypatch):
    monkeypatch.setenv('AXIS_FOCUS_NEAR_BUTTON', '5')
    monkeypatch.setenv('AXIS_FOCUS_FAR_BUTTON', '4')
    monkeypatch.setenv('AXIS_FOCUS_SPEED', '20')
    settings = Settings.load()
    assert settings.joystick['focus_near_button'] == 5
    assert settings.joystick['focus_far_button'] == 4
    assert settings.joystick['focus_speed'] == 20


def test_focus_release_bypasses_rate_limit_while_panning(camera_environment, monkeypatch):
    import axispy
    import signal
    import sys
    from types import SimpleNamespace
    clock, frame = [0.], [0]
    fake = Mock()
    fake.error = type('SDLError', (Exception,), {})
    fake.JOYDEVICEREMOVED, fake.JOYBUTTONDOWN, fake.JOYAXISMOTION = 1, 2, 3
    device = Mock()
    device.get_numaxes.return_value = 3
    device.get_numbuttons.return_value = 6
    device.get_axis.side_effect = lambda _: 0. if frame[0] <= 6 else .8
    device.get_button.side_effect = lambda button: frame[0] == 8 and button == 4
    fake.joystick.get_count.return_value = 1
    fake.joystick.Joystick.return_value = device
    handlers = {}
    monkeypatch.setattr(signal, 'signal', lambda sig, handler: handlers.setdefault(sig, handler))
    def events():
        frame[0] += 1
        if frame[0] == 10:
            handlers[signal.SIGTERM]()
        return []
    fake.event.get.side_effect = events
    monkeypatch.setitem(sys.modules, 'pygame', fake)
    monkeypatch.setattr(axispy.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(axispy.time, 'sleep', lambda _: clock.__setitem__(0, clock[0] + .02))
    cameras = [Mock() for _ in range(4)]
    monkeypatch.setattr(axispy, 'Camera', Mock(side_effect=cameras))
    axispy.run(SimpleNamespace(list=False, inspect=False, config=None, device=0))
    sent = [call.args[0] for call in cameras[0].move.call_args_list]
    focus_index = next(i for i, motion in enumerate(sent) if motion[3] == -30)
    assert sent[focus_index][0] != 0
    assert sent[focus_index + 1] == (*sent[focus_index][:3], 0)


def test_holding_both_focus_buttons_does_not_rearm_after_reconnect():
    cameras = [Mock() for _ in range(4)]
    controller = Controller(cameras)
    controller.send(STOP, neutral=False)
    controller.send(STOP, neutral=False)
    controller.send((0, 0, 0, -30))
    cameras[0].move.assert_called_with(STOP)
    assert not controller.armed
    controller.send(STOP, neutral=True)
    controller.send((0, 0, 0, -30))
    cameras[0].move.assert_called_with((0, 0, 0, -30))


@pytest.mark.parametrize('index', range(4))
def test_camera_button_five_second_hold_once_per_press(index):
    from axispy import CameraButtonHolds
    holds = CameraButtonHolds()
    assert holds.update({index}, 10) == []
    assert holds.update({index}, 14.999) == []
    assert holds.update({index}, 15) == [index]
    assert holds.update({index}, 50) == []
    assert holds.update(set(), 51) == []
    assert holds.update({index}, 52) == []
    assert holds.update({index}, 57) == [index]


def test_short_presses_and_usb_reconnect_do_not_enable_autofocus():
    from axispy import CameraButtonHolds
    holds = CameraButtonHolds()
    holds.update({0}, 0)
    holds.update(set(), 4)
    assert holds.update({0}, 6) == []
    assert holds.update({0}, 10) == []
    holds.reset({0})
    assert holds.update({0}, 100) == []
    holds.update(set(), 101)
    holds.update({0}, 102)
    assert holds.update({0}, 107) == [0]


def test_release_event_resets_hold_even_if_button_is_pressed_again_before_poll():
    from axispy import CameraButtonHolds
    holds = CameraButtonHolds()
    holds.update({1}, 0)
    holds.release(1)
    assert holds.update({1}, 4) == []
    assert holds.update({1}, 5) == []
    assert holds.update({1}, 9) == [1]


@pytest.mark.parametrize('status', [200, 204])
def test_autofocus_http_stops_manual_focus_before_enabling(http_camera, monkeypatch, status):
    url, received, handler = http_camera
    handler.status = status
    monkeypatch.setenv('TEST_CAMERA_PASSWORD', 'local-test-only')
    camera = Camera(dict(name='J3', url=url, username='operator', password_env='TEST_CAMERA_PASSWORD', channel=3), .5, 2)
    try:
        camera.move((20, 0, 0, -30))
        camera.enable_autofocus()
        params = [parse_qs(request.query) for request in received]
        assert params[-2] == {'camera': ['3'], 'continuousfocusmove': ['0']}
        assert params[-1] == {'camera': ['3'], 'autofocus': ['on']}
        assert not camera.focus_active
        camera.move((21, 0, 0, 0))  # Changed PTZ must preserve autofocus.
        assert 'autofocus' not in parse_qs(received[-1].query)
    finally:
        camera.close()


def test_autofocus_stop_failure_does_not_send_enable(http_camera, monkeypatch):
    url, received, handler = http_camera
    monkeypatch.setenv('TEST_CAMERA_PASSWORD', 'local-test-only')
    camera = Camera(dict(name='J1', url=url, username='operator', password_env='TEST_CAMERA_PASSWORD'), .5, 2)
    try:
        camera.move((0, 0, 0, -30))
        handler.body = b'Error: focus busy'
        with pytest.raises(RuntimeError):
            camera.enable_autofocus()
        assert camera.focus_active
        assert 'autofocus' not in parse_qs(received[-1].query)
    finally:
        camera.close()


@pytest.mark.parametrize('index', range(4))
def test_autofocus_targets_the_button_camera_and_logs_success(index, caplog):
    import logging
    cameras = [Mock() for _ in range(4)]
    for i, camera in enumerate(cameras):
        camera.name = f'J{i+1}'
    controller = Controller(cameras)
    with caplog.at_level(logging.INFO):
        assert controller.enable_autofocus(index)
    for i, camera in enumerate(cameras):
        assert camera.enable_autofocus.call_count == int(i == index)
        camera.move.assert_not_called()
    assert controller.selected == 0
    assert f'Autofocus enabled for J{index+1}' in caplog.text


def test_autofocus_outage_uses_backoff_and_does_not_claim_success(caplog):
    camera = Mock()
    camera.name = 'J1'
    camera.enable_autofocus.side_effect = requests.Timeout()
    controller = Controller([camera])
    assert not controller.enable_autofocus(0)
    assert not controller.enable_autofocus(0)
    assert camera.enable_autofocus.call_count == 1
    assert 0 in controller.stop_required
    assert 'Autofocus enabled' not in caplog.text


@pytest.mark.parametrize('index', range(4))
@pytest.mark.parametrize('hold_seconds,autofocus_frame', [(None, 7), ('7', 9), ('2.5', 5)])
def test_runtime_camera_hold_enables_autofocus_once_and_suppresses_held_manual_focus(camera_environment, monkeypatch, index, hold_seconds, autofocus_frame):
    import axispy
    import signal
    import sys
    from types import SimpleNamespace
    if hold_seconds is None:
        monkeypatch.delenv('AXIS_AUTOFOCUS_HOLD_SECONDS', raising=False)
    else:
        monkeypatch.setenv('AXIS_AUTOFOCUS_HOLD_SECONDS', hold_seconds)
    clock, frame = [0.], [0]
    fake = Mock()
    fake.error = type('SDLError', (Exception,), {})
    fake.JOYDEVICEREMOVED, fake.JOYBUTTONDOWN, fake.JOYAXISMOTION, fake.JOYBUTTONUP = 1, 2, 3, 4
    device = Mock()
    device.get_numaxes.return_value = 3
    device.get_numbuttons.return_value = 6
    device.get_instance_id.return_value = 42
    device.get_axis.return_value = 0.
    def pressed(button):
        if button == index:
            return 2 <= frame[0] <= 9
        return button == 4 and (4 <= frame[0] <= 9 or frame[0] == 11)
    device.get_button.side_effect = pressed
    fake.joystick.get_count.return_value = 1
    fake.joystick.Joystick.return_value = device
    handlers = {}
    monkeypatch.setattr(signal, 'signal', lambda sig, handler: handlers.setdefault(sig, handler))
    def events():
        frame[0] += 1
        if frame[0] == 2:
            return [SimpleNamespace(type=2, instance_id=42, button=index)]
        if frame[0] == 10:
            return [SimpleNamespace(type=4, instance_id=42, button=index)]
        if frame[0] == 12:
            handlers[signal.SIGTERM]()
        return []
    fake.event.get.side_effect = events
    monkeypatch.setitem(sys.modules, 'pygame', fake)
    monkeypatch.setattr(axispy.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(axispy.time, 'sleep', lambda _: clock.__setitem__(0, clock[0] + 1))
    cameras = [Mock() for _ in range(4)]
    sent = []
    cameras[index].move.side_effect = lambda motion: sent.append((frame[0], motion))
    autofocus_frames = []
    cameras[index].enable_autofocus.side_effect = lambda: autofocus_frames.append(frame[0])
    monkeypatch.setattr(axispy, 'Camera', Mock(side_effect=cameras))
    axispy.run(SimpleNamespace(list=False, inspect=False, config=None, device=0))
    assert autofocus_frames == [autofocus_frame]
    assert any(moment < autofocus_frame and motion[3] == -30 for moment, motion in sent)
    assert all(motion[3] == 0 for moment, motion in sent if autofocus_frame <= moment <= 10)
    assert any(moment == 11 and motion[3] == -30 for moment, motion in sent)
    for i, camera in enumerate(cameras):
        if i != index:
            camera.enable_autofocus.assert_not_called()



@pytest.mark.parametrize('direction', [-30, 30])
def test_held_focus_coalesces_requests_and_disables_autofocus_only_once(http_camera, monkeypatch, direction):
    url, received, _ = http_camera
    monkeypatch.setenv('TEST_CAMERA_PASSWORD', 'local-test-only')
    camera = Camera(dict(name='J1', url=url, username='operator', password_env='TEST_CAMERA_PASSWORD'), .5, 2)
    now = [0.]
    camera.clock = lambda: now[0]
    try:
        camera.move((0, 0, 0, direction))
        for tick in range(1, 10):
            now[0] = tick / 10
            camera.move((0, 0, 0, direction))
        params = [parse_qs(request.query) for request in received]
        assert len(params) == 2  # Initial command, then one watchdog refresh at 0.5s.
        assert params[0]['autofocus'] == ['off']
        assert 'autofocus' not in params[1]
        assert all(item['continuousfocusmove'] == [str(direction)] for item in params)
        now[0] = .91
        camera.move((0, 0, 0, 0))
        assert parse_qs(received[-1].query)['continuousfocusmove'] == ['0']
        camera.move((0, 0, 0, direction))
        assert parse_qs(received[-1].query)['autofocus'] == ['off']  # A fresh press.
    finally:
        camera.close()


def test_changed_ptz_does_not_repeat_focus_or_autofocus_writes(http_camera, monkeypatch):
    url, received, _ = http_camera
    monkeypatch.setenv('TEST_CAMERA_PASSWORD', 'local-test-only')
    camera = Camera(dict(name='J1', url=url, username='operator', password_env='TEST_CAMERA_PASSWORD'), .5, 2)
    now = [0.]
    camera.clock = lambda: now[0]
    try:
        camera.move((0, 0, 0, 30))
        now[0] = .1
        camera.move((20, 0, 0, 30))
        params = parse_qs(received[-1].query)
        assert params['continuouspantiltmove'] == ['20,0']
        assert 'continuousfocusmove' not in params
        assert 'autofocus' not in params
        now[0] = .2
        camera.move((20, 0, 0, -30))  # Reversal bypasses the refresh interval.
        assert parse_qs(received[-1].query)['continuousfocusmove'] == ['-30']
    finally:
        camera.close()


def test_slow_focus_response_uses_longer_read_timeout(http_camera, monkeypatch):
    url, received, handler = http_camera
    handler.focus_delay = .65
    monkeypatch.setenv('TEST_CAMERA_PASSWORD', 'local-test-only')
    camera = Camera(dict(name='J1', url=url, username='operator', password_env='TEST_CAMERA_PASSWORD'), .1, 2)
    try:
        controller = Controller([camera])
        controller.send(STOP)
        controller.send(STOP)
        controller.send((0, 0, 0, 30))
        assert controller.unavailable == set()
        assert controller.armed
        assert parse_qs(received[-1].query)['continuousfocusmove'] == ['30']
        controller.send(STOP)
        assert parse_qs(received[-1].query)['continuousfocusmove'] == ['0']
    finally:
        camera.close()


def test_focus_read_timeout_attempts_stop_without_waiting_for_backoff(http_camera, monkeypatch):
    url, received, handler = http_camera
    handler.focus_delay = .3
    monkeypatch.setenv('TEST_CAMERA_PASSWORD', 'local-test-only')
    camera = Camera(dict(name='J1', url=url, username='operator', password_env='TEST_CAMERA_PASSWORD'), .1, 2, focus_timeout=.1)
    try:
        controller = Controller([camera])
        controller.send(STOP)
        controller.send(STOP)
        controller.send((0, 0, 0, 30))
        assert not controller.armed
        assert not controller.may_be_moving
        assert controller.unavailable == set()  # Immediate stop was acknowledged.
        assert parse_qs(received[-1].query)['continuousfocusmove'] == ['0']
        controller.send((0, 0, 0, 30))
        assert parse_qs(received[-1].query).get('continuousfocusmove') != ['30']
    finally:
        camera.close()


def test_failed_stop_after_read_timeout_retains_backoff():
    now = [0.]
    camera = Mock()
    controller = Controller([camera], clock=lambda: now[0])
    controller.send(STOP)
    controller.send(STOP)
    camera.move.side_effect = [requests.ReadTimeout(), requests.ConnectionError()]
    controller.send((0, 0, 0, 30))
    assert camera.move.call_args_list[-1].args == (STOP,)
    assert 0 in controller.stop_required
    assert 0 in controller.may_be_moving
    assert controller.retry_at[0] == 5
    assert not controller.armed


def test_focus_timeout_from_environment(camera_environment, monkeypatch):
    monkeypatch.setenv('AXIS_FOCUS_TIMEOUT', '3')
    assert Settings.load().focus_timeout == 3
    monkeypatch.setenv('AXIS_FOCUS_TIMEOUT', '0')
    with pytest.raises(ValueError, match='focus_timeout'):
        Settings.load()


def test_waiting_for_neutral_after_timeout_does_not_flood_stops(caplog):
    camera = Mock()
    controller = Controller([camera])
    controller.send(STOP)
    controller.send(STOP)
    camera.move.side_effect = [requests.ReadTimeout(), None]
    controller.send((0, 0, 0, 30))
    camera.move.reset_mock()
    for _ in range(100):
        controller.send((0, 0, 0, 30))
    camera.move.assert_not_called()
    assert 'stop confirmed' in caplog.text
    assert 'unavailable' not in caplog.text



@pytest.mark.parametrize('value,expected', [(None, 5), ('', 5), ('  ', 5), ('3', 3), ('2.5', 2.5)])
def test_autofocus_hold_duration_environment(camera_environment, monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv('AXIS_AUTOFOCUS_HOLD_SECONDS', raising=False)
    else:
        monkeypatch.setenv('AXIS_AUTOFOCUS_HOLD_SECONDS', value)
    settings = Settings.load()
    assert settings.autofocus_hold_seconds == expected


@pytest.mark.parametrize('value', ['0', '-1', 'nan', 'inf', '-inf', 'invalid'])
def test_invalid_autofocus_hold_duration(camera_environment, monkeypatch, value):
    monkeypatch.setenv('AXIS_AUTOFOCUS_HOLD_SECONDS', value)
    with pytest.raises(ValueError):
        Settings.load()


def test_autofocus_hold_environment_overrides_toml(camera_environment, monkeypatch):
    monkeypatch.setenv('AXIS_AUTOFOCUS_HOLD_SECONDS', '1.5')
    assert Settings.load('config.example.toml').autofocus_hold_seconds == 1.5


@pytest.mark.parametrize('duration', [.5, 2.5, 10])
def test_custom_hold_duration_threshold_and_repeat(duration):
    from axispy import CameraButtonHolds
    holds = CameraButtonHolds(duration)
    assert holds.update({0}, 0) == []
    assert holds.update({0}, duration - .001) == []
    assert holds.update({0}, duration) == [0]
    assert holds.update({0}, duration + 10) == []
    holds.release(0)
    assert holds.update({0}, 100) == []
    assert holds.update({0}, 100 + duration) == [0]


def test_persistent_file_logs_append_with_utc_timestamps(tmp_path, monkeypatch, caplog):
    import axispy
    path = tmp_path / 'logs' / 'axispy.log'
    monkeypatch.setenv('AXIS_LOG_FILE', str(path))
    for message in ('first run', 'second run'):
        handler = axispy.configure_file_logging()
        try:
            axispy.LOG.info(message)
        finally:
            axispy.LOG.removeHandler(handler)
            handler.close()
    text = path.read_text()
    assert 'Z INFO: first run' in text
    assert 'Z INFO: second run' in text
    assert 'second run' in caplog.text  # Console/root handlers still receive logs.


def test_file_log_rotation_is_bounded(tmp_path, monkeypatch):
    import axispy
    path = tmp_path / 'axispy.log'
    monkeypatch.setenv('AXIS_LOG_FILE', str(path))
    monkeypatch.setenv('AXIS_LOG_MAX_BYTES', '150')
    monkeypatch.setenv('AXIS_LOG_BACKUP_COUNT', '2')
    handler = axispy.configure_file_logging()
    try:
        for i in range(30):
            axispy.LOG.info('rotation sample %s %s', i, 'x' * 40)
    finally:
        axispy.LOG.removeHandler(handler)
        handler.close()
    assert {item.name for item in tmp_path.iterdir()} == {'axispy.log', 'axispy.log.1', 'axispy.log.2'}
    assert 'rotation sample 29' in path.read_text()


def test_file_logging_is_optional(monkeypatch):
    import axispy
    monkeypatch.delenv('AXIS_LOG_FILE', raising=False)
    assert axispy.configure_file_logging() is None


@pytest.mark.parametrize('key,value', [('AXIS_LOG_MAX_BYTES', '0'), ('AXIS_LOG_BACKUP_COUNT', '0'), ('AXIS_LOG_MAX_BYTES', 'invalid')])
def test_invalid_file_log_settings(tmp_path, monkeypatch, key, value):
    import axispy
    monkeypatch.setenv('AXIS_LOG_FILE', str(tmp_path / 'axispy.log'))
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        axispy.configure_file_logging()


def test_selecting_already_stopped_cameras_does_not_send_lens_commands(http_camera, monkeypatch):
    url, received, _ = http_camera
    monkeypatch.setenv('TEST_CAMERA_PASSWORD', 'local-test-only')
    cameras = [Camera(dict(name=f'J{i}', url=url, username='operator', password_env='TEST_CAMERA_PASSWORD', channel=i), .5, 2) for i in range(1, 5)]
    try:
        controller = Controller(cameras)
        # Establish a confirmed stop on each camera; unknown cameras retain a
        # safety stop on first contact, which cannot be skipped safely.
        controller.send(STOP)
        controller.send(STOP)
        for index in range(1, 4):
            controller.select(index)
            controller.send(STOP)
        count = len(received)
        for index in [0, 1, 2, 3, 0]:
            controller.select(index)
            assert not controller.armed
            controller.send((0, 0, 0, 30))  # Held control cannot arm the new target.
            controller.send(STOP)
            assert controller.armed
        assert len(received) == count
    finally:
        for camera in cameras:
            camera.close()


def test_selection_stops_only_moving_old_camera_and_leaves_other_lenses_alone():
    cameras = [Mock() for _ in range(4)]
    controller = Controller(cameras)
    controller.send(STOP)
    controller.send(STOP)
    controller.select(1)
    controller.send(STOP)
    controller.select(0)
    controller.send(STOP)
    controller.send((0, 0, 25, 0))
    for camera in cameras:
        camera.move.reset_mock()
    controller.select(1)
    cameras[0].move.assert_called_once_with(STOP)
    for camera in cameras[1:]:
        camera.move.assert_not_called()


def test_first_selection_of_unknown_camera_preserves_safety_stop():
    cameras = [Mock() for _ in range(4)]
    controller = Controller(cameras)
    controller.send(STOP)
    controller.select(2)
    cameras[2].move.assert_called_once_with(STOP)
    cameras[1].move.assert_not_called()
    cameras[3].move.assert_not_called()
    assert not controller.armed


def test_reselecting_idle_camera_does_not_repeat_stop_commands():
    camera = Mock()
    controller = Controller([camera])
    controller.send(STOP)
    controller.send(STOP)
    camera.move.reset_mock()
    for _ in range(10):
        controller.select(0)
        controller.send(STOP)
    camera.move.assert_not_called()
    assert controller.armed
    controller.stop()  # Explicit shutdown stop remains unconditional.
    camera.move.assert_called_once_with(STOP)


@pytest.mark.parametrize('index', [0, 1])
def test_runtime_reselect_button_does_not_send_idle_lens_request(camera_environment, monkeypatch, index):
    import axispy
    import signal
    import sys
    from types import SimpleNamespace
    clock, frame = [0.], [0]
    fake = Mock()
    fake.error = type('SDLError', (Exception,), {})
    fake.JOYDEVICEREMOVED, fake.JOYBUTTONDOWN, fake.JOYAXISMOTION, fake.JOYBUTTONUP = 1, 2, 3, 4
    device = Mock()
    device.get_numaxes.return_value = 3
    device.get_numbuttons.return_value = 6
    device.get_instance_id.return_value = 42
    device.get_axis.return_value = 0.
    device.get_button.side_effect = lambda button: button == index and frame[0] in (3, 5)
    fake.joystick.get_count.return_value = 1
    fake.joystick.Joystick.return_value = device
    handlers = {}
    monkeypatch.setattr(signal, 'signal', lambda sig, handler: handlers.setdefault(sig, handler))
    def events():
        frame[0] += 1
        if frame[0] in (3, 5):
            return [SimpleNamespace(type=2, instance_id=42, button=index)]
        if frame[0] in (4, 6):
            return [SimpleNamespace(type=4, instance_id=42, button=index)]
        if frame[0] == 8:
            handlers[signal.SIGTERM]()
        return []
    fake.event.get.side_effect = events
    monkeypatch.setitem(sys.modules, 'pygame', fake)
    monkeypatch.setattr(axispy.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(axispy.time, 'sleep', lambda _: clock.__setitem__(0, clock[0] + .2))
    cameras = [Mock() for _ in range(4)]
    sent = []
    for i, camera in enumerate(cameras):
        camera.move.side_effect = lambda motion, i=i: sent.append((frame[0], i, motion))
    monkeypatch.setattr(axispy, 'Camera', Mock(side_effect=cameras))
    axispy.run(SimpleNamespace(list=False, inspect=False, config=None, device=0))
    assert not any(moment == 5 for moment, _, _ in sent)
    if index == 0:
        assert not any(moment == 3 for moment, _, _ in sent)
    else:
        assert (3, 1, STOP) in sent  # First contact still performs the safety stop.
    for camera in cameras:
        camera.enable_autofocus.assert_not_called()


def test_per_camera_speed_limits_fall_back_to_global(camera_environment, monkeypatch):
    monkeypatch.setenv('AXIS_MAX_ZOOM', '50')
    monkeypatch.setenv('AXIS_J1_MAX_ZOOM', '30')
    monkeypatch.setenv('AXIS_J2_MAX_PAN', '40')
    monkeypatch.setenv('AXIS_J2_MAX_TILT', '45')
    settings = Settings.load()
    joystick = Mock()
    joystick.get_axis.return_value = 1.
    joystick.get_button.return_value = False
    assert read_motion(joystick, settings.motion_config(0)) == (70, -70, 30, 0)
    assert read_motion(joystick, settings.motion_config(1)) == (40, -45, 50, 0)
    assert read_motion(joystick, settings.motion_config(2)) == (70, -70, 50, 0)
    assert settings.joystick['max_zoom'] == 50
    assert settings.motion_config(0)['max_pan'] == 70  # No other-camera state leaked.


@pytest.mark.parametrize('value', ['-1', '101', '1.5', 'invalid'])
def test_invalid_per_camera_speed(camera_environment, monkeypatch, value):
    monkeypatch.setenv('AXIS_J3_MAX_ZOOM', value)
    with pytest.raises(ValueError):
        Settings.load()


@pytest.mark.parametrize('value', ['0', '100', '', ' '])
def test_per_camera_speed_bounds_and_blank_fallback(camera_environment, monkeypatch, value):
    monkeypatch.setenv('AXIS_J1_MAX_ZOOM', value)
    expected = int(value) if value.strip() else 50
    assert Settings.load().motion_config(0)['max_zoom'] == expected


def test_per_camera_toml_speed_and_env_precedence(tmp_path, monkeypatch):
    from pathlib import Path
    config = tmp_path / 'config.toml'
    content = Path('config.example.toml').read_text().replace('name = "J1"', 'name = "J1"\nmax_zoom = 25')
    config.write_text(content)
    assert Settings.load(str(config)).motion_config(0)['max_zoom'] == 25
    monkeypatch.setenv('AXIS_J1_MAX_ZOOM', '35')
    assert Settings.load(str(config)).motion_config(0)['max_zoom'] == 35


@pytest.mark.parametrize('enabled_slots', [(1, 2), (2, 4), (4,)])
def test_disabled_camera_slots(camera_environment, monkeypatch, enabled_slots):
    for slot in range(1, 5):
        monkeypatch.setenv(f'AXIS_J{slot}_ENABLED', str(slot in enabled_slots).lower())
        if slot not in enabled_slots:
            monkeypatch.delenv(f'AXIS_J{slot}_URL')
            monkeypatch.delenv(f'AXIS_J{slot}_PASSWORD')
    settings = Settings.load()
    assert [c['slot'] for c in settings.cameras] == list(enabled_slots)
    assert settings.joystick['camera_buttons'] == [slot - 1 for slot in enabled_slots]
    cameras = [Camera(c, .5, 2) for c in settings.cameras]
    try:
        controller = Controller(cameras)
        assert controller.cameras[controller.selected].name == f'J{enabled_slots[0]}'
    finally:
        for camera in cameras:
            camera.close()


@pytest.mark.parametrize('value', ['false', '0', ' FALSE '])
def test_disabled_camera_skips_unused_settings(camera_environment, monkeypatch, value):
    monkeypatch.setenv('AXIS_J3_ENABLED', value)
    monkeypatch.setenv('AXIS_J3_CHANNEL', 'unused')
    monkeypatch.setenv('AXIS_J3_MAX_ZOOM', 'unused')
    assert [c['slot'] for c in Settings.load().cameras] == [1, 2, 4]


@pytest.mark.parametrize('value', ['true', '1', '', ' TRUE '])
def test_camera_enable_defaults_and_values(camera_environment, monkeypatch, value):
    monkeypatch.setenv('AXIS_J3_ENABLED', value)
    assert len(Settings.load().cameras) == 4


def test_all_cameras_disabled(monkeypatch):
    for slot in range(1, 5):
        monkeypatch.setenv(f'AXIS_J{slot}_ENABLED', 'false')
    with pytest.raises(ValueError, match='At least one camera'):
        Settings.load()


def test_invalid_camera_enabled(camera_environment, monkeypatch):
    monkeypatch.setenv('AXIS_J2_ENABLED', 'maybe')
    with pytest.raises(ValueError, match='AXIS_J2_ENABLED'):
        Settings.load()


def test_disabled_camera_toml_and_environment_override(tmp_path, monkeypatch):
    from pathlib import Path
    path = tmp_path / 'config.toml'
    text = Path('config.example.toml').read_text()
    path.write_text(text.replace('[[cameras]]', '[[cameras]]\nenabled = false', 1))
    assert [c['slot'] for c in Settings.load(str(path)).cameras] == [2, 3, 4]
    monkeypatch.setenv('AXIS_J1_ENABLED', 'true')
    assert len(Settings.load(str(path)).cameras) == 4


def test_disabled_buttons_ignored_in_controller_loop(camera_environment, monkeypatch):
    import signal
    import sys
    from types import SimpleNamespace
    import axispy

    monkeypatch.setenv('AXIS_J1_ENABLED', 'false')
    monkeypatch.setenv('AXIS_J3_ENABLED', 'false')
    monkeypatch.setenv('AXIS_J4_MAX_ZOOM', '25')
    settings = Settings.load()
    assert settings.motion_config(1)['max_zoom'] == 25
    fake = Mock()
    fake.error = RuntimeError
    fake.JOYDEVICEREMOVED, fake.JOYBUTTONDOWN, fake.JOYBUTTONUP = 1, 2, 3
    fake.JOYAXISMOTION = 4
    device = Mock()
    device.get_numaxes.return_value = 3
    device.get_numbuttons.return_value = 6
    device.get_instance_id.return_value = 42
    device.get_axis.return_value = 0.
    frame, clock, handlers = [0], [0.], {}
    # Hold disabled J1/J3 for longer than the autofocus threshold.
    device.get_button.side_effect = lambda button: button in (0, 2)
    fake.joystick.get_count.return_value = 1
    fake.joystick.Joystick.return_value = device
    monkeypatch.setattr(signal, 'signal', lambda sig, handler: handlers.setdefault(sig, handler))
    def events():
        frame[0] += 1
        if frame[0] in (2, 3, 4):
            # J4 selects the second enabled camera; disabled J1/J3 must not switch it.
            return [SimpleNamespace(type=2, instance_id=42, button={2: 3, 3: 0, 4: 2}[frame[0]])]
        if frame[0] == 12:
            handlers[signal.SIGTERM]()
        return []
    fake.event.get.side_effect = events
    monkeypatch.setitem(sys.modules, 'pygame', fake)
    monkeypatch.setattr(axispy.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(axispy.time, 'sleep', lambda _: clock.__setitem__(0, clock[0] + 1))
    cameras = [Mock(name='J2'), Mock(name='J4')]
    factory = Mock(side_effect=cameras)
    monkeypatch.setattr(axispy, 'Camera', factory)
    controllers = []
    def controller_factory(cameras):
        controller = Controller(cameras)
        controllers.append(controller)
        return controller
    monkeypatch.setattr(axispy, 'Controller', controller_factory)
    axispy.run(SimpleNamespace(list=False, inspect=False, config=None, device=0))
    assert [call.args[0]['slot'] for call in factory.call_args_list] == [2, 4]
    assert controllers[0].selected == 1
    for camera in cameras:
        camera.enable_autofocus.assert_not_called()
        camera.close.assert_called_once()



@pytest.mark.parametrize('digest_retry', [False, True])
def test_slow_control_diagnostics_are_rate_limited_and_do_not_expose_credentials(monkeypatch, caplog, digest_retry):
    import axispy
    from datetime import timedelta
    clock = [0.]
    monkeypatch.setenv('TEST_CAMERA_PASSWORD', 'private-test-password')
    camera = Camera(dict(name='J2', url='https://camera.example', username='operator',
                         password_env='TEST_CAMERA_PASSWORD'), 2, 2)
    monkeypatch.setattr(axispy.time, 'monotonic', lambda: clock[0])
    response = requests.Response()
    response.status_code = 200
    response._content = b''
    response.headers['Content-Length'] = '0'
    response.elapsed = timedelta(seconds=.01)
    if digest_retry:
        challenge = requests.Response()
        challenge.status_code = 401
        response.history = [challenge]
    def get(*args, **kwargs):
        clock[0] += .4
        return response
    monkeypatch.setattr(camera.session, 'get', get)
    try:
        with caplog.at_level('WARNING', logger='axispy'):
            camera.move(STOP)
            camera.move(STOP)
            assert caplog.text.count('slow control stop: acknowledged') == 1
            clock[0] += 31
            camera.move(STOP)
        assert caplog.text.count('slow control stop: acknowledged') == 2
        assert ('final response headers unavailable (Digest retry)' if digest_retry else 'final response headers 0.01s') in caplog.text
        assert 'body framing Content-Length' in caplog.text
        assert 'private-test-password' not in caplog.text
        assert 'camera.example' not in caplog.text
    finally:
        camera.close()


def test_slow_control_failure_preserves_timeout_and_logs_without_raw_exception(monkeypatch, caplog):
    import axispy
    clock = [0.]
    monkeypatch.setenv('TEST_CAMERA_PASSWORD', 'private-test-password')
    camera = Camera(dict(name='J3', url='https://camera.example', username='operator',
                         password_env='TEST_CAMERA_PASSWORD'), 2, 2)
    monkeypatch.setattr(axispy.time, 'monotonic', lambda: clock[0])
    def get(*args, **kwargs):
        clock[0] += 2
        raise requests.ReadTimeout('private-test-password https://camera.example')
    monkeypatch.setattr(camera.session, 'get', get)
    try:
        with caplog.at_level('WARNING', logger='axispy'), pytest.raises(requests.ReadTimeout):
            camera.move(STOP)
        assert 'J3 slow control stop: failed after 2.00s' in caplog.text
        assert 'final response headers unavailable' in caplog.text
        assert 'private-test-password' not in caplog.text
        assert 'camera.example' not in caplog.text
    finally:
        camera.close()



def test_diagnostic_mode_does_not_start_controller_or_file_logging(camera_environment, monkeypatch, tmp_path):
    import axispy
    import sys
    monkeypatch.setenv('AXISPY_ENV_FILE', str(tmp_path / 'missing.env'))
    monkeypatch.setenv('AXIS_LOG_FILE', str(tmp_path / 'service.log'))
    diagnostic = Mock(return_value=True)
    run = Mock(side_effect=AssertionError('Controller must not run'))
    configure_logging = Mock(side_effect=AssertionError('Service log must not be opened'))
    monkeypatch.setattr(axispy, 'diagnose_camera', diagnostic)
    monkeypatch.setattr(axispy, 'run', run)
    monkeypatch.setattr(axispy, 'configure_file_logging', configure_logging)
    monkeypatch.setattr(sys, 'argv', ['axispy', '--diagnose-camera', 'j3'])
    assert axispy.main() == 0
    assert diagnostic.call_args.args[1] == 'J3'
    run.assert_not_called()
    configure_logging.assert_not_called()
    assert not (tmp_path / 'service.log').exists()


def test_diagnostic_request_failure_is_sanitized_and_session_closed(camera_environment, monkeypatch, capsys):
    import axispy
    client = Mock()
    client.session.verify = False
    client.session.get.side_effect = requests.ReadTimeout('secret-password https://private-camera.example')
    monkeypatch.setattr(axispy, 'Camera', Mock(return_value=client))
    assert not axispy.diagnose_camera(Settings.load(), 'J2')
    output = capsys.readouterr().out
    assert 'failed (ReadTimeout)' in output
    assert 'secret-password' not in output
    assert 'private-camera.example' not in output
    client.close.assert_called_once()


def test_diagnostic_rejects_disabled_camera(camera_environment, monkeypatch):
    from axispy import diagnose_camera
    monkeypatch.setenv('AXIS_J2_ENABLED', 'false')
    with pytest.raises(ValueError, match='J2 is disabled'):
        diagnose_camera(Settings.load(), 'J2')



@pytest.mark.parametrize('status,headers,will_close,framing,persistence', [
    (204, {}, False, 'no-body', 'keep-alive'),
    (204, {}, True, 'no-body', 'close'),
    (200, {'Content-Length': '0'}, False, 'Content-Length', 'keep-alive'),
    (200, {'Transfer-Encoding': 'chunked'}, False, 'chunked', 'keep-alive'),
    (200, {}, True, 'connection-close', 'close'),
    (200, {}, None, 'connection-close', 'unknown'),
])
def test_response_framing_does_not_confuse_204_with_connection_close(status, headers, will_close, framing, persistence):
    from types import SimpleNamespace
    from axispy import response_transport_details
    response = requests.Response()
    response.status_code = status
    response.headers.update(headers)
    response.raw = SimpleNamespace(version=11, _original_response=SimpleNamespace(will_close=will_close))
    assert response_transport_details(response) == ('1.1', framing, persistence)
