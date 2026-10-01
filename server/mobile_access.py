"""Local administration and hashed, revocable credentials for the optional mobile gateway."""
import hashlib
from contextlib import contextmanager
import re
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlsplit


class AccessError(Exception):
    pass


def gateway_url(value):
    if not isinstance(value,str):raise ValueError('网关地址无效')
    url=urlsplit(value)
    if (url.scheme!='https' or not re.fullmatch(r'[a-z0-9-]+(?:\.[a-z0-9-]+)*\.ts\.net',url.hostname or '')
        or url.username or url.password or url.port not in (None,443)
        or url.path not in ('','/') or url.query or url.fragment):
        raise ValueError('网关必须是 Tailscale Serve 的 https://主机名.ts.net 根地址')
    return 'https://'+url.hostname


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class DeviceStore:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        # Create with restrictive permissions before SQLite writes any credentials.
        self.path.touch(mode=0o600,exist_ok=True);self.path.chmod(0o600)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS pairings(hash TEXT PRIMARY KEY, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS devices(
                id TEXT PRIMARY KEY, name TEXT NOT NULL, hash TEXT UNIQUE NOT NULL,
                state TEXT NOT NULL, can_write INTEGER NOT NULL, created REAL NOT NULL,
                expires REAL NOT NULL, last_seen REAL);
            ''')
            db.execute('INSERT OR IGNORE INTO metadata VALUES(?,?)',('host_id',secrets.token_hex(16)))
            self.host_id=db.execute('SELECT value FROM metadata WHERE key=?',('host_id',)).fetchone()['value']

    @contextmanager
    def connect(self):
        db=sqlite3.connect(self.path,timeout=10)
        db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    def issue_pairing(self,url,*,now=None):
        url=gateway_url(url);now=time.time() if now is None else now;ticket=secrets.token_urlsafe(32)
        with self.connect() as db:
            db.execute('DELETE FROM pairings WHERE expires<=?',(now,))
            db.execute('INSERT INTO pairings VALUES(?,?)',(digest(ticket),now+300))
        return {'version':1,'gateway':url,'ticket':ticket,'expires_at':now+300}

    def redeem(self,ticket,name,*,now=None):
        now=time.time() if now is None else now
        if not isinstance(ticket,str) or len(ticket)!=43 or not isinstance(name,str) or not 1<=len(name.strip())<=80:
            raise AccessError('配对内容或设备名称无效。')
        token=secrets.token_urlsafe(32);device_id=secrets.token_hex(12)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT expires FROM pairings WHERE hash=?',(digest(ticket),)).fetchone()
            if row is None or row['expires']<=now:raise AccessError('配对码已过期或已使用，请在电脑重新生成。')
            db.execute('DELETE FROM pairings WHERE hash=?',(digest(ticket),))
            db.execute('INSERT INTO devices VALUES(?,?,?,?,?,?,?,NULL)',(device_id,name.strip(),digest(token),'pending',0,now,now+600))
        return {'device_id':device_id,'token':token,'state':'pending','host_id':self.host_id}

    def status(self,token,*,now=None):
        now=time.time() if now is None else now
        if not isinstance(token,str) or len(token)!=43:raise AccessError('手机凭证无效，请重新配对。')
        with self.connect() as db:
            row=db.execute('SELECT * FROM devices WHERE hash=?',(digest(token),)).fetchone()
            if row is None or row['state']=='revoked' or row['expires']<=now:
                raise AccessError('手机已撤销或凭证已过期，请重新配对。')
            db.execute('UPDATE devices SET last_seen=? WHERE id=?',(now,row['id']))
        return {**{k:row[k] for k in ('id','name','state','can_write','expires')},'host_id':self.host_id}

    def authorize(self,token,*,write=False,now=None):
        device=self.status(token,now=now)
        if device['state']!='approved':raise AccessError('请先在电脑批准这部手机。')
        if write and not device['can_write']:raise AccessError('这部手机只有查看权限，请在电脑批准任务管理权限。')
        return device

    def approve(self,device_id,*,write=False,now=None):
        now=time.time() if now is None else now
        with self.connect() as db:
            result=db.execute("UPDATE devices SET state='approved',can_write=?,expires=? WHERE id=? AND state!='revoked' AND expires>?",(int(write),now+90*86400,device_id,now))
            if result.rowcount!=1:raise AccessError('设备不存在、已撤销或已过期。')

    def revoke(self,device_id):
        with self.connect() as db:
            if db.execute("UPDATE devices SET state='revoked' WHERE id=?",(device_id,)).rowcount!=1:raise AccessError('设备不存在。')

    def devices(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT id,name,state,can_write,created,expires,last_seen FROM devices ORDER BY created DESC')]
