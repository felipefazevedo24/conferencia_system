from datetime import date
from pathlib import Path
import json
import shutil
import subprocess
from unittest.mock import patch

import pytest

from test_app import build_test_app, set_logged_user
from conferencia_app.extensions import db
from conferencia_app.models import ExpedicaoRomaneio, ExpedicaoRomaneioNF, PermissaoAcesso


@pytest.mark.parametrize("permitido", [False, True])
def test_gestao_romaneio_por_usuario(tmp_path, permitido):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "gestor_romaneio", "Portaria")
    with app.app_context():
        if permitido:
            db.session.add(PermissaoAcesso(scope_type="USER", scope_id="gestor_romaneio",
                                          permission_key="MANAGE_EXPEDICAO_ROMANEIO", allow=True))
        rom = ExpedicaoRomaneio(numero_romaneio="ROM-GESTAO", data_romaneio=date.today(),
                               criado_por="admin", status="Expedido", tipo_frete="FOB")
        db.session.add(rom)
        db.session.commit()
        rom_id = rom.id
    base = f"/api/expedicao/romaneio-fat/{rom_id}"
    esperado = 200 if permitido else 403
    assert client.post(base + "/estornar-expedicao").status_code == esperado
    assert client.post(base + "/estornar-finalizacao").status_code == esperado
    assert client.put(base, json={"placa": "ABC1D23"}).status_code == esperado
    if permitido:
        pagina = client.get("/expedicao/romaneio")
        assert pagina.status_code == 200
        assert b'data-can-manage-romaneio="true"' in pagina.data
        with app.app_context():
            rom = db.session.get(ExpedicaoRomaneio, rom_id)
            assert rom.status == "Rascunho"
            assert rom.placa == "ABC1D23"
            assert rom.atualizado_por == "gestor_romaneio"
    assert client.delete(base + "/deletar").status_code == esperado


@pytest.mark.parametrize("duplicar", [False, True])
def test_montagem_romaneio_atomica(tmp_path, duplicar):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "admin", "Admin")
    nfs = [{"numero_nf": "100", "peso_bruto": 10, "qtde_volumes": 2},
           {"numero_nf": "100" if duplicar else "200", "peso_bruto": 20, "qtde_volumes": 3}]
    with patch("conferencia_app.routes.expedicao_romaneio_routes._dados_nf_do_bridge", return_value={}), \
         patch("conferencia_app.routes.expedicao_romaneio_routes.enviar_aviso_coleta_fob") as aviso:
        resp = client.post("/api/expedicao/romaneio-fat", json={"nfs": nfs, "tipo_frete": "FOB", "placa": "ABC1D23"})
    assert resp.status_code == (400 if duplicar else 201)
    with app.app_context():
        assert ExpedicaoRomaneio.query.count() == (0 if duplicar else 1)
        assert ExpedicaoRomaneioNF.query.count() == (0 if duplicar else 2)
        if not duplicar:
            rom = ExpedicaoRomaneio.query.one()
            assert rom.peso_bruto_total == 30
            assert rom.qtde_volumes_total == 5
            assert rom.placa == "ABC1D23"
    assert aviso.call_count == (0 if duplicar else 2)


def test_montagem_rejeita_romaneio_vazio(tmp_path):
    app = build_test_app(tmp_path)
    client = app.test_client()
    set_logged_user(client, "admin", "Admin")
    assert client.post("/api/expedicao/romaneio-fat", json={}).status_code == 400
    with app.app_context():
        assert ExpedicaoRomaneio.query.count() == 0


def test_preparar_cancelar_e_confirmar_romaneio_na_tela():
    root = Path(__file__).resolve().parents[1]
    node = shutil.which("node") or str(root / ".venv/Lib/site-packages/playwright/driver/node.exe")
    if not Path(node).exists():
        pytest.skip("Node indisponivel para validar o JavaScript")
    html = (root / "templates/expedicao_romaneio.html").read_text(encoding="utf-8")
    eventos = html[html.index('    $("rom-btn-novo").addEventListener'):html.index('    async function consultarCnpjTransportadora')]
    helpers = html[html.index('    let proximoIdLocal'):html.index('    async function finalizarRomaneio')]
    script = r'''
const assert = require('node:assert/strict');
const vm = require('node:vm');
const elementos = new Map();
const $ = id => {
    if (!elementos.has(id)) elementos.set(id, {
        value: '', disabled: false, textContent: '', handlers: {},
        classList: { add() {}, remove() {} },
        addEventListener(tipo, fn) { this.handlers[tipo] = fn; }
    });
    return elementos.get(id);
};
let romaneioAtual = null, nfsAtual = [], romTransportadoraDados = null;
const chamadas = [];
const api = async (url, opts) => { chamadas.push({url, ...opts}); return {id: 1}; };
const toast = (msg, tipo) => { if (tipo === 'error') throw Error(msg); };
const renderNFsList = () => {};
const atualizarSelectModal = async () => {};
const carregarRomaneios = () => {};
const window = {};
'''
    script += helpers + eventos + r'''
(async () => {
    await $('rom-btn-novo').handlers.click();
    $('rom-nf-manual').value = '100';
    await $('rom-btn-add-nf-manual').handlers.click();
    assert.equal(nfsAtual.length, 1);
    assert.equal(chamadas.length, 0);
    $('rom-modal-cancel').handlers.click();
    await $('rom-btn-novo').handlers.click();
    assert.equal(nfsAtual.length, 0);
    assert.equal(chamadas.length, 0);
    $('rom-nf-manual').value = '200';
    await $('rom-btn-add-nf-manual').handlers.click();
    const salvar = $('rom-modal-save').handlers.click;
    await Promise.all([salvar(), salvar()]);
    assert.equal(chamadas.length, 1);
    assert.equal(chamadas[0].method, 'POST');
    assert.equal(JSON.parse(chamadas[0].body).nfs[0].numero_nf, '200');
})().catch(err => { console.error(err); process.exitCode = 1; });
'''
    # Compila tambem o script completo para detectar erros fora dos handlers.
    js = html.split("<script>", 1)[1].split("</script>", 1)[0]
    script += "\nnew vm.Script(" + json.dumps(js) + ");\n"
    result = subprocess.run([node, "-"], input=script, text=True, encoding="utf-8", capture_output=True)
    assert result.returncode == 0, result.stderr
