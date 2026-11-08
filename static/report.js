const reportDialog=document.createElement('dialog');reportDialog.id='reportDialog';reportDialog.setAttribute('aria-labelledby','reportTitle');
reportDialog.innerHTML=`<form id="reportForm"><div class="sectionhead"><h2 id="reportTitle">Configurar relatório</h2><button id="closeReport" type="button">Fechar</button></div><p>Escolha as seções do documento. O relatório usa todo o histórico do período selecionado, sem o corte da tabela da tela.</p><p id="reportScope" class="hint"></p><fieldset style="border:1px solid var(--line);border-radius:8px;padding:18px"><legend>Seções incluídas</legend><label class="check"><input type="checkbox" name="reportSection" value="speed" checked>Velocidade da conexão</label><p class="hint">Resumo por serviço, gráficos de download/upload, histórico completo e detalhes das regras.</p><label class="check"><input type="checkbox" name="reportSection" value="uptime" checked>Disponibilidade e uptime</label><p class="hint">Cobertura, disponibilidade, incidentes, tempos de resposta, lacunas, pausas e manutenção de todos os monitores.</p><label class="check"><input type="checkbox" name="reportSection" value="analysis" checked>Análises da conexão</label><p class="hint">Velocidade contratada, metas, mediana e P95, qualidade por horário e correlação com uptime.</p></fieldset><label class="check" style="margin-top:20px"><input id="reportHideFailed" type="checkbox">Ocultar testes de velocidade com falha neste relatório</label><p class="hint">Essa opção também afeta as análises dessas medições. Incidentes de uptime continuam incluídos. Descrições, critérios de cálculo e limitações acompanham cada seção.</p><p id="reportError" class="errorText" role="alert"></p><button id="generateReport" class="primary">Gerar relatório</button></form>`;
document.body.append(reportDialog);let reportKind='pdf',reportQuery;
function openReport(kind){
 reportKind=kind;reportQuery=params();$('reportTitle').textContent='Configurar relatório '+kind.toUpperCase();$('reportHideFailed').checked=$('hideFailed').checked;$('reportError').textContent='';
 const selected=$('filterProvider').selectedOptions[0]?.textContent||'Todos os serviços';
 $('reportScope').textContent='Período: '+($('from').value?new Date($('from').value).toLocaleString('pt-BR'):'últimos 7 dias')+' até '+($('to').value?new Date($('to').value).toLocaleString('pt-BR'):'agora')+'. Serviço de velocidade: '+selected+'. Uptime: todos os monitores, no mesmo período. Datas do filtro no horário local do navegador.';
 reportDialog.showModal();
}
$('closeReport').onclick=()=>reportDialog.close();
$('hideFailed').checked=localStorage.getItem('speedpanel.hideFailed')==='1';
$('hideFailed').onchange=()=>{localStorage.setItem('speedpanel.hideFailed',$('hideFailed').checked?'1':'0');page=0;loadResults().catch(e=>notice(e.message))};
$('reportForm').onsubmit=async e=>{
 e.preventDefault();const chosen=[...reportDialog.querySelectorAll('[name=reportSection]:checked')].map(c=>c.value);
 if(!chosen.length){$('reportError').textContent='Selecione pelo menos uma seção.';return}
 const query=new URLSearchParams(reportQuery);query.set('sections',chosen.join(','));query.set('hide_failed',$('reportHideFailed').checked?'1':'0');
 const button=$('generateReport');button.textContent='Gerando relatório…';$('closeReport').disabled=true;
 try{const ok=await downloadReport(reportKind,button,query);if(ok)reportDialog.close()}finally{button.textContent='Gerar relatório';$('closeReport').disabled=false}
};
