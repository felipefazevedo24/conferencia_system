(() => {
 'use strict';
 const $ = id => document.getElementById(id), base = '/api/enderecamento';
 const manage = $('end-module').dataset.manage === 'true';
 const admin = $('end-module').dataset.admin === 'true';
 let page = 1, historyPage = 1, balances = [], busy = false, key, submitted = null, scanner, scanTarget, scanStarting = false;
 let listVersion = 0, historyVersion = 0, placesVersion = 0, atuais = null, atuaisSku = '';
 // Barra de busca: "buscar por" + situação + só com saldo.
 let por = 'material', filtro = 'todos', comSaldo = false, situacaoLocal = 'todos', soPendentes = false;
 const PLACEHOLDER = {material:'Código ou descrição do material', endereco:'Endereço exato · use * no fim para a estante inteira (ex.: AL-BI*)', os:'Número da OS (ex.: 11078)'};
 const ROTULO_POR = {material:'material', endereco:'endereço', os:'OS'};
 function marcar(grupo, attr, valor){document.querySelectorAll(`#${grupo} button`).forEach(b=>{const on=b.dataset[attr]===valor;b.classList.toggle('active',on);b.setAttribute('aria-checked',String(on));});}
 function setPor(valor){por=valor;marcar('end-por','por',valor);$('end-search').placeholder=PLACEHOLDER[valor];}
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
   const data=await request('/saldos?'+new URLSearchParams({busca:$('end-search').value,pagina:page,filtro,saldo:comSaldo?'1':'0',por}));
   if(version!==listVersion)return;
   balances=data.itens; $('end-balances').replaceChildren();
   for(const [name,value] of Object.entries(data.metricas))$('end-kpi-'+name).textContent=value;
   if(data.erro)message(data.erro);
   for(const s of balances){
    const row=node('tr');
    cell(row,'Endereço',s.endereco?node('span',s.endereco,'end-location'):node('span','Sem endereço','end-badge warn'));
    cell(row,'SKU',s.sku,'end-code');
    const desc=node('div');desc.append(node('div',s.descricao||'—'));
    if(s.os)desc.append(node('span',`OS ${s.os.n_os} · ${s.os.situacao}`,'end-badge end-badge--os'));
    cell(row,'Descrição',desc);
    cell(row,'Saldo no GRV',`${number(s.saldo)} ${s.unidade||''}`.trim());
    // Sem endereço, "movimentar" é endereçar: sem origem, o destino é o 1º endereço.
    const actions=node('div',null,'end-actions');actions.append(button(s.endereco?'Movimentar':'Endereçar',()=>open(s)));
    // Histórico e sincronização é só de admin (o servidor também confere).
    if(admin)actions.append(button('Histórico',()=>{$('end-history-search').value=s.sku;historyPage=1;showPanel('historico');}));
    cell(row,'Ações',actions);$('end-balances').append(row);
   }
   if(!balances.length&&!data.erro){const tr=node('tr'),td=node('td','Nenhum endereço encontrado no GRV para este filtro.','end-empty');td.colSpan=5;tr.append(td);$('end-balances').append(tr);}
   const termo=$('end-search').value.trim();
   $('end-search-summary').textContent=termo?`${data.total} resultado(s) para ${ROTULO_POR[por]} ${por==='endereco'&&!termo.endsWith('*')?'= ':''}"${termo}"`:`${data.total} registro(s)`;
   $('end-page').textContent=`Página ${page} · ${data.total} registro(s)`;$('end-prev').disabled=page===1;$('end-next').disabled=page*40>=data.total;
  }catch(e){message(e.message);}
 }
 async function history(){
  const version=++historyVersion;
  try{
   const data=await request('/historico?'+new URLSearchParams({busca:$('end-history-search').value,pagina:historyPage,pendentes:soPendentes?'1':'0'}));
   if(version!==historyVersion)return;
   $('end-history').replaceChildren();
   if(!data.itens.length)$('end-history').append(node('p','Nenhuma movimentação para este filtro.','end-empty'));
   for(const m of data.itens){
    const card=node('article'),head=node('div',null,'end-history-head');
    head.append(node('strong',`${m.sku} · ${m.tipo}`),node('span',m.sincronizado?'Sincronizado':'Envio pendente','end-badge'+(m.sincronizado?'':' warn')));card.append(head);
    card.append(node('p',m.tipo==='Endereço desativado'
     ?`Endereço ${m.origem} desativado: o material deixou de apontar para ele`
     :`${number(m.quantidade)} ${m.unidade} · ${m.origem||(m.tipo==='Recebimento'?'Entrada / contagem':'Sem origem')} → ${m.destino||'Endereços do recebimento'}`));
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
   // Situação filtra na tela: a lista de endereços já vem inteira.
   const filtroLocal={todos:()=>true,ocupados:l=>l.ativo&&l.materiais>0,vazios:l=>l.ativo&&!l.materiais,desativados:l=>!l.ativo}[situacaoLocal];
   data.itens=data.itens.filter(filtroLocal);
   const termoLocal=$('end-place-search').value.trim();
   $('end-place-summary').textContent=`${data.itens.length} endereço(s)${termoLocal?` para "${termoLocal}"`:''}`;
   if(data.indisponivel)message('Ocupação indisponível: a consulta ao GRV falhou. A lista de endereços continua válida.');
   for(const l of data.itens){
    const row=node('tr');cell(row,'Endereço',node('span',l.codigo,'end-location'));
    cell(row,'Materiais agora',l.materiais?`${l.materiais} material(is)`:'Vazio');
    cell(row,'Situação',node('span',l.ativo?'Ativo':'Desativado','end-badge'+(l.ativo?'':' warn')));
    const actions=node('div',null,'end-actions');
    actions.append(button('Ver materiais',()=>{setPor('endereco');$('end-search').value=l.codigo;page=1;showPanel('saldos');}));
    if(admin)actions.append(button(l.ativo?'Desativar':'Ativar',()=>alternarLocal(l)));
    cell(row,'Ações',actions);$('end-places').append(row);
   }
   if(!data.itens.length){const tr=node('tr'),td=node('td','Nenhum endereço encontrado. Eles passam a existir conforme o operador usa cada local.','end-empty');td.colSpan=4;tr.append(td);$('end-places').append(tr);}
  }catch(e){message(e.message);}
 }
 // Desativar escreve no GRV em vários materiais de uma vez: confirma antes e
 // conta depois o que saiu e o que não deu.
 async function alternarLocal(l){
  if(l.ativo){
   const aviso=l.materiais
    ? `Desativar ${l.codigo} vai tirar este endereço de ${l.materiais} material(is) no GRV. Confirmar?`
    : `Desativar ${l.codigo}? Ele deixa de receber material.`;
   if(!confirm(aviso))return;
  }
  try{
   const r=await requestAbsolute('/api/recebimento/enderecamento/locais',{codigo:l.codigo,ativo:!l.ativo});
   const rel=r.relatorio;
   if(rel){
    const partes=[`${l.codigo} desativado.`];
    const outros=rel.limpos.filter(s=>!rel.sem_outro_endereco.includes(s));if(outros.length)partes.push(`${outros.length} material(is) deixaram de apontar para ele.`);
    if(rel.sem_outro_endereco.length)partes.push(`${rel.sem_outro_endereco.length} ficaram sem endereço no GRV (só estavam neste). Enderece de novo quando guardar: ${rel.sem_outro_endereco.slice(0,5).join(', ')}${rel.sem_outro_endereco.length>5?'…':''}.`);
    if(rel.falhas.length)partes.push(`${rel.falhas.length} falharam; veja o histórico.`);
    message(partes.join(' '));
   }else message(`${l.codigo} ativado.`);
   await Promise.all([places(),refresh()]);
  }catch(e){message(e.message);}
 }
 function showPanel(name){
  document.querySelectorAll('.end-tabs button').forEach(b=>{const on=b.dataset.panel===name;b.classList.toggle('active',on);b.setAttribute('aria-pressed',String(on));});
  for(const p of ['saldos','locais','receber','historico'])$('end-panel-'+p).hidden=p!==name;
  if(name==='historico'&&!admin)return showPanel('saldos');
  if(name==='historico')history();
  if(name==='saldos')refresh();
  if(name==='locais')places();
  if(name==='receber')document.dispatchEvent(new CustomEvent('recebimento:abrir-enderecamento'));
 }
 // Transferência: sai da origem. Inclusão de endereço: o material passa a constar
 // também no destino (no servidor, é a operação sem origem).
 let modo = 'transferir';
 function setModo(valor){
  modo=valor;
  document.querySelectorAll('.end-mode button').forEach(b=>{const on=b.dataset.mode===valor;b.classList.toggle('active',on);b.setAttribute('aria-checked',String(on));});
  $('end-origin-label').hidden=valor!=='transferir';$('end-origin').required=valor==='transferir';
  if(valor!=='transferir')$('end-origin').value='';
  $('end-destination-label').textContent=`${valor==='transferir'?3:2} · Endereço de destino`;
  preview();
 }
 function open(s={}){
  key=crypto.randomUUID?crypto.randomUUID():Array.from(crypto.getRandomValues(new Uint32Array(4)),n=>n.toString(16)).join('-');submitted=null;
  $('end-form').reset();message('',true);atuais=null;atuaisSku='';$('end-current').replaceChildren();
  $('end-sku').value=s.sku||'';$('end-unit').value=s.unidade||'';$('end-origin').value=s.endereco||'';
  $('end-destination').value='';$('end-quantity').value='';
  // Material sem endereço não tem de onde sair: abre direto em "Inclusão de endereço".
  setModo(s.sku&&!s.endereco?'acrescentar':'transferir');
  $('end-work').showModal();preview();(s.sku?$(modo==='transferir'&&!s.endereco?'end-origin':'end-destination'):$('end-sku')).focus();
  if(s.sku)consultarMaterial();
 }
 // Consulta ao vivo no GRV: só ao terminar de informar o SKU, nunca a cada tecla.
 async function consultarMaterial(){
  const sku=$('end-sku').value.trim();
  if(!sku||sku===atuaisSku)return;
  atuaisSku=sku;atuais=null;$('end-current').replaceChildren(node('span','Consultando o material no GRV…','end-muted'));
  try{
   const data=await request('/material?'+new URLSearchParams({sku}));
   if($('end-sku').value.trim()!==sku)return;
   atuais=data.enderecos||[];
   // Unidade do GRV: evita grafia diferente da do estoque (M x MT).
   if(data.unidade&&!$('end-unit').value.trim())$('end-unit').value=data.unidade;
   const card=node('div',null,'end-material');
   card.append(node('strong',data.descricao||sku));
   const meta=[sku];if(data.saldo!=null)meta.push(`Saldo no GRV: ${number(data.saldo)} ${data.unidade||''}`.trim());
   card.append(node('div',meta.join(' · '),'end-muted'));
   const chips=node('div',null,'end-chips');
   if(atuais.length){
    chips.append(node('span','Hoje em:','end-muted'));
    for(const e of atuais){
     const b=node('button',e,'end-chip'+(norm(e)===norm($('end-origin').value)?' is-origem':''));b.type='button';
     b.title='Usar como origem';
     b.onclick=()=>{setModo('transferir');$('end-origin').value=e;$('end-destination').focus();marcarOrigem();preview();};
     chips.append(b);
    }
   }else chips.append(node('span','Sem endereço no GRV: use "Inclusão de endereço".','end-muted'));
   card.append(chips);$('end-current').replaceChildren(card);
   if(!atuais.length&&modo==='transferir'&&!$('end-origin').value.trim())setModo('acrescentar');
  }catch(e){atuais=null;if($('end-sku').value.trim()===sku)$('end-current').replaceChildren(node('span',e.message,'end-muted end-erro'));}
  preview();
 }
 function marcarOrigem(){
  const o=norm($('end-origin').value);
  document.querySelectorAll('.end-chip').forEach(c=>c.classList.toggle('is-origem',norm(c.textContent)===o));
 }
 function preview(){
  const box=$('end-preview');box.replaceChildren();
  const origem=modo==='transferir'?norm($('end-origin').value):'',destino=norm($('end-destination').value);
  const q=$('end-quantity').value.trim(),unit=$('end-unit').value.trim();
  const resumo=node('div',null,'end-preview-line');
  resumo.append(node('strong',$('end-sku').value.trim()||'Material'));
  resumo.append(document.createTextNode(` · ${q||'…'} ${unit} · ${modo==='transferir'?($('end-origin').value.trim()||'origem?')+' → ':'inclusão em '}${$('end-destination').value.trim()||'destino?'}`));
  box.append(resumo);
  marcarOrigem();
  if(!atuais)return;
  // Não prometer um resultado que o servidor vai recusar.
  if(origem&&!atuais.some(e=>norm(e)===origem)){box.append(node('div','A origem não consta nos endereços atuais deste material.','end-erro'));return;}
  if(!destino)return;
  const depois=atuais.filter(e=>!(origem&&norm(e)===origem));
  const novo=!depois.some(e=>norm(e)===destino);
  if(novo)depois.push($('end-destination').value.trim());
  const linha=(rotulo,lista,classe)=>{const l=node('div',null,'end-preview-row');l.append(node('span',rotulo,'end-muted'));
   if(!lista.length)l.append(node('span','sem endereço','end-chip end-chip--vazio'));
   for(const [txt,cls] of lista)l.append(node('span',txt,'end-chip '+cls));return l;};
  box.append(linha('Antes',atuais.map(e=>[e,origem&&norm(e)===origem?'end-chip--sai':''])));
  box.append(linha('Depois',depois.map(e=>[e,norm(e)===destino&&novo?'end-chip--entra':''])));
  if(!novo)box.append(node('div','O material já consta neste destino.','end-muted'));
 }
 function setBusy(value){busy=value;for(const elem of $('end-form').elements)elem.disabled=value;}
 $('end-form').onsubmit=async e=>{
  e.preventDefault();if(busy)return;
  if(modo==='transferir'&&!$('end-origin').value.trim()){message('Informe de qual endereço o material sai, ou escolha "Inclusão de endereço".',true);$('end-origin').focus();return;}
  const data={chave:key,sku:$('end-sku').value.trim(),origem:modo==='transferir'?$('end-origin').value.trim():'',destino:$('end-destination').value.trim(),quantidade:$('end-quantity').value.trim(),unidade:$('end-unit').value.trim(),motivo:$('end-reason').value.trim()};
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
 document.querySelectorAll('.end-mode button').forEach(b=>b.onclick=()=>{setModo(b.dataset.mode);(modo==='transferir'?$('end-origin'):$('end-destination')).focus();});
 for(const [a,b] of [['end-sku','end-origin'],['end-origin','end-destination'],['end-destination','end-quantity'],['end-quantity','end-unit'],['end-unit','end-reason']])$(a).onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();$(b).focus();}};
 $('end-move').onclick=()=>open();
 $('end-refresh').onclick=refresh;$('end-search').onchange=()=>{page=1;refresh();};
 document.querySelectorAll('#end-por button').forEach(b=>b.onclick=()=>{setPor(b.dataset.por);page=1;$('end-search').focus();if($('end-search').value.trim())refresh();});
 document.querySelectorAll('#end-filter button').forEach(b=>b.onclick=()=>{filtro=b.dataset.filtro;marcar('end-filter','filtro',filtro);page=1;refresh();});
 $('end-only-stock').onclick=()=>{comSaldo=!comSaldo;$('end-only-stock').setAttribute('aria-pressed',String(comSaldo));$('end-only-stock').classList.toggle('active',comSaldo);page=1;refresh();};
 // Bipar a etiqueta responde "o que tem neste endereço?".
 $('end-scan-search').onclick=()=>{setPor('endereco');scan('end-search');};$('end-prev').onclick=()=>{page--;refresh();};$('end-next').onclick=()=>{page++;refresh();};
 $('end-place-refresh').onclick=places;$('end-place-search').onchange=places;
 $('end-history-refresh').onclick=history;$('end-history-search').onchange=()=>{historyPage=1;history();};
 $('end-only-pending').onclick=()=>{soPendentes=!soPendentes;$('end-only-pending').setAttribute('aria-pressed',String(soPendentes));$('end-only-pending').classList.toggle('active',soPendentes);historyPage=1;history();};
 document.querySelectorAll('#end-place-filter button').forEach(b=>b.onclick=()=>{situacaoLocal=b.dataset.situacao;marcar('end-place-filter','situacao',situacaoLocal);places();});$('end-history-prev').onclick=()=>{historyPage--;history();};$('end-history-next').onclick=()=>{historyPage++;history();};
 document.querySelectorAll('.end-tabs button').forEach(b=>b.onclick=()=>showPanel(b.dataset.panel));
 document.addEventListener('visibilitychange',()=>{if(document.hidden)stopCamera();});window.addEventListener('pagehide',stopCamera);
 $('end-search').value=new URLSearchParams(location.search).get('sku')||'';
 TabelaOrdenavel.ativar($('end-balances').closest('table'));
 TabelaOrdenavel.ativar($('end-places').closest('table'));
 refresh();
})();
