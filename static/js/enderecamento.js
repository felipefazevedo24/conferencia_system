(() => {
 'use strict';
 const $ = id => document.getElementById(id), base = '/api/enderecamento';
 const manage = $('end-module').dataset.manage === 'true';
 let page = 1, historyPage = 1, balances = [], busy = false, key, submitted = null, scanner, scanTarget, scanStarting = false;
 let listVersion = 0, historyVersion = 0, placesVersion = 0, atuais = null, atuaisSku = '';
 const norm = value => String(value||'').trim().replace(/\s+/g,' ').toUpperCase();
 const node = (tag, text, cls) => {const n=document.createElement(tag); if(text!=null)n.textContent=text; if(cls)n.className=cls; return n;};
 const number = value => Number(value).toLocaleString('pt-BR', {maximumFractionDigits:6});
 const date = value => new Date(value).toLocaleString('pt-BR');
 const message = (text, work=false) => $(work?'end-work-feedback':'end-feedback').textContent=text;
 async function requestAbsolute(path, body) {
  const resp = await fetch(path, body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data=await resp.json().catch(()=>({}));
  if(!resp.ok){const err=new Error(data.erro||data.error||'Não foi possível concluir. Verifique a conexão e tente novamente.');err.status=resp.status;throw err;}
  return data;
 }
 const request = (path, body) => requestAbsolute(base+path, body);
 function button(text, action, cls='eui-btn--secondary') {const b=node('button',text,'eui-btn '+cls);b.type='button';b.onclick=action;return b;}
 function cell(row,label,value,cls) {const td=node('td',null,cls);td.dataset.label=label;td.append(value instanceof Node?value:document.createTextNode(value));row.append(td);}
 async function refresh() {
  const version=++listVersion;
  try {
   const data=await request('/saldos?'+new URLSearchParams({busca:$('end-search').value,pagina:page}));
   if(version!==listVersion)return;
   balances=data.itens; $('end-balances').replaceChildren();
   for(const [name,value] of Object.entries(data.metricas))$('end-kpi-'+name).textContent=value;
   if(data.erro)message(data.erro);
   for(const s of balances){
    const row=node('tr');cell(row,'Endereço',node('span',s.endereco,'end-location'));cell(row,'SKU',s.sku,'end-code');
    cell(row,'Descrição',s.descricao||'—');
    const actions=node('div',null,'end-actions');actions.append(button('Movimentar',()=>open(s)));
    actions.append(button('Histórico',()=>{$('end-history-search').value=s.sku;historyPage=1;showPanel('historico');}));
    cell(row,'Ações',actions);$('end-balances').append(row);
   }
   if(!balances.length&&!data.erro){const tr=node('tr'),td=node('td','Nenhum endereço encontrado no GRV para este filtro.','end-empty');td.colSpan=4;tr.append(td);$('end-balances').append(tr);}
   $('end-page').textContent=`Página ${page} · ${data.total} registro(s)`;$('end-prev').disabled=page===1;$('end-next').disabled=page*40>=data.total;
  }catch(e){message(e.message);}
 }
 async function history(){
  const version=++historyVersion;
  try{
   const data=await request('/historico?'+new URLSearchParams({busca:$('end-history-search').value,pagina:historyPage,pendentes:$('end-only-pending').checked?'1':'0'}));
   if(version!==historyVersion)return;
   $('end-history').replaceChildren();
   if(!data.itens.length)$('end-history').append(node('p','Nenhuma movimentação para este filtro.','end-empty'));
   for(const m of data.itens){
    const card=node('article'),head=node('div',null,'end-history-head');
    head.append(node('strong',`${m.sku} · ${m.tipo}`),node('span',m.sincronizado?'Sincronizado':'Envio pendente','end-badge'+(m.sincronizado?'':' warn')));card.append(head);
    card.append(node('p',`${number(m.quantidade)} ${m.unidade} · ${m.origem||(m.tipo==='Recebimento'?'Entrada / contagem':'Sem origem')} → ${m.destino||'Endereços do recebimento'}`));
    if(m.detalhes?.depois)card.append(node('p',`Endereços do material após a operação: ${m.detalhes.depois.join(' · ')}`));
    if(m.detalhes?.alocacoes)card.append(node('p',m.detalhes.alocacoes.map(a=>`${a.endereco}: ${number(a.quantidade)} ${m.unidade}`).join(' · ')));
    if(m.detalhes?.diferenca!=null)card.append(node('p',`Saldo anterior: ${number(m.detalhes.destino_antes)} · Ajuste: ${number(m.detalhes.diferenca)} ${m.unidade}`));
    card.append(node('p',`${date(m.criado_em)} · ${m.usuario} · ${m.motivo||''}`));
    if(m.erro)card.append(node('p',m.erro,'end-feedback'));
    if(!m.sincronizado){const retry=button('Tentar sincronizar',async()=>{retry.disabled=true;try{const r=await request('/sincronizar',{sku:m.sku});message(r.pendente?'Operação salva. O envio continua pendente; consulte o detalhe.':'Endereços sincronizados com o GRV.');await Promise.all([history(),refresh()]);}catch(e){message(e.message);}finally{retry.disabled=false;}});card.append(retry);}
    $('end-history').append(card);
   }
   $('end-history-page').textContent=`Página ${historyPage} · ${data.total} operação(ões)`;$('end-history-prev').disabled=historyPage===1;$('end-history-next').disabled=historyPage*40>=data.total;
  }catch(e){message(e.message);}
 }
 async function places(){
  const version=++placesVersion;
  try{
   const data=await request('/locais?'+new URLSearchParams({busca:$('end-place-search').value}));
   if(version!==placesVersion)return;
   $('end-places').replaceChildren();
   if(data.indisponivel)message('Ocupação indisponível: a consulta ao GRV falhou. A lista de endereços continua válida.');
   for(const l of data.itens){
    const row=node('tr');cell(row,'Endereço',node('span',l.codigo,'end-location'));
    cell(row,'Materiais agora',l.materiais?`${l.materiais} material(is)`:'Vazio');
    cell(row,'Situação',node('span',l.ativo?'Ativo':'Desativado','end-badge'+(l.ativo?'':' warn')));
    const actions=node('div',null,'end-actions');
    actions.append(button('Ver materiais',()=>{$('end-search').value=l.codigo;page=1;showPanel('saldos');}));
    if(manage)actions.append(button(l.ativo?'Desativar':'Ativar',async()=>{
     try{await requestAbsolute('/api/recebimento/enderecamento/locais',{codigo:l.codigo,ativo:!l.ativo});await places();}
     catch(e){message(e.message);}}));
    cell(row,'Ações',actions);$('end-places').append(row);
   }
   if(!data.itens.length){const tr=node('tr'),td=node('td','Nenhum endereço encontrado. Eles passam a existir conforme o operador usa cada local.','end-empty');td.colSpan=4;tr.append(td);$('end-places').append(tr);}
  }catch(e){message(e.message);}
 }
 function showPanel(name){
  document.querySelectorAll('.end-tabs button').forEach(b=>{const on=b.dataset.panel===name;b.classList.toggle('active',on);b.setAttribute('aria-pressed',String(on));});
  for(const p of ['saldos','locais','receber','historico'])$('end-panel-'+p).hidden=p!==name;
  if(name==='historico')history();
  if(name==='saldos')refresh();
  if(name==='locais')places();
  if(name==='receber')document.dispatchEvent(new CustomEvent('recebimento:abrir-enderecamento'));
 }
 function open(s={}){
  key=crypto.randomUUID?crypto.randomUUID():Array.from(crypto.getRandomValues(new Uint32Array(4)),n=>n.toString(16)).join('-');submitted=null;
  $('end-form').reset();message('',true);atuais=null;atuaisSku='';$('end-current').textContent='';
  $('end-sku').value=s.sku||'';$('end-unit').value=s.unidade||'';$('end-origin').value=s.endereco||'';
  $('end-destination').value='';$('end-quantity').value='';
  $('end-work').showModal();preview();$('end-sku').focus();
  if(s.sku)consultarMaterial();
 }
 // Consulta ao vivo no GRV: só ao terminar de informar o SKU, nunca a cada tecla.
 async function consultarMaterial(){
  const sku=$('end-sku').value.trim();
  if(!sku||sku===atuaisSku)return;
  atuaisSku=sku;atuais=null;$('end-current').textContent='Consultando os endereços deste material no GRV…';
  try{
   const data=await request('/material?'+new URLSearchParams({sku}));
   if($('end-sku').value.trim()!==sku)return;
   atuais=data.enderecos;
   $('end-current').textContent=atuais.length?`Hoje em: ${atuais.join(' · ')}`:'Este material ainda não tem endereço no GRV.';
  }catch(e){atuais=null;if($('end-sku').value.trim()===sku)$('end-current').textContent=e.message;}
  preview();
 }
 function preview(){
  const q=$('end-quantity').value||'…',unit=$('end-unit').value||'',sku=$('end-sku').value||'Material';
  const origem=norm($('end-origin').value),destino=norm($('end-destination').value);
  let linha=`${sku} · ${q} ${unit} · ${$('end-origin').value||'sem origem'} → ${$('end-destination').value||'Destino'}`;
  // Não prometer um resultado que o servidor vai recusar.
  if(atuais&&origem&&!atuais.some(e=>norm(e)===origem)){
   linha+=' · A origem não consta nos endereços atuais deste material';
  }else if(atuais&&destino){
   const depois=atuais.filter(e=>!(origem&&norm(e)===origem));
   if(!depois.some(e=>norm(e)===destino))depois.push(destino);
   linha+=` · Depois: ${depois.join(' · ')}`;
  }
  $('end-preview').textContent=linha;
 }
 function setBusy(value){busy=value;for(const elem of $('end-form').elements)elem.disabled=value;}
 $('end-form').onsubmit=async e=>{
  e.preventDefault();if(busy)return;
  const data={chave:key,sku:$('end-sku').value.trim(),origem:$('end-origin').value.trim(),destino:$('end-destination').value.trim(),quantidade:$('end-quantity').value.trim(),unidade:$('end-unit').value.trim(),motivo:$('end-reason').value.trim()};
  // Resultado de rede incerto: repetir exatamente a mesma chave e conteúdo.
  if(submitted&&JSON.stringify(data)!==submitted){message('A tentativa anterior pode ter sido salva. Consulte o histórico antes de alterar os dados e iniciar outra operação.',true);return;}
  submitted=JSON.stringify(data);setBusy(true);message('Registrando operação…',true);
  try{
   const result=await request('/movimentar',data);
   $('end-work').close();message(result.item.sincronizado?'Operação registrada e endereços sincronizados com o GRV.':'Operação registrada no Sync. O envio ao GRV está pendente e será repetido automaticamente.');
   await Promise.all([refresh(),history()]);
  }catch(err){message(err.message,true);/* Preserve chave em falha de rede; validações podem ser corrigidas. */
   if(err.status===409||err.status===403)submitted=null;
  }finally{setBusy(false);}
 };
 // Bipar a etiqueta na busca responde "o que tem neste endereço?"; no diálogo,
 // alimenta a prévia da operação.
 function aposLeitura(target){
  if(target==='end-search'){page=1;refresh();return;}
  if(target==='end-place-search'){places();return;}
  preview();
 }
 async function stopCamera(){const current=scanner;scanner=null;try{if(current?.isScanning)await current.stop();current?.clear();}catch(_){}if($('end-camera').open)$('end-camera').close();if(scanTarget&&$('end-work').open)$(scanTarget).focus();}
 async function scan(target){
  if(busy||scanStarting||scanner)return;scanStarting=true;scanTarget=target;$('end-camera-feedback').textContent='';$('end-camera').showModal();
  try{const instance=new Html5Qrcode('end-reader');scanner=instance;let accepted=false;await instance.start({facingMode:'environment'},{fps:10,qrbox:220},async text=>{if(accepted||scanner!==instance)return;accepted=true;$(target).value=text.trim();aposLeitura(target);await stopCamera();});if(scanner!==instance||!$('end-camera').open){if(instance.isScanning)await instance.stop();instance.clear();}}
  catch(_){$('end-camera-feedback').textContent='Não foi possível abrir a câmera. Cancele e use o leitor ou digite a etiqueta.';}
  finally{scanStarting=false;}
 }
 document.querySelectorAll('[data-scan]').forEach(b=>b.onclick=()=>scan(b.dataset.scan));
 $('end-camera-close').onclick=stopCamera;$('end-camera').addEventListener('cancel',e=>{e.preventDefault();stopCamera();});
 $('end-work').addEventListener('cancel',e=>{if(busy)e.preventDefault();});
 $('end-close').onclick=$('end-cancel').onclick=()=>{if(!busy)$('end-work').close();};
 $('end-form').addEventListener('input',preview);
 $('end-sku').addEventListener('change',consultarMaterial);
 for(const [a,b] of [['end-sku','end-origin'],['end-origin','end-destination'],['end-destination','end-quantity'],['end-quantity','end-unit'],['end-unit','end-reason']])$(a).onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();$(b).focus();}};
 $('end-move').onclick=()=>open();
 $('end-refresh').onclick=refresh;$('end-search').onchange=()=>{page=1;refresh();};$('end-prev').onclick=()=>{page--;refresh();};$('end-next').onclick=()=>{page++;refresh();};
 $('end-place-refresh').onclick=places;$('end-place-search').onchange=places;
 $('end-history-refresh').onclick=history;$('end-history-search').onchange=$('end-only-pending').onchange=()=>{historyPage=1;history();};$('end-history-prev').onclick=()=>{historyPage--;history();};$('end-history-next').onclick=()=>{historyPage++;history();};
 document.querySelectorAll('.end-tabs button').forEach(b=>b.onclick=()=>showPanel(b.dataset.panel));
 document.addEventListener('visibilitychange',()=>{if(document.hidden)stopCamera();});window.addEventListener('pagehide',stopCamera);
 $('end-search').value=new URLSearchParams(location.search).get('sku')||'';
 TabelaOrdenavel.ativar($('end-balances').closest('table'));
 TabelaOrdenavel.ativar($('end-places').closest('table'));
 refresh();
})();
