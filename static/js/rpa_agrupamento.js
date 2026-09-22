(() => {
  const form = document.getElementById('rpa-search-form');
  const search = document.getElementById('rpa-search');
  const body = document.getElementById('rpa-results-body');
  const message = document.getElementById('rpa-message');
  const count = document.getElementById('rpa-count');
  const selectedCount = document.getElementById('rpa-selected-count');
  const prepare = document.getElementById('rpa-prepare');
  const copy = document.getElementById('rpa-copy');
  const exportButton = document.getElementById('rpa-export');
  const payloadView = document.getElementById('rpa-payload');
  let rows = [];
  let selected = new Set();
  let payload = null;
  let lastSearch = '';

  function status(text, error = false) {
    message.textContent = text;
    message.classList.toggle('error', error);
  }
  function selectionChanged() {
    selectedCount.textContent = `${selected.size} selecionado${selected.size === 1 ? '' : 's'}`;
    prepare.disabled = selected.size < 2;
    copy.disabled = true;
    exportButton.disabled = true;
    payload = null;
    payloadView.hidden = true;
  }
  function td(row, value) {
    const cell = document.createElement('td');
    cell.textContent = value == null || value === '' ? '—' : String(value);
    row.append(cell);
  }
  function render() {
    body.replaceChildren();
    count.textContent = `${rows.length} linha${rows.length === 1 ? '' : 's'}`;
    if (!rows.length) {
      const tr = document.createElement('tr');
      const cell = document.createElement('td');
      cell.colSpan = 9;
      cell.className = 'rpa-empty';
      cell.textContent = 'Nenhum processo de Corte Laser elegível encontrado.';
      tr.append(cell);
      body.append(tr);
      return;
    }
    rows.forEach(item => {
      const tr = document.createElement('tr');
      const first = document.createElement('td');
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.setAttribute('aria-label', `Selecionar processo ${item.codigo_processo} da OS ${item.os_completa}`);
      input.disabled = !item.elegivel;
      input.checked = selected.has(item.codigo_processo);
      tr.classList.toggle('selected', input.checked);
      input.addEventListener('change', () => {
        if (input.checked) selected.add(item.codigo_processo);
        else selected.delete(item.codigo_processo);
        document.querySelectorAll('#rpa-results-body input[type=checkbox]').forEach(box => {
          if (box.getAttribute('data-code') === item.codigo_processo) {
            box.checked = input.checked;
            box.closest('tr').classList.toggle('selected', input.checked);
          }
        });
        selectionChanged();
      });
      input.dataset.code = item.codigo_processo;
      first.append(input);
      tr.append(first);
      td(tr, item.os_completa); td(tr, item.codigo_processo);
      td(tr, item.classificacao); td(tr, item.descricao);
      td(tr, item.material); td(tr, item.espessura);
      td(tr, item.quantidade_formato); td(tr, item.cliente);
      body.append(tr);
    });
  }
  async function consult() {
    lastSearch = search.value.trim();
    status('Consultando o GRV…');
    count.textContent = 'Consultando…';
    selected = new Set();
    selectionChanged();
    try {
      const response = await fetch(`/api/producao/rpa-agrupamento?q=${encodeURIComponent(lastSearch)}`);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Falha na consulta.');
      rows = data.resultados || [];
      render();
      status(`${rows.length} linha${rows.length === 1 ? '' : 's'} carregada${rows.length === 1 ? '' : 's'}.`);
    } catch (error) {
      rows = [];
      render();
      status(error.message || 'Falha na consulta.', true);
    }
  }
  form.addEventListener('submit', event => { event.preventDefault(); consult(); });
  prepare.addEventListener('click', async () => {
    prepare.disabled = true;
    status('Validando seleção no GRV…');
    try {
      const response = await fetch('/api/producao/rpa-agrupamento/payload', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({busca: lastSearch, codigos: [...selected]})
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Seleção inválida.');
      payload = data.payload;
      payloadView.textContent = JSON.stringify(payload, null, 2);
      payloadView.hidden = false;
      copy.disabled = false;
      exportButton.disabled = false;
      status(`${payload.quantidade_itens} códigos preparados para agrupamento.`);
    } catch (error) { status(error.message || 'Falha ao preparar agrupamento.', true); }
    finally { prepare.disabled = selected.size < 2; }
  });
  copy.addEventListener('click', async () => {
    if (!payload) return;
    try { await navigator.clipboard.writeText(payload.codigos_destacados_para_agrupamento.join(', ')); status('Códigos copiados.'); }
    catch (_) { status('Não foi possível copiar os códigos.', true); }
  });
  exportButton.addEventListener('click', () => {
    if (!payload) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], {type: 'application/json'}));
    const anchor = document.createElement('a');
    anchor.href = url; anchor.download = 'rpa-agrupamento.json'; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
  consult();
})();
