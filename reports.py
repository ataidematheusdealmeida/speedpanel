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

def make_xlsx(rows,context):
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
    out=io.BytesIO(); wb.save(out); return out.getvalue()

def make_pdf(rows,context):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4,landscape
    from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,LongTable,TableStyle,PageBreak,KeepTogether
    from reportlab.graphics.shapes import Drawing,String
    from reportlab.graphics.charts.lineplots import LinePlot
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import os
    fontdir='/usr/share/fonts/truetype/dejavu/'
    regular=fontdir+'DejaVuSans.ttf'; bold=fontdir+'DejaVuSans-Bold.ttf'
    font='Helvetica'; fontbold='Helvetica-Bold'
    if os.path.exists(regular) and os.path.exists(bold):
        if 'PanelSans' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont('PanelSans',regular)); pdfmetrics.registerFont(TTFont('PanelSans-Bold',bold))
        font='PanelSans'; fontbold='PanelSans-Bold'
    styles=getSampleStyleSheet()
    for name in ('Normal','BodyText','Heading1','Heading2','Title'): styles[name].fontName=font
    styles.add(ParagraphStyle('PanelTitle',fontName=fontbold,fontSize=24,leading=29,textColor=colors.HexColor('#15283F'),spaceAfter=12))
    styles.add(ParagraphStyle('PanelSmall',fontName=font,fontSize=8,leading=11,textColor=colors.HexColor('#43546A')))
    styles.add(ParagraphStyle('PanelCell',fontName=font,fontSize=7,leading=10,wordWrap='CJK'))
    styles.add(ParagraphStyle('PanelHeader',fontName=fontbold,fontSize=7,leading=10,textColor=colors.white))
    def para(value,style='PanelCell'):return Paragraph(escape(str(value if value is not None else '-')).replace('\n','<br/>'),styles[style])
    out=io.BytesIO(); width,height=landscape(A4); available=width-72
    doc=SimpleDocTemplate(out,pagesize=(width,height),leftMargin=36,rightMargin=36,topMargin=32,bottomMargin=35,title='SpeedPanel - Relatório do período',author='SpeedPanel')
    story=[Paragraph('Relatório de velocidade da internet',styles['PanelTitle'])]
    period=context['start'].strftime('%d/%m/%Y %H:%M')+' a '+context['end'].strftime('%d/%m/%Y %H:%M')
    story += [para('Período: '+period+' | Fuso: '+context['timezone'],'PanelSmall'),para('Serviço: '+context['service']+' | Gerado em: '+context['generated'].strftime('%d/%m/%Y %H:%M:%S'),'PanelSmall'),Spacer(1,14)]
    overview=[['TESTES NO PERÍODO','CONCLUÍDOS','FALHAS','FORA DAS REGRAS'],[len(rows),len(completed(rows)),sum(r['status']=='error' for r in rows),sum(r['status']=='warning' for r in rows)]]
    top=Table(overview,colWidths=[available/4]*4); top.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#EEF5F7')),('TEXTCOLOR',(0,0),(-1,-1),colors.HexColor('#15283F')),('FONTNAME',(0,0),(-1,-1),fontbold),('FONTSIZE',(0,0),(-1,0),8),('FONTSIZE',(0,1),(-1,1),21),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8)])); story.extend([top,Spacer(1,14),Paragraph('Comparativo por serviço',styles['Heading2'])])
    compare=[[para(x,'PanelHeader') for x in ['Serviço','Concluídos','Download médio\nMbps','Upload médio\nMbps','Latência média\nms','Jitter médio\nms','Perda média\n%']]]
    for g in groups(rows):compare.append([para(g[-1]['provider_name']),len(completed(g))]+[display(average(g,k)) for k,_ in METRICS])
    if len(compare)==1: compare.append([para('Sem medições no período')]+['-']*6)
    tbl=Table(compare,colWidths=[210,65]+[(available-275)/5]*5,repeatRows=1); tbl.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#15283F')),('FONTNAME',(0,1),(-1,-1),font),('FONTSIZE',(0,1),(-1,-1),8),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#F1F5FA')]),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)])); story += [tbl,Spacer(1,8),para('Médias calculadas somente com medições concluídas. Falhas, testes pendentes e métricas não fornecidas não entram nas médias. Os testes medem a conexão do servidor.','PanelSmall'),Spacer(1,12)]
    def chart(metric,title):
        drawing=Drawing(available/2-8,170); drawing.add(String(5,157,title,fontName=fontbold,fontSize=10,fillColor=colors.HexColor('#15283F')))
        series=[]; names=[]; seriescolors=[]
        good=completed(rows)
        origin=min((r['created'] for r in good),default=0)
        for i,g in enumerate(groups(rows)):
            points=[(r['created']-origin,r[metric]) for r in completed(g) if r.get(metric) is not None]
            if points:series.append(points); names.append(g[-1]['provider_name']); seriescolors.append(PALETTE[i%len(PALETTE)])
        if not series:
            drawing.add(String(5,90,'Sem medições concluídas neste período.',fontName=font,fontSize=9)); return drawing
        plot=LinePlot(); plot.x=42; plot.y=35; plot.width=available/2-68; plot.height=105; plot.data=series
        plot.xValueAxis.valueMin=0; plot.xValueAxis.valueMax=max(60,max(p[0] for s in series for p in s)); plot.xValueAxis.valueStep=plot.xValueAxis.valueMax/2
        plot.xValueAxis.labelTextFormat=lambda v:datetime.fromtimestamp(origin+v,ZoneInfo(context['timezone'])).strftime('%d/%m %H:%M')
        plot.xValueAxis.labels.fontName=font; plot.xValueAxis.labels.fontSize=6
        plot.yValueAxis.valueMin=0; plot.yValueAxis.valueMax=max(1,max(p[1] for s in series for p in s))*1.12; plot.yValueAxis.labels.fontName=font; plot.yValueAxis.labels.fontSize=7; plot.yValueAxis.visibleGrid=True; plot.yValueAxis.gridStrokeColor=colors.HexColor('#DCE5EF')
        for i,color in enumerate(seriescolors):
            plot.lines[i].strokeColor=colors.HexColor(color); plot.lines[i].strokeWidth=1.5
            from reportlab.graphics.widgets.markers import makeMarker
            plot.lines[i].symbol=makeMarker('FilledCircle'); plot.lines[i].symbol.size=3; plot.lines[i].symbol.fillColor=colors.HexColor(color)
        drawing.add(plot)
        return drawing
    story.append(Table([[chart('download','Download (Mbps)'),chart('upload','Upload (Mbps)')]],colWidths=[available/2]*2,style=[('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0)]))
    legend='   |   '.join('<font color="'+PALETTE[i%len(PALETTE)]+'">'+escape(g[-1]['provider_name'])+'</font>' for i,g in enumerate(groups(rows)))
    story.append(Paragraph('Séries por serviço: '+legend,styles['PanelSmall']))
    story += [PageBreak(),Paragraph('Histórico completo do período',styles['Heading1']),para('Todas as medições do filtro, inclusive falhas e trabalhos pendentes. Datas em '+context['timezone']+'.','PanelSmall'),Spacer(1,10)]
    labels=['Data / hora','Serviço','Status','Download\nMbps','Upload\nMbps','Ping\nms','Jitter\nms','Perda\n%']
    history=[[para(x,'PanelHeader') for x in labels]]
    for r in rows:
        history.append([para(local_date(r['created'],context).strftime('%d/%m/%Y\n%H:%M:%S')),para(r['provider_name']),para(STATUS.get(r['status'],r['status']))]+[para(display(r.get(k))) for k,_ in METRICS])
    if len(history)==1:history.append([para('Sem medições')]+['-']*7)
    table=LongTable(history,colWidths=[100,190,100]+[(available-390)/5]*5,repeatRows=1)
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#15283F')),('VALIGN',(0,0),(-1,-1),'TOP'),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#F1F5FA')]),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6)])); story.append(table)
    notes=[r for r in rows if details(r)]
    if notes:
        story.extend([Spacer(1,16),Paragraph('Falhas e regras sinalizadas',styles['Heading2'])])
        for r in notes:
            story.append(para(f"#{r['id']} | {r['provider_name']} | {local_date(r['created'],context):%d/%m/%Y %H:%M:%S}", 'PanelSmall'))
            story.extend([para(details(r)),Spacer(1,7)])
    def footer(canvas,document):
        canvas.saveState(); canvas.setFont(font,7); canvas.setFillColor(colors.HexColor('#617086'))
        canvas.drawString(36,18,'SpeedPanel | Relatório gerado a partir do histórico persistente')
        canvas.drawRightString(width-36,18,'Página '+str(document.page)); canvas.restoreState()
    doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return out.getvalue()
