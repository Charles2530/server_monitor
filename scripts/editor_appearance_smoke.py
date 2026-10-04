"""Render the actual code-server workbench in an isolated local workspace, no cloud."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile

import httpx
from playwright.async_api import async_playwright, expect
from browser_support import browser_launch_options

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server.ide_appearance import appearance_bundle, prepare_appearance
from server.ide_proxy import EditorProxy


async def main(binary):
    with tempfile.TemporaryDirectory(prefix='ilab-editor-preview-') as directory:
        root = Path(directory)
        workspace = root / 'training-workspace'
        for folder in ('src', 'configs', 'datasets', 'notebooks', 'checkpoints', 'logs', 'tests'):
            (workspace / folder).mkdir(parents=True)
        code = '''from pathlib import Path

import torch
from torch import nn


class TrainingConfig:
    learning_rate = 3e-4
    batch_size = 32
    device = "cuda" if torch.cuda.is_available() else "cpu"


def train_step(model: nn.Module, batch: torch.Tensor) -> float:
    """Run a training step and return the loss."""
    features, labels = batch
    predictions = model(features)
    loss = nn.functional.cross_entropy(predictions, labels)

    # Keep gradients stable for mixed precision training.
    loss.backward()
    nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
    return loss.item()


if __name__ == "__main__":
    config = TrainingConfig()
    output_dir = Path("checkpoints")
    output_dir.mkdir(exist_ok=True)
    print(f"Training on {config.device} · 实验准备就绪")
'''
        (workspace / 'src/train.py').write_text(code)
        (workspace / 'configs/training.yaml').write_text('model: transformer\nbatch_size: 32\nlearning_rate: 0.0003\n')
        (workspace / 'datasets/metadata.json').write_text('{"split": "train", "samples": 12000}\n')
        (workspace / 'notebooks/analysis.ipynb').write_text('{"cells":[],"nbformat":4,"nbformat_minor":5,"metadata":{}}')
        (workspace / 'checkpoints/model.safetensors').write_bytes(b'')
        (workspace / 'logs/training.log').write_text('Preview workspace only.\n')
        (workspace / 'tests/test_training.py').write_text('def test_config():\n    assert True\n')
        (workspace / 'requirements.txt').write_text('torch\nnumpy\n')
        (workspace / 'README.md').write_text('# Training workspace\n\nIsolated appearance preview.\n')
        base = root / 'editor'
        env = {**os.environ, 'HOME': str(root), 'SHELL': '/bin/bash'}
        settings = base / 'user-data/User/settings.json'
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({'workbench.colorTheme': 'Dark Modern', 'workbench.startupEditor': 'none',
                                        'telemetry.telemetryLevel': 'off', 'update.mode': 'none', 'security.workspace.trust.enabled': False}))
        result = await asyncio.to_thread(prepare_appearance, base, env, binary, appearance_bundle())
        assert result['installed'], 'Appearance extension installation failed'
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
        import secrets
        password = secrets.token_urlsafe(32)
        config = base / 'config.json'
        config.write_text(json.dumps({'bind-addr': f'127.0.0.1:{port}', 'auth': 'password', 'password': password, 'cert': False}))
        config.chmod(0o600)
        log = (base / 'editor.log').open('wb')
        process = subprocess.Popen([str(binary), '--config', str(config), '--user-data-dir', str(base / 'user-data'),
                                    '--extensions-dir', str(base / 'extensions'), '--disable-telemetry', '--disable-update-check',
                                    '--disable-workspace-trust', str(workspace)], env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log)
        proxy = None
        try:
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', trust_env=False, timeout=2) as client:
                for _ in range(120):
                    assert process.poll() is None, 'Isolated editor exited before startup'
                    try:
                        response = await client.get('/login')
                        if response.status_code == 200: break
                    except httpx.HTTPError: pass
                    await asyncio.sleep(.25)
                response = await client.post('/login', data={'password': password, 'base': '/'})
                assert response.status_code in (302, 303)
                cookie = '; '.join(k + '=' + v for k, v in client.cookies.items())
            proxy = EditorProxy(port, cookie, 'http://127.0.0.1:8766')
            await proxy.start()
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(**browser_launch_options())
                page = await browser.new_page(viewport={'width': 1440, 'height': 960}, color_scheme='dark')
                page.on('pageerror', lambda error: print('PAGE ERROR:', str(error)[:400]))
                await page.goto(proxy.origin + '/_ilab_open/' + proxy.ticket(), wait_until='domcontentloaded')
                await expect(page.locator('.monaco-workbench')).to_be_visible(timeout=60000)
                await page.get_by_role('treeitem', name='src', exact=True).click()
                try:
                    await page.get_by_role('treeitem', name='train.py', exact=True).dblclick(timeout=30000)
                except Exception:
                    await page.screenshot(path='/tmp/ilab-editor-failure.png')
                    print((await page.locator('body').inner_text())[-4000:])
                    print((base / 'editor.log').read_text()[-4000:])
                    raise
                try:
                    await expect(page.locator('.view-lines').first).to_contain_text('import torch', timeout=15000)
                except AssertionError:
                    await page.screenshot(path='/tmp/ilab-editor-failure.png')
                    print((await page.locator('body').inner_text())[-6000:])
                    print((base / 'editor.log').read_text()[-4000:])
                    raise
                await page.wait_for_function("[...document.fonts].some(f => f.family.includes('iLab JetBrains Mono') && f.status === 'loaded')")
                style = await page.locator('.view-lines').first.evaluate("e => ({font:getComputedStyle(e).fontFamily,size:getComputedStyle(e).fontSize,lineHeight:getComputedStyle(e).lineHeight})")
                assert 'iLab JetBrains Mono' in style['font'], style
                assert style['size'] == '14px' and style['lineHeight'] == '24px', style
                icon_count = await page.locator('.explorer-folders-view .monaco-icon-label').evaluate_all("els => els.filter(e => decodeURIComponent(getComputedStyle(e,'::before').backgroundImage).includes('/icons/')).length")
                assert icon_count >= 4, f'Expected Material icons, found {icon_count}'
                theme = await page.evaluate("getComputedStyle(document.querySelector('.monaco-workbench')).getPropertyValue('--vscode-editor-background').trim()")
                assert theme == '#182025', theme
                output = ROOT / 'data/ide-appearance-preview'
                output.mkdir(exist_ok=True)
                await page.screenshot(path=str(output / 'vscode-desktop.png'))
                (output / 'verification.json').write_text(json.dumps({'typography':style,'material_icons':icon_count,'editor_background':theme},indent=2))
                await browser.close()
                print('Actual code-server workbench verified:', style, 'Material icons:', icon_count, 'theme:', theme)
        finally:
            if proxy: await proxy.close()
            process.terminate()
            try: await asyncio.to_thread(process.wait, timeout=10)
            except subprocess.TimeoutExpired: process.kill(); await asyncio.to_thread(process.wait)
            log.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--code-server', type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(main(args.code_server.resolve()))
