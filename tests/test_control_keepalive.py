"""Control-session warming must be read-only and preserve synchronous ordering."""
import threading
from unittest.mock import Mock

import pytest
import requests

from axispy import Camera, Settings, STOP


@pytest.fixture
def camera(monkeypatch):
    monkeypatch.setenv('TEST_WARM_PASSWORD', 'test-only')
    client = Camera(dict(name='J2', url='https://camera.example', username='operator',
                         password_env='TEST_WARM_PASSWORD'), .1, 2)
    yield client
    client.close()


def test_warming_failed_read_only_query_does_not_disable_control(camera, monkeypatch):
    warmed = threading.Event()
    def warm():
        warmed.set()
        return False
    monkeypatch.setattr(camera, '_warm_connection', warm)
    commands = []
    monkeypatch.setattr(camera, '_request_direct', lambda params, timeout: commands.append(dict(params)))
    camera.start_keepalive(.01)
    assert warmed.wait(1)
    camera.move(STOP)
    camera.move((20, 0, 0, 0))
    assert len(commands) == 2
    assert commands[0]['continuouspantiltmove'] == '0,0'
    assert commands[1]['continuouspantiltmove'] == '20,0'


def test_request_waits_for_worker_acknowledgement_and_preserves_exception(camera, monkeypatch):
    warmed = threading.Event()
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setattr(camera, '_warm_connection', lambda: (warmed.set() or True))
    def request(params, timeout):
        entered.set()
        assert release.wait(2)
    monkeypatch.setattr(camera, '_request_direct', request)
    camera.start_keepalive(10)
    assert warmed.wait(1)
    def move():
        camera.move(STOP)
        completed.set()
    caller = threading.Thread(target=move)
    caller.start()
    assert entered.wait(1)
    assert not completed.is_set()
    release.set()
    caller.join(2)
    assert completed.is_set()
    monkeypatch.setattr(camera, '_request_direct', Mock(side_effect=requests.ReadTimeout('timeout')))
    with pytest.raises(requests.ReadTimeout):
        camera.move(STOP)


def test_close_discards_queued_commands_instead_of_executing_them(camera, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def warm():
        entered.set()
        assert release.wait(2)
        return True
    monkeypatch.setattr(camera, '_warm_connection', warm)
    request = Mock()
    monkeypatch.setattr(camera, '_request_direct', request)
    camera.start_keepalive(10)
    assert entered.wait(1)
    errors = []
    def move():
        try:
            camera.move((10, 0, 0, 0))
        except RuntimeError as exc:
            errors.append(str(exc))
    caller = threading.Thread(target=move)
    caller.start()
    # Hold the lifecycle lock until the request is definitely queued.
    with camera._lifecycle_lock:
        queued = not camera._control_queue.empty()
    if not queued:
        for _ in range(100):
            if not camera._control_queue.empty():
                break
            threading.Event().wait(.001)
    assert not camera._control_queue.empty()
    closer = threading.Thread(target=camera.close)
    closer.start()
    for _ in range(100):
        if camera._closing:
            break
        threading.Event().wait(.001)
    assert camera._closing
    release.set()
    caller.join(2)
    closer.join(2)
    assert errors == ['Camera is closed']
    request.assert_not_called()


def test_http_and_disabled_keepalive_preserve_direct_requests(camera):
    camera.start_keepalive(0)
    assert camera._control_worker is None
    camera.url = 'http://camera.example/axis-cgi/com/ptz.cgi'
    camera.start_keepalive(1)
    assert camera._control_worker is None


@pytest.mark.parametrize('value,expected', [('0', 0), ('0.5', .5), ('1', 1), ('30', 30)])
def test_keepalive_configuration(monkeypatch, value, expected):
    monkeypatch.setenv('AXIS_CONTROL_KEEPALIVE_SECONDS', value)
    assert Settings.load('config.example.toml').control_keepalive_seconds == expected


@pytest.mark.parametrize('value', ['-1', '.1', '31', 'nan', 'inf', 'bad'])
def test_invalid_keepalive_configuration(monkeypatch, value):
    monkeypatch.setenv('AXIS_CONTROL_KEEPALIVE_SECONDS', value)
    with pytest.raises(ValueError):
        Settings.load('config.example.toml')
