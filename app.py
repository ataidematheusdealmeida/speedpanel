import csv
import io
import json
import math
import os
import queue
import re
import secrets
import sqlite3
import subprocess
import sys
import signal
import threading
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from functools import wraps
from urllib.parse import urlparse

from croniter import croniter
from flask import Flask, request, session, jsonify, send_from_directory, Response
from werkzeug.security import generate_password_hash, check_password_hash

DATA = os.environ.get('DATA_DIR', '/data')
os.makedirs(DATA, exist_ok=True)
app = Flask(__name__, static_folder='static')
app.config.update(SECRET_KEY=os.environ['SESSION_SECRET'], MAX_CONTENT_LENGTH=65536,
                  SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict')
db_lock = threading.Lock()

def db(sql, args=(), one=False):
    with db_lock, sqlite3.connect(DATA + '/speedtest.db', timeout=30) as c:
        c.row_factory = sqlite3.Row
        cur = c.execute(sql, args)
        if cur.description:
            rows = [dict(r) for r in cur.fetchall()]
            return (rows[0] if rows else None) if one else rows
        return cur.lastrowid

db('CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, value TEXT NOT NULL)')
db('CREATE TABLE IF NOT EXISTS providers (id INTEGER PRIMARY KEY, value TEXT NOT NULL)')
db('''CREATE TABLE IF NOT EXISTS results (id INTEGER PRIMARY KEY, provider_id INTEGER,
    provider_name TEXT, created REAL, status TEXT, download REAL, upload REAL, ping REAL,
    jitter REAL, loss REAL, isp TEXT, ip TEXT, server TEXT, error TEXT, flags TEXT, raw TEXT,
    config TEXT, scheduled TEXT UNIQUE)''')
db('CREATE INDEX IF NOT EXISTS results_created ON results(created)')
if not db('SELECT id FROM settings WHERE id=1', one=True):
    db('INSERT INTO settings VALUES (1,?)', (json.dumps(dict(timezone='America/Sao_Paulo', retention_days=365,
        password=generate_password_hash(os.environ['ADMIN_PASSWORD']))),))

def settings():
    return json.loads(db('SELECT value FROM settings WHERE id=1', one=True)['value'])

def providers():
    return [dict(json.loads(r['value']), id=r['id']) for r in db('SELECT * FROM providers')]

def defaults(name, engine, url):
    return dict(name=name, engine=engine, url=url, enabled=False, schedules='7 */6 * * *', timeout=240,
                retries=0, min_download=0, min_upload=0, max_ping=0, max_jitter=0, max_loss=0,
                server_id='', interface='', selectors={}, start_selectors=[], done_selector='', paths={})

if not providers():
    for p in [defaults('Speedtest / Ookla','ookla','https://www.speedtest.net/pt'),
              defaults('Fast.com / Netflix','fast','https://fast.com/pt/'),
              defaults('Brasil Banda Larga / ESAQ','brasil','https://www.brasilbandalarga.com.br/')]:
        db('INSERT INTO providers(value) VALUES (?)', (json.dumps(p),))

def guard(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not session.get('auth'):
            return jsonify(error='Entre para continuar.'), 401
        if request.method not in ('GET','HEAD') and not secrets.compare_digest(request.headers.get('X-CSRF-Token',''), session.get('csrf','missing')):
            return jsonify(error='Sessao expirada. Atualize a pagina.'), 403
        return fn(*args, **kwargs)
    return wrapped

@app.errorhandler(ValueError)
def invalid(e): return jsonify(error=str(e)), 400

@app.get('/health')
def health(): return jsonify(status='ok', worker=worker.is_alive())

@app.get('/')
def index(): return send_from_directory('static','index.html')

attempts = {}
@app.post('/api/login')
def login():
    key = request.remote_addr
    now = time.time()
    with db_lock:
        attempts[key] = [t for t in attempts.get(key,[]) if now-t<300]
        if len(attempts[key]) >= 10:
            return jsonify(error='Aguarde 5 minutos antes de tentar novamente.'),429
        attempts[key].append(now)
    if not check_password_hash(settings()['password'], str((request.get_json() or {}).get('password',''))):
        return jsonify(error='Senha incorreta.'),401
    session.clear(); session.update(auth=True,csrf=secrets.token_hex(24))
    return jsonify(csrf=session['csrf'])

@app.post('/api/logout')
@guard
def logout(): session.clear(); return jsonify(ok=True)

@app.get('/api/state')
@guard
def state():
    s=settings(); s.pop('password')
    tz=ZoneInfo(s['timezone']); now=datetime.now(tz)
    ps=providers()
    for p in ps:
        p['next_run']=min([croniter(x.strip(),now).get_next(datetime).isoformat() for x in p['schedules'].split(';') if x.strip()],default=None) if p['enabled'] else None
    return jsonify(csrf=session['csrf'],providers=ps,settings=s,
                   pending=db("SELECT COUNT(*) AS n FROM results WHERE status IN ('queued','running')",one=True)['n'])

def validate_provider(p):
    if not isinstance(p,dict): raise ValueError('Configuracao invalida.')
    base=defaults('','browser',''); base.update({k:v for k,v in p.items() if k in base})
    if not isinstance(base['name'],str) or not base['name'].strip() or len(base['name'])>100: raise ValueError('Informe um nome com ate 100 caracteres.')
    if base['engine'] not in ('ookla','fast','brasil','browser','json'): raise ValueError('Motor invalido.')
    u=urlparse(base['url'])
    if u.scheme not in ('https','http') or not u.hostname or u.username: raise ValueError('Informe uma URL HTTP(S) valida.')
    if type(base['enabled']) is not bool: raise ValueError('Ativo deve ser verdadeiro ou falso.')
    for k,lo,hi in [('timeout',30,600),('retries',0,3),('min_download',0,100000),('min_upload',0,100000),('max_ping',0,60000),('max_jitter',0,60000),('max_loss',0,100)]:
        n=float(base[k])
        if not math.isfinite(n) or not lo<=n<=hi: raise ValueError('Valor fora do intervalo: '+k)
        base[k]=int(n) if k in ('timeout','retries') else n
    if not isinstance(base['schedules'],str) or len(base['schedules'])>1000: raise ValueError('Agendamento invalido.')
    for c in filter(None,(v.strip() for v in base['schedules'].split(';'))):
        if len(c.split())!=5 or not croniter.is_valid(c): raise ValueError('Cron invalido: '+c)
        try: croniter(c,datetime.now(timezone.utc)).get_next(datetime)
        except Exception: raise ValueError('Cron sem proxima execucao valida: '+c)
    if base['enabled'] and not base['schedules'].strip(): raise ValueError('Defina ao menos um horario.')
    if base['server_id'] and not re.fullmatch(r'\d+',str(base['server_id'])): raise ValueError('ID de servidor deve ser numerico.')
    if not isinstance(base['interface'],str) or len(base['interface'])>64: raise ValueError('Interface invalida.')
    if not isinstance(base['selectors'],dict) or not isinstance(base['paths'],dict) or not isinstance(base['start_selectors'],list): raise ValueError('Seletores/caminhos devem ser objetos e cliques uma lista.')
    for k in ('selectors','paths'):
        if any(not isinstance(v,str) or len(v)>500 for v in base[k].values()): raise ValueError('Caminho ou seletor invalido.')
    if any(not isinstance(v,str) or len(v)>500 for v in base['start_selectors']): raise ValueError('Cliques invalidos.')
    if len(base['start_selectors'])>10: raise ValueError('Use no maximo dez cliques.')
    if not isinstance(base['done_selector'],str) or len(base['done_selector'])>500: raise ValueError('Seletor de conclusao invalido.')
    if base['engine']=='browser' and (not base['done_selector'] or not base['selectors'].get('download')): raise ValueError('Informe seletor de conclusao e de download para o site personalizado.')
    if base['engine']=='json' and not base['paths'].get('download'): raise ValueError('Informe o caminho JSON de download (em Mbps).')
    return base

@app.post('/api/providers')
@guard
def save_provider():
    body=request.get_json(); p=validate_provider(body); pid=body.get('id')
    if pid:
        if not any(x['id']==int(pid) for x in providers()): raise ValueError('Servico nao encontrado.')
        db('UPDATE providers SET value=? WHERE id=?',(json.dumps(p),int(pid)))
    else: pid=db('INSERT INTO providers(value) VALUES (?)',(json.dumps(p),))
    return jsonify(id=pid)

@app.delete('/api/providers/<int:pid>')
@guard
def delete_provider(pid):
    db('DELETE FROM providers WHERE id=?',(pid,))
    return jsonify(ok=True)

@app.post('/api/settings')
@guard
def save_settings():
    body=request.get_json(); s=settings()
    try: ZoneInfo(body['timezone'])
    except Exception: raise ValueError('Fuso horario invalido.')
    days=int(body['retention_days'])
    if not 1<=days<=36500: raise ValueError('Retencao deve ficar entre 1 e 36500 dias.')
    s.update(timezone=body['timezone'],retention_days=days)
    if body.get('password'):
        if len(body['password'])<12: raise ValueError('Use uma senha com ao menos 12 caracteres.')
        s['password']=generate_password_hash(body['password'])
    db('UPDATE settings SET value=? WHERE id=1',(json.dumps(s),))
    return jsonify(ok=True)

def enqueue(p, scheduled=None):
    with db_lock,sqlite3.connect(DATA+'/speedtest.db',timeout=30) as c:
        if c.execute("SELECT COUNT(*) FROM results WHERE status IN ('queued','running')").fetchone()[0]>=100:
            raise ValueError('Fila cheia. Aguarde a conclusao dos testes.')
        c.execute("INSERT OR IGNORE INTO results(provider_id,provider_name,created,status,config,scheduled) VALUES (?,?,?,'queued',?,?)",(p['id'],p['name'],time.time(),json.dumps(p),scheduled))

@app.post('/api/run/<int:pid>')
@guard
def run(pid):
    p=next((p for p in providers() if p['id']==pid),None)
    if not p: raise ValueError('Servico nao encontrado.')
    enqueue(p)
    return jsonify(ok=True)

def filter_results():
    start=request.args.get('from',''); end=request.args.get('to',''); pid=request.args.get('provider','')
    where=['created>=?']; args=[datetime.fromisoformat(start.replace('Z','+00:00')).timestamp() if start else time.time()-7*86400]
    if end: where.append('created<=?'); args.append(datetime.fromisoformat(end.replace('Z','+00:00')).timestamp())
    if end and args[0]>args[1]: raise ValueError('A data inicial deve ser anterior ou igual a data final.')
    if pid: where.append('provider_id=?'); args.append(int(pid))
    return ' AND '.join(where),args

@app.get('/api/results')
@guard
def results():
    where,args=filter_results()
    rows=db('SELECT id,provider_id,provider_name,created,status,download,upload,ping,jitter,loss,isp,ip,server,error,flags FROM results WHERE '+where+' ORDER BY created DESC LIMIT 10000',args)
    count=db('SELECT COUNT(*) AS n FROM results WHERE '+where,args,one=True)['n']
    return jsonify(results=rows,total=count,truncated=count>len(rows))

@app.get('/api/export')
@guard
def export():
    where,args=filter_results()
    rows=db('SELECT id,provider_name,created,status,download,upload,ping,jitter,loss,isp,ip,server,error,flags FROM results WHERE '+where+' ORDER BY created',args)
    out=io.StringIO(); w=csv.writer(out); w.writerow(['id','servico','data_UTC','status','download_Mbps','upload_Mbps','ping_ms','jitter_ms','perda_pct','operadora','IP','servidor','erro','regras'])
    for r in rows:
        r['created']=datetime.fromtimestamp(r['created'],timezone.utc).isoformat()
        w.writerow([("'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v) for v in r.values()])
    return Response('\ufeff'+out.getvalue(),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=resultados.csv'})

@app.get('/api/report.<kind>')
@guard
def report(kind):
    if kind not in ('pdf','xlsx'): raise ValueError('Formato de relatorio invalido.')
    from reports import make_pdf, make_xlsx
    where,args=filter_results()
    rows=db('SELECT * FROM results WHERE '+where+' ORDER BY created,id',args)
    tz=ZoneInfo(settings()['timezone']); now=datetime.now(tz)
    start=datetime.fromtimestamp(args[0],tz)
    end=datetime.fromtimestamp(args[1],tz) if request.args.get('to') else now
    pid=request.args.get('provider')
    selected=next((p['name'] for p in providers() if str(p['id'])==pid),None)
    if pid and not selected:
        selected=rows[0]['provider_name'] if rows else 'Servico '+pid
    context=dict(timezone=str(tz),start=start,end=end,generated=now,service=selected or 'Todos os servicos')
    data=(make_pdf if kind=='pdf' else make_xlsx)(rows,context)
    filename='speedpanel_'+start.strftime('%Y-%m-%d')+'_'+end.strftime('%Y-%m-%d')+'.'+kind
    mimetype='application/pdf' if kind=='pdf' else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    return Response(data,mimetype=mimetype,headers={'Content-Disposition':'attachment; filename="'+filename+'"','Cache-Control':'no-store'})

def number(v):
    if v is None or v=='': return None
    if isinstance(v,str):
        m=re.search(r'-?\d+(?:[.,]\d+)?',v.replace('\u00a0',''))
        if not m: raise ValueError('Resultado sem valor numerico: '+v[:80])
        v=m.group().replace(',','.')
    v=float(v)
    if not math.isfinite(v) or v<0: raise ValueError('Resultado numerico invalido.')
    return v

def browser_test(p):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True,args=['--disable-dev-shm-usage'])
        try:
            page=browser.new_page(locale='pt-BR'); page.set_default_timeout(p['timeout']*1000)
            page.goto(p['url'],wait_until='domcontentloaded')
            if p['engine']=='brasil':
                page.locator('#btnIniciar').click()
                page.wait_for_function("JSON.parse(localStorage.getItem('historico') || '[]').length > 0")
                raw=page.evaluate("JSON.parse(localStorage.getItem('historico'))[0]")
                return dict(download=raw.get('download'),upload=raw.get('upload'),ping=raw.get('ping'),jitter=raw.get('jitter'),loss=raw.get('packetLoss'),isp=raw.get('isp'),ip=raw.get('ip'),server=str(raw.get('pot','')),raw=raw)
            if p['engine']=='fast':
                page.wait_for_selector('#speed-value.succeeded')
                page.locator('#show-more-details-link').click()
                page.wait_for_selector('#upload-value.succeeded')
                def val(sel): return page.locator(sel).inner_text()
                def speed(value,unit):
                    n=number(value); u=unit.strip().lower()
                    return n*({'gbps':1000,'mbps':1,'kbps':.001,'bps':.000001}.get(u,1))
                raw={k:val(v) for k,v in dict(download='#speed-value',download_unit='#speed-units',upload='#upload-value',upload_unit='#upload-units',ping='#latency-value',loaded_ping='#bufferbloat-value').items()}
                return dict(download=speed(raw['download'],raw['download_unit']),upload=speed(raw['upload'],raw['upload_unit']),ping=number(raw['ping']),raw=raw)
            for selector in p['start_selectors']: page.locator(selector).click()
            page.wait_for_selector(p['done_selector'],state='visible')
            raw={k:page.locator(v).inner_text() for k,v in p['selectors'].items() if v}
            return dict(raw,raw=raw)
        finally: browser.close()

def measure(p):
    if p['engine']=='ookla':
        cmd=['speedtest','--accept-license','--accept-gdpr','--format=json','--progress=no']
        if p['server_id']: cmd+=['--server-id',str(p['server_id'])]
        if p['interface']: cmd+=['--interface',p['interface']]
        done=subprocess.run(cmd,capture_output=True,text=True,timeout=p['timeout'])
        if done.returncode:
            logs=[]
            for line in (done.stderr+'\n'+done.stdout).splitlines():
                try:
                    entry=json.loads(line)
                    if entry.get('message'): logs.append(entry['message'])
                except (ValueError,AttributeError): pass
            raise RuntimeError('; '.join(logs)[-1000:] or 'O servidor de teste Ookla nao respondeu. Tente outro ID de servidor.')
        raw=json.loads(done.stdout)
        return dict(download=raw['download']['bandwidth']*8/1e6,upload=raw['upload']['bandwidth']*8/1e6,ping=raw['ping']['latency'],jitter=raw['ping'].get('jitter'),loss=raw.get('packetLoss'),isp=raw.get('isp'),ip=raw.get('interface',{}).get('externalIp'),server=str(raw.get('server',{}).get('name','')),raw=raw)
    if p['engine']=='json':
        from urllib.request import urlopen,Request
        with urlopen(Request(p['url'],headers={'User-Agent':'SpeedPanel/1.0'}),timeout=p['timeout']) as r:
            data=r.read(1048577)
        if len(data)>1048576: raise ValueError('Resposta JSON excede 1 MB.')
        raw=json.loads(data); result={}
        for k,path in p['paths'].items():
            if not path: continue
            v=raw
            for part in path.split('.'): v=v[int(part)] if isinstance(v,list) else v[part]
            result[k]=v
        return dict(result,raw=raw)
    return browser_test(p)

def violations(p,r):
    flags=[]
    for rule,metric,minimum in [('min_download','download',True),('min_upload','upload',True),('max_ping','ping',False),('max_jitter','jitter',False),('max_loss','loss',False)]:
        limit=p.get(rule,0); value=r.get(metric)
        if limit>0:
            if value is None: flags.append(metric+': indisponivel para validar regra')
            elif (value<limit if minimum else value>limit): flags.append(metric+': limite '+str(limit))
    return flags

def process(row):
    p=json.loads(row['config']); started=time.monotonic(); error=''
    for attempt in range(p['retries']+1):
        try:
            child=subprocess.Popen([sys.executable,'runner.py'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
            try:
                stdout,stderr=child.communicate(json.dumps(p),timeout=p['timeout'])
            except subprocess.TimeoutExpired:
                os.killpg(child.pid,signal.SIGKILL); child.communicate()
                raise RuntimeError('Tempo limite de '+str(p['timeout'])+' segundos excedido.')
            if child.returncode: raise RuntimeError(stderr[-1800:] or 'Integracao falhou.')
            r=json.loads(stdout)
            if 'error' in r: raise RuntimeError(r['error'])
            for key in ('download','upload','ping','jitter','loss'): r[key]=number(r.get(key))
            if r['download'] is None: raise ValueError('O servico nao retornou download. Verifique o adaptador.')
            flags=violations(p,r)
            db('UPDATE results SET status=?,download=?,upload=?,ping=?,jitter=?,loss=?,isp=?,ip=?,server=?,flags=?,raw=? WHERE id=?',
               ('warning' if flags else 'ok',r['download'],r['upload'],r['ping'],r['jitter'],r['loss'],str(r.get('isp','') or ''),str(r.get('ip','') or ''),str(r.get('server','') or ''),json.dumps(flags),json.dumps(r.get('raw',{})),row['id']))
            return
        except Exception as e:
            error=str(e)[-1800:]
            if attempt<p['retries']: time.sleep(5)
    db("UPDATE results SET status='error',error=? WHERE id=?",(error,row['id']))

busy=threading.Event()
def execute_job(row):
    try: process(row)
    except Exception as e:
        db("UPDATE results SET status='error',error=? WHERE id=?",(str(e)[-1800:],row['id']))
        app.logger.exception('Erro no executor')
    finally: busy.clear()

def work():
    # ponytail: one worker serializes bandwidth tests; distribute only for independent network links.
    last_minute=None; last_cleanup=0
    db("UPDATE results SET status='error',error='Teste interrompido pelo reinicio do servico.' WHERE status='running'")
    while True:
        try:
            now=time.time(); minute=int(now//60)
            if minute!=last_minute:
                last_minute=minute; current=datetime.fromtimestamp(minute*60,ZoneInfo(settings()['timezone']))
                for p in providers():
                    if p['enabled'] and any(croniter.match(c.strip(),current) for c in p['schedules'].split(';') if c.strip()):
                        enqueue(p,str(p['id'])+':'+str(minute))
            if now-last_cleanup>3600:
                db("DELETE FROM results WHERE created<? AND status NOT IN ('queued','running')",(now-settings()['retention_days']*86400,)); last_cleanup=now
            if not busy.is_set():
                row=db("SELECT * FROM results WHERE status='queued' ORDER BY id LIMIT 1",one=True)
                if row:
                    busy.set()
                    db("UPDATE results SET status='running' WHERE id=?",(row['id'],))
                    threading.Thread(target=execute_job,args=(row,),daemon=True).start()
            time.sleep(2)
        except Exception:
            app.logger.exception('Erro no agendador'); time.sleep(5)

worker=threading.Thread(target=work,daemon=True)
if os.environ.get('DISABLE_WORKER')!='1': worker.start()
