/* SKU e local entram pela câmera ou por digitação (registrada no histórico). */
(() => {
 'use strict';
 const $ = id => document.getElementById(id);
 const api = '/api/recebimento/enderecamento';
 let status = 'Pendente', page = 1, items = [], selected, skuToken, allocations = [], scanner, scanning = false, busy = false, listRequest = 0;
 let ids = new URLSearchParams(location.search).get('ids') || '';
 const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
 const date = value => value ? new Date(value).toLocaleString('pt-BR') : '—';
 function feedback(message, work = false) { $(work ? 'pa-work-feedback' : 'pa-feedback').textContent = message; }
 let audioCtx;
 function prepararAudio() {
  try {
   audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
   if (audioCtx.state === 'suspended') audioCtx.resume().catch(() => {});
  } catch (_) {}
 }
 $('putaway').addEventListener('pointerdown', prepararAudio);
 $('putaway').addEventListener('keydown', prepararAudio);
 function sinal(ok) {
  try { if (navigator.vibrate) navigator.vibrate(ok ? 60 : [90, 60, 90]); } catch (_) {}
  try {
   audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
   if (audioCtx.state === 'suspended') audioCtx.resume().catch(() => {});
   const osc = audioCtx.createOscillator(), gain = audioCtx.createGain();
   osc.connect(gain); gain.connect(audioCtx.destination);
   osc.frequency.value = ok ? 880 : 220; gain.gain.value = 0.08;
   osc.start(); osc.stop(audioCtx.currentTime + (ok ? 0.12 : 0.3));
  } catch (_) {}
 }
 // Coletores também funcionam em aparelhos touch: pointer:fine não detecta Bluetooth.
 function focar(id) { const i = $(id); if ($('pa-work').open && i && !i.disabled) i.focus({preventScroll:true}); }
 async function request(url, data) {
  const response = await fetch(url, data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.erro || 'Não foi possível concluir. Verifique a conexão e tente novamente.');
  return result;
 }
 function button(text, action, icon, variant) {
  const b = el('button', null, 'pa-act' + (variant ? ` pa-act--${variant}` : ''));
  b.type = 'button';
  if (icon) { const i = document.createElement('i'); i.className = `fas ${icon}`; i.setAttribute('aria-hidden', 'true'); b.append(i); }
  b.append(document.createTextNode(text)); b.onclick = action; return b;
 }
 let menuAberto = null;
 function fecharMenu() { if (menuAberto) { menuAberto.remove(); menuAberto = null; } }
 document.addEventListener('click', e => { if (menuAberto && !menuAberto.contains(e.target) && !e.target.closest('.pa-menu-btn')) fecharMenu(); });
 document.addEventListener('scroll', fecharMenu, true);
 function kebab(itens) {
  const b = el('button', null, 'pa-act pa-menu-btn'); b.type = 'button'; b.setAttribute('aria-label', 'Mais ações'); b.title = 'Mais ações';
  const i = document.createElement('i'); i.className = 'fas fa-ellipsis-v'; i.setAttribute('aria-hidden', 'true'); b.append(i);
  b.onclick = () => {
   if (menuAberto) { fecharMenu(); return; }
   const list = el('div', null, 'pa-menu-list');
   itens.forEach(({texto, icone, acao, perigo}) => {
    const item = el('button', null, 'pa-menu-item' + (perigo ? ' pa-menu-item--perigo' : '')); item.type = 'button';
    const ic = document.createElement('i'); ic.className = `fas ${icone}`; ic.setAttribute('aria-hidden', 'true'); item.append(ic, document.createTextNode(texto));
    item.onclick = () => { fecharMenu(); acao(); }; list.append(item);
   });
   document.body.append(list);
   const r = b.getBoundingClientRect();
   list.style.top = `${r.bottom + 4}px`; list.style.left = `${Math.max(8, r.right - list.offsetWidth)}px`;
   menuAberto = list;
  };
  return b;
 }
 async function refresh() {
  const version = ++listRequest;
  try {
   const query = new URLSearchParams({status, pagina:page, busca:$('pa-search').value, ids});
   const data = await request(`${api}?${query}`);
   if (version !== listRequest) return;
   items = data.itens; $('pa-list').replaceChildren();
   if (!ids && !$('pa-search').value) $('dash-enderecamento').textContent = data.contadores.Pendente;
   document.querySelectorAll('#pa-tabs button').forEach(b => { b.classList.toggle('active', b.dataset.status === status); b.setAttribute('aria-pressed', String(b.dataset.status === status)); b.querySelector('.pa-kpi-num').textContent = b.dataset.status === 'Concluído' ? data.concluidos_hoje : data.contadores[b.dataset.status]; });
    if (!items.length) {
     const row = el('tr');
     const cell = el('td', 'Nenhum material nesta fila para o filtro selecionado.', 'pa-empty');
     cell.colSpan = 7;
     row.append(cell);
     $('pa-list').append(row);
    }
   items.forEach(item => {
     const card = el('tr', null, 'pa-row');
     const skuCell = el('td', item.sku || 'Sem vínculo', item.sku ? 'pa-sku' : 'pa-sku pa-sku--pendente');
     const materialCell = el('td', null, 'pa-description');
     materialCell.append(el('div', item.descricao || 'Material sem descrição', 'pa-desc'), el('div', item.fornecedor || '', 'pa-meta'));
     const badgeCls = {'Pendente': 'pa-badge--pendente', 'Aguardando sincronização': 'pa-badge--aguardando', 'Concluído': 'pa-badge--concluido'}[item.status] || '';
     const statusCell = el('td');
     statusCell.append(el('span', item.status, `pa-badge ${item.erro ? 'pa-badge--erro' : badgeCls}`));
     const qtdCell = el('td', `${item.quantidade} ${item.unidade || ''}`); qtdCell.dataset.label = 'Quantidade';
     const nfCell = el('td', item.nota, 'pa-nf'); nfCell.dataset.label = 'NF-e';
     const dateCell = el('td', date(item.criado_em), 'pa-date'); dateCell.dataset.label = 'Recebido em';
     card.append(skuCell, materialCell, qtdCell, nfCell, dateCell, statusCell);
    const actions = el('div', null, 'pa-tools');
    if (item.status === 'Pendente') actions.append(button('Endereçar material', () => openWork(item), 'fa-qrcode', 'primary'));
    const syncActions = item.erro ? el('div', null, 'pa-sync-error') : actions;
    if (item.erro) syncActions.append(el('span', item.erro));
    if (item.status === 'Aguardando sincronização') syncActions.append(button('Tentar sincronizar', async e => {
     const alvo = e.currentTarget; alvo.disabled = true;
     try { const data = await request(`${api}/${item.id}/sincronizar`, {}); feedback(data.item.status === 'Concluído' ? 'Endereço sincronizado com o GRV.' : data.item.erro || 'Sincronização pendente.'); await refresh(); } catch (err) { feedback(err.message); } finally { alvo.disabled = false; }
    }, 'fa-sync-alt'));
    const isAdmin = $('putaway').dataset.admin === 'true';
    const reabrir = mensagem => async () => {
     const justificativa = await dialogRecebimento({titulo:'Revisar endereçamento', mensagem, confirmar:'Confirmar revisão', cancelar:'Cancelar', justificativa:true});
     if (!justificativa) return;
     try { await request(`${api}/${item.id}/reabrir`, {justificativa}); status='Pendente'; page=1; await refresh(); } catch(err) {feedback(err.message);}
    };
    const menuItens = [];
    if (isAdmin) menuItens.push({texto:'Ver histórico e origem', icone:'fa-history', acao:() => history(item.id)});
    if (item.status === 'Aguardando sincronização' && $('putaway').dataset.manage === 'true') menuItens.push({texto:'Revisar leituras', icone:'fa-redo', acao:reabrir('Informe o motivo da revisão. Será necessário bipar novamente o SKU e o endereço. Os endereços já enviados ao GRV serão preservados.')});
    if (item.status === 'Concluído' && isAdmin) menuItens.push({texto:'Estornar endereçamento', icone:'fa-undo', perigo:true, acao:reabrir('Informe o motivo do estorno. O material volta para a fila "A endereçar"; os endereços já enviados ao GRV são preservados.')});
    if (menuItens.length) actions.append(kebab(menuItens));
    const actionCell = el('td', null, 'pa-actions'); actionCell.append(actions); card.append(actionCell); $('pa-list').append(card);
    if (item.erro) { const errorRow = el('tr', null, 'pa-error-row'); const errorCell = el('td', null, 'pa-alert pa-alert--erro'); errorCell.append(syncActions); errorCell.colSpan = 7; errorRow.append(errorCell); $('pa-list').append(errorRow); }
   });
   $('pa-page').textContent = `Página ${page}`; $('pa-prev').disabled = page === 1; $('pa-next').disabled = page * 40 >= data.contadores[status];
  } catch (err) { feedback(err.message); }
 }
 async function history(id) {
  try {
   const data = await request(`${api}/${id}/historico`), body = $('pa-history-body'); body.replaceChildren();
   body.append(el('p', `NF ${data.item.nota} · item ${data.item.item_id} · ${data.item.sku}`), el('p', `Chave: ${data.item.chave || 'Não informada'}`, 'pa-meta'));
   body.append(el('p', `${data.item.fornecedor || ''} · ${data.item.descricao}`), el('p', `Conferência: ${data.item.conferencia_por || 'Não informado'} · ${date(data.item.conferencia_em)}`, 'pa-meta'));
   const link = el('a', 'Abrir recebimentos'); link.href = '/conferencia'; body.append(link);
   const labels = {antes:'Endereços anteriores', enviado:'Endereços enviados ao GRV', motivo:'Motivo', justificativa:'Justificativa', erro:'Ocorrência', quantidade:'Quantidade conferida', nota:'NF', chave_acesso:'Chave da NF', sku:'SKU'};
   data.eventos.forEach(e => {
    const row = el('section', null, 'pa-card'); row.append(el('strong', e.tipo), el('p', `${date(e.data)} · ${e.usuario}`, 'pa-meta'));
    if (e.detalhes) Object.entries(e.detalhes).forEach(([key,value]) => {
     if (key === 'alocacoes') value.forEach(a => row.append(el('p', `${a.endereco}: ${a.quantidade} ${data.item.unidade || ''}${a.lote ? ' · Lote '+a.lote : ''}`)));
     else row.append(el('p', `${labels[key] || key}: ${Array.isArray(value) ? value.join('; ') || 'Sem endereço' : value ?? '—'}`));
    }); body.append(row);
   });
   $('pa-history').showModal();
  } catch (err) { feedback(err.message); }
 }
 function setAddressEnabled(on) { ['pa-address','pa-address-manual','pa-address-manual-btn'].forEach(id => $(id).disabled = !on); }
 function resetSkuFlow() { skuToken = null; allocations = []; setAddressEnabled(false); $('pa-sku-result').textContent = ''; $('pa-reason-wrap').hidden = true; renderAllocations(); }
 function marcarStep(id, done) {
  const step = $(id); step.classList.toggle('pa-step--done', done);
  const num = step.querySelector('.pa-step-num'); num.textContent = done ? '✓' : num.dataset.num;
 }
 function atualizarWizard() {
  if (!selected) return;
  const sum = allocations.reduce((n,a) => n + Number(a.quantidade || 0),0);
  marcarStep('pa-step-sku', !!skuToken);
  marcarStep('pa-step-addr', allocations.length > 0);
  marcarStep('pa-step-qty', allocations.length > 0 && Number.isFinite(sum) && Math.abs(sum - selected.quantidade) <= 0.000001);
  const pct = selected.quantidade > 0 ? Math.min(100, (sum / selected.quantidade) * 100) : 0;
  const bar = $('pa-progress-bar');
  bar.style.width = `${Number.isFinite(pct) ? pct : 0}%`;
  bar.classList.toggle('ok', allocations.length > 0 && Math.abs(sum - selected.quantidade) <= 0.000001);
  bar.classList.toggle('excesso', sum - selected.quantidade > 0.000001);
  const progress = bar.parentElement;
  progress.setAttribute('aria-valuemax', selected.quantidade);
  progress.setAttribute('aria-valuenow', Number.isFinite(sum) ? Math.max(0, Math.min(sum, selected.quantidade)) : 0);
  progress.setAttribute('aria-valuetext', $('pa-total').textContent);
 }
 function openWork(item, emSequencia) {
  selected = item; skuToken = null; allocations = []; $('pa-title').textContent = `NF ${item.nota} · ${item.sku || 'Sem SKU GRV'}`;
  $('pa-description').textContent = `${item.descricao} · ${item.quantidade} ${item.unidade || ''}`;
  $('pa-sku-result').textContent = ''; $('pa-reason').value = 'normal'; $('pa-justification').value = ''; $('pa-justification-label').hidden = true;
  $('pa-reason-wrap').hidden = true; $('pa-sku-manual').value = ''; $('pa-address-manual').value = '';
  setAddressEnabled(false);
  if (!emSequencia) feedback('Bipe ou digite o SKU para consultar o endereço atual no GRV.', true);
  renderAllocations();
  if (!$('pa-work').open) $('pa-work').showModal();
  focar('pa-sku-manual');
 }
 function renderAllocations() {
  $('pa-allocations').replaceChildren();
  allocations.forEach((a, idx) => {
   const row = el('div', null, 'pa-alloc'); row.append(el('strong', a.codigo));
   const label = el('label', 'Quantidade neste endereço'); const quantity = el('input'); quantity.type = 'number'; quantity.inputMode = 'decimal'; quantity.min = '0.000001'; quantity.step = 'any'; quantity.value = a.quantidade;
   quantity.oninput = () => { a.quantidade = quantity.value; total(); }; label.append(quantity); row.append(label);
   const lotLabel = el('label', 'Lote (se aplicável)'); const lot = el('input'); lot.maxLength = 80; lot.value = a.lote || ''; lot.oninput = () => a.lote = lot.value; lotLabel.append(lot); row.append(lotLabel, button('Remover parcela', () => { allocations.splice(idx,1); renderAllocations(); })); $('pa-allocations').append(row);
  }); total();
 }
 function total() {
  const sum = allocations.reduce((n,a) => n + Number(a.quantidade || 0),0);
  $('pa-total').textContent = `Distribuído: ${sum} de ${selected ? selected.quantidade : 0} ${selected && selected.unidade || ''}`;
  $('pa-submit').disabled = busy || !skuToken || !allocations.length || !Number.isFinite(sum) || !selected || Math.abs(sum-selected.quantidade)>0.000001 || allocations.some(a => Number(a.quantidade)<=0);
  atualizarWizard();
 }
 async function stopCamera() {
  scanning = false;
  if (scanner) { try { if (scanner.isScanning) await scanner.stop(); scanner.clear(); } catch (_) {} scanner = null; }
  if ($('pa-camera').open) $('pa-camera').close();
 }
 async function processarLeitura(tipo, codigo, manual) {
  busy = true; total(); $('pa-close').disabled = true;
  let ok = false;
  try {
   const data = await request(`${api}/${selected.id}/leitura`, {tipo, codigo, manual: !!manual});
   if (tipo === 'sku') {
    skuToken = data.token; allocations = []; setAddressEnabled(true);
    $('pa-sku-result').textContent = `SKU validado: ${data.codigo}`;
    $('pa-reason-wrap').hidden = !data.enderecos.length;
    feedback(data.enderecos.length ? `Este material já está endereçado em: ${data.enderecos.join('; ')}. Informe o local. Se estiver lotado, marque a opção de endereço adicional.` : 'Material sem endereço no GRV. Informe a etiqueta do local onde vai guardar.', true);
    focar('pa-address-manual');
   } else {
    const sum = allocations.reduce((n,a) => n + Number(a.quantidade || 0),0);
    allocations.push({...data, quantidade:Math.max(0,selected.quantidade-sum), lote:''});
    focar('pa-address-manual');
   }
   sinal(true);
   ok = true;
   renderAllocations();
  } catch (err) { sinal(false); feedback(err.message, true); } finally {
   busy = false; total(); $('pa-close').disabled = false;
   const input = $(tipo === 'sku' ? 'pa-sku-manual' : 'pa-address-manual');
   input.value = '';
   focar(ok ? 'pa-address-manual' : input.id);
  }
 }
 async function scan(tipo) {
  if (busy || scanner) return;
  if (tipo === 'sku') resetSkuFlow();
  if (!window.isSecureContext || !navigator.mediaDevices || typeof Html5Qrcode === 'undefined') { feedback('Câmera indisponível. Acesse por HTTPS, permita a câmera ou digite o código da etiqueta.', true); return; }
  $('pa-camera-title').textContent = tipo === 'sku' ? 'Bipe o SKU do material' : 'Bipe a etiqueta do endereço';
  $('pa-camera').showModal(); scanning = true;
  scanner = new Html5Qrcode('reader');
  try {
   await scanner.start({facingMode:'environment'}, {fps:10, disableFlip:false}, async codigo => {
    if (!scanning) return; scanning = false; await stopCamera();
    await processarLeitura(tipo, codigo, false);
   });
  } catch (err) { await stopCamera(); feedback('Não foi possível abrir a câmera. Confira a permissão no navegador ou digite o código da etiqueta.', true); }
 }
 async function entradaManual(tipo) {
  if (busy || scanner) return;
  const input = $(tipo === 'sku' ? 'pa-sku-manual' : 'pa-address-manual');
  const codigo = input.value.trim();
  if (!codigo) { feedback('Digite o código exatamente como está na etiqueta.', true); return; }
  if (tipo === 'sku') resetSkuFlow();
  await processarLeitura(tipo, codigo, true);
  input.value = '';
 }
 $('pa-submit').onclick = async () => {
  if (busy) return; busy = true; total(); $('pa-close').disabled = true;
  try {
   const data = await request(`${api}/${selected.id}/confirmar`, {sku_token:skuToken, motivo:$('pa-reason').value, justificativa:$('pa-justification').value, alocacoes:allocations.map(a => ({token:a.token,quantidade:a.quantidade,lote:a.lote}))});
   const msg = data.item.status === 'Concluído' ? `NF ${selected.nota}: endereçado e sincronizado com o GRV.` : `NF ${selected.nota}: salvo. ${data.item.erro || 'Aguardando sincronização com o GRV.'}`;
   sinal(true);
   const proximo = data.proximo;
   if (proximo) {
    busy = false; $('pa-close').disabled = false;
    openWork(proximo, true);
    feedback(`✓ ${msg} Próximo item carregado — pode bipar.`, true);
    refresh();
   } else {
    $('pa-work').close(); status = data.item.status; page = 1;
    feedback(msg); await refresh();
   }
  } catch (err) { sinal(false); feedback(err.message, true); } finally { busy = false; $('pa-close').disabled = false; total(); }
 };
 $('pa-sku').onclick = () => scan('sku'); $('pa-address').onclick = () => scan('local');
 $('pa-sku-manual-btn').onclick = () => entradaManual('sku'); $('pa-address-manual-btn').onclick = () => entradaManual('local');
 $('pa-sku-manual').onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); entradaManual('sku'); } };
 $('pa-address-manual').onkeydown = e => { if (e.key === 'Enter') { e.preventDefault(); entradaManual('local'); } };
 $('pa-camera-close').onclick = stopCamera; $('pa-camera').addEventListener('cancel', e => {e.preventDefault(); stopCamera();});
 $('pa-work').addEventListener('cancel', e => { if (busy) e.preventDefault(); });
 $('pa-close').onclick = () => { if (!busy) $('pa-work').close(); };
 $('pa-history-close').onclick = () => $('pa-history').close();
 $('pa-reason').onchange = () => $('pa-justification-label').hidden = $('pa-reason').value !== 'alternativo';
 $('pa-refresh').onclick = refresh; $('pa-search').onchange = () => {page=1; refresh();};
 $('pa-prev').onclick = () => {page--; refresh();}; $('pa-next').onclick = () => {page++; refresh();};
 document.querySelectorAll('#pa-tabs button').forEach(b => b.onclick = () => {status=b.dataset.status; page=1; refresh();});
 document.addEventListener('visibilitychange', () => {if (document.hidden) stopCamera();});
 // Atualiza a fila após retries do servidor, sem interromper leituras ou menus.
 setInterval(() => { if (!document.hidden && !busy && !menuAberto && !$('pa-work').open && !$('pa-history').open && $('putaway').getClientRects().length) refresh(); }, 30000);
 window.addEventListener('pagehide', stopCamera);
 document.addEventListener('recebimento:abrir-enderecamento', e => {
  if (e.detail?.ids) { ids = e.detail.ids.join(','); status = 'Pendente'; page = 1; }
  refresh();
 });
 $('pa-clear-filter').onclick = () => { ids=''; page=1; $('pa-search').value=''; refresh(); };
 if (new URLSearchParams(location.search).get('etapa') === 'enderecamento') {
  aplicarFiltroReceb('enderecamento', document.querySelector('[data-filter="enderecamento"]'));
 } else { refresh(); }
})();
