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
    controller.send((50, 0, 0))
    cameras[0].move.assert_called_with((50, 0, 0))
    controller.select(3)
    cameras[0].move.assert_called_with(STOP)
    controller.send((50, 0, 0))
    cameras[3].move.assert_called_with(STOP)
    controller.send(STOP)
    controller.send((0, -50, 20))
    cameras[3].move.assert_called_with((0, -50, 20))


def test_failed_stop_prevents_selection():
    cameras = [Mock() for _ in range(4)]
    cameras[0].move.side_effect = requests.Timeout()
    controller = Controller(cameras)
    with pytest.raises(requests.Timeout):
        controller.select(1)
    assert controller.selected == 0
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
        def do_GET(self):
            received.append(urlsplit(self.path))
            self.send_response(200)
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
