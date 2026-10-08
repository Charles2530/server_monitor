import json
from pathlib import Path
import pytest


def existing_extension(base,version='99.0.0'):
    folder=base/'extensions'/('openai.chatgpt-'+version+'-linux-x64')
    folder.mkdir(parents=True)
    (folder/'package.json').write_text(json.dumps({'publisher':'openai','name':'chatgpt','version':version,'main':'./out/extension.js'}))
    (folder/'out').mkdir();(folder/'out/extension.js').write_text('installed')
    return folder


def test_absent_codex_configuration_does_not_install(tmp_path):
    from server.ide_codex import prepare_codex
    env={'HOME':str(tmp_path)}
    assert prepare_codex(tmp_path/'editor',env,Path('/not-invoked'))=={'configured':False,'installed':False}
    assert not (tmp_path/'editor').exists()


def test_existing_codex_uses_same_home_preserves_credentials_and_newer_extension(tmp_path,monkeypatch):
    from server.ide_codex import prepare_codex
    codex=tmp_path/'.codex';codex.mkdir();auth=codex/'auth.json';auth.write_text('SECRET_AUTH');config=codex/'config.toml';config.write_text('telemetry = false\n')
    base=tmp_path/'editor';existing_extension(base)
    env={'HOME':str(tmp_path)}
    def forbidden(*a,**kw):raise AssertionError('existing extension must not download')
    monkeypatch.setattr('urllib.request.urlopen',forbidden)
    assert prepare_codex(base,env,Path('/not-invoked'))=={'configured':True,'installed':True,'full_access':True}
    assert env['CODEX_HOME']==str(codex)
    assert auth.read_text()=='SECRET_AUTH'
    assert config.read_text().startswith('telemetry = false\n')
    assert 'sandbox_mode = "danger-full-access"' in config.read_text()
    assert not list(base.rglob('auth.json'))


def test_explicit_codex_home_wins_and_obsolete_extension_requires_install(tmp_path,monkeypatch):
    import server.ide_codex as module
    root=tmp_path/'external';root.mkdir();(root/'config.toml').touch()
    base=tmp_path/'editor';old=existing_extension(base)
    (base/'extensions/.obsolete').write_text(json.dumps({old.name:True}))
    calls=[]
    def install(base,binary,env,**kwargs):calls.append(env['CODEX_HOME']);(base/'extensions/.obsolete').unlink()
    monkeypatch.setattr(module,'install_codex',install)
    env={'HOME':str(tmp_path),'CODEX_HOME':str(root)}
    assert module.prepare_codex(base,env,Path('/code-server'))['installed']
    assert calls==[str(root)]


def test_download_hash_failure_never_executes_installer(tmp_path,monkeypatch):
    import server.ide_codex as module
    import io
    base=tmp_path/'editor';base.mkdir()
    monkeypatch.setattr('urllib.request.urlopen',lambda *a,**kw:io.BytesIO(b'tampered archive'))
    def forbidden(*a,**kw):raise AssertionError('installer must not run')
    monkeypatch.setattr('subprocess.run',forbidden)
    with pytest.raises(RuntimeError,match='校验'):module.install_codex(base,Path('/code-server'),{'HOME':str(tmp_path)})
    assert not list(base.glob('codex-install-*'))


def test_unavailable_registry_keeps_editor_usable_and_reports_missing_plugin(tmp_path,monkeypatch):
    import server.ide_codex as module
    codex=tmp_path/'.codex';codex.mkdir();(codex/'config.toml').write_text('unchanged\n')
    def fail(*a,**kw):raise OSError('registry unavailable')
    monkeypatch.setattr(module,'install_codex',fail)
    assert module.prepare_codex(tmp_path/'editor',{'HOME':str(tmp_path)},Path('/code-server'))=={'configured':True,'installed':False,'full_access':False}
    assert (codex/'config.toml').read_text() == 'unchanged\n'


@pytest.mark.parametrize('content', [
    'model = "example"',
    '"approval_policy" = "on-request"\n',
    'instructions = """\n[hello]\ntext\n"""\n',
    'approval_policy = "never"\nsandbox_mode = "danger-full-access"\n',
])
@pytest.mark.parametrize('symlink', [False, True])
def test_editor_setup_preserves_unrelated_values_permissions_and_symlinks(tmp_path, content, symlink):
    from server.ide_codex import prepare_codex
    codex = tmp_path/'.codex'; codex.mkdir()
    config = codex/'config.toml'
    target = tmp_path/'shared-config.toml' if symlink else config
    target.write_text(content); target.chmod(0o600)
    if symlink: config.symlink_to(target)
    original = target.read_bytes(); inode = target.stat().st_ino
    base = tmp_path/'editor'; existing_extension(base)
    result = prepare_codex(base, {'HOME': str(tmp_path)}, Path('/not-invoked'), install=False)
    assert result == {'configured': True, 'installed': True, 'full_access': True}
    import tomli
    assert tomli.loads(target.read_text()) == {**tomli.loads(original.decode()), 'approval_policy': 'never', 'sandbox_mode': 'danger-full-access'}
    if b'danger-full-access' in original:
        assert target.read_bytes() == original
        assert target.stat().st_ino == inode
    assert target.stat().st_mode & 0o777 == 0o600
    assert config.is_symlink() is symlink


def test_full_access_defaults_preserve_other_toml_and_symlink_mode(tmp_path):
    import tomli
    from server.ide_codex import ensure_full_access_defaults
    home = tmp_path / 'home'; home.mkdir()
    target = tmp_path / 'shared.toml'
    original = '''# personal config
model = "example"
instructions = """
[not_a_table]
approval_policy = "inside text"
"""
"approval_policy" = "on-request" # keep this comment
'sandbox_mode' = 'workspace-write'
[profiles.review]
approval_policy = "on-request"
'''
    target.write_text(original); target.chmod(0o640)
    (home / 'config.toml').symlink_to(target)
    assert ensure_full_access_defaults(home)
    updated = target.read_text()
    config = tomli.loads(updated)
    assert config['approval_policy'] == 'never'
    assert config['sandbox_mode'] == 'danger-full-access'
    assert config['instructions'] == tomli.loads(original)['instructions']
    assert config['profiles']['review']['approval_policy'] == 'on-request'
    assert '# keep this comment' in updated
    assert (home / 'config.toml').is_symlink()
    assert target.stat().st_mode & 0o777 == 0o640
    before = target.stat().st_mtime_ns
    assert ensure_full_access_defaults(home)
    assert target.stat().st_mtime_ns == before


@pytest.mark.parametrize('original', [
    '', 'model = "example"', '# comment without newline',
    'instructions = """\n[bracket]\n"""\n[features]\na = true\n',
    'values = [\n"[not_table]",\n]\n[features]\na = true\n',
    'instructions = \'\'\'\n[bracket]\n\'\'\'\n',
])
def test_full_access_defaults_insert_at_root(tmp_path, original):
    import tomli
    from server.ide_codex import ensure_full_access_defaults
    path = tmp_path / 'config.toml'; path.write_text(original)
    before = tomli.loads(original)
    assert ensure_full_access_defaults(tmp_path)
    after = tomli.loads(path.read_text())
    assert after.pop('approval_policy') == 'never'
    assert after.pop('sandbox_mode') == 'danger-full-access'
    assert after == before


def test_full_access_defaults_create_config_for_existing_auth_without_reading_auth(tmp_path):
    import tomli
    from server.ide_codex import prepare_codex
    home = tmp_path / 'home'; home.mkdir()
    (home / 'auth.json').write_bytes(b'\x00UNCHANGED_AUTH')
    base = tmp_path / 'editor'; existing_extension(base)
    result = prepare_codex(base, {'HOME':str(tmp_path), 'CODEX_HOME':str(home)}, Path('/unused'))
    assert result['configured'] and result['installed']
    assert tomli.loads((home / 'config.toml').read_text())['approval_policy'] == 'never'
    assert (home / 'auth.json').read_bytes() == b'\x00UNCHANGED_AUTH'
    assert (home / 'config.toml').stat().st_mode & 0o777 == 0o600


def test_full_access_defaults_do_not_mix_air_and_mini_homes(tmp_path):
    import tomli
    from server.ide_codex import prepare_codex
    for machine in ('air', 'mini'):
        home = tmp_path / machine; home.mkdir()
        (home / 'config.toml').write_text(f'model = "{machine}"\n')
        base = tmp_path / (machine+'-editor'); existing_extension(base)
        prepare_codex(base, {'CODEX_HOME':str(home)}, Path('/unused'), install=False)
        value = tomli.loads((home / 'config.toml').read_text())
        assert value == {'model':machine,'approval_policy':'never','sandbox_mode':'danger-full-access'}


def test_full_access_defaults_reject_malformed_config_without_replacing_it(tmp_path):
    from server.ide_codex import ensure_full_access_defaults, prepare_codex
    home = tmp_path / 'home'; home.mkdir()
    path = home / 'config.toml'
    original = b'approval_policy = [broken\n'
    path.write_bytes(original)
    inode = path.stat().st_ino
    assert ensure_full_access_defaults(home) is False
    assert path.read_bytes() == original
    assert path.stat().st_ino == inode
    result = prepare_codex(tmp_path / 'editor', {'CODEX_HOME': str(home)}, Path('/unused'), install=False)
    assert result == {'configured': True, 'installed': False, 'full_access': False}
