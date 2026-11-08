"""Regression checks with isolated SQLite, deterministic outages, and local probes."""
import json
import os
import socket
import sqlite3
import tempfile
import threading
import time
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

os.environ.update(DATA_DIR=tempfile.mkdtemp(), SESSION_SECRET='monitor-test-secret',
                  ADMIN_PASSWORD='monitor-test-password', DISABLE_WORKER='1')
import app
from analytics import percentile
from uptime import probe, segments, summarize, validate, in_maintenance


def test():
    client=app.app.test_client()
    for path in ('/api/uptime/monitors','/api/uptime/report','/api/analysis','/api/analysis/settings'):
        assert client.get(path).status_code==401, path
    login=client.post('/api/login',json={'password':'monitor-test-password'})
    headers={'X-CSRF-Token':login.json['csrf']}
    p=validate(dict(name='Local HTTP',kind='http',target='http://127.0.0.1',interval=10,timeout=1,failures=3))
    assert client.post('/api/uptime/monitors',json=p).status_code==403
    for changes in ({'kind':'shell'},{'target':'file:///etc/passwd'}, {'target':'https://user:pass@example.com'},
                    {'interval':1},{'timeout':10},{'enabled':'false'},{'failures':1.5}, {'slow_ms':float('nan')}):
        assert client.post('/api/uptime/monitors',json=dict(p,**changes),headers=headers).status_code==400, changes
    assert client.post('/api/uptime/monitors',json=dict(p,kind='ping',target='-c 100 localhost'),headers=headers).status_code==400
    pid=client.post('/api/uptime/monitors',json=p,headers=headers).json['id']
    p=next(x for x in app.uptime_service['monitors']() if x['id']==pid)
    app.db('DELETE FROM uptime_checks WHERE monitor_id=?',(pid,))
    now=time.time(); base=now-200
    up=dict(ok=True,ms=12,error='',code=200); down=dict(ok=False,ms=None,error='timeout',code=None)
    record=app.uptime_service['record']
    record(p,up,base)
    record(p,down,base+10); record(p,down,base+20)
    assert app.db('SELECT state FROM uptime_checks ORDER BY created DESC LIMIT 1',one=True)['state']=='pending'
    record(p,down,base+30)
    failures=app.db('SELECT state FROM uptime_checks WHERE ok=0')
    assert len(failures)==3 and all(c['state']=='down' for c in failures)
    record(p,up,base+40)
    parts=segments(app.uptime_service['history'](pid,base,base+50),base,base+50)
    summary=summarize(parts)
    assert summary['seconds']['down']==30 and summary['seconds']['up']==20
    assert summary['availability']==40 and summary['mttr']==30 and summary['incidents']==1
    # Collection gaps cannot be reported as availability or confirmed recovery.
    parts=segments(app.uptime_service['history'](pid,base,now),base,now)
    assert summarize(parts)['seconds']['unknown']>100
    record(p,down,base+150)
    assert app.db('SELECT state FROM uptime_checks ORDER BY created DESC LIMIT 1',one=True)['state']=='pending'
    report=client.get('/api/uptime/report?monitor='+str(pid)).json['monitors'][0]
    assert report['summary']['incidents']==1 and report['windows']['1']['coverage']<1
    assert client.get('/api/uptime/report?days=2').status_code==400
    assert client.post('/api/uptime/monitors',json=dict(p,enabled=False),headers=headers).status_code==200
    assert client.get('/api/uptime/monitors').json['monitors'][0]['status']=='paused'
    assert client.post('/api/uptime/monitors',json=dict(p,maintenance_until=int(now)+3600),headers=headers).status_code==200
    assert client.get('/api/uptime/monitors').json['monitors'][0]['status']=='maintenance'
    future=validate(dict(p,maintenance_from=int(now)+600,maintenance_until=int(now)+1200))
    future['id']=pid
    assert not in_maintenance(future,now) and in_maintenance(future,now+700) and not in_maintenance(future,now+1300)
    assert client.post('/api/uptime/monitors',json=future,headers=headers).status_code==200
    assert client.get('/api/uptime/monitors').json['monitors'][0]['status']=='unknown'
    assert client.post('/api/uptime/monitors',json=dict(future,maintenance_until=int(now)+500),headers=headers).status_code==400
    # Predecessor samples are clipped, and downtime ending in unknown is not a recovery.
    s=segments([dict(created=0,valid_until=20,state='down'),dict(created=100,valid_until=120,state='up')],10,110)
    assert summarize(s)['seconds']==dict(up=10,down=10,unknown=80,pending=0,maintenance=0,paused=0)
    assert summarize(s)['mttr'] is None
    assert percentile([1,2,3,4,5],.95)==4.8
    provider=app.providers()[0]
    with sqlite3.connect(app.DATA+'/speedtest.db') as c:
        c.executemany('INSERT INTO results(provider_id,provider_name,created,status,download,upload,ping,jitter,loss) VALUES (?,?,?,?,?,?,?,?,?)',[
            (provider['id'],provider['name'],base+i,'ok',v,10,v/10,None,0) for i,v in enumerate((50,100,150))])
        c.execute("INSERT INTO results(provider_id,created,status) VALUES (?,?,'error')",(provider['id'],base+11))
    assert client.post('/api/analysis/settings',json=dict(download=100,upload=20,target_pct=80),headers=headers).status_code==200
    data=client.get('/api/analysis').json
    assert data['metrics']['download']['median']==100 and data['metrics']['jitter']['count']==0
    assert data['targets']['download']['below']==1 and data['targets']['download']['delivered_pct']==100
    assert data['targets']['upload']['below']==3
    assert data['errors']==1 and sum(c['tests'] for c in data['heatmap'])==4
    assert data['correlation'][0]['groups']['down']['errors']==1
    assert data['approximate_timestamps']==4
    # A queued test must correlate at execution start rather than queue entry.
    app.db("UPDATE results SET started=? WHERE status='error'",(base+1,))
    precise=client.get('/api/analysis').json
    assert precise['correlation'][0]['groups']['up']['errors']==1
    assert precise['correlation'][0]['groups']['down']['errors']==0
    assert precise['approximate_timestamps']==3
    assert client.get('/api/analysis?from=2100-01-01T00:00:00Z').json['completed']==0
    assert client.get('/api/analysis?provider=9999').json['completed']==0
    assert client.post('/api/analysis/settings',json=dict(download=-1,upload=0,target_pct=80),headers=headers).status_code==400
    # Full aggregates must not silently inherit the UI's 10k row cap.
    with sqlite3.connect(app.DATA+'/speedtest.db') as c:
        c.executemany("INSERT INTO results(created,status,download) VALUES (?,'ok',100)",[(base,)]*10001)
    assert client.get('/api/analysis').json['completed']==10004
    assert client.delete('/api/uptime/monitors/'+str(pid),headers=headers).status_code==200
    assert not app.uptime_service['monitors']()
    assert client.get('/api/uptime/report').json['monitors'][0]['summary']['incidents']==1
    # Real network probes against a local service, no dependence on internet connectivity.
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self): self.send_response(204 if self.path=='/ok' else 503); self.end_headers()
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    url='http://127.0.0.1:'+str(server.server_port)
    assert probe(dict(p,target=url+'/ok',expected=204))['ok']
    assert not probe(dict(p,target=url+'/fail',expected=200))['ok']
    assert probe(dict(p,kind='tcp',target='127.0.0.1',port=server.server_port))['ok']
    server.shutdown(); server.server_close()
    assert not probe(dict(p,kind='tcp',target='127.0.0.1',port=server.server_port))['ok']
    import subprocess
    with patch('uptime.subprocess.run',side_effect=subprocess.TimeoutExpired('probe',1)):
        assert not probe(p)['ok']
    if sys.platform.startswith('linux'):
        assert probe(dict(p,kind='ping',target='127.0.0.1'))['ok'], 'ICMP unavailable for the runtime user'
    print('Uptime: auth/CSRF, validation, thresholds, durations, gaps, maintenance, archive and real HTTP/TCP: OK')
    print('Analytics: percentiles, targets, filters, missing metrics, heatmap, correlation and >10k rows: OK')

if __name__=='__main__': test()
