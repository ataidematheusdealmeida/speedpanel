"""Independent, bounded uptime probes and duration-based availability."""
import concurrent.futures
import ipaddress
import json
import math
import re
import socket
import subprocess
import threading
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from flask import request, jsonify


def validate(body):
    if not isinstance(body, dict):
        raise ValueError('Configuração inválida.')
    p = dict(name='', kind='http', target='', port=443, interval=60, timeout=5,
             failures=3, expected=200, slow_ms=1000, enabled=True, maintenance_from=0, maintenance_until=0)
    p.update({k: v for k, v in body.items() if k in p})
    for key in ('name', 'target'):
        if not isinstance(p[key], str) or not p[key].strip() or len(p[key]) > (100 if key == 'name' else 2000):
            raise ValueError('Nome ou destino inválido.')
        p[key] = p[key].strip()
    if p['kind'] not in ('http', 'tcp', 'ping') or type(p['enabled']) is not bool:
        raise ValueError('Tipo ou ativação inválidos.')
    for k, lo, hi in [('port', 1, 65535), ('interval', 10, 86400), ('timeout', 1, 30),
                      ('failures', 1, 10), ('expected', 100, 599), ('slow_ms', 0, 60000),
                      ('maintenance_from', 0, time.time() + 366*86400),
                      ('maintenance_until', 0, time.time() + 366*86400)]:
        try: n = float(p[k])
        except (TypeError, ValueError): raise ValueError('Valor inválido: ' + k)
        if not math.isfinite(n) or not lo <= n <= hi or n != int(n):
            raise ValueError('Valor fora do intervalo: ' + k)
        p[k] = int(n)
    if p['maintenance_from'] and p['maintenance_until'] <= p['maintenance_from']:
        raise ValueError('O término da manutenção deve ser posterior ao início.')
    if p['timeout'] >= p['interval']:
        raise ValueError('O tempo limite deve ser menor que o intervalo.')
    if p['kind'] == 'http':
        try:
            u = urlsplit(p['target'])
            valid = u.scheme in ('http', 'https') and u.hostname and not u.username and not u.password
            u.port
        except ValueError: valid = False
        if not valid: raise ValueError('Informe uma URL HTTP(S) sem credenciais.')
    else:
        host = p['target']
        try: ipaddress.ip_address(host)
        except ValueError:
            if len(host) > 253 or not re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?', host):
                raise ValueError('Informe somente um IP ou nome de host.')
    return p


# DNS and HTTP redirects run in a child process so the entire probe has a hard deadline.
PROBE = r'''
import json, socket, sys, time
from urllib.request import Request, urlopen
from urllib.error import HTTPError
p=json.loads(sys.argv[1]); start=time.monotonic()
try:
    if p['kind']=='tcp':
        with socket.create_connection((p['target'],p['port']),timeout=p['timeout']): pass
        code=None
    else:
        try:
            with urlopen(Request(p['target'],headers={'User-Agent':'SpeedPanel-Uptime/1.0'}),timeout=p['timeout']) as r: code=r.status
        except HTTPError as e: code=e.code; e.close()
        if code!=p['expected']: raise ValueError('HTTP '+str(code)+'; esperado '+str(p['expected']))
    print(json.dumps(dict(ok=True,ms=(time.monotonic()-start)*1000,error='',code=code)))
except Exception as e:
    print(json.dumps(dict(ok=False,ms=None,error=str(e)[:500],code=None)))
'''


def probe(p):
    import sys
    try:
        if p['kind'] == 'ping':
            start = time.monotonic()
            r = subprocess.run(['ping', '-n', '-c', '1', '-W', str(p['timeout']), '--', p['target']],
                               capture_output=True, text=True, timeout=p['timeout'] + 1)
            match = re.search(r'time[=<]([\d.]+)', r.stdout)
            return dict(ok=r.returncode == 0, ms=float(match[1]) if match else None,
                        error='' if r.returncode == 0 else (r.stderr.strip() or 'Sem resposta ICMP.')[:500], code=None)
        r = subprocess.run([sys.executable, '-c', PROBE, json.dumps(p)],
                           capture_output=True, text=True, timeout=p['timeout'])
        if r.returncode: raise RuntimeError(r.stderr[-500:])
        return json.loads(r.stdout)
    except subprocess.TimeoutExpired:
        return dict(ok=False, ms=None, error='Tempo limite excedido.', code=None)
    except Exception as e:
        return dict(ok=False, ms=None, error=str(e)[:500], code=None)


def in_maintenance(p, now=None):
    now=time.time() if now is None else now
    return p.get('maintenance_from',0) <= now < p['maintenance_until']


def segments(checks, start, end):
    """Never extend a sample over collection gaps; pause/maintenance are explicit."""
    out = []
    cursor = start
    for i, c in enumerate(checks):
        a = max(start, c['created'])
        b = min(end, c['valid_until'], checks[i+1]['created'] if i+1 < len(checks) else end)
        if b <= a: continue
        if a > cursor: out.append(dict(start=cursor, end=a, state='unknown'))
        out.append(dict(start=a, end=b, state=c['state']))
        cursor = b
    if cursor < end: out.append(dict(start=cursor, end=end, state='unknown'))
    merged = []
    for s in out:
        if merged and merged[-1]['state'] == s['state'] and abs(merged[-1]['end'] - s['start']) < .001:
            merged[-1]['end'] = s['end']
        else: merged.append(dict(s))
    return merged


def summarize(parts):
    totals = {k: 0 for k in ('up', 'down', 'unknown', 'pending', 'maintenance', 'paused')}
    for s in parts: totals[s['state']] += s['end'] - s['start']
    known = totals['up'] + totals['down']
    incidents = [s for s in parts if s['state'] == 'down']
    # Only a subsequent observed up segment confirms recovery.
    recovered = [s['end']-s['start'] for i,s in enumerate(parts)
                 if s['state']=='down' and i+1<len(parts) and parts[i+1]['state']=='up']
    return dict(seconds=totals, availability=100*totals['up']/known if known else None,
                coverage=100*known/sum(totals.values()) if sum(totals.values()) else 0,
                incidents=len(incidents), longest=max((s['end']-s['start'] for s in incidents), default=0),
                mttr=sum(recovered)/len(recovered) if recovered else None)


def install(app, db, guard, settings):
    db('CREATE TABLE IF NOT EXISTS monitors (id INTEGER PRIMARY KEY, value TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0)')
    db('''CREATE TABLE IF NOT EXISTS uptime_checks (id INTEGER PRIMARY KEY, monitor_id INTEGER,
           created REAL, valid_until REAL, state TEXT, ok INTEGER, ms REAL, error TEXT, code INTEGER)''')
    db('CREATE INDEX IF NOT EXISTS uptime_monitor_time ON uptime_checks(monitor_id,created)')
    lock = threading.RLock()
    inflight = set()
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix='uptime')

    def monitors(deleted=False):
        return [dict(json.loads(r['value']), id=r['id'], deleted=bool(r['deleted']))
                for r in db('SELECT * FROM monitors' + ('' if deleted else ' WHERE deleted=0'))]

    def marker(p, state):
        now = time.time()
        until = p['maintenance_until'] if state == 'maintenance' else (now+36600*86400 if state == 'paused' else now)
        db('INSERT INTO uptime_checks(monitor_id,created,valid_until,state) VALUES (?,?,?,?)', (p['id'],now,until,state))

    @app.get('/api/uptime/monitors')
    @guard
    def list_monitors():
        ps = monitors(True)
        for p in ps:
            last = db('SELECT * FROM uptime_checks WHERE monitor_id=? ORDER BY created DESC,id DESC LIMIT 1',(p['id'],),one=True)
            p['last'] = last
            p['status'] = last['state'] if last and last['valid_until'] > time.time() else 'unknown'
        return jsonify(monitors=ps)

    @app.post('/api/uptime/monitors')
    @guard
    def save_monitor():
        body = request.get_json(); p = validate(body)
        with lock:
            pid = body.get('id')
            if pid:
                old = next((m for m in monitors() if m['id']==int(pid)), None)
                if not old: raise ValueError('Monitor não encontrado.')
                db('UPDATE monitors SET value=? WHERE id=?',(json.dumps(p),int(pid)))
            else:
                if len(monitors()) >= 100: raise ValueError('Limite de 100 monitores.')
                pid = db('INSERT INTO monitors(value) VALUES (?)',(json.dumps(p),))
            p['id'] = int(pid)
            marker(p, 'paused' if not p['enabled'] else 'maintenance' if in_maintenance(p) else 'unknown')
        return jsonify(id=int(pid))

    @app.delete('/api/uptime/monitors/<int:pid>')
    @guard
    def delete_monitor(pid):
        with lock:
            p = next((m for m in monitors() if m['id']==pid), None)
            if not p: raise ValueError('Monitor não encontrado.')
            marker(p, 'unknown')
            db('UPDATE monitors SET deleted=1 WHERE id=?',(pid,))
        return jsonify(ok=True)

    @app.post('/api/uptime/test/<int:pid>')
    @guard
    def test_monitor(pid):
        p = next((m for m in monitors() if m['id']==pid), None)
        if not p: raise ValueError('Monitor não encontrado.')
        with lock:
            if pid in inflight: raise ValueError('Verificação já em andamento.')
            if len(inflight)>=8: raise ValueError('Verificações ocupadas. Tente novamente em instantes.')
            inflight.add(pid)
        try: return jsonify(probe(p))
        finally:
            with lock: inflight.discard(pid)

    def record(p, result, now):
        last = db('SELECT * FROM uptime_checks WHERE monitor_id=? ORDER BY created DESC,id DESC LIMIT 1', (p['id'],),one=True)
        contiguous = last and last['valid_until'] >= now and last['state'] in ('up','down','pending')
        state = 'up' if result['ok'] else 'pending'
        if not result['ok']:
            recent = db('SELECT * FROM uptime_checks WHERE monitor_id=? ORDER BY created DESC,id DESC LIMIT ?', (p['id'],p['failures']))
            failures = []
            edge = now
            for r in recent:
                if r['ok'] != 0 or r['valid_until'] < edge or r['state'] not in ('pending','down'): break
                failures.append(r); edge = r['created']
            if (contiguous and last['state']=='down') or len(failures)+1 >= p['failures']:
                state = 'down'
                if failures:
                    db("UPDATE uptime_checks SET state='down' WHERE monitor_id=? AND created>=? AND state='pending'",(p['id'],failures[-1]['created']))
        db('INSERT INTO uptime_checks(monitor_id,created,valid_until,state,ok,ms,error,code) VALUES (?,?,?,?,?,?,?,?)',
           (p['id'],now,now+p['interval']*1.5+p['timeout'],state,int(result['ok']),result['ms'],result['error'],result.get('code')))

    def run(p):
        try:
            result = probe(p)
            with lock:
                current = next((m for m in monitors() if m['id']==p['id']), None)
                if current == p and current['enabled'] and not in_maintenance(current):
                    record(p,result,time.time())
        except Exception: app.logger.exception('Falha no monitor de uptime')
        finally:
            with lock: inflight.discard(p['id'])

    def work():
        cleanup = 0
        while True:
            try:
                now = time.time()
                with lock:
                    for p in monitors():
                        if not p['enabled']: continue
                        last = db('SELECT created,state FROM uptime_checks WHERE monitor_id=? ORDER BY created DESC,id DESC LIMIT 1',(p['id'],),one=True)
                        if in_maintenance(p,now):
                            if not last or last['state']!='maintenance': marker(p,'maintenance')
                            continue
                        if p['id'] in inflight: continue
                        if len(inflight)<8 and (not last or last['state'] in ('unknown','paused','maintenance') or now-last['created']>=p['interval']):
                            inflight.add(p['id']); pool.submit(run,p)
                if now-cleanup>3600:
                    # Keep one predecessor per monitor so retained windows have their initial state.
                    cutoff=now-settings()['retention_days']*86400
                    db('DELETE FROM uptime_checks WHERE created<? AND id NOT IN (SELECT MAX(id) FROM uptime_checks WHERE created<? GROUP BY monitor_id)',(cutoff,cutoff))
                    cleanup=now
            except Exception: app.logger.exception('Erro no agendador de uptime')
            time.sleep(1)

    def history(pid, start, end):
        before=db('SELECT * FROM uptime_checks WHERE monitor_id=? AND created<? ORDER BY created DESC,id DESC LIMIT 1',(pid,start),one=True)
        checks=db('SELECT * FROM uptime_checks WHERE monitor_id=? AND created>=? AND created<=? ORDER BY created,id',(pid,start,end))
        return ([before] if before else [])+checks

    def build_report(start,end,pid=''):
        ps=monitors(True)
        if pid: ps=[p for p in ps if p['id']==int(pid)]
        output=[]
        for p in ps:
            checks=history(p['id'],min(start,end-30*86400),end)
            parts=segments(checks,start,end)
            windows={str(d):summarize(segments(checks,end-d*86400,end)) for d in (1,7,30)}
            incidents=[]
            for i,s in enumerate(parts):
                if s['state']!='down': continue
                next_state=parts[i+1]['state'] if i+1<len(parts) else None
                incidents.append(dict(s,duration=s['end']-s['start'], outcome='recovered' if next_state=='up' else 'open' if next_state is None else 'unobserved', clipped=s['start']==start))
            # Hourly buckets bound payloads without hiding the original aggregate calculations.
            buckets={}
            for c in checks:
                if c['created']<start or c['ms'] is None: continue
                key=int(c['created']//3600)*3600
                b=buckets.setdefault(key,dict(created=key,total=0,count=0,maximum=0,slow=0))
                b['total']+=c['ms']; b['count']+=1; b['maximum']=max(b['maximum'],c['ms'])
                b['slow']+=int(p['slow_ms']>0 and c['ms']>p['slow_ms'])
            points=[dict(created=b['created'],ms=b['total']/b['count'],maximum=b['maximum'],count=b['count'],slow=b['slow']) for b in buckets.values()]
            config={k:p[k] for k in ('kind','target','port','interval','timeout','failures','slow_ms')}
            output.append(dict(id=p['id'],name=p['name'],deleted=p['deleted'],config=config,windows=windows,summary=summarize(parts),segments=parts,incidents=incidents,points=points))
        return dict(start=start,end=end,monitors=output)

    @app.get('/api/uptime/report')
    @guard
    def uptime_report():
        days=int(request.args.get('days',7))
        if days not in (1,7,30): raise ValueError('Período inválido.')
        end=time.time()
        return jsonify(build_report(end-days*86400,end,request.args.get('monitor','')))

    worker=threading.Thread(target=work,daemon=True,name='uptime-scheduler')
    import os
    if os.environ.get('DISABLE_WORKER')!='1': worker.start()
    return dict(monitors=monitors,history=history,record=record,worker=worker,build=build_report)
