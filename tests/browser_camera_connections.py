"""Optional Chromium badge/link checks: python tests/browser_camera_connections.py."""
from pathlib import Path
import shutil
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from axispy_ui import HTML


def main():
    with sync_playwright() as playwright:
        executable = shutil.which('chromium') or shutil.which('chromium-browser')
        browser = playwright.chromium.launch(
            **({'executable_path': executable} if executable else {}),
            headless=True, args=['--no-sandbox'])
        context = browser.new_context(viewport=dict(width=1920, height=1080))
        cameras = [dict(id=i, slot=i+1, name=f'Camera J{i+1}', url=url, channel=1,
            protocol=protocol, tls_verification_enabled=verify, online=True,
            selected=i == 0, focus='unknown', ptz_activity='idle')
            for i, (url, protocol, verify) in enumerate([
                ('http://camera-http.test:8081/', 'http', None),
                ('https://camera-verified.test/', 'https', True),
                ('https://camera-unverified.test:8443/', 'https', False),
            ])]
        payload = dict(version='test', time='2026-01-01T00:00:00Z', cameras=cameras,
                       selected=0, armed=False, joystick=None, poll_interval_ms=60000,
                       preview_fps=25, preview_resolution='640x360', system=None)
        navigation_requests = []
        errors = []
        def route_request(route):
            url = route.request.url
            if url == 'http://dashboard.test/':
                route.fulfill(content_type='text/html', body=HTML)
            elif url == 'http://dashboard.test/api/status':
                route.fulfill(json=payload)
            elif url in [camera['url'] for camera in cameras]:
                navigation_requests.append(route.request)
                route.fulfill(content_type='text/html', body='<title>Camera web interface</title>')
            else:
                route.fulfill(status=404)
        context.route('**/*', route_request)
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://dashboard.test/', wait_until='domcontentloaded')
        page.wait_for_function('document.querySelectorAll(".camera").length===3')
        assert page.locator('.transport-badge').all_text_contents() == ['HTTP', 'HTTPS', 'HTTPS · unverified']
        assert 'not encrypted' in page.locator('.transport-badge').nth(0).get_attribute('title')
        assert 'verification enabled' in page.locator('.transport-badge').nth(1).get_attribute('title')
        assert 'verification disabled' in page.locator('.transport-badge').nth(2).get_attribute('title')
        for i, camera in enumerate(cameras):
            link = page.locator('.camera-address').nth(i)
            assert page.locator('.address').nth(i).text_content() == camera['url']
            assert link.text_content() == camera['url']
            assert link.get_attribute('href') == camera['url']
            assert set(link.get_attribute('rel').split()) == {'noopener', 'noreferrer'}
            assert link.get_attribute('target') == '_blank'
            with page.expect_popup() as popup_info:
                link.click()
            popup = popup_info.value
            popup.wait_for_load_state('domcontentloaded')
            assert popup.url == camera['url']
            assert popup.title() == 'Camera web interface'
            assert popup.evaluate('window.opener === null')
            assert page.url == 'http://dashboard.test/'
            popup.close()
        assert len(navigation_requests) == 3
        assert all('authorization' not in r.headers and 'referer' not in r.headers for r in navigation_requests)
        for camera in cameras:
            camera['online'] = False
        page.evaluate('status()')
        assert page.locator('.transport-badge').all_text_contents() == ['HTTP', 'HTTPS', 'HTTPS · unverified']
        assert all('Offline' in text for text in page.locator('.connection-status').all_text_contents())
        assert not errors, errors
        browser.close()
        print('PASS HTTP/HTTPS badges, unverified state, offline cards, camera links, new-tab navigation and no credentials/referrer')


if __name__ == '__main__':
    main()
