"""Per-camera TLS settings and real self-signed HTTPS camera integration."""
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
import shutil
import ssl
import subprocess
import threading
import time
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
import requests
from urllib3.exceptions import InsecureRequestWarning

from axispy import Camera, Controller, Settings, STOP, camera_tls_verify
from axispy_web import Dashboard


@pytest.fixture
def camera_env(monkeypatch):
    for slot in range(1, 5):
        monkeypatch.setenv(f'AXIS_J{slot}_URL', f'https://camera{slot}.example')
        monkeypatch.setenv(f'AXIS_J{slot}_PASSWORD', 'test-only')
        monkeypatch.delenv(f'AXIS_J{slot}_VERIFY_TLS', raising=False)


@pytest.mark.parametrize('value,expected', [(None, True), ('', True), (' ', True),
    ('true', True), (' TRUE ', True), ('1', True), ('false', False), (' FALSE ', False), ('0', False)])
def test_per_camera_environment_verification(camera_env, monkeypatch, value, expected):
    if value is not None:
        monkeypatch.setenv('AXIS_J2_VERIFY_TLS', value)
    settings = Settings.load()
    assert camera_tls_verify(settings.cameras[1]) is expected
    assert all(camera_tls_verify(settings.cameras[i]) is True for i in (0, 2, 3))


@pytest.mark.parametrize('value', ['no', 'yes', '2', 'disabled'])
def test_invalid_verify_tls(camera_env, monkeypatch, value):
    monkeypatch.setenv('AXIS_J3_VERIFY_TLS', value)
    with pytest.raises(ValueError, match='AXIS_J3_VERIFY_TLS'):
        Settings.load()


def test_toml_verification_and_environment_precedence(camera_env, monkeypatch, tmp_path):
    config = tmp_path / 'config.toml'
    config.write_text(Path('config.example.toml').read_text().replace(
        '[[cameras]]', '[[cameras]]\nverify_tls = false', 1))
    assert camera_tls_verify(Settings.load(str(config)).cameras[0]) is False
    monkeypatch.setenv('AXIS_J1_VERIFY_TLS', 'true')
    assert camera_tls_verify(Settings.load(str(config)).cameras[0]) is True


def test_disabled_slots_and_ca_bundle_precedence(camera_env, monkeypatch):
    monkeypatch.setenv('AXIS_J1_ENABLED', 'false')
    monkeypatch.setenv('AXIS_J1_VERIFY_TLS', 'unused')
    monkeypatch.setenv('AXIS_J3_VERIFY_TLS', 'false')
    monkeypatch.setenv('AXIS_J3_CA_BUNDLE', '/missing/ca.pem')
    settings = Settings.load()
    assert [camera['slot'] for camera in settings.cameras] == [2, 3, 4]
    assert camera_tls_verify(settings.cameras[1]) is False
    assert camera_tls_verify(settings.cameras[0]) is True


@pytest.mark.parametrize('config,expected', [({}, True), ({'verify_tls': False}, False),
    ({'ca_bundle': '/certs/ca.pem'}, '/certs/ca.pem'), ({'ca_bundle': ''}, True),
    ({'verify_tls': False, 'ca_bundle': '/missing/ca.pem'}, False)])
def test_shared_verification_setting(config, expected):
    assert camera_tls_verify(config) == expected


@pytest.mark.parametrize('config', [{'verify_tls': 'false'}, {'verify_tls': 0}, {'ca_bundle': False}, {'ca_bundle': None}])
def test_invalid_toml_or_direct_verification_setting(config):
    with pytest.raises(ValueError):
        camera_tls_verify(config)


@pytest.fixture
def https_camera(tmp_path, monkeypatch):
    openssl = shutil.which('openssl')
    if not openssl:
        pytest.skip('HTTPS integration tests require openssl to generate a temporary certificate')
    cert, key = tmp_path / 'cert.pem', tmp_path / 'key.pem'
    subprocess.run([openssl, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-keyout', str(key), '-out', str(cert), '-subj', '/CN=localhost',
                    '-addext', 'subjectAltName=IP:127.0.0.1,DNS:localhost'],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    received = []
    frame = b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 2048\r\n\r\n' + b'x' * 2048 + b'\r\n--frame--\r\n'
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if config.get('_test_digest') and not self.headers.get('Authorization', '').startswith('Digest '):
                self.send_response(401)
                self.send_header('WWW-Authenticate', 'Digest realm="camera", nonce="test-timing", qop="auth"')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            received.append(self.path)
            params = parse_qs(urlsplit(self.path).query)
            stream = urlsplit(self.path).path == '/axis-cgi/mjpg/video.cgi'
            body = frame if stream else b'pan=0\ntilt=0\nzoom=1\nautofocus=on' if params.get('query') == ['position'] else b''
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame' if stream else 'text/plain')
            if not config.get('_test_close_delay'):
                self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            if config.get('_test_close_delay'):
                time.sleep(config['_test_close_delay'])
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('TEST_TLS_PASSWORD', 'test-only')
    config = dict(name='J1', url=f'https://127.0.0.1:{server.server_port}',
                  username='operator', password_env='TEST_TLS_PASSWORD', channel=1)
    try:
        yield config, str(cert), received, frame
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_default_rejects_untrusted_https_certificate(https_camera, monkeypatch):
    config, _, received, _ = https_camera
    camera = Camera(config, 1, 2)
    try:
        with pytest.raises(requests.exceptions.SSLError):
            camera.move(STOP)
    finally:
        camera.close()
    dashboard = Dashboard([config], Controller([Mock()]))
    original_probe = dashboard.probe
    def probe_once(index, session):
        original_probe(index, session)
        dashboard.finished.set()
    monkeypatch.setattr(dashboard, 'probe', probe_once)
    dashboard._monitor(0)
    assert dashboard.snapshot()['cameras'][0]['error'] == 'SSLError'
    dashboard.finished.clear()
    handler = Mock()
    dashboard.stream(0, handler)
    handler.send_error.assert_called_once_with(502, 'Camera MJPEG preview unavailable')
    assert received == []


@pytest.mark.parametrize('verify', [True, False])
def test_https_control_health_and_video_share_verification(https_camera, monkeypatch, verify):
    config, cert, received, frame = https_camera
    # false must also work when a configured bundle path does not exist.
    config.update(verify_tls=verify, ca_bundle=cert if verify else '/missing/camera-ca.pem')
    camera = Camera(config, 1, 2)
    dashboard = Dashboard([config], Controller([camera]))
    try:
        with (nullcontext() if verify else pytest.warns(InsecureRequestWarning)):
            camera.move(STOP)
            original_probe = dashboard.probe
            def probe_once(index, session):
                original_probe(index, session)
                dashboard.finished.set()
            monkeypatch.setattr(dashboard, 'probe', probe_once)
            dashboard._monitor(0)
            assert dashboard.snapshot()['cameras'][0]['online'] is True
            dashboard.finished.clear()
            handler = Mock()
            handler.wfile = BytesIO()
            dashboard.stream(0, handler)
            handler.send_response.assert_called_once_with(200)
            assert handler.wfile.getvalue() == frame
        paths = [urlsplit(path).path for path in received]
        assert paths == ['/axis-cgi/com/ptz.cgi', '/axis-cgi/com/ptz.cgi', '/axis-cgi/mjpg/video.cgi']
        assert camera.session.verify == (cert if verify else False)
    finally:
        camera.close()



def test_slow_https_body_reports_fast_headers_and_close_framing(https_camera, caplog):
    config, _, _, _ = https_camera
    config.update(verify_tls=False, _test_close_delay=.4)
    camera = Camera(config, 2, 2)
    try:
        with pytest.warns(InsecureRequestWarning), caplog.at_level('WARNING', logger='axispy'):
            camera.move(STOP)
        assert 'slow control stop: acknowledged' in caplog.text
        assert 'body framing connection-close' in caplog.text
        assert 'connect/read limits 2.00/2.00s' in caplog.text
        assert camera.last_ptz == (0, 0, 0)
    finally:
        camera.close()



@pytest.mark.parametrize('verify', [True, False])
def test_read_only_diagnostic_uses_control_tls_and_digest_settings(https_camera, camera_env, monkeypatch, capsys, verify):
    from axispy import diagnose_camera
    config, cert, received, _ = https_camera
    config['_test_digest'] = True
    monkeypatch.setenv('AXIS_J3_URL', config['url'])
    monkeypatch.setenv('AXIS_J3_CA_BUNDLE', cert if verify else '/missing/ca.pem')
    monkeypatch.setenv('AXIS_J3_VERIFY_TLS', str(verify).lower())
    with (nullcontext() if verify else pytest.warns(InsecureRequestWarning)):
        assert diagnose_camera(Settings.load(), 'J3')
    assert len(received) == 3
    assert all(parse_qs(urlsplit(path).query) == {'camera': ['1'], 'query': ['position']} for path in received)
    output = capsys.readouterr().out
    assert output.count('J3 sample') == 3
    assert 'digest_challenges=1' in output
    assert 'digest_challenges=0' in output
    assert 'HTTP=200' in output
    assert ('TLS verification enabled' if verify else 'TLS verification disabled') in output
    assert 'test-only' not in output
    assert config['url'] not in output


def test_read_only_diagnostic_separates_headers_and_body(https_camera, camera_env, monkeypatch, capsys):
    import re
    from axispy import diagnose_camera
    config, _, _, _ = https_camera
    config.update(_test_close_delay=.4)
    monkeypatch.setenv('AXIS_J3_URL', config['url'])
    monkeypatch.setenv('AXIS_J3_VERIFY_TLS', 'false')
    with pytest.warns(InsecureRequestWarning):
        assert diagnose_camera(Settings.load(), 'J3')
    output = capsys.readouterr().out
    samples = re.findall(r'headers=([0-9.]+)s body=([0-9.]+)s total=([0-9.]+)s', output)
    assert len(samples) == 3
    assert all(float(body) >= .3 and float(total) >= float(body) for _, body, total in samples)
    assert 'framing=connection-close' in output
