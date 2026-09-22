const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('../frontend_producao/node_modules/jsdom');

const root = path.join(__dirname, '..');
const markup = fs.readFileSync(path.join(root, 'templates/rpa_agrupamento.html'), 'utf8')
  .replace(/{%[\s\S]*?%}/g, '').replace(/{{[\s\S]*?}}/g, '');
const source = fs.readFileSync(path.join(root, 'static/js/rpa_agrupamento.js'), 'utf8');
const dom = new JSDOM(markup, {url: 'http://localhost/producao/rpa-agrupamento', runScripts: 'outside-only'});
const {window} = dom;
const document = window.document;
const rows = Array.from({length: 60}, (_, index) => ({
  os: String(index === 3 ? 6129 : 6128 + index), os_completa: index === 3 ? '6129/002' : `${6128 + index}/001`,
  codigo_processo: String(100070 + index), classificacao: index % 2 ? 'CMS' : 'MOLDE',
  descricao: `Peça ${index}`, material: index % 2 ? 'CHAPA A36 - 1/2' : 'CHAPA A572 - 3/8',
  espessura: index < 19 ? '4.75MM' : (index % 2 ? '12.70MM' : '9.53MM'), norma: index % 2 ? 'A36' : 'A572',
  quantidade_formato: 2, cliente: 'Cliente', observacao: 'Corte Laser',
  status_os: 'ABERTA', tipo_servico: 'CORTE LASER', elegivel: true,
}));
let failNext = false;
let modalOpened = false;
window.HTMLDialogElement.prototype.showModal = function () { this.open = true; modalOpened = true; };
window.HTMLDialogElement.prototype.close = function () { this.open = false; };
window.fetch = async (url, options) => {
  if (failNext) {
    failNext = false;
    return {ok: false, json: async () => ({error: 'Falha simulada'})};
  }
  if (options?.method === 'POST') {
    const codes = JSON.parse(options.body).codigos;
    const items = rows.filter(row => codes.includes(row.codigo_processo));
    if (new Set(items.map(row => row.material)).size !== 1) {
      return {ok: false, json: async () => ({error: 'Selecione processos da mesma chapa, espessura e norma.'})};
    }
    return {ok: true, json: async () => ({payload: {quantidade_itens: codes.length, codigos_destacados_para_agrupamento: codes}})};
  }
  return {ok: true, json: async () => ({resultados: rows})};
};
const tick = (ms = 0) => new Promise(resolve => setTimeout(resolve, ms));
const get = id => document.getElementById(id);

(async () => {
  window.eval(source);
  await tick(10);
  assert.equal(document.querySelectorAll('#rpa-results-body tr').length, 35);
  assert.equal(get('rpa-material').options.length, 3);
  assert.equal(get('rpa-thickness').options.length, 4);
  assert.equal(get('rpa-pagination').hidden, false);
  assert.equal(get('rpa-group-empty').hidden, false);
  assert.equal(get('rpa-group-description').maxLength, 80);

  get('rpa-group-toggle').click();
  assert.equal(get('rpa-group-panel').classList.contains('is-collapsed'), true);
  get('rpa-group-toggle').click();
  assert.equal(get('rpa-group-panel').classList.contains('is-collapsed'), false);

  get('rpa-next').click();
  assert.match(get('rpa-page-status').textContent, /36–60 de 60/);
  get('rpa-prev').click();
  document.querySelector('.detail-button').click();
  assert.equal(modalOpened, true);
  assert.match(get('rpa-details-content').textContent, /Código do processo/);

  [...document.querySelectorAll('#rpa-results-body tr')].find(row => row.cells[3].textContent === '100071').querySelector('input[type=checkbox]').click();
  assert.equal(get('rpa-group-count').textContent, '1');
  assert.equal(get('rpa-group-content').hidden, false);
  [...document.querySelectorAll('#rpa-results-body tr')].find(row => row.cells[3].textContent === '100073').querySelector('input[type=checkbox]').click();
  assert.equal(get('rpa-summary-os').textContent, '6129');
  assert.equal(get('rpa-summary-count').textContent, '2');
  assert.equal(get('rpa-summary-quantity').textContent, '4');
  await tick(350);
  assert.match(get('rpa-validation').textContent, /Agrupamento válido/);
  assert.equal(get('rpa-prepare').disabled, false);
  get('rpa-prepare').click();
  await tick(10);
  assert.equal(get('rpa-payload-actions').hidden, false);
  document.querySelectorAll('#rpa-results-body input[type=checkbox]')[1].click();
  assert.equal(get('rpa-payload-actions').hidden, true);
  document.querySelectorAll('#rpa-results-body input[type=checkbox]')[0].click();
  await tick(350);
  assert.match(get('rpa-validation').textContent, /mesma chapa/);
  assert.equal(get('rpa-prepare').disabled, true);
  get('rpa-clear-selection').click();
  assert.equal(get('rpa-group-empty').hidden, false);

  get('rpa-select-all').click();
  assert.equal(get('rpa-group-count').textContent, '35');
  get('rpa-clear-selection').click();

  get('rpa-classification').value = 'CMS';
  get('rpa-search-form').dispatchEvent(new window.Event('submit', {cancelable: true}));
  await tick(10);
  assert.equal(get('rpa-table-count').textContent, '30 processo(s)');
  get('rpa-clear').click();
  await tick(10);
  assert.equal(get('rpa-classification').value, '');
  assert.equal(get('rpa-table-count').textContent, '60 processo(s)');

  get('rpa-thickness').value = '4.75MM';
  get('rpa-search-form').dispatchEvent(new window.Event('submit', {cancelable: true}));
  await tick(10);
  const filteredRows = document.querySelectorAll('#rpa-results-body tr');
  assert.equal(get('rpa-table-count').textContent, '19 processo(s)');
  assert.equal(filteredRows.length, 19);
  assert.equal(get('rpa-pagination').hidden, true);
  document.querySelectorAll('#rpa-results-body tr')[0].querySelector('input[type=checkbox]').click();
  document.querySelectorAll('#rpa-results-body tr')[9].querySelector('input[type=checkbox]').click();
  document.querySelectorAll('#rpa-results-body tr')[18].querySelector('input[type=checkbox]').click();
  assert.equal(get('rpa-group-count').textContent, '3');
  assert.match(get('rpa-summary-codes').textContent, /100088/);
  get('rpa-group-description').value = 'Agrupamento teste';
  get('rpa-clear-selection').click();
  assert.equal(get('rpa-group-description').value, '');
  assert.equal(document.querySelector('.mode-banner'), null);

  failNext = true;
  get('rpa-refresh').click();
  await tick(10);
  assert.match(get('rpa-message').textContent, /Falha simulada/);
  assert.equal(get('rpa-message').classList.contains('error'), true);
  console.log('RPA agrupamento UI: filtros, paginação, seleção, detalhes, preparo, limpeza e erro OK');
})().catch(error => { console.error(error); process.exitCode = 1; });
