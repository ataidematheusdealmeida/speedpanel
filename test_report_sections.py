"""Selection, shared periods, failed-test filtering and complete composite reports."""
import io,json,os,tempfile,time
from datetime import datetime,timezone
from pathlib import Path
from itertools import combinations
os.environ.update(DATA_DIR=tempfile.mkdtemp(),SESSION_SECRET='composite-report-test',ADMIN_PASSWORD='report-test-password',DISABLE_WORKER='1')
import app


def test():
    from openpyxl import load_workbook
    from pypdf import PdfReader
    client=app.app.test_client()
    login=client.post('/api/login',json={'password':'report-test-password'})
    h={'X-CSRF-Token':login.json['csrf']};start=time.time()-7200;end=start+1000
    def iso(t):return datetime.fromtimestamp(t,timezone.utc).isoformat()
    for offset,status,down in [(0,'ok',100),(10,'warning',200),(20,'error',None)]:
        app.db('INSERT INTO results(provider_id,provider_name,created,started,status,download,upload,ping,jitter,loss,error,flags) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
               (1,'=FORMULA()',start+offset,start+offset,status,down,30 if down else None,12 if down else None,2 if down else None,0 if down else None,'FAILURE_SENTINEL <rede>' if status=='error' else None,json.dumps(['Regra de qualidade'] if status=='warning' else [])))
    p=dict(name='Monitor <rede> & local',kind='http',target='https://example.com',interval=60,timeout=5,failures=2,enabled=False)
    pid=client.post('/api/uptime/monitors',json=p,headers=h).json['id']
    p=app.uptime_service['monitors']()[0];app.db('DELETE FROM uptime_checks')
    for offset,ok in [(0,True),(60,False),(120,False),(180,True)]:
        app.uptime_service['record'](p,dict(ok=ok,ms=12 if ok else None,error='' if ok else 'UPTIME_FAILURE',code=200 if ok else None),start+offset)
    client.post('/api/analysis/settings',json=dict(download=600,upload=60,target_pct=80),headers=h)
    q='?from='+iso(start-1)+'&to='+iso(end)
    from urllib.parse import urlencode
    q='?'+urlencode(dict(frm=iso(start-1),to=iso(end))).replace('frm=','from=')
    assert client.get('/api/results'+q).json['total']==3
    assert client.get('/api/results'+q+'&hide_failed=1').json['total']==2
    assert client.get('/api/analysis'+q+'&hide_failed=1').json['errors']==0
    assert 'FAILURE_SENTINEL' not in client.get('/api/export'+q+'&hide_failed=1').text
    assert client.get('/api/results?hide_failed=bad').status_code==400
    for value in ('','bad','speed,bad'):
        assert client.get('/api/report.pdf'+q+'&sections='+value).status_code==400
    # All seven nonempty combinations must work independently in both formats.
    directory=Path(os.environ.get('REPORT_QA_DIR','qa/reports'));directory.mkdir(parents=True,exist_ok=True)
    for n in (1,2,3):
        for chosen in combinations(('speed','uptime','analysis'),n):
            query=q+'&sections='+','.join(chosen)+'&hide_failed=1'
            pdf=client.get('/api/report.pdf'+query);assert pdf.status_code==200,pdf.text[:200]
            reader=PdfReader(io.BytesIO(pdf.data));text='\n'.join(page.extract_text() for page in reader.pages)
            assert 'FAILURE_SENTINEL' not in text and 'Ocultados' in text
            assert ('Histórico completo de velocidade' in text)==('speed' in chosen)
            assert ('Disponibilidade no período selecionado' in text)==('uptime' in chosen)
            assert ('Estabilidade e distribuição' in text)==('analysis' in chosen)
            if 'uptime' in chosen:assert 'Monitor <rede> & local' in text and 'Recuperado' in text
            if 'analysis' in chosen:assert '480,00' in text and '48,00' in text
            response=client.get('/api/report.xlsx'+query);assert response.status_code==200
            wb=load_workbook(io.BytesIO(response.data))
            assert ('Medições' in wb.sheetnames)==('speed' in chosen)
            assert ('Uptime incidentes' in wb.sheetnames)==('uptime' in chosen)
            assert ('Análise metas' in wb.sheetnames)==('analysis' in chosen)
            for ws in wb:
                for row in ws:
                    for cell in row:
                        if cell.value=='=FORMULA()':assert cell.data_type=='s'
            if 'speed' in chosen:assert wb['Medições'].max_row==3
            if 'uptime' in chosen:
                ws=wb['Uptime incidentes'];assert ws['D5'].value==2 and ws['B5'].is_date
            if n==3:
                (directory/'complete.pdf').write_bytes(pdf.data);(directory/'complete.xlsx').write_bytes(response.data)
    # Empty periods are explained and selected sections still appear.
    response=client.get('/api/report.pdf?provider=9999&sections=speed,uptime,analysis')
    assert response.status_code==200
    # Filter must not remove warnings or mutate history.
    assert app.db('SELECT COUNT(*) AS n FROM results',one=True)['n']==3
    # Reports may span more than the UI's 30-day uptime selector.
    old=start-40*86400
    app.db("INSERT INTO uptime_checks(monitor_id,created,valid_until,state,ok,ms) VALUES (?,?,?,'up',1,12)",(pid,old,old+60))
    long_report=app.uptime_service['build'](old-1,end)
    assert long_report['monitors'][0]['summary']['seconds']['up']==215
    print('Composite reports: 7 section combinations, PDF/XLSX, shared dates, incidents, failure filtering, typed cells and formula safety OK')

if __name__=='__main__':test()
