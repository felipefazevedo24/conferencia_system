/* SKU e local são preenchidos apenas pelo callback do decodificador da câmera. */
(() => {
 'use strict';
 const $ = id => document.getElementById(id);
 const api = '/api/recebimento/enderecamento';
 let status = 'Pendente', page = 1, items = [], selected, skuToken, allocations = [], scanner, scanning = false, busy = false, listRequest = 0;
 let ids = new URLSearchParams(location.search).get('ids') || '';
 const el = (tag, text, cls) => { const e = document.createElement(tag); if (text != null) e.textContent = text; if (cls) e.className = cls; return e; };
 const date = value => value ? new Date(value).toLocaleString('pt-BR') : '—';
 function feedback(message, work = false) { $(work ? 'pa-work-feedback' : 'pa-feedback').textContent = message; }
 async function request(url, data) {
  const response = await fetch(url, data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(result.erro || 'Não foi possível concluir. Verifique a conexão e tente novamente.');
  return result;
 }
 function button(text, action) { const b = el('button', text); b.type = 'button'; b.onclick = action; return b; }
 async function refresh() {
  const version = ++listRequest;
  try {
   const query = new URLSearchParams({status, pagina:page, busca:$('pa-search').value, ids});
   const data = await request(`${api}?${query}`);
   if (version !== listRequest) return;
   items = data.itens; $('pa-list').replaceChildren();
   if (!ids && !$('pa-search').value) $('dash-enderecamento').textContent = data.contadores.Pendente;
   document.querySelectorAll('#pa-tabs button').forEach(b => { b.classList.toggle('active', b.dataset.status === status); b.querySelector('span').textContent = `(${data.contadores[b.dataset.status]})`; });
    if (!items.length) {
     const row = el('tr');
     const cell = el('td', 'Nenhum material nesta fila para o filtro selecionado.', 'pa-empty');
     cell.colSpan = 7;
     row.append(cell);
     $('pa-list').append(row);
    }
   items.forEach(item => {
     const card = el('tr', null, 'pa-row');
     const statusCell = el('td');
     statusCell.append(el('span', item.status, 'pa-badge'));
     card.append(el('td', `NF ${item.nota}`, 'pa-nf'), el('td', item.fornecedor || '---'), el('td', item.descricao || 'Material sem descrição', 'pa-description'), el('td', `${item.quantidade} ${item.unidade || ''}`), el('td', date(item.criado_em), 'pa-date'), statusCell);
    const actions = el('div', null, 'pa-tools');
    if (item.status === 'Pendente') actions.append(button('Endereçar material', () => openWork(item)));
    if (item.status === 'Aguardando sincronização') actions.append(button('Tentar sincronizar', async e => {
     e.target.disabled = true;
     try { const data = await request(`${api}/${item.id}/sincronizar`, {}); feedback(data.item.status === 'Concluído' ? 'Endereço sincronizado com o GRV.' : data.item.erro || 'Sincronização pendente.'); await refresh(); } catch (err) { feedback(err.message); } finally { e.target.disabled = false; }
    }));
    actions.append(button('Ver histórico e origem', () => history(item.id))); const actionCell = el('td', null, 'pa-actions'); actionCell.append(actions); card.append(actionCell); $('pa-list').append(card);
    if (item.erro) { const errorRow = el('tr', null, 'pa-error-row'); const errorCell = el('td', item.erro, 'pa-alert'); errorCell.colSpan = 7; errorRow.append(errorCell); $('pa-list').append(errorRow); }
    if (item.status === 'Aguardando sincronização' && $('putaway').dataset.manage === 'true') actions.append(button('Revisar leituras', async () => {
     const justificativa = prompt('Informe o motivo da revisão. Será necessário bipar novamente o SKU e o endereço. Os endereços já enviados ao GRV serão preservados.');
     if (!justificativa) return;
     try { await request(`${api}/${item.id}/reabrir`, {justificativa}); status='Pendente'; page=1; await refresh(); } catch(err) {feedback(err.message);}
    }));
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
 function openWork(item) {
  selected = item; skuToken = null; allocations = []; $('pa-title').textContent = `NF ${item.nota} · ${item.sku || 'Sem SKU GRV'}`;
  $('pa-description').textContent = `${item.descricao} · ${item.quantidade} ${item.unidade || ''}`;
  $('pa-sku-result').textContent = ''; $('pa-reason').value = 'normal'; $('pa-justification').value = ''; $('pa-justification-label').hidden = true;
  $('pa-address').disabled = true; feedback('Bipe o SKU para consultar o endereço atual no GRV.', true); renderAllocations(); $('pa-work').showModal();
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
  $('pa-total').textContent = `Distribuído: ${sum} de ${selected.quantidade} ${selected.unidade || ''}`;
  $('pa-submit').disabled = busy || !skuToken || !allocations.length || !Number.isFinite(sum) || Math.abs(sum-selected.quantidade)>0.000001 || allocations.some(a => Number(a.quantidade)<=0);
 }
 async function stopCamera() {
  scanning = false;
  if (scanner) { try { if (scanner.isScanning) await scanner.stop(); scanner.clear(); } catch (_) {} scanner = null; }
  if ($('pa-camera').open) $('pa-camera').close();
 }
 async function scan(tipo) {
  if (busy || scanner) return;
  if (tipo === 'sku') { skuToken=null; allocations=[]; $('pa-address').disabled=true; $('pa-sku-result').textContent=''; renderAllocations(); }
  if (!window.isSecureContext || !navigator.mediaDevices || typeof Html5Qrcode === 'undefined') { feedback('Câmera indisponível. Acesse por HTTPS, permita a câmera e atualize a página.', true); return; }
  $('pa-camera-title').textContent = tipo === 'sku' ? 'Bipe o SKU do material' : 'Bipe a etiqueta do endereço';
  $('pa-camera').showModal(); scanning = true;
  scanner = new Html5Qrcode('reader');
  try {
   await scanner.start({facingMode:'environment'}, {fps:10, disableFlip:false}, async codigo => {
    if (!scanning) return; scanning = false; await stopCamera();
    busy = true; total();
    try {
     const data = await request(`${api}/${selected.id}/leitura`, {tipo,codigo});
     if (tipo === 'sku') {
      skuToken = data.token; allocations = []; $('pa-address').disabled = false;
      $('pa-sku-result').textContent = `SKU validado: ${data.codigo}`;
      feedback(data.enderecos.length ? `Este material já está endereçado em: ${data.enderecos.join('; ')}. Bipe o local. Se estiver lotado, marque a opção de endereço adicional.` : 'Material sem endereço no GRV. Bipe um local cadastrado e ativo.', true);
     } else {
      const sum = allocations.reduce((n,a) => n + Number(a.quantidade || 0),0);
      allocations.push({...data, quantidade:Math.max(0,selected.quantidade-sum), lote:''});
     }
     renderAllocations();
    } catch (err) { feedback(err.message, true); } finally { busy = false; total(); }
   });
  } catch (err) { await stopCamera(); feedback('Não foi possível abrir a câmera. Confira a permissão de câmera no navegador e tente novamente.', true); }
 }
 $('pa-submit').onclick = async () => {
  if (busy) return; busy = true; total(); $('pa-close').disabled = true;
  try {
   const data = await request(`${api}/${selected.id}/confirmar`, {sku_token:skuToken, motivo:$('pa-reason').value, justificativa:$('pa-justification').value, alocacoes:allocations.map(a => ({token:a.token,quantidade:a.quantidade,lote:a.lote}))});
   $('pa-work').close(); status = data.item.status; page = 1;
   feedback(status === 'Concluído' ? 'Material endereçado e sincronizado com o GRV.' : `Endereçamento salvo. ${data.item.erro || 'Aguardando sincronização com o GRV.'}`); await refresh();
  } catch (err) { feedback(err.message, true); } finally { busy = false; $('pa-close').disabled = false; total(); }
 };
 $('pa-sku').onclick = () => scan('sku'); $('pa-address').onclick = () => scan('local');
 $('pa-camera-close').onclick = stopCamera; $('pa-camera').addEventListener('cancel', e => {e.preventDefault(); stopCamera();});
 $('pa-work').addEventListener('cancel', e => { if (busy) e.preventDefault(); });
 $('pa-close').onclick = () => { if (!busy) $('pa-work').close(); };
 $('pa-history-close').onclick = () => $('pa-history').close();
 $('pa-reason').onchange = () => $('pa-justification-label').hidden = $('pa-reason').value !== 'alternativo';
 $('pa-refresh').onclick = refresh; $('pa-search').onchange = () => {page=1; refresh();};
 $('pa-prev').onclick = () => {page--; refresh();}; $('pa-next').onclick = () => {page++; refresh();};
 document.querySelectorAll('#pa-tabs button').forEach(b => b.onclick = () => {status=b.dataset.status; page=1; refresh();});
 if ($('pa-locals')) {
  const showLocals = data => { $('pa-local-list').replaceChildren(); data.locais.forEach(l => $('pa-local-list').append(el('p', `${l.codigo} · ${l.ativo ? 'Ativo':'Inativo'}`))); };
  $('pa-locals').onsubmit = async e => { e.preventDefault(); try { showLocals(await request(`${api}/locais`, {codigo:$('pa-local-code').value, ativo:$('pa-local-active').checked})); feedback('Cadastro de endereço salvo.'); } catch(err) {feedback(err.message);} };
  $('pa-show-locals').onclick = async () => {try {showLocals(await request(`${api}/locais`));} catch(err) {feedback(err.message);}};
 }
 document.addEventListener('visibilitychange', () => {if (document.hidden) stopCamera();});
 window.addEventListener('pagehide', stopCamera);
 document.addEventListener('recebimento:abrir-enderecamento', refresh);
 $('pa-clear-filter').onclick = () => { ids=''; page=1; $('pa-search').value=''; refresh(); };
 if (new URLSearchParams(location.search).get('etapa') === 'enderecamento') {
  aplicarFiltroReceb('enderecamento', document.querySelector('[data-filter="enderecamento"]'));
 } else { refresh(); }
})();
