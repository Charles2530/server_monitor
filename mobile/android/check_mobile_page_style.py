"""Exercise the Android-generated injection payload under the server's CSP.

Run after Gradle unit tests with the repository venv and the same JAVA_HOME /
ANDROID_HOME used for the Android build. Uses fixture pages, never a live gateway.
"""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from browser_support import browser_launch_options
from playwright.async_api import async_playwright

ORIGIN = 'http://127.0.0.1:1234'
CSP = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'"
MONITOR = '<aside class="sidebar"></aside><main id="main"><div id="detail-panel"></div></main>'


def generated_payloads():
    java = Path(os.environ['JAVA_HOME']) / 'bin'
    android = Path(os.environ['ANDROID_HOME']) / 'platforms/android-35/android.jar'
    gradle = Path(os.environ.get('GRADLE_USER_HOME', Path.home() / '.gradle'))
    json_jar = next(gradle.glob('caches/modules-2/files-2.1/org.json/json/20240303/*/*.jar'))
    source = ROOT / 'mobile/android/app/src/main/java/com/charles/monitor'
    with tempfile.TemporaryDirectory(prefix='mobile-style-check-') as temporary:
        harness = Path(temporary) / 'MobilePageStyleBrowserCheck.java'
        harness.write_text('''package com.charles.monitor;
import java.nio.file.Files;
import java.nio.file.Path;
public class MobilePageStyleBrowserCheck {
    public static void main(String[] args) throws Exception {
        String javascript = args.length == 0
            ? "window.nativeStyleExecutions=(window.nativeStyleExecutions||0)+1; // final comment"
            : Files.readString(Path.of(args[0]));
        System.out.print(new MobilePageStyle("body{color:rgb(25,75,58)}", javascript)
            .scriptFor("http://127.0.0.1:1234/", "http://127.0.0.1:1234"));
    }
}''')
        classpath = os.pathsep.join(map(str, (json_jar, android)))
        subprocess.run([str(java / 'javac'), '-cp', classpath, '-d', temporary,
                        str(source / 'MobilePageStyle.java'), str(source / 'NavigationPolicy.java'), str(harness)], check=True)
        command = [str(java / 'java'), '-cp', temporary + os.pathsep + classpath,
                   'com.charles.monitor.MobilePageStyleBrowserCheck']
        return (subprocess.check_output(command, text=True),
                subprocess.check_output(command + [str(ROOT / 'static/mobile.js')], text=True))


async def main():
    probe, bundled = generated_payloads()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**browser_launch_options())
        page = await browser.new_page(viewport={'width': 393, 'height': 780})
        for url, body, allowed in ((ORIGIN + '/', MONITOR, True),
                                   (ORIGIN + '/', '<p>Login document</p>', False),
                                   (ORIGIN + '/terminal', MONITOR, False),
                                   ('http://127.0.0.1:9999/', MONITOR, False),
                                   ('https://example.test/', MONITOR, False)):
            await page.route('**/*', lambda route: route.fulfill(content_type='text/html',
                             headers={'Content-Security-Policy': CSP},
                             body='<!doctype html><html><head></head><body>' + body + '</body></html>'))
            await page.goto(url)
            await page.evaluate(probe)
            await page.evaluate(probe)
            assert await page.evaluate('window.nativeStyleExecutions||0') == int(allowed), (url, 'CSP execution / once guard')
            assert await page.locator('#ilab-mobile-style').count() == int(allowed), (url, 'style guard')
            assert await page.locator('#ilab-mobile-script').count() == int(allowed), (url, 'script marker')
            await page.unroute('**/*')
        await page.route('**/*', lambda route: route.fulfill(content_type='text/html',
                         headers={'Content-Security-Policy': CSP},
                         body='<!doctype html><html><head></head><body>' + MONITOR + '</body></html>'))
        await page.goto(ORIGIN + '/')
        await page.evaluate(bundled)
        await page.evaluate(bundled)
        assert await page.evaluate('window.ilabMobileReady === true'), 'Bundled mobile.js must execute under CSP'
        assert await page.locator('#mobile-task-back').count() == 1, 'Bundled enhancement must remain idempotent'
        await browser.close()
        print('Android payload passed: CSP, bundled JS, double injection, monitor DOM, terminal, foreign port and origin.')


asyncio.run(main())
