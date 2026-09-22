(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const form = byId('rpa-search-form');
  const body = byId('rpa-results-body');
  const message = byId('rpa-message');
  const inputs = {
    material: byId('rpa-material'), thickness: byId('rpa-thickness'),
    classification: byId('rpa-classification'), os: byId('rpa-os'),
    code: byId('rpa-code'), general: byId('rpa-search')
  };
  const PAGE_SIZE = 35;
  let rows = [];
  let filtered = [];
  let selected = new Set();
  let payload = null;
  let sourceSearch = '';
  let page = 0;
  let busy = false;
  let validationSequence = 0;
  let validationTimer;

  function status(text, error = false) {
    message.textContent = text;
    message.classList.toggle('error', error);
  }
  function text(value) { return value == null || value === '' ? '—' : String(value); }
  function cell(tr, value) {
    const td = document.createElement('td');
    td.textContent = text(value);
    tr.append(td);
    return td;
  }
  function button(label, className, handler) {
    const node = document.createElement('button');
    node.type = 'button';
    node.className = className;
    node.textContent = label;
    node.addEventListener('click', handler);
    return node;
  }
  function selectedRows() {
    const unique = new Map();
    rows.forEach(item => { if (selected.has(item.codigo_processo) && !unique.has(item.codigo_processo)) unique.set(item.codigo_processo, item); });
    return [...unique.values()];
  }
  function resetPayload() {
    payload = null;
    byId('rpa-payload-actions').hidden = true;
    byId('rpa-payload-details').hidden = true;
    byId('rpa-payload').textContent = '';
  }
  function renderGroup() {
    const items = selectedRows();
    byId('rpa-selected-count').textContent = `${items.length} selecionado${items.length === 1 ? '' : 's'}`;
    byId('rpa-group-count').textContent = String(items.length);
    byId('rpa-group-empty').hidden = Boolean(items.length);
    byId('rpa-group-content').hidden = !items.length;
    byId('rpa-clear-selection').disabled = !items.length;
    const list = byId('rpa-selected-list');
    list.replaceChildren();
    items.forEach(item => {
      const card = document.createElement('div');
      card.className = 'rpa-selected-item';
      const top = document.createElement('div');
      top.className = 'rpa-selected-item__top';
      const title = document.createElement('strong');
      title.textContent = `Processo ${item.codigo_processo}`;
      const remove = button('Remover', 'eui-btn eui-btn--subtle eui-btn--sm', () => {
        selected.delete(item.codigo_processo);
        selectionChanged();
      });
      remove.setAttribute('aria-label', `Remover processo ${item.codigo_processo} da seleção`);
      top.append(title, remove);
      const meta = document.createElement('small');
      meta.textContent = `OS ${item.os_completa} · ${item.material} · ${text(item.espessura)} · Qtde ${text(item.quantidade_formato)}`;
      card.append(top, meta);
      list.append(card);
    });
  }
  function validation(textValue, state = '') {
    const node = byId('rpa-validation');
    node.textContent = textValue;
    node.classList.toggle('valid', state === 'valid');
    node.classList.toggle('invalid', state === 'invalid');
  }
  async function requestPayload(codes) {
    const response = await fetch('/api/producao/rpa-agrupamento/payload', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({busca: sourceSearch, codigos: codes})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Não foi possível validar o agrupamento.');
    return data.payload;
  }
  function validateSelection() {
    clearTimeout(validationTimer);
    const sequence = ++validationSequence;
    const codes = [...selected];
    byId('rpa-prepare').disabled = true;
    if (codes.length < 2) {
      validation('Selecione pelo menos dois processos.');
      return;
    }
    validation('Validando processos...');
    validationTimer = setTimeout(async () => {
      try {
        const result = await requestPayload(codes);
        if (sequence !== validationSequence) return;
        validation(`Agrupamento válido: ${result.quantidade_itens} códigos compatíveis.`, 'valid');
        byId('rpa-prepare').disabled = false;
      } catch (error) {
        if (sequence !== validationSequence) return;
        validation(error.message || 'Processos incompatíveis.', 'invalid');
      }
    }, 300);
  }
  function selectionChanged() {
    resetPayload();
    renderGroup();
    renderTable();
    validateSelection();
  }
  function options(select, values, placeholder) {
    const previous = select.value;
    select.replaceChildren(new Option(placeholder, ''));
    [...new Set(values.filter(Boolean))].sort((a, b) => a.localeCompare(b, 'pt-BR', {numeric:true})).forEach(value => select.add(new Option(value, value)));
    if ([...select.options].some(option => option.value === previous)) select.value = previous;
  }
  function populateFilters() {
    options(inputs.material, rows.map(item => item.material), 'Todos os materiais');
    options(inputs.thickness, rows.map(item => item.espessura), 'Todas as espessuras');
    options(inputs.classification, rows.map(item => item.classificacao), 'Todas as classificações');
  }
  function includes(value, query) { return String(value || '').toLocaleLowerCase('pt-BR').includes(query.toLocaleLowerCase('pt-BR')); }
  function applyLocalFilters() {
    const material = inputs.material.value;
    const thickness = inputs.thickness.value;
    const classification = inputs.classification.value;
    const os = inputs.os.value.trim();
    const code = inputs.code.value.trim();
    const general = inputs.general.value.trim();
    filtered = rows.filter(item =>
      (!material || item.material === material) &&
      (!thickness || item.espessura === thickness) &&
      (!classification || item.classificacao === classification) &&
      (!os || includes(item.os, os) || includes(item.os_completa, os)) &&
      (!code || includes(item.codigo_processo, code)) &&
      (!general || [item.os, item.os_completa, item.codigo_processo, item.classificacao,
        item.descricao, item.material, item.norma, item.espessura, item.cliente].some(value => includes(value, general)))
    );
    page = 0;
    renderTable();
  }
  function stateRow(label) {
    const tr = document.createElement('tr');
    const td = document.createElement('td');
    td.colSpan = 12;
    td.className = 'rpa-state';
    td.textContent = label;
    tr.append(td);
    body.replaceChildren(tr);
  }
  function details(item) {
    const values = [
      ['Nº OS', item.os], ['OS completa', item.os_completa], ['Código do processo', item.codigo_processo],
      ['Classificação', item.classificacao], ['Descrição', item.descricao], ['Material', item.material],
      ['Norma', item.norma], ['Espessura', item.espessura], ['Quantidade/Formato', item.quantidade_formato],
      ['Cliente', item.cliente], ['Observação', item.observacao], ['Status', item.status_os],
      ['Tipo de serviço', item.tipo_servico], ['Elegibilidade', item.elegivel ? 'Elegível' : 'Não elegível'],
      ['Largura (mm)', item.largura_mm], ['Altura (mm)', item.altura_mm], ['Comprimento (mm)', item.comprimento_mm]
    ];
    byId('rpa-details-title').textContent = `Processo ${item.codigo_processo}`;
    const content = byId('rpa-details-content');
    content.replaceChildren();
    values.forEach(([label, value]) => {
      const wrapper = document.createElement('div');
      const term = document.createElement('dt'); term.textContent = label;
      const description = document.createElement('dd'); description.textContent = text(value);
      wrapper.append(term, description); content.append(wrapper);
    });
    bootstrap.Modal.getOrCreateInstance(byId('rpa-details-modal')).show();
  }
  function renderTable() {
    const visible = filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
    byId('rpa-count').textContent = `${rows.length} processos encontrados`;
    byId('rpa-result-count').textContent = `${filtered.length} processo${filtered.length === 1 ? '' : 's'} nos filtros`;
    if (busy) stateRow('Buscando processos...');
    else if (!filtered.length) stateRow('Nenhum processo encontrado para os filtros informados.');
    else {
      body.replaceChildren();
      visible.forEach(item => {
        const tr = document.createElement('tr');
        tr.classList.toggle('is-selected', selected.has(item.codigo_processo));
        const selection = document.createElement('td');
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.disabled = !item.elegivel;
        checkbox.checked = selected.has(item.codigo_processo);
        checkbox.setAttribute('aria-label', `Selecionar processo ${item.codigo_processo} da OS ${item.os_completa}`);
        checkbox.addEventListener('change', () => {
          if (checkbox.checked) selected.add(item.codigo_processo);
          else selected.delete(item.codigo_processo);
          selectionChanged();
        });
        selection.append(checkbox); tr.append(selection);
        [item.os, item.os_completa, item.codigo_processo, item.classificacao, item.descricao,
          item.material, item.espessura, item.norma, item.quantidade_formato].forEach(value => cell(tr, value));
        const situation = document.createElement('td');
        const badge = document.createElement('span');
        badge.className = `eui-badge eui-badge--${selected.has(item.codigo_processo) ? 'primary' : item.elegivel ? 'success' : 'neutral'}`;
        badge.textContent = selected.has(item.codigo_processo) ? 'Selecionado' : item.elegivel ? 'Compatível' : 'Neutro';
        situation.append(badge); tr.append(situation);
        const action = document.createElement('td');
        action.append(button('Ver detalhes', 'eui-btn eui-btn--ghost eui-btn--sm rpa-detail-button', () => details(item)));
        tr.append(action); body.append(tr);
      });
    }
    const all = byId('rpa-select-all');
    const eligible = visible.filter(item => item.elegivel);
    all.disabled = busy || eligible.length === 0;
    all.checked = eligible.length > 0 && eligible.every(item => selected.has(item.codigo_processo));
    all.indeterminate = !all.checked && eligible.some(item => selected.has(item.codigo_processo));
    const totalPages = Math.ceil(filtered.length / PAGE_SIZE);
    byId('rpa-pagination').hidden = totalPages <= 1;
    byId('rpa-page-status').textContent = `Página ${page + 1} de ${totalPages}`;
    byId('rpa-prev').disabled = page === 0;
    byId('rpa-next').disabled = page >= totalPages - 1;
  }
  async function consult() {
    if (busy) return;
    busy = true;
    byId('rpa-refresh').disabled = true;
    byId('rpa-apply').disabled = true;
    byId('rpa-clear').disabled = true;
    byId('rpa-clear-link').disabled = true;
    const query = inputs.os.value.trim() || inputs.code.value.trim() || inputs.general.value.trim();
    sourceSearch = query;
    selected = new Set();
    selectionChanged();
    status('Buscando processos...');
    renderTable();
    try {
      const response = await fetch(`/api/producao/rpa-agrupamento?q=${encodeURIComponent(query)}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Não foi possível consultar os processos.');
      rows = data.resultados || [];
      populateFilters();
      applyLocalFilters();
      status(`${rows.length} processos carregados. A consulta mostra até 500 registros.`);
    } catch (error) {
      rows = []; filtered = [];
      renderTable();
      status(error.message || 'Falha na consulta.', true);
    } finally {
      busy = false;
      byId('rpa-refresh').disabled = false;
      byId('rpa-apply').disabled = false;
      byId('rpa-clear').disabled = false;
      byId('rpa-clear-link').disabled = false;
      renderTable();
    }
  }
  function clearFilters() {
    Object.values(inputs).forEach(input => { input.value = ''; });
    consult();
  }
  form.addEventListener('submit', event => { event.preventDefault(); consult(); });
  byId('rpa-clear').addEventListener('click', clearFilters);
  byId('rpa-clear-link').addEventListener('click', clearFilters);
  byId('rpa-refresh').addEventListener('click', consult);
  byId('rpa-select-all').addEventListener('change', event => {
    filtered.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE).filter(item => item.elegivel).forEach(item => {
      if (event.target.checked) selected.add(item.codigo_processo);
      else selected.delete(item.codigo_processo);
    });
    selectionChanged();
  });
  byId('rpa-clear-selection').addEventListener('click', () => { selected.clear(); selectionChanged(); });
  byId('rpa-prev').addEventListener('click', () => { page--; renderTable(); });
  byId('rpa-next').addEventListener('click', () => { page++; renderTable(); });
  byId('rpa-prepare').addEventListener('click', async () => {
    const prepare = byId('rpa-prepare');
    const sequence = validationSequence;
    const codes = [...selected];
    prepare.disabled = true;
    validation('Atualizando validação...');
    try {
      const result = await requestPayload(codes);
      if (sequence !== validationSequence) return;
      payload = result;
      byId('rpa-payload').textContent = JSON.stringify(payload, null, 2);
      byId('rpa-payload-actions').hidden = false;
      byId('rpa-payload-details').hidden = false;
      validation(`Agrupamento válido: ${payload.quantidade_itens} códigos preparados.`, 'valid');
    } catch (error) { if (sequence === validationSequence) { resetPayload(); validation(error.message || 'Falha ao preparar agrupamento.', 'invalid'); } }
    finally { if (sequence === validationSequence) prepare.disabled = !payload; }
  });
  byId('rpa-copy').addEventListener('click', async () => {
    if (!payload) return;
    try { await navigator.clipboard.writeText(payload.codigos_destacados_para_agrupamento.join(', ')); status('Códigos copiados.'); }
    catch (_) { status('Não foi possível copiar os códigos.', true); }
  });
  byId('rpa-export').addEventListener('click', () => {
    if (!payload) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], {type: 'application/json'}));
    const anchor = document.createElement('a');
    anchor.href = url; anchor.download = 'rpa-agrupamento.json'; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  consult();
})();
