"""Exact aggregates over the selected speed-test period, without UI row limits."""
import json
import math
import statistics
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import jsonify, request
from uptime import segments


def percentile(values, q):
    values=sorted(v for v in values if v is not None)
    if not values: return None
    pos=(len(values)-1)*q; a=int(pos); b=min(a+1,len(values)-1)
    return values[a]+(values[b]-values[a])*(pos-a)


def install(app, db, guard, settings, filter_results, uptime):
    db('CREATE TABLE IF NOT EXISTS analysis_settings (id INTEGER PRIMARY KEY, download REAL, upload REAL, target_pct REAL)')
    db('INSERT OR IGNORE INTO analysis_settings VALUES (1,0,0,80)')

    @app.get('/api/analysis/settings')
    @guard
    def get_settings(): return jsonify(db('SELECT * FROM analysis_settings WHERE id=1',one=True))

    @app.post('/api/analysis/settings')
    @guard
    def save_analysis_settings():
        body=request.get_json()
        if not isinstance(body,dict): raise ValueError('Configuração inválida.')
        vals=[]
        for k,lo,hi in [('download',0,100000),('upload',0,100000),('target_pct',1,100)]:
            try: n=float(body.get(k,0))
            except (ValueError,TypeError): raise ValueError('Meta inválida: '+k)
            if not math.isfinite(n) or not lo<=n<=hi: raise ValueError('Meta inválida: '+k)
            vals.append(n)
        db('UPDATE analysis_settings SET download=?,upload=?,target_pct=? WHERE id=1',vals)
        return jsonify(ok=True)

    def build_analysis(rows,start,end):
        rows=[dict(r,started=r.get('started'),observed=r.get('started') or r['created']) for r in rows]
        config=db('SELECT * FROM analysis_settings WHERE id=1',one=True)
        good=[r for r in rows if r['status'] in ('ok','warning')]
        metrics={}
        for key in ('download','upload','ping','jitter','loss'):
            values=[r[key] for r in good if r[key] is not None]
            metrics[key]=dict(count=len(values),median=percentile(values,.5),p95=percentile(values,.95),
                              minimum=min(values) if values else None,maximum=max(values) if values else None)
        targets={}
        for key in ('download','upload'):
            vals=[r[key] for r in good if r[key] is not None]
            contracted=config[key]; threshold=contracted*config['target_pct']/100
            targets[key]=dict(contracted=contracted,threshold=threshold,count=len(vals),
                delivered_pct=100*statistics.mean(vals)/contracted if vals and contracted else None,
                below=sum(v<threshold for v in vals) if contracted else None,
                below_pct=100*sum(v<threshold for v in vals)/len(vals) if vals and contracted else None)
        tz=ZoneInfo(settings()['timezone']); cells={}
        for r in rows:
            if r['status'] not in ('ok','warning','error'): continue
            dt=datetime.fromtimestamp(r['observed'],tz); key=(dt.weekday(),dt.hour)
            cell=cells.setdefault(key,dict(day=key[0],hour=key[1],tests=0,errors=0,downloads=[],pings=[]))
            cell['tests']+=1; cell['errors']+=int(r['status']=='error')
            if r['status']!='error':
                if r['download'] is not None: cell['downloads'].append(r['download'])
                if r['ping'] is not None: cell['pings'].append(r['ping'])
        heat=[]
        for c in cells.values():
            heat.append(dict(day=c['day'],hour=c['hour'],tests=c['tests'],errors=c['errors'],
                download=statistics.mean(c['downloads']) if c['downloads'] else None,
                ping=statistics.mean(c['pings']) if c['pings'] else None))
        correlation=[]
        # Association at the recorded start time; does not imply that one event caused another.
        for p in uptime['monitors'](True):
            parts=segments(uptime['history'](p['id'],start,end),start,end)
            groups={k:dict(tests=0,errors=0,slow=0,approximate=0) for k in ('up','down','unknown')}
            cursor=0
            for r in sorted(rows,key=lambda r:r['observed']):
                if r['status'] not in ('ok','warning','error'): continue
                while cursor<len(parts) and parts[cursor]['end']<=r['observed']: cursor+=1
                state=parts[cursor]['state'] if cursor<len(parts) and parts[cursor]['start']<=r['observed']<parts[cursor]['end'] else 'unknown'
                if state not in ('up','down'): state='unknown'
                g=groups[state]; g['tests']+=1; g['errors']+=int(r['status']=='error')
                g['approximate']+=int(r['started'] is None)
                if r['status']!='error':
                    g['slow']+=int(any(config[k]>0 and r[k] is not None and r[k]<targets[k]['threshold'] for k in ('download','upload')))
            correlation.append(dict(id=p['id'],name=p['name'],groups=groups))
        return dict(metrics=metrics,targets=targets,heatmap=heat,correlation=correlation,
                       total=len(rows),completed=len(good),errors=sum(r['status']=='error' for r in rows),
                       config=config,timezone=str(tz),start=start,end=end,
                       approximate_timestamps=sum(r['started'] is None and r['status'] in ('ok','warning','error') for r in rows))


    @app.get('/api/analysis')
    @guard
    def analysis():
        where,args=filter_results()
        rows=db('SELECT provider_id,provider_name,created,started,status,download,upload,ping,jitter,loss FROM results WHERE '+where+' ORDER BY created',args)
        start=args[0]; end=min(args[1],time.time()) if request.args.get('to') else time.time()
        return jsonify(build_analysis(rows,start,end))

    return dict(build=build_analysis)
