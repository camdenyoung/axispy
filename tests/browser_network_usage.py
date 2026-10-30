"""Optional Chromium network display check: python tests/browser_network_usage.py."""
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
        page = browser.new_page(viewport=dict(width=1920, height=1080))
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        system = dict(cpu_percent=25, cpu_status='ok',
                      memory=dict(percent=50, used_bytes=2**30, total_bytes=2**31),
                      network=None, network_status='measuring')
        payload = dict(version='test', time='2026-01-01T00:00:00Z', selected=0,
                       joystick=None, armed=False, poll_interval_ms=60000,
                       system=system, cameras=[dict(id=0, slot=1, name='J1',
                       url='http://camera.local', channel=1, online=False,
                       focus='unknown', ptz_activity='idle')])
        def route_request(route):
            if route.request.url == 'http://dashboard.test/':
                route.fulfill(content_type='text/html', body=HTML)
            elif route.request.url == 'http://dashboard.test/api/status':
                route.fulfill(json=payload)
            else:
                route.fulfill(status=404)
        page.route('http://dashboard.test/**', route_request)
        page.goto('http://dashboard.test/', wait_until='domcontentloaded')
        page.wait_for_function('document.querySelectorAll(".camera").length===1')
        assert page.locator('#resources').is_hidden()
        before = page.locator('#cameras').bounding_box()
        page.get_by_role('button', name='Show system usage').click()
        assert page.locator('#cameras').bounding_box() == before
        assert page.locator('#networkIn').text_content() == 'Measuring…'
        assert page.locator('#networkOut').text_content() == 'Measuring…'
        for rx, tx in [(12.34, 56.78), (0, 0), (1000.01, 2500.55)]:
            system.update(network=dict(rx_mbps=rx, tx_mbps=tx), network_status='ok')
            page.evaluate('status()')
            assert page.locator('#networkIn').text_content() == f'{rx:.2f} Mbps'
            assert page.locator('#networkOut').text_content() == f'{tx:.2f} Mbps'
            assert page.locator('#cpuUsage').text_content() == '25.0%'
            assert page.locator('#memoryUsage').text_content() == '50.0%'
        for width in (1920, 1281, 1280, 768, 600, 599, 390):
            page.set_viewport_size(dict(width=width, height=1080))
            page.evaluate('window.scrollTo(0,0)')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'), width
            before = page.locator('#cameras').bounding_box()
            page.get_by_role('button', name='Hide system usage').click()
            assert page.locator('#cameras').bounding_box() == before
            page.get_by_role('button', name='Show system usage').click()
            assert page.locator('#cameras').bounding_box() == before
        system.update(network=None, network_status='unavailable')
        page.evaluate('status()')
        assert page.locator('#networkIn').text_content() == 'Unavailable'
        assert page.locator('#networkOut').text_content() == 'Unavailable'
        page.route('http://dashboard.test/api/status', lambda route: route.fulfill(status=503))
        page.evaluate('status()')
        assert page.locator('#cpuUsage').text_content() == 'Unavailable'
        assert page.locator('#networkIn').text_content() == 'Unavailable'
        assert not errors, errors
        browser.close()
        print('PASS network Mbps values, zero/high rates, measuring/unavailable, host metrics, responsive header and stable toggles')


if __name__ == '__main__':
    main()
