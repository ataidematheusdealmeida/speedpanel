"""Exercise both authenticated exports with the same service/date boundaries."""
import io
import json
import os
import tempfile
from datetime import datetime,timezone
from pathlib import Path
os.environ.update(DATA_DIR=tempfile.mkdtemp(),SESSION_SECRET='test-report-secret',ADMIN_PASSWORD='test-password-only',DISABLE_WORKER='1')
import app

def test():
    from openpyxl import load_workbook
    from pypdf import PdfReader
    client=app.app.test_client()
    assert client.get('/api/report.pdf').status_code==401
    assert client.get('/api/report.xlsx').status_code==401
    client.post('/api/login',json={'password':'test-password-only'})
    moment=datetime(2026,10,18,12,tzinfo=timezone.utc).timestamp()
    for index in range(65):
        app.db('INSERT INTO results(provider_id,provider_name,created,status,download,upload,ping,jitter,loss,error,flags) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
               (1,'Serviço com nome extenso & acentuação / Ookla',moment+index*60,'ok',100+index,30,12,2,0,None,'[]'))
    app.db('INSERT INTO results(provider_id,provider_name,created,status,download,error) VALUES (?,?,?,?,?,?)',(2,'=FORMULA()',moment,'error',None,'Falha de conexão <servidor> & regra'))
    app.db('INSERT INTO results(provider_id,provider_name,created,status,download) VALUES (?,?,?,?,?)',(1,'FORA DO PERIODO',moment-86400,'ok',900))
    query='?from=2026-10-18T12:00:00Z&to=2026-10-18T14:00:00Z&provider=1'
    pdf=client.get('/api/report.pdf'+query); assert pdf.status_code==200 and pdf.data.startswith(b'%PDF')
    reader=PdfReader(io.BytesIO(pdf.data)); text='\n'.join(p.extract_text() for p in reader.pages)
    assert 'FORA DO PERIODO' not in text and '=FORMULA()' not in text and '65' in text
    assert len(reader.pages)>=3
    excel=client.get('/api/report.xlsx'+query); assert excel.status_code==200
    wb=load_workbook(io.BytesIO(excel.data)); ws=wb['Medições']
    assert ws.max_row==66 and ws['F2'].value==100 and ws['D2'].is_date
    assert wb['Resumo']['F11'].value==132
    allquery='?from=2026-10-18T12:00:00Z&to=2026-10-18T14:00:00Z'
    allwb=load_workbook(io.BytesIO(client.get('/api/report.xlsx'+allquery).data))
    formulas=[c for row in allwb['Medições'] for c in row if c.value=='=FORMULA()']
    assert formulas and all(c.data_type=='s' for c in formulas)
    assert client.get('/api/report.pdf?from=2026-10-19T00:00:00Z&to=2026-10-18T00:00:00Z').status_code==400
    assert client.get('/api/report.xlsx?provider=99999').status_code==200
    empty=client.get('/api/report.pdf?provider=99999'); assert empty.status_code==200
    assert 'Sem medições' in ''.join(p.extract_text() for p in PdfReader(io.BytesIO(empty.data)).pages)
    directory=os.environ.get('REPORT_QA_DIR')
    if directory:
        Path(directory).mkdir(parents=True,exist_ok=True)
        Path(directory,'sample.pdf').write_bytes(pdf.data)
        Path(directory,'sample.xlsx').write_bytes(excel.data)
    print('PDF/XLSX: authentication, date/service filters, full history, averages, numeric/date types, formula safety, pagination and empty period OK')

if __name__=='__main__':test()
