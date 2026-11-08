"""Portable server-side reports: static snapshots of the complete filtered history."""
import io
import json
import math
from collections import defaultdict
from datetime import datetime
from statistics import mean
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

STATUS={'ok':'Concluído','warning':'Fora das regras','error':'Falhou','queued':'Na fila','running':'Em execução'}
METRICS=[('download','Download (Mbps)'),('upload','Upload (Mbps)'),('ping','Latência (ms)'),('jitter','Jitter (ms)'),('loss','Perda (%)')]
PALETTE=['#17866f','#356fc4','#9b59b6','#c7781b','#c44751','#338e9c']

def completed(rows): return [r for r in rows if r['status'] in ('ok','warning')]
def average(rows,key):
    values=[r[key] for r in completed(rows) if r.get(key) is not None]
    return mean(values) if values else None
def display(value): return '-' if value is None else f'{value:,.2f}'.replace(',','X').replace('.',',').replace('X','.')
def local_date(value,context): return datetime.fromtimestamp(value,ZoneInfo(context['timezone'])).replace(tzinfo=None)
def details(row):
    try: flags=json.loads(row.get('flags') or '[]')
    except (ValueError,TypeError): flags=[]
    return row.get('error') or '; '.join(flags) or ''
def groups(rows):
    grouped=defaultdict(list)
    for r in rows: grouped[r['provider_id']].append(r)
    return sorted(grouped.values(),key=lambda g:g[-1]['provider_name'].casefold())

def speed_workbook(rows,context):
    # The deployed Python container has no artifact-tool/Node runtime; use its native XLSX library.
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.worksheet.table import Table,TableStyleInfo
    from openpyxl.chart import BarChart,Reference
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    wb=Workbook(); summary=wb.active; summary.title='Resumo'; data=wb.create_sheet('Medições')
    def write(ws,row,values,header=False):
        for col,value in enumerate(values,1):
            cell=ws.cell(row,col)
            if isinstance(value,str):
                cell.value=ILLEGAL_CHARACTERS_RE.sub('',value)[:32767]; cell.data_type='s'
            else: cell.value=value
            cell.font=Font(name='Calibri',size=11,color='17263C')
            cell.alignment=Alignment(vertical='top',wrap_text=True)
            if isinstance(value,float): cell.number_format='#,##0.00'
            if isinstance(value,datetime): cell.number_format='dd/mm/yyyy hh:mm:ss'
            if header:
                cell.fill=PatternFill('solid',fgColor='15283F'); cell.font=Font(name='Calibri',size=11,color='FFFFFF',bold=True)
        if header: ws.row_dimensions[row].height=32
    write(summary,1,['SpeedPanel - relatório de velocidade'])
    summary['A1'].font=Font(name='Calibri',size=22,bold=True,color='15283F'); summary.merge_cells('A1:J1'); summary.row_dimensions[1].height=36
    for row,values in enumerate([
        ['Período',context['start'].replace(tzinfo=None),'até',context['end'].replace(tzinfo=None)],
        ['Serviço selecionado',context['service']],['Fuso horário',context['timezone']],
        ['Gerado em',context['generated'].replace(tzinfo=None)],
        ['Fonte','Histórico persistente do SpeedPanel; conexão medida pelo servidor.'],
        ['Critério','Médias incluem apenas medições concluídas. Métricas ausentes ficam em branco.'],
    ],3): write(summary,row,values)
    summary.merge_cells('B7:J7'); summary.merge_cells('B8:J8')
    header=['Serviço','Testes','Concluídos','Falhas','Fora das regras']+[x[1]+' média' for x in METRICS]
    write(summary,10,header,True)
    for index,group in enumerate(groups(rows),11):
        write(summary,index,[group[-1]['provider_name'],len(group),len(completed(group)),sum(r['status']=='error' for r in group),sum(r['status']=='warning' for r in group)]+[average(group,k) for k,_ in METRICS])
        summary.row_dimensions[index].height=max(28,math.ceil(len(group[-1]['provider_name'])/30)*15)
    totalrow=11+len(groups(rows))
    write(summary,totalrow,['TOTAL',len(rows),len(completed(rows)),sum(r['status']=='error' for r in rows),sum(r['status']=='warning' for r in rows)]+[average(rows,k) for k,_ in METRICS],True)
    for c in summary[totalrow]:
        if isinstance(c.value,float): c.number_format='#,##0.00'
    summary.column_dimensions['A'].width=34
    for col in 'BCDE':summary.column_dimensions[col].width=21
    for col in 'FGHIJ':summary.column_dimensions[col].width=23
    summary.freeze_panes='B11'; summary.sheet_view.showGridLines=False
    if groups(rows):
        chart=BarChart(); chart.type='col'; chart.title='Velocidade média por serviço'; chart.y_axis.title='Mbps'; chart.x_axis.title='Serviço'; chart.height=9; chart.width=22
        chart.add_data(Reference(summary,min_col=6,max_col=7,min_row=10,max_row=totalrow-1),titles_from_data=True)
        chart.set_categories(Reference(summary,min_col=1,min_row=11,max_row=totalrow-1)); summary.add_chart(chart,'A'+str(totalrow+3))
    headers=['ID medição','ID serviço','Serviço','Data ('+context['timezone']+')','Status']+[x[1] for x in METRICS]+['Operadora','IP','Servidor','Erro / regras']
    write(data,1,headers,True)
    for index,r in enumerate(rows,2):
        write(data,index,[r['id'],r['provider_id'],r['provider_name'],local_date(r['created'],context),STATUS.get(r['status'],r['status'])]+[r.get(k) for k,_ in METRICS]+[r.get('isp') or '',r.get('ip') or '',r.get('server') or '',details(r)])
        if r['status'] in ('error','warning'):
            data.cell(index,5).fill=PatternFill('solid',fgColor='FDE9E7' if r['status']=='error' else 'FFF2CC')
        textfields=[(r['provider_name'],30),(r.get('isp') or '',26),(r.get('server') or '',40),(details(r),62)]
        lines=max(sum(max(1,math.ceil(len(line)/width)) for line in str(value).split('\n')) for value,width in textfields)
        data.row_dimensions[index].height=min(409,max(34,lines*15))
    if rows:
        table=Table(displayName='Medicoes',ref=f'A1:N{len(rows)+1}'); table.tableStyleInfo=TableStyleInfo(name='TableStyleMedium2',showRowStripes=True); data.add_table(table)
    else:
        write(data,2,['Nenhuma medição no período selecionado.'])
    widths=[13,13,34,28,21,20,20,20,20,18,30,22,45,70]
    for col,width in enumerate(widths,1): data.column_dimensions[data.cell(1,col).column_letter].width=width
    data.freeze_panes='F2'; data.sheet_view.showGridLines=False
    for ws in wb:
        ws.sheet_properties.pageSetUpPr.fitToPage=True; ws.page_setup.orientation='landscape'; ws.page_setup.paperSize=ws.PAPERSIZE_A3; ws.page_setup.fitToWidth=1; ws.page_setup.fitToHeight=0
        ws.oddFooter.center.text='SpeedPanel | Página &P de &N'
    data.print_title_rows='1:1'
    return wb

def make_pdf(rows,context):
    from report_sections import make_pdf as render
    return render(rows,context)

def make_xlsx(rows,context):
    from report_sections import make_xlsx as render
    return render(rows,context)
