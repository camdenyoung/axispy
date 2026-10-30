"""Optional Chromium stream recovery check; no hardware or VPN needed."""
from pathlib import Path
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import time
from unittest.mock import Mock, patch
import shutil
import os
import sys
from playwright.sync_api import sync_playwright
from browser_preview_support import STREAM_FIXTURE

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from axispy_ui import HTML
from axispy import Controller
from axispy_web import Dashboard


def check_real_relay(browser, jpeg):
    """Exercise the actual browser fetch, Python relay, and upstream MJPEG wire format."""
    connections = []
    finished = threading.Event()
    class Camera(BaseHTTPRequestHandler):
        def do_GET(self):
            connections.append(self.path)
            first = len(connections) == 1
            self.send_response(200)
            self.send_header('Content-Type', 'multipart/x-mixed-replace; boundary=frame')
            self.end_headers()
            frame = b'--frame\r\nContent-Type: image/jpeg\r\nContent-Length: ' + str(len(jpeg)).encode() + b'\r\n\r\n' + jpeg + b'\r\n'
            try:
                for i in range(1000):
                    if finished.is_set() or (first and i == 5): return
                    self.wfile.write(frame)
                    self.wfile.flush()
                    time.sleep(.04)
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *_): pass
    camera = ThreadingHTTPServer(('127.0.0.1', 0), Camera)
    camera.daemon_threads = True
    thread = threading.Thread(target=camera.serve_forever, daemon=True)
    thread.start()
    config = dict(name='J1', slot=1, url=f'http://127.0.0.1:{camera.server_port}',
                  username='test', password_env='TEST_BROWSER_PASSWORD')
    with patch.dict(os.environ, TEST_BROWSER_PASSWORD='test'):
        dashboard = Dashboard([config], Controller([Mock()]), port=0)
        dashboard.probe = Mock()
        dashboard.states[0]['online'] = True
        page = browser.new_page()
        try:
            dashboard.start()
            page.goto(f'http://127.0.0.1:{dashboard.port}/')
            page.wait_for_function('document.querySelectorAll(".camera").length === 1')
            page.evaluate('setVideoEnabled(true)')
            page.wait_for_function('!document.querySelector(".preview img").hidden')
            page.wait_for_function('document.querySelector(".preview-placeholder").textContent.includes("reconnecting")')
            page.wait_for_function('!document.querySelector(".preview img").hidden')
            assert len(connections) >= 2
            page.evaluate('setVideoEnabled(false)')
            assert page.locator('.preview img').count() == 0
        finally:
            page.close()
            finished.set()
            dashboard.stop()
            camera.shutdown()
            camera.server_close()
            thread.join(timeout=2)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=shutil.which('chromium'), args=['--no-sandbox'])
        page = browser.new_page()
        page.add_init_script(STREAM_FIXTURE)
        page.clock.install()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        payload = dict(version='test', time='2026-01-01T00:00:00Z', selected=0,
                       armed=False, joystick=None, cameras=[dict(id=0, slot=1,
                       name='J1', url='http://camera.local', online=True, focus='unknown')])
        def route(r):
            path = r.request.url.removeprefix('http://dashboard.test')
            if path == '/': r.fulfill(content_type='text/html', body=HTML)
            elif path == '/api/status': r.fulfill(json=payload)
            else: r.fulfill(json=dict(cursor=0, entries=[]))
        page.route('http://dashboard.test/**', route)
        page.goto('http://dashboard.test/', wait_until='domcontentloaded')
        page.wait_for_function('document.querySelectorAll(".camera").length === 1')
        page.evaluate("streamMode = 'stall'; setVideoEnabled(true)")
        page.wait_for_function('document.querySelector(".preview img").naturalWidth === 640')
        # Multipart parsing across every possible network split, without Content-Length.
        page.evaluate(r'''() => {
          const data = new TextEncoder().encode('--frame\r\nContent-Type: image/jpeg\r\n\r\nabc\r\n--frame\r\nContent-Type: image/jpeg\r\n\r\ndef\r\n--frame--\r\n');
          for (let split = 1; split < data.length; split++) {
            const frames = [];
            const parser = new MjpegFrames('frame', bytes => frames.push(new TextDecoder().decode(bytes)));
            parser.push(data.slice(0, split)); parser.push(data.slice(split));
            if (frames.join(',') !== 'abc,def') throw Error('Split failed: ' + split);
          }
        }''')
        # A silent freeze must show a message and restart; no native img error occurs.
        page.evaluate("streamMode = 'live'")
        page.clock.fast_forward(16000)
        assert 'reconnecting' in page.locator('.preview-placeholder').text_content()
        page.clock.fast_forward(3000)
        page.wait_for_function('streamStarts === 2 && !document.querySelector(".preview img").hidden')
        # EOF, HTTP failures, and repeated failures use bounded backoff.
        page.evaluate("setVideoEnabled(false); streamMode = 'eof'; setVideoEnabled(true)")
        page.clock.run_for(150)
        assert 'reconnecting' in page.locator('.preview-placeholder').text_content()
        page.evaluate("streamMode = 'error'")
        page.clock.fast_forward(3000)
        starts = page.evaluate('streamStarts')
        page.clock.fast_forward(3000)
        assert page.evaluate('streamStarts') == starts
        page.clock.fast_forward(3000)
        assert page.evaluate('streamStarts') == starts + 1
        # Turning previews off cancels pending retries, and switching on starts afresh.
        page.evaluate('setVideoEnabled(false)')
        starts = page.evaluate('streamStarts')
        page.clock.fast_forward(60000)
        assert page.evaluate('streamStarts') == starts
        assert page.locator('.preview img').count() == 0
        page.evaluate("streamMode = 'live'; setVideoEnabled(true)")
        page.wait_for_function('!document.querySelector(".preview img").hidden')
        # Offline cameras cancel the stream, then recover through normal status polling.
        payload['cameras'][0]['online'] = False
        page.evaluate('status()')
        assert page.locator('.preview-placeholder').text_content() == 'Camera Offline'
        assert page.locator('.preview img').count() == 0
        payload['cameras'][0]['online'] = True
        page.evaluate('status()')
        page.wait_for_function('!document.querySelector(".preview img").hidden')
        # Hidden tabs cancel active streams and do not retry until visible.
        stops = page.evaluate('streamStops')
        page.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, value: true}); document.dispatchEvent(new Event('visibilitychange'))")
        assert page.evaluate('streamStops') == stops + 1
        starts = page.evaluate('streamStarts')
        page.clock.fast_forward(60000)
        assert page.evaluate('streamStarts') == starts
        page.evaluate("Object.defineProperty(document, 'hidden', {configurable: true, value: false}); document.dispatchEvent(new Event('visibilitychange'))")
        page.wait_for_function('!document.querySelector(".preview img").hidden')
        assert not errors, errors
        jpeg = base64.b64decode(page.evaluate('''() => {
            const canvas = document.createElement('canvas');
            canvas.width = 640; canvas.height = 360;
            return canvas.toDataURL('image/jpeg').split(',')[1];
        }'''))
        check_real_relay(browser, jpeg)
        browser.close()
        print('PASS multipart parsing, silent stalls, EOF, HTTP failures, backoff, toggle/hidden-tab cancellation, offline recovery, real MJPEG relay reconnect')


if __name__ == '__main__':
    main()
