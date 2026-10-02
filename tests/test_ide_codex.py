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
    codex=tmp_path/'.codex';codex.mkdir();auth=codex/'auth.json';auth.write_text('SECRET_AUTH');config=codex/'config.toml';config.write_text('SECRET_CONFIG')
    base=tmp_path/'editor';existing_extension(base)
    env={'HOME':str(tmp_path)}
    def forbidden(*a,**kw):raise AssertionError('existing extension must not download')
    monkeypatch.setattr('urllib.request.urlopen',forbidden)
    assert prepare_codex(base,env,Path('/not-invoked'))=={'configured':True,'installed':True}
    assert env['CODEX_HOME']==str(codex)
    assert auth.read_text()=='SECRET_AUTH' and config.read_text()=='SECRET_CONFIG'
    assert not list(base.rglob('auth.json'))


def test_explicit_codex_home_wins_and_obsolete_extension_requires_install(tmp_path,monkeypatch):
    import server.ide_codex as module
    root=tmp_path/'external';root.mkdir();(root/'config.toml').touch()
    base=tmp_path/'editor';old=existing_extension(base)
    (base/'extensions/.obsolete').write_text(json.dumps({old.name:True}))
    calls=[]
    def install(base,binary,env):calls.append(env['CODEX_HOME']);(base/'extensions/.obsolete').unlink()
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
    codex=tmp_path/'.codex';codex.mkdir();(codex/'config.toml').write_text('unchanged')
    def fail(*a):raise OSError('registry unavailable')
    monkeypatch.setattr(module,'install_codex',fail)
    assert module.prepare_codex(tmp_path/'editor',{'HOME':str(tmp_path)},Path('/code-server'))=={'configured':True,'installed':False}
    assert (codex/'config.toml').read_text()=='unchanged'
