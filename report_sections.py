"""Selected report sections share descriptions and tables across PDF and Excel."""
import io
from datetime import datetime
from statistics import mean
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from reports import METRICS, STATUS, PALETTE, average, completed, details, display, groups, local_date

NAMES={'speed':'Velocidade da conexão','uptime':'Disponibilidade e uptime','analysis':'Análises da conexão'}
STATES={'up':'Disponível','down':'Indisponível','unknown':'Sem dados','pending':'Falha pendente','maintenance':'Manutenção','paused':'Pausado'}
OUTCOMES={'recovered':'Recuperado','open':'Em andamento no fim do período','unobserved':'Observação interrompida'}
DESCRIPTIONS={
    'speed':'Medições realizadas a partir do servidor, não do dispositivo que abre o painel. Download e upload são expressos em Mbps; latência e jitter em ms; perda de pacotes em %. Médias e gráficos usam somente testes concluídos, incluindo os sinalizados por regras. Falhas e métricas ausentes não são tratadas como zero.',
    'uptime':'Disponibilidade = tempo disponível / (tempo disponível + tempo indisponível). Cobertura = tempo observado / período total. Falhas ainda pendentes, lacunas de coleta, pausas e manutenção são excluídas da disponibilidade e descritas separadamente. Uma disponibilidade alta com cobertura baixa não comprova estabilidade durante todo o período. Os monitores verificam destinos diferentes; seus percentuais não são somados nem representam, isoladamente, a disponibilidade de toda a internet.',
    'analysis':'Comparações calculadas com todas as medições do filtro, sem o corte da tabela da tela. A mediana descreve o centro das amostras; P95 é o percentil interpolado abaixo do qual ficam 95% dos valores. Os percentuais abaixo da meta se referem à quantidade de medições, não ao tempo contínuo de lentidão. Falha de um teste não comprova queda da conexão.'}


def sections(context): return context.get('sections',['speed'])


def report_tables(rows,c):
    """Tables retain numeric/date types for XLSX; PDF formats them at the boundary."""
    result={}
    if 'speed' in sections(c):
        result['speed']=[
            ('Comparativo por serviço','Médias calculadas separadamente por serviço de medição. Ausência de uma métrica é exibida como indisponível.',
             ['Serviço','Testes','Concluídos','Falhas','Fora das regras']+[label+' média' for _,label in METRICS],
             [[g[-1]['provider_name'],len(g),len(completed(g)),sum(r['status']=='error' for r in g),sum(r['status']=='warning' for r in g)]+[average(g,k) for k,_ in METRICS] for g in groups(rows)]),
            ('Histórico completo de velocidade','Todas as medições do período e serviço selecionados, respeitando a opção de ocultar falhas. Datas nesta seção representam a entrada no histórico/fila; o início real, quando disponível, é usado nas análises temporais.',
             ['ID','Data / hora','Serviço','Status']+[label for _,label in METRICS],
             [[r['id'],local_date(r['created'],c),r.get('provider_name'),STATUS.get(r['status'],r['status'])]+[r.get(k) for k,_ in METRICS] for r in rows]),
            ('Detalhes das falhas e regras','As mensagens explicam falhas do adaptador de medição e violações das regras configuradas; não determinam, por si só, a causa de um problema de rede.',
             ['ID','Data / hora','Serviço','Descrição'],
             [[r['id'],local_date(r['created'],c),r.get('provider_name'),details(r)] for r in rows if details(r)])]
    if 'uptime' in sections(c):
        monitors=c['uptime']['monitors']
        def date(t):return datetime.fromtimestamp(t,ZoneInfo(c['timezone'])).replace(tzinfo=None)
        result['uptime']=[
            ('Configuração dos monitores','Configuração vigente no momento da geração, inclusive para monitores arquivados. HTTP/HTTPS valida o código de resposta e o certificado; TCP verifica a abertura da porta; ping depende de o destino permitir ICMP. A queda é confirmada após o número de falhas consecutivas configurado.',
             ['Monitor','Tipo','Destino','Intervalo s','Tempo limite s','Falhas para queda','Lento acima de ms','Arquivado'],
             [[p['name'],p['config']['kind'],p['config']['target']+(':'+str(p['config']['port']) if p['config']['kind']=='tcp' else ''),p['config']['interval'],p['config']['timeout'],p['config']['failures'],p['config']['slow_ms'],'Sim' if p['deleted'] else 'Não'] for p in monitors]),
            ('Disponibilidade no período selecionado','Cada monitor usa exatamente o período indicado na capa. A cobertura inclui a ausência de dados anterior ao cadastro ou causada pela retenção. Durações são estimadas entre verificações.',
             ['Monitor','Disponibilidade %','Cobertura %','Disponível h','Indisponível h','Sem dados h','Pendente h','Pausa h','Manutenção h'],
             [[p['name'],p['summary']['availability'],p['summary']['coverage']]+[p['summary']['seconds'][s]/3600 for s in ('up','down','unknown','pending','paused','maintenance')] for p in monitors]),
            ('Resumo dos incidentes','Somente uma resposta disponível observada após a queda confirma recuperação. O tempo médio de recuperação considera incidentes recuperados no recorte. Uma lacuna de coleta não é interpretada como recuperação.',
             ['Monitor','Incidentes','Maior interrupção min','Recuperação média min'],
             [[p['name'],p['summary']['incidents'],p['summary']['longest']/60,p['summary']['mttr']/60 if p['summary']['mttr'] is not None else None] for p in monitors]),
            ('Incidentes detalhados','Incidentes que cruzam os limites do filtro são recortados ao período. Em andamento descreve o estado observado no término selecionado, não necessariamente o estado atual.',
             ['Monitor','Início observado','Fim observado','Duração min','Situação','Recortado no início'],
             [[p['name'],date(i['start']),date(i['end']),i['duration']/60,OUTCOMES[i['outcome']],'Sim' if i['clipped'] else 'Não'] for p in monitors for i in p['incidents']]),
            ('Respostas por hora','Média e máximo são calculados somente com respostas cujo tempo foi medido. Lentas indica quantas excederam o limite de lentidão do monitor. A hora identifica o início do agrupamento, que pode estar parcialmente fora do filtro; apenas as amostras do filtro são agregadas.',
             ['Monitor','Hora','Média ms','Máximo ms','Respostas medidas','Respostas lentas'],
             [[p['name'],date(v['created']),v['ms'],v['maximum'],v['count'],v['slow']] for p in monitors for v in p['points']]),
            ('Lacunas, pausas e manutenção','Períodos sem coleta e falhas pendentes são explicitados, sem transformá-los em disponibilidade. Inclui também janelas de manutenção e pausas observadas no histórico.',
             ['Monitor','Início','Fim','Duração min','Estado'],
             [[p['name'],date(s['start']),date(s['end']),(s['end']-s['start'])/60,STATES[s['state']]] for p in monitors for s in p['segments'] if s['state'] not in ('up','down')])]
    if 'analysis' in sections(c):
        a=c['analysis']; days=['Segunda','Terça','Quarta','Quinta','Sexta','Sábado','Domingo']
        result['analysis']=[
            ('Velocidade contratada e metas','O percentual entregue é a velocidade média medida dividida pela contratada. A meta usa a configuração vigente na geração e recalcula a comparação histórica; zero desativa a comparação de uma velocidade.',
             ['Métrica','Contratado Mbps','Meta mínima Mbps','Entregue %','Medições','Abaixo da meta','Abaixo %'],
             [[label,a['targets'][k]['contracted'],a['targets'][k]['threshold'],a['targets'][k]['delivered_pct'],a['targets'][k]['count'],a['targets'][k]['below'],a['targets'][k]['below_pct']] for k,label in [('download','Download'),('upload','Upload')]]),
            ('Estabilidade e distribuição','Latência é o tempo de resposta; jitter é sua variação; perda é a fração de pacotes não recebidos. Para essas três métricas, valores menores geralmente indicam melhor qualidade. Mínimos e máximos são extremos das amostras, não limites garantidos da conexão.',
             ['Métrica','Amostras','Mediana','P95','Mínimo','Máximo'],
             [[label]+[a['metrics'][k][v] for v in ('count','median','p95','minimum','maximum')] for k,label in METRICS]),
            ('Qualidade por dia e horário','Agrupamento por dia da semana e hora no fuso '+c['timezone']+'. Cada linha informa a quantidade de testes, falhas, download médio e latência média. '+str(a['approximate_timestamps'])+' teste(s) usam horário aproximado de entrada na fila por não possuírem início real registrado.',
             ['Dia da semana','Hora','Testes','Falhas','Falhas %','Download médio Mbps','Latência média ms'],
             [[days[v['day']],v['hour'],v['tests'],v['errors'],100*v['errors']/v['tests'],v['download'],v['ping']] for v in sorted(a['heatmap'],key=lambda v:(v['day'],v['hour']))]),
            ('Correlação entre medições e uptime','Estado de cada monitor no início real do teste ou, quando ausente, no horário aproximado de entrada na fila. Abaixo da meta significa download ou upload inferior à meta vigente. Sem dados inclui pausa, manutenção e falha pendente, pois esses estados não confirmam disponibilidade. Associação temporal não comprova causalidade.',
             ['Monitor','Estado','Testes','Falhas de teste','Abaixo da meta','Horários aproximados'],
             [[p['name'],STATES[s]]+[p['groups'][s][k] for k in ('tests','errors','slow','approximate')] for p in a['correlation'] for s in ('up','down','unknown')])]
    return result


def scope_rows(c):
    rows=[['Período inicial',c['start'].replace(tzinfo=None)],['Período final',c['end'].replace(tzinfo=None)],
            ['Gerado em',c['generated'].replace(tzinfo=None)],['Fuso horário',c['timezone']],
            ['Seções incluídas',', '.join(NAMES[s] for s in sections(c))],
            ['Serviço de velocidade',c['service']],['Testes de velocidade com falha','Ocultados' if c.get('hide_failed') else 'Incluídos'],
            ['Escopo do uptime','Todos os monitores, inclusive arquivados com histórico; o filtro de serviço e a opção de ocultar falhas se aplicam somente às medições de velocidade e suas análises.'],
            ['Ausência de dados','Não representa valor zero nem comprova disponibilidade. O relatório é uma fotografia do histórico persistente no momento da geração.']]
    return [r for r in rows if r[0]!='Escopo do uptime' or 'uptime' in sections(c)]


def make_xlsx(rows,c):
    from openpyxl import Workbook
    from openpyxl.styles import Font,PatternFill,Alignment
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    from reports import speed_workbook
    wb=speed_workbook(rows,c) if 'speed' in sections(c) else Workbook()
    if 'speed' not in sections(c): wb.remove(wb.active)
    def sheet(title,description,headers,records):
        ws=wb.create_sheet(title)
        for row in [[title],[description],[],headers]+(records or [['Sem dados no período selecionado.']]): ws.append(row)
        ws.merge_cells(start_row=1,start_column=1,end_row=1,end_column=max(2,len(headers)))
        ws.merge_cells(start_row=2,start_column=1,end_row=2,end_column=max(2,len(headers)))
        ws.row_dimensions[1].height=30; ws.row_dimensions[2].height=65
        for row in ws:
            for cell in row:
                if isinstance(cell.value,str): cell.value=ILLEGAL_CHARACTERS_RE.sub('',cell.value)[:32767]; cell.data_type='s'
                cell.font=Font(name='Calibri',size=11,color='17263C')
                cell.alignment=Alignment(wrap_text=True,vertical='top')
                if isinstance(cell.value,float): cell.number_format='#,##0.00'
                if isinstance(cell.value,datetime):cell.number_format='dd/mm/yyyy hh:mm:ss'
                if cell.row in (1,4):
                    cell.fill=PatternFill('solid',fgColor='15283F');cell.font=Font(name='Calibri',size=14 if cell.row==1 else 11,bold=True,color='FFFFFF')
            if row[0].row>=4:ws.row_dimensions[row[0].row].height=44
        for i,h in enumerate(headers,1):ws.column_dimensions[ws.cell(4,i).column_letter].width=36 if i==1 else 27
        ws.freeze_panes='B5';ws.auto_filter.ref=f'A4:{ws.cell(max(4,ws.max_row),len(headers)).coordinate}'
        ws.sheet_view.showGridLines=False;ws.print_title_rows='1:4'
        ws.sheet_properties.pageSetUpPr.fitToPage=True;ws.page_setup.orientation='landscape';ws.page_setup.fitToWidth=1;ws.page_setup.fitToHeight=0
        return ws
    sheet('Escopo e metodologia','Critérios, filtros e definições utilizados neste relatório.',['Item','Descrição / valor'],scope_rows(c)+[[NAMES[s],DESCRIPTIONS[s]] for s in sections(c)])
    titles={'uptime':['Uptime configuração','Uptime resumo','Uptime incidentes resumo','Uptime incidentes','Uptime respostas','Uptime lacunas'],
            'analysis':['Análise metas','Análise estabilidade','Análise horários','Análise correlação']}
    tables=report_tables(rows,c)
    for section in ('uptime','analysis'):
        for i,(heading,desc,headers,records) in enumerate(tables.get(section,[])):
            sheet(titles[section][i],heading+'. '+desc,headers,records)
    for ws in wb:
        ws.oddFooter.center.text='SpeedPanel | Página &P de &N'
    import math
    ws=wb['Escopo e metodologia'];ws.column_dimensions['B'].width=115
    for row in ws.iter_rows(min_row=5):
        ws.row_dimensions[row[0].row].height=min(409,max(44,15*max(math.ceil(len(str(cell.value or ''))/ws.column_dimensions[cell.column_letter].width) for cell in row)))
    out=io.BytesIO();wb.save(out);return out.getvalue()


def make_pdf(rows,c):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4,landscape
    from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,LongTable,TableStyle,PageBreak,Table,KeepTogether
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.graphics.shapes import Drawing,String,Rect
    from reportlab.graphics.charts.lineplots import LinePlot
    from pathlib import Path
    font='Helvetica';bold='Helvetica-Bold'
    directory=Path('/usr/share/fonts/truetype/dejavu')
    if not directory.exists():directory=Path('C:/Windows/Fonts')
    regular=directory/('DejaVuSans.ttf' if (directory/'DejaVuSans.ttf').exists() else 'arial.ttf')
    heavy=directory/('DejaVuSans-Bold.ttf' if (directory/'DejaVuSans-Bold.ttf').exists() else 'arialbd.ttf')
    if regular.exists() and heavy.exists():
        if 'PanelSans' not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont('PanelSans',str(regular)));pdfmetrics.registerFont(TTFont('PanelSans-Bold',str(heavy)))
        font='PanelSans';bold='PanelSans-Bold'
    styles=getSampleStyleSheet()
    for name in ('Normal','BodyText','Heading1','Heading2','Title'):styles[name].fontName=font
    styles['Heading2'].keepWithNext=True
    styles.add(ParagraphStyle('ReportTitle',fontName=bold,fontSize=26,leading=32,textColor=colors.HexColor('#15283F'),spaceAfter=16))
    styles.add(ParagraphStyle('ReportBody',fontName=font,fontSize=9,leading=13,spaceAfter=9,textColor=colors.HexColor('#43546A')))
    styles.add(ParagraphStyle('ReportCell',fontName=font,fontSize=7,leading=10,wordWrap='CJK'))
    styles.add(ParagraphStyle('ReportHeader',fontName=bold,fontSize=7,leading=10,textColor=colors.white))
    styles.add(ParagraphStyle('ReportIntro',parent=styles['ReportBody'],keepWithNext=True))
    def text(v):
        if v is None:return '-'
        if isinstance(v,datetime):return v.strftime('%d/%m/%Y %H:%M:%S')
        if isinstance(v,float):return display(v)
        return str(v)
    def para(v,style='ReportBody'):return Paragraph(escape(text(v)).replace('\n','<br/>'),styles[style])
    width,height=landscape(A4);available=width-72;out=io.BytesIO()
    class ReportDocument(SimpleDocTemplate):
        def afterPage(self):footer(self.canv,self)
    doc=ReportDocument(out,pagesize=(width,height),leftMargin=36,rightMargin=36,topMargin=42,bottomMargin=36,title='SpeedPanel - Relatório de conexão',author='SpeedPanel')
    story=[para('Relatório de qualidade e disponibilidade','ReportTitle'),para('SPEEDPANEL | HISTÓRICO DA CONEXÃO')]
    def table(headers,records,widths=None):
        if widths is None:
            def weight(h):
                h=h.lower()
                if h=='id':return .4
                if 'descrição' in h or h=='destino':return 3
                if h in ('monitor','serviço'):return 2
                if h in ('data / hora','início','fim','início observado','fim observado','hora'):return 1.5
                return 1
            weights=[weight(h) for h in headers]
            widths=[available*w/sum(weights) for w in weights]
        data=[[para(h,'ReportHeader') for h in headers]]
        data += [[para(v,'ReportCell') for v in row] for row in records] if records else [[para('Sem medições / sem dados neste período.','ReportCell')]+[para('-','ReportCell')]*(len(headers)-1)]
        t=LongTable(data,colWidths=widths,repeatRows=1,splitInRow=1)
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#15283F')),('VALIGN',(0,0),(-1,-1),'TOP'),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#F1F5FA')]),('TOPPADDING',(0,0),(-1,-1),6),('BOTTOMPADDING',(0,0),(-1,-1),6),('LINEBELOW',(0,0),(-1,0),1,colors.HexColor('#53B99B'))]))
        return t
    story += [table(['Escopo','Seleção'],scope_rows(c),[170,available-170]),Spacer(1,12)]
    for s in sections(c):story.append(para(NAMES[s]+': '+{'speed':'resumo, gráficos, histórico e detalhes das regras.','uptime':'cobertura, disponibilidade, incidentes, resposta e períodos sem observação.','analysis':'metas contratadas, percentis, qualidade por horário e correlação.'}[s]))

    def chart(series,title,unit,connect=True):
        d=Drawing(available,170);d.add(String(0,159,title,fontName=bold,fontSize=11,fillColor=colors.HexColor('#15283F')))
        indices=[i for i,s in enumerate(series) if s[1]]
        series=[s for s in series if s[1]]
        if not series:d.add(String(0,95,'Sem respostas / medições disponíveis para o gráfico.',fontName=font,fontSize=9));return d
        origin=min(v[0] for _,points in series for v in points);last=max(v[0] for _,points in series for v in points)
        if last==origin:origin-=1800;last+=1800
        plot=LinePlot();plot.x=55;plot.y=35;plot.width=available-80;plot.height=98
        plot.data=[[(t-origin,v) for t,v in points] for _,points in series]
        plot.xValueAxis.valueMin=0;plot.xValueAxis.valueMax=max(1,last-origin);plot.xValueAxis.valueStep=plot.xValueAxis.valueMax/4
        plot.xValueAxis.labelTextFormat=lambda t:datetime.fromtimestamp(origin+t,ZoneInfo(c['timezone'])).strftime('%H:%M:%S' if last-origin<300 else '%d/%m %H:%M')
        plot.xValueAxis.labels.fontName=font;plot.xValueAxis.labels.fontSize=7
        plot.yValueAxis.valueMin=0;plot.yValueAxis.valueMax=max(1,max(v for _,points in series for _,v in points))*1.12
        plot.yValueAxis.labels.fontName=font;plot.yValueAxis.labels.fontSize=7;plot.yValueAxis.visibleGrid=True;plot.yValueAxis.gridStrokeColor=colors.HexColor('#DCE5EF')
        for i in range(len(series)):
            color=colors.HexColor(PALETTE[indices[i]%len(PALETTE)])
            plot.lines[i].strokeColor=color if connect else None;plot.lines[i].strokeWidth=1.2
            from reportlab.graphics.widgets.markers import makeMarker
            plot.lines[i].symbol=makeMarker('FilledCircle');plot.lines[i].symbol.size=3;plot.lines[i].symbol.fillColor=color
        d.add(plot);d.add(String(0,136,unit,fontName=font,fontSize=8));return d

    def heatmap(a):
        d=Drawing(available,155);left=38;cw=(available-left)/24;ch=16
        cells={(v['day'],v['hour']):v for v in a['heatmap']}
        vals=[v['download'] for v in cells.values() if v['download'] is not None]
        lo=min(vals,default=0);hi=max(vals,default=0)
        for hour in range(24):d.add(String(left+cw*hour+3,142,str(hour)+'h',fontName=font,fontSize=6))
        for day,name in enumerate(['Seg','Ter','Qua','Qui','Sex','Sáb','Dom']):
            y=121-day*ch;d.add(String(0,y+5,name,fontName=font,fontSize=7))
            for hour in range(24):
                cell=cells.get((day,hour));v=cell['download'] if cell else None
                strength=(.5 if hi==lo else (v-lo)/(hi-lo)) if v is not None else 0
                color=colors.Color(.86-.45*strength,.96-.2*strength,.92-.25*strength) if v is not None else colors.HexColor('#F1F5FA')
                x=left+cw*hour;d.add(Rect(x,y,cw-1,ch-1,fillColor=color,strokeColor=colors.HexColor('#C44751') if cell and cell['errors'] else None,strokeWidth=.7))
                d.add(String(x+3,y+5,'-' if v is None else f'{v:.1f}',fontName=font,fontSize=5.5,fillColor=colors.HexColor('#15283F')))
        return d

    tables=report_tables(rows,c)
    for s in ('speed','uptime','analysis'):
        if s not in sections(c):continue
        story += [PageBreak(),para(NAMES[s],'ReportTitle'),para(DESCRIPTIONS[s])]
        if s=='speed':
            story.append(para(f'{len(rows)} registros selecionados: {len(completed(rows))} concluídos; {sum(r["status"]=="error" for r in rows)} falhas; {sum(r["status"]=="warning" for r in rows)} sinalizados por regras.'))
            for metric,label in METRICS[:2]:
                series=[(g[-1]['provider_name'],[(r['created'],r[metric]) for r in completed(g) if r.get(metric) is not None]) for g in groups(rows)]
                legend=' | '.join('<font color="'+PALETTE[i%len(PALETTE)]+'">'+escape(name)+'</font>' for i,(name,_) in enumerate(series))
                story += [KeepTogether([chart(series,label,'Mbps'),Paragraph('Séries: '+legend,styles['ReportBody'])])]
        if s=='uptime':
            ps=c['uptime']['monitors'];story.append(para(str(len(ps))+' monitores com configuração/histórico disponível. Se o próprio servidor desligar, a ausência de coleta é exibida como sem dados.'))
            series=[(p['name'],[(v['created'],v['ms']) for v in p['points']]) for p in ps]
            legend=' | '.join('<font color="'+PALETTE[i%len(PALETTE)]+'">'+escape(name)+'</font>' for i,(name,_) in enumerate(series))
            story += [KeepTogether([chart(series,'Tempo médio de resposta por hora','ms',False),Paragraph('Séries: '+legend+'. Pontos sem ligação para não sugerir continuidade durante lacunas.',styles['ReportBody'])])]
        if s=='analysis':
            a=c['analysis'];story.append(para(f'Meta vigente: {display(a["config"]["target_pct"])}% do contratado. Amostras concluídas: {a["completed"]}; falhas de teste: {a["errors"]}.'))
            for k,label in [('download','Download'),('upload','Upload')]:
                t=a['targets'][k]
                story.append(para(label+(': '+display(t['delivered_pct'])+'% da velocidade contratada, em média; '+str(t['below'])+' de '+str(t['count'])+' medições abaixo de '+display(t['threshold'])+' Mbps.' if t['delivered_pct'] is not None else ': sem comparação disponível; confira a velocidade contratada e a existência de amostras.')))
            story += [KeepTogether([para('Mapa de download médio por horário','Heading2'),para('Valores em Mbps. Tons mais fortes representam médias maiores; contorno vermelho indica falha de teste no horário. Traço indica ausência de download medido. Fuso: '+c['timezone']+'.'),heatmap(a)])]
        for heading,desc,headers,records in tables[s]:
            story += [Spacer(1,14),para(heading,'Heading2'),para(desc,'ReportIntro'),table(headers,records)]
    def footer(canvas,document):
        canvas.saveState();canvas.setFont(font,7);canvas.setFillColor(colors.HexColor('#617086'))
        canvas.drawString(36,height-23,'SPEEDPANEL | Relatório da conexão');canvas.drawRightString(width-36,height-23,c['generated'].strftime('%d/%m/%Y %H:%M')+' | '+c['timezone'])
        canvas.drawString(36,18,'Histórico persistente | Ausência de dados não equivale a valor zero');canvas.drawRightString(width-36,18,'Página '+str(document.page));canvas.restoreState()
    doc.build(story);return out.getvalue()
