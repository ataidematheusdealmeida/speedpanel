"""Check authentication, validation, thresholds, exports and real database persistence."""
import os
import tempfile
import json
os.environ.update(DATA_DIR=tempfile.mkdtemp(),SESSION_SECRET='test-secret-only',ADMIN_PASSWORD='test-password-only',DISABLE_WORKER='1')
import app

def test():
    client=app.app.test_client()
    assert client.get('/api/state').status_code==401
    assert client.post('/api/login',json={'password':'wrong'}).status_code==401
    r=client.post('/api/login',json={'password':'test-password-only'})
    assert r.status_code==200
    headers={'X-CSRF-Token':r.json['csrf']}
    p=app.defaults('Custom API','json','https://example.com/results')
    p['paths']={'download':'download'}
    p['min_download']=100
    p['max_jitter']=10
    assert client.post('/api/providers',json=p).status_code==403
    bad=dict(p,schedules='not cron')
    assert client.post('/api/providers',json=bad,headers=headers).status_code==400
    r=client.post('/api/providers',json=p,headers=headers); assert r.status_code==200
    pid=r.json['id']
    assert client.post('/api/run/'+str(pid),headers=headers).status_code==200
    row=app.db('SELECT * FROM results WHERE provider_id=?',(pid,),one=True)
    assert row['status']=='queued' and json.loads(row['config'])['min_download']==100
    flags=app.violations(p,{'download':50,'jitter':None})
    assert len(flags)==2 and 'indisponivel' in flags[1]
    assert app.number('12,34 Mbps')==12.34
    assert app.number(None) is None
    app.db("UPDATE results SET status='ok', download=50, provider_name='=FORMULA()' WHERE id=?",(row['id'],))
    export=client.get('/api/export').text
    assert "'=FORMULA()" in export
    assert client.delete('/api/providers/'+str(pid),headers=headers).status_code==200
    assert app.db('SELECT * FROM results WHERE id=?',(row['id'],),one=True)
    assert client.post('/api/settings',json={'timezone':'invalid','retention_days':10},headers=headers).status_code==400
    print('Authentication, CSRF, cron validation, persistence, thresholds and CSV: OK')

if __name__=='__main__':test()
