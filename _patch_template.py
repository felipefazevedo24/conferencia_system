# -*- coding: utf-8 -*-
"""Patch: update expedicao_conferencia_simples.html with estorno UI."""
import pathlib

p = pathlib.Path("templates/expedicao_conferencia_simples.html")
t = p.read_text("utf-8")

# 1. Add "Aguardando estorno" to filter dropdown + summary card
old_filter = '''                <select id="filtro-status">
                    <option value="">Todos os status</option>
                    <option value="Pendente de expedi\u00e7\u00e3o">Pendente de expedi\u00e7\u00e3o</option>
                    <option value="Expedido">Expedido</option>
                </select>'''

new_filter = '''                <select id="filtro-status">
                    <option value="">Todos os status</option>
                    <option value="Pendente de expedi\u00e7\u00e3o">Pendente de expedi\u00e7\u00e3o</option>
                    <option value="Expedido">Expedido</option>
                    <option value="Aguardando estorno">Aguardando estorno</option>
                </select>'''

if old_filter in t:
    t = t.replace(old_filter, new_filter, 1)
    print("OK: filter updated")

# 2. Add summary card for aguardando estorno
old_summary = '''                <div class="exp-summary-card"><span>Expedidos</span><strong id="resumo-expedidos">0</strong></div>
            </div>'''

new_summary = '''                <div class="exp-summary-card"><span>Expedidos</span><strong id="resumo-expedidos">0</strong></div>
                <div class="exp-summary-card" style="border-left:3px solid #f59e0b;"><span>Aguardando estorno</span><strong id="resumo-aguardando">0</strong></div>
            </div>'''

if old_summary in t:
    t = t.replace(old_summary, new_summary, 1)
    print("OK: summary card added")

# 3. Add estorno modal HTML before the toast element
old_toast = '<div id="expedicao-toast" class="exp-toast"></div>'

estorno_modal = '''<!-- Modal estorno -->
<div id="modal-estorno" style="display:none;position:fixed;inset:0;z-index:999;background:rgba(0,0,0,.45);align-items:center;justify-content:center;">
    <div style="background:#fff;border-radius:14px;padding:28px 24px;max-width:420px;width:92%;box-shadow:0 12px 40px rgba(0,0,0,.18);">
        <h3 style="margin:0 0 6px;font-size:17px;color:#1e293b;">Solicitar estorno de expedi\u00e7\u00e3o</h3>
        <p id="modal-estorno-info" style="margin:0 0 16px;font-size:13px;color:#64748b;">A solicita\u00e7\u00e3o ser\u00e1 enviada para o administrador aprovar.</p>
        <label style="font-size:13px;font-weight:600;color:#374151;display:block;margin-bottom:6px;">Motivo do estorno *</label>
        <textarea id="modal-estorno-motivo" rows="3" style="width:100%;border:1px solid #d1d5db;border-radius:8px;padding:10px;font-size:14px;resize:vertical;" placeholder="Descreva o motivo do estorno..."></textarea>
        <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:16px;">
            <button type="button" onclick="fecharModalEstorno()" style="padding:8px 18px;border:1px solid #d1d5db;border-radius:8px;background:#fff;cursor:pointer;font-size:14px;">Cancelar</button>
            <button type="button" id="btn-confirmar-estorno" onclick="confirmarEstorno()" style="padding:8px 18px;border:none;border-radius:8px;background:#ef4444;color:#fff;cursor:pointer;font-size:14px;font-weight:600;">Solicitar estorno</button>
        </div>
    </div>
</div>

<!-- Modal admin aprovar/rejeitar estorno -->
<div id="modal-admin-estorno" style="display:none;position:fixed;inset:0;z-index:999;background:rgba(0,0,0,.45);align-items:center;justify-content:center;">
    <div style="background:#fff;border-radius:14px;padding:28px 24px;max-width:420px;width:92%;box-shadow:0 12px 40px rgba(0,0,0,.18);">
        <h3 style="margin:0 0 6px;font-size:17px;color:#1e293b;">Autorizar estorno?</h3>
        <p id="modal-admin-estorno-info" style="margin:0 0 16px;font-size:13px;color:#64748b;"></p>
        <label style="font-size:13px;font-weight:600;color:#374151;display:block;margin-bottom:6px;">Observa\u00e7\u00e3o (opcional)</label>
        <textarea id="modal-admin-estorno-obs" rows="2" style="width:100%;border:1px solid #d1d5db;border-radius:8px;padding:10px;font-size:14px;resize:vertical;" placeholder="Observa\u00e7\u00e3o do admin..."></textarea>
        <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:16px;">
            <button type="button" onclick="fecharModalAdminEstorno()" style="padding:8px 18px;border:1px solid #d1d5db;border-radius:8px;background:#fff;cursor:pointer;font-size:14px;">Cancelar</button>
            <button type="button" id="btn-rejeitar-estorno" onclick="decidirEstorno('rejeitar')" style="padding:8px 18px;border:1px solid #ef4444;border-radius:8px;background:#fff;color:#ef4444;cursor:pointer;font-size:14px;font-weight:600;">Rejeitar</button>
            <button type="button" id="btn-aprovar-estorno" onclick="decidirEstorno('aprovar')" style="padding:8px 18px;border:none;border-radius:8px;background:#16a34a;color:#fff;cursor:pointer;font-size:14px;font-weight:600;">Aprovar</button>
        </div>
    </div>
</div>

''' + old_toast

if old_toast in t:
    t = t.replace(old_toast, estorno_modal, 1)
    print("OK: estorno modals added")

# 4. Update renderHistorico to handle the new status + estorno buttons
old_render_actions = '''                <div class="exp-record-actions">
                    <div class="exp-caption">${registro.expedido_at ? `Atualizado para expedido em ${registro.expedido_at} por ${registro.expedido_by || "sistema"}.` : `Origem do cliente: ${registro.cliente_origem || "Manual"}.`}</div>
                    <div style="display:flex; gap:12px; flex-wrap:wrap;">
                        ${pending && !(registro.fotos || []).length ? `<button class="exp-btn" data-action="completar" data-id="${registro.id}" data-orcamento="${registro.orcamento}" data-nf="${registro.numero_nf}" data-cliente="${registro.nome_cliente}">Completar confer\u00eancia</button>` : ""}
                        <button class="${pending ? "exp-btn" : "exp-btn-secondary"}" data-action="status" data-id="${registro.id}" data-status-slug="${pending ? "expedido" : "pendente_expedicao"}" data-status-label="${pending ? "Expedido" : "Pendente de expedi\u00e7\u00e3o"}">${pending ? "Marcar expedido" : "Voltar para pendente"}</button>
                        ${isAdmin ? `<button class="exp-btn-secondary" data-action="delete" data-id="${registro.id}" data-orcamento="${registro.orcamento}">Excluir</button>` : ""}
                    </div>
                </div>'''

new_render_actions = '''                <div class="exp-record-actions">
                    <div class="exp-caption">${registro.expedido_at ? `Atualizado para expedido em ${registro.expedido_at} por ${registro.expedido_by || "sistema"}.` : (aguardando ? `Estorno solicitado por ${registro.estorno_pendente?.solicitante || "---"} em ${registro.estorno_pendente?.data || "---"}: ${registro.estorno_pendente?.motivo || ""}` : `Origem do cliente: ${registro.cliente_origem || "Manual"}.`)}</div>
                    <div style="display:flex; gap:12px; flex-wrap:wrap;">
                        ${pending && !(registro.fotos || []).length ? `<button class="exp-btn" data-action="completar" data-id="${registro.id}" data-orcamento="${registro.orcamento}" data-nf="${registro.numero_nf}" data-cliente="${registro.nome_cliente}">Completar confer\u00eancia</button>` : ""}
                        ${aguardando ? "" : (pending ? `<button class="exp-btn" data-action="status" data-id="${registro.id}" data-status-slug="expedido" data-status-label="Expedido">Marcar expedido</button>` : "")}
                        ${(!pending && !aguardando) ? (isAdmin ? `<button class="exp-btn-secondary" data-action="status" data-id="${registro.id}" data-status-slug="pendente_expedicao" data-status-label="Pendente de expedi\u00e7\u00e3o">Voltar para pendente</button>` : `<button class="exp-btn-secondary" style="border-color:#ef4444;color:#ef4444;" data-action="solicitar-estorno" data-id="${registro.id}" data-orcamento="${registro.orcamento}">Solicitar estorno</button>`) : ""}
                        ${aguardando && isAdmin && registro.estorno_pendente ? `<button class="exp-btn" style="background:#16a34a;" data-action="admin-estorno" data-estorno-id="${registro.estorno_pendente.id}" data-orcamento="${registro.orcamento}" data-motivo="${registro.estorno_pendente.motivo}" data-solicitante="${registro.estorno_pendente.solicitante}">Aprovar / Rejeitar estorno</button>` : ""}
                        ${aguardando && !isAdmin ? `<span style="font-size:13px;color:#f59e0b;font-weight:600;">&#9200; Aguardando aprova\u00e7\u00e3o do administrador</span>` : ""}
                        ${isAdmin ? `<button class="exp-btn-secondary" data-action="delete" data-id="${registro.id}" data-orcamento="${registro.orcamento}">Excluir</button>` : ""}
                    </div>
                </div>'''

if old_render_actions in t:
    t = t.replace(old_render_actions, new_render_actions, 1)
    print("OK: actions updated")
else:
    print("NOT FOUND: actions")

# 5. Update the badge status to handle aguardando
old_badge = '''        const pending = registro.status_slug === "pendente_expedicao";
        const badgeClass = pending ? "pending" : "done";'''

new_badge = '''        const pending = registro.status_slug === "pendente_expedicao";
        const aguardando = registro.status_slug === "aguardando_estorno";
        const badgeClass = pending ? "pending" : (aguardando ? "pending" : "done");'''

if old_badge in t:
    t = t.replace(old_badge, new_badge, 1)
    print("OK: badge updated")

# 6. Update resumo rendering to show aguardando count
old_resumo_js = '''    document.getElementById("resumo-expedidos").textContent = data?.resumo?.expedidos || 0;'''

new_resumo_js = '''    document.getElementById("resumo-expedidos").textContent = data?.resumo?.expedidos || 0;
    document.getElementById("resumo-aguardando").textContent = data?.resumo?.aguardando_estorno || 0;'''

if old_resumo_js in t:
    t = t.replace(old_resumo_js, new_resumo_js, 1)
    print("OK: resumo JS updated")

# 7. Update the event listener for solicitar-estorno and admin-estorno actions
old_listener = '''    const completarButton = event.target.closest("[data-action='completar']");
    if (completarButton) {'''

new_listener = '''    const estornoButton = event.target.closest("[data-action='solicitar-estorno']");
    if (estornoButton) {
        abrirModalEstorno(estornoButton.dataset.id, estornoButton.dataset.orcamento);
        return;
    }

    const adminEstornoButton = event.target.closest("[data-action='admin-estorno']");
    if (adminEstornoButton) {
        abrirModalAdminEstorno(
            adminEstornoButton.dataset.estornoId,
            adminEstornoButton.dataset.orcamento,
            adminEstornoButton.dataset.motivo,
            adminEstornoButton.dataset.solicitante,
        );
        return;
    }

    const completarButton = event.target.closest("[data-action='completar']");
    if (completarButton) {'''

if old_listener in t:
    t = t.replace(old_listener, new_listener, 1)
    print("OK: event listener updated")

# 8. Add the modal JS functions before the BUSCA RÁPIDA section
busca_marker = '/* ========== BUSCA R\u00c1PIDA DE NF ========== */'

modal_js = '''/* ========== ESTORNO ========== */
let estornoConferenciaId = null;
let adminEstornoId = null;

function abrirModalEstorno(id, orcamento) {
    estornoConferenciaId = id;
    document.getElementById("modal-estorno-info").textContent = `Or\u00e7amento ${orcamento || id}. A solicita\u00e7\u00e3o ser\u00e1 enviada para o administrador aprovar.`;
    document.getElementById("modal-estorno-motivo").value = "";
    document.getElementById("modal-estorno").style.display = "flex";
}

function fecharModalEstorno() {
    document.getElementById("modal-estorno").style.display = "none";
    estornoConferenciaId = null;
}

async function confirmarEstorno() {
    const motivo = document.getElementById("modal-estorno-motivo").value.trim();
    if (!motivo) {
        showToast("Informe o motivo do estorno.", "error");
        return;
    }
    const btn = document.getElementById("btn-confirmar-estorno");
    btn.disabled = true;
    btn.textContent = "Enviando...";
    try {
        const response = await fetch(`/api/expedicao/conferencia-simples/${estornoConferenciaId}/status`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status_slug: "pendente_expedicao", motivo }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "N\u00e3o foi poss\u00edvel solicitar o estorno.");
        if (data.aguardando_admin) {
            showToast("Solicita\u00e7\u00e3o de estorno enviada. Aguarde aprova\u00e7\u00e3o do administrador.");
        } else {
            showToast("Estorno realizado com sucesso.");
        }
        fecharModalEstorno();
        loadHistorico();
    } catch (error) {
        showToast(error.message, "error");
    } finally {
        btn.disabled = false;
        btn.textContent = "Solicitar estorno";
    }
}

function abrirModalAdminEstorno(estornoId, orcamento, motivo, solicitante) {
    adminEstornoId = estornoId;
    document.getElementById("modal-admin-estorno-info").innerHTML = `<strong>Or\u00e7amento:</strong> ${orcamento || "---"}<br><strong>Solicitante:</strong> ${solicitante || "---"}<br><strong>Motivo:</strong> ${motivo || "---"}`;
    document.getElementById("modal-admin-estorno-obs").value = "";
    document.getElementById("modal-admin-estorno").style.display = "flex";
}

function fecharModalAdminEstorno() {
    document.getElementById("modal-admin-estorno").style.display = "none";
    adminEstornoId = null;
}

async function decidirEstorno(acao) {
    const observacao = document.getElementById("modal-admin-estorno-obs").value.trim();
    const btnA = document.getElementById("btn-aprovar-estorno");
    const btnR = document.getElementById("btn-rejeitar-estorno");
    btnA.disabled = true;
    btnR.disabled = true;
    try {
        const response = await fetch(`/api/expedicao/conferencia-simples/estorno/${adminEstornoId}/${acao}`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ observacao }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Falha ao processar estorno.");
        showToast(acao === "aprovar" ? "Estorno aprovado. Confer\u00eancia voltou para pendente." : "Estorno rejeitado. Confer\u00eancia permanece expedida.");
        fecharModalAdminEstorno();
        loadHistorico();
    } catch (error) {
        showToast(error.message, "error");
    } finally {
        btnA.disabled = false;
        btnR.disabled = false;
    }
}

'''

if busca_marker in t:
    t = t.replace(busca_marker, modal_js + busca_marker, 1)
    print("OK: modal JS functions added")

# 9. Also handle the case where atualizarStatus gets "aguardando_admin" back
old_atualizar = '''        showToast(`Confer\u00eancia atualizada para ${statusLabel}.`);'''
new_atualizar = '''        if (data.aguardando_admin) {
            showToast("Solicita\u00e7\u00e3o de estorno enviada. Aguarde aprova\u00e7\u00e3o do admin.");
        } else {
            showToast(`Confer\u00eancia atualizada para ${statusLabel}.`);
        }'''

if old_atualizar in t:
    t = t.replace(old_atualizar, new_atualizar, 1)
    print("OK: atualizarStatus updated")

p.write_text(t, "utf-8")
print("\nTemplate patch complete!")
