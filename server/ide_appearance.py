"""Editor defaults and scoped context actions; user appearance settings win."""
import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

APPEARANCE_VERSION = '1.4.3'


def appearance_bundle():
    root = Path(__file__).resolve().parent.parent / 'static/editor-appearance/extension'
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob('*')):
            if path.is_file():
                info = zipfile.ZipInfo('extension/' + path.relative_to(root).as_posix(), (2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, path.read_bytes())
        archive.writestr(zipfile.ZipInfo('[Content_Types].xml', (2026, 1, 1, 0, 0, 0)), '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="js" ContentType="application/javascript"/><Default Extension="json" ContentType="application/json"/><Default Extension="svg" ContentType="image/svg+xml"/><Default Extension="vsixmanifest" ContentType="text/xml"/></Types>')
        archive.writestr(zipfile.ZipInfo('extension.vsixmanifest', (2026, 1, 1, 0, 0, 0)), '''<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011"><Metadata>
<Identity Language="en-US" Id="workspace-appearance" Version="1.4.3" Publisher="ilab"/><DisplayName>iLab Workspace Appearance</DisplayName><Description xml:space="preserve">Code typography and Material file icons.</Description><Tags>theme,icons</Tags><Categories>Themes</Categories><GalleryFlags>Public</GalleryFlags><Properties><Property Id="Microsoft.VisualStudio.Code.Engine" Value="^1.80.0"/></Properties></Metadata><Installation><InstallationTarget Id="Microsoft.VisualStudio.Code"/></Installation><Dependencies/><Assets><Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true"/></Assets></PackageManifest>''')
    raw = output.getvalue()
    return {'version': APPEARANCE_VERSION, 'sha256': hashlib.sha256(raw).hexdigest(), 'payload': base64.b64encode(raw).decode()}


def appearance_installed(base, bundle):
    try:
        marker = json.loads((base / 'appearance.json').read_text())
        if marker != {'version': bundle['version'], 'sha256': bundle['sha256']}: return False
        obsolete = json.loads((base / 'extensions/.obsolete').read_text().strip() or '{}') if (base / 'extensions/.obsolete').is_file() else {}
        for path in (base / 'extensions').glob('ilab.workspace-appearance-*/package.json'):
            if obsolete.get(path.parent.name): continue
            package = json.loads(path.read_text())
            if (package.get('publisher') == 'ilab' and package.get('name') == 'workspace-appearance'
                    and package.get('version') == bundle['version'] and (path.parent / 'icons.json').is_file()):
                return True
    except (OSError, ValueError, AttributeError):
        pass
    return False


def prepare_appearance(base, env, binary, bundle, progress=None):
    result = {'installed': False, 'version': bundle['version']}
    if appearance_installed(base, bundle): return {**result, 'installed': True}
    temporary = None
    try:
        if progress: progress('install_appearance')
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = Path(tempfile.mkdtemp(prefix='appearance-install-', dir=str(base)))
        raw = base64.b64decode(bundle['payload'], validate=True)
        if hashlib.sha256(raw).hexdigest() != bundle['sha256']: return result
        archive = temporary / 'appearance.vsix'
        archive.write_bytes(raw)
        config = temporary / 'config.json'
        config.write_text('{}')
        # Install our bundled appearance/context extension. Never parse or rewrite personal settings.json (JSONC).
        with (base / 'appearance-install.log').open('ab') as log:
            installed = subprocess.run([str(binary), '--config', str(config), '--user-data-dir', str(base / 'user-data'),
                                        '--extensions-dir', str(base / 'extensions'), '--install-extension', str(archive), '--force'],
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env, timeout=90)
        if installed.returncode: return result
        marker = temporary / 'appearance.json'
        marker.write_text(json.dumps({'version': bundle['version'], 'sha256': bundle['sha256']}))
        marker.replace(base / 'appearance.json')
        result['installed'] = appearance_installed(base, bundle)
        return result
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return result
    finally:
        if temporary: shutil.rmtree(str(temporary), ignore_errors=True)
