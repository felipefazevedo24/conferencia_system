/* Prévia independente: dados fictícios, sem APIs, autenticação ou acesso ao banco. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const budgets = [
    {id:'DEMO-1042',client:'Cliente demonstrativo A',date:'2026-09-22',orders:['DEMO-7807','DEMO-7808'],sector:'Usinagem',items:12,label:'Em produção',color:'amber'},
    {id:'DEMO-1048',client:'Cliente demonstrativo B',date:'2026-09-25',orders:['DEMO-7812'],sector:'Montagem',items:6,label:'Em montagem',color:'amber'},
    {id:'DEMO-1051',client:'Cliente demonstrativo C',date:'2026-09-28',orders:[],sector:'',items:null,label:'OS não gerada',color:''},
    {id:'DEMO-1056',client:'Cliente demonstrativo D',date:'2026-09-30',orders:['DEMO-7820'],sector:'Montagem',items:4,label:'Produção concluída',color:'green'},
    {id:'DEMO-1063',client:'Cliente demonstrativo E',date:'2026-10-08',orders:['DEMO-7831'],sector:'Usinagem',items:3,label:'Em produção',color:'amber'}
  ];
  const pieces = [{code:'001',name:'Conjunto principal',qty:1},{code:'002',name:'Flange de fixação',qty:4},{code:'003',name:'Corpo usinado',qty:2},{code:'004',name:'Suporte lateral',qty:6}];
  let selected = null, selectedOrder = '', selectedPiece = '003';
  const dateText = value => new Intl.DateTimeFormat('pt-BR',{day:'2-digit',month:'2-digit',year:'numeric'}).format(new Date(value+'T12:00:00'));
  const cube = '<svg viewBox="0 0 64 56" aria-hidden="true"><path d="M32 3 59 17 32 32 5 17Z" fill="#b6cce3" stroke="#7395b8"/><path d="M5 17 32 32 32 53 5 38Z" fill="#6482a4" stroke="#7395b8"/><path d="M32 32 59 17 59 38 32 53Z" fill="#3d5b7e" stroke="#7395b8"/><path d="M23 12 42 22 42 34" fill="none" stroke="#e3edf8" stroke-width="3"/></svg>';
  function renderList() {
    const month = $('month').value;
    const term = $('search').value.trim().toLocaleLowerCase('pt-BR');
    const filtered = budgets.filter(b => b.date.startsWith(month+'-') && (!$('sector').value || b.sector === $('sector').value) && [b.id,b.client,...b.orders].join(' ').toLocaleLowerCase('pt-BR').includes(term));
    if (!filtered.some(b => b.id === selected?.id)) { selected = filtered[0] || null; selectedOrder=selected?.orders[0] || ''; selectedPiece='003'; }
    $('count').textContent = filtered.length;
    $('period-label').textContent = month ? 'Entrega em '+new Intl.DateTimeFormat('pt-BR',{month:'long',year:'numeric'}).format(new Date(month+'-01T12:00:00')) : 'Selecione um mês e ano';
    $('budget-list').replaceChildren();
    for (const b of filtered) {
      const button=document.createElement('button');button.type='button';button.className='budget'+(b.id===selected?.id?' selected':'');button.setAttribute('aria-pressed',String(b.id===selected?.id));
      button.innerHTML=`<div class="budget-head"><strong>${b.id}</strong><span class="${b.color}"><span class="dot"></span></span></div><p>${b.client}</p><div class="budget-bottom"><span>${dateText(b.date).slice(0,5)}${b.items===null?'':' · '+b.items+' peças'}</span><span class="${b.color}">${b.label}</span></div>`;
      button.onclick=()=>{selected=b;selectedOrder=b.orders[0]||'';selectedPiece='003';renderList();};$('budget-list').append(button);
    }
    if(!filtered.length){const empty=document.createElement('div');empty.className='empty';empty.textContent=term||$('sector').value?'Nenhum orçamento corresponde aos filtros.':'Nenhum orçamento com entrega prevista para este período.';$('budget-list').append(empty);}
    renderDelivery();
  }
  function pieceCard(p){return `<button type="button" class="piece ${p.code===selectedPiece?'selected':''}" data-piece="${p.code}" aria-pressed="${p.code===selectedPiece}"><div class="piece-top">${cube}<div><strong>${selectedOrder}/${p.code}</strong><small>${p.name}</small></div></div><div class="piece-bottom"><span>Qtd. ${p.qty}</span><span class="${p.code==='002'?'green':'amber'}">${p.code==='002'?'Finalizada':p.code==='001'?'Em produção':'Em fabricação'}</span></div></button>`;}
  function renderDelivery(){
    if(!selected){$('delivery').innerHTML='<div class="panel empty"><strong>Nenhuma entrega selecionada</strong><p>Escolha outro período ou ajuste os filtros.</p></div>';return;}
    $('delivery').innerHTML=`<div class="delivery-info panel"><div><span class="info-label">CLIENTE</span><span class="info-value">${selected.client}</span></div><div><span class="info-label">ENTREGA PREVISTA</span><span class="info-value">${dateText(selected.date)}</span></div><div><span class="info-label">ORÇAMENTO</span><span class="info-value">${selected.id}</span></div><div><label class="info-label" for="order">ORDEM DE SERVIÇO${selected.orders.length>1?' · '+selected.orders.length+' OS':''}</label>${selected.orders.length?`<select id="order">${selected.orders.map(o=>`<option ${o===selectedOrder?'selected':''}>${o}</option>`).join('')}</select>`:'<span class="info-value">OS não gerada</span>'}</div></div><div id="detail-content"></div>`;
    if(!selected.orders.length){$('detail-content').innerHTML='<div class="panel empty"><strong>OS não gerada</strong><p>O orçamento permanece no cronograma.<br>A estrutura estará disponível quando houver uma OS vinculada.</p></div>';return;}
    $('order').onchange=e=>{selectedOrder=e.target.value;selectedPiece='003';renderDelivery();};
    if(selectedOrder==='DEMO-7808'){$('detail-content').innerHTML='<div class="panel empty"><strong>OS sem estrutura cadastrada</strong><p>Nenhuma peça ou conjunto disponível para esta OS.</p></div>';return;}
    $('detail-content').innerHTML=`<section class="panel"><div class="section-head"><div><h2>Mapa visual da OS</h2><p>${selectedOrder} · conjunto principal e componentes</p></div><span class="hint">Selecione uma peça para ver as operações</span></div><div class="map"><div class="root-node">${pieceCard(pieces[0])}</div><div class="children">${pieces.slice(1).map(pieceCard).join('')}</div></div><div class="map-legend"><span class="green">● Finalizada</span><span class="amber">● Em andamento</span><span>● Aguardando</span><span class="red">● Bloqueada</span></div></section><section class="panel sequence"><div class="section-head"><div><h2>Sequência de Montagem — ${selectedOrder}/${selectedPiece}</h2><p>${pieces.find(p=>p.code===selectedPiece).name}</p></div><span id="current" class="current-pill"></span></div><div id="operations"></div></section>`;
    document.querySelectorAll('[data-piece]').forEach(button=>button.onclick=()=>{selectedPiece=button.dataset.piece;renderDelivery();});
    if(selectedPiece==='004'){$('current').hidden=true;$('operations').innerHTML='<div class="empty"><strong>Peça sem sequência de operações</strong><p>Nenhuma operação cadastrada para esta peça.</p></div>';return;}
    const completed=selectedPiece==='002'||selected.id==='DEMO-1056';
    $('current').textContent=completed?'Operações finalizadas':'Operação atual: Seq. 20';
    const ops=[['10','Corte','Serra CNC','Preparação'],['20','Usinagem','Centro CNC 02','Usinagem'],['30','Montagem','Bancada 01','Montagem']];
    $('operations').innerHTML='<div class="sequence-scroll" tabindex="0" aria-label="Sequência de operações com rolagem horizontal">'+ops.map((op,i)=>`${i?'<span class="arrow" aria-hidden="true">→</span>':''}<article class="operation ${i===1&&!completed?'current':''}"><div class="op-top"><span class="step">${i+1}</span><span>Seq. ${op[0]}</span></div><h3>${op[1]}</h3><p>${selectedOrder}/${selectedPiece}</p><p>${op[3]} · ${op[2]}</p><span class="op-status ${completed||i===0?'green':i===1?'amber':''}">● ${completed||i===0?'Finalizada':i===1?'Em execução agora':'Aguardando'}</span></article>`).join('')+'</div>';
  }
  $('month').onchange=()=>{selected=null;renderList();};$('sector').onchange=renderList;$('search').oninput=renderList;
  function shiftMonth(amount){const value=$('month').value;if(!value)return;const d=new Date(value+'-01T12:00:00');d.setMonth(d.getMonth()+amount);$('month').value=`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`;selected=null;renderList();}
  $('previous').onclick=()=>shiftMonth(-1);$('next').onclick=()=>shiftMonth(1);
  $('theme').onclick=()=>{document.documentElement.dataset.theme=document.documentElement.dataset.theme==='escuro'?'claro':'escuro';};
  $('toggle-list').onclick=()=>{const open=$('toggle-list').getAttribute('aria-expanded')==='true';$('toggle-list').setAttribute('aria-expanded',String(!open));$('budget-list').hidden=open;};
  renderList();
})();
