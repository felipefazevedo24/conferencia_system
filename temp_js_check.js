
const form = document.getElementById("expedicao-form");
const nfInput = document.getElementById("numero_nf");
const nfFeedback = document.getElementById("nf-feedback");
const nomeClienteWrapper = document.getElementById("nome-cliente-wrapper");
const nomeClienteInput = document.getElementById("nome_cliente");
const fotosCameraInput = document.getElementById("fotos-camera");
const fotosGaleriaInput = document.getElementById("fotos-galeria");
const fotosPreview = document.getElementById("fotos-preview");
const fotosFeedback = document.getElementById("fotos-feedback");
const historicoLista = document.getElementById("historico-lista");
const buscaHistorico = document.getElementById("busca-historico");
const filtroStatus = document.getElementById("filtro-status");
const btnConsultarNf = document.getElementById("btn-consultar-nf");
const btnAbrirCamera = document.getElementById("btn-abrir-camera");
const btnEscolherFotos = document.getElementById("btn-escolher-fotos");
const btnSalvar = document.getElementById("btn-salvar-conferencia");
const toastEl = document.getElementById("expedicao-toast");
const isAdmin = {{ "true" if is_admin else "false" }};
let toastTimer = null;
let fotosSelecionadas = [];

function showToast(message, type = "success") {
    toastEl.textContent = message;
    toastEl.className = `exp-toast ${type} show`;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toastEl.classList.remove("show"), 3200);
}

function debounce(fn, wait = 250) {
    let timer = null;
    return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), wait);
    };
}

function resetNfState() {
    nfFeedback.textContent = "";
    nfFeedback.className = "exp-subtle";
    nomeClienteWrapper.classList.add("hidden");
    nomeClienteInput.required = false;
    nomeClienteInput.readOnly = false;
    nomeClienteInput.value = "";
}

function syncFotoFeedback() {
    if (!fotosSelecionadas.length) {
        fotosFeedback.textContent = "Nenhuma foto selecionada ainda.";
        return;
    }
    fotosFeedback.textContent = `${fotosSelecionadas.length} foto(s) pronta(s) para upload.`;
}

function addFotos(files) {
    const novasFotos = Array.from(files || []);
    if (!novasFotos.length) return;
    fotosSelecionadas = fotosSelecionadas.concat(novasFotos);
    updateFotoPreview();
}

async function consultarNf() {
    const numero = nfInput.value.trim();
    if (!numero) {
        resetNfState();
        return;
    }
    nfFeedback.textContent = "Consultando a Consyste...";
    btnConsultarNf.disabled = true;
    try {
        const response = await fetch(`/api/expedicao/conferencia-simples/consultar-nf?numero_nf=${encodeURIComponent(numero)}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "N├úo foi poss├¡vel consultar a nota fiscal.");
        nfFeedback.textContent = data.warning || (data.encontrada ? `NF encontrada. Cliente: ${data.nome_cliente || "sem raz├úo social retornada"}.` : "");
        nfFeedback.className = `exp-subtle ${data.encontrada ? "success" : (data.warning ? "warning" : "")}`;
        if (data.encontrada && data.nome_cliente) {
            nomeClienteWrapper.classList.remove("hidden");
            nomeClienteInput.readOnly = true;
            nomeClienteInput.required = false;
            nomeClienteInput.value = data.nome_cliente;
        } else if (data.manual_required) {
            nomeClienteWrapper.classList.remove("hidden");
            nomeClienteInput.readOnly = false;
            nomeClienteInput.required = true;
            if (!data.encontrada) nomeClienteInput.focus();
        } else {
            resetNfState();
        }
    } catch (error) {
        nfFeedback.textContent = error.message;
        nfFeedback.className = "exp-subtle warning";
        nomeClienteWrapper.classList.remove("hidden");
        nomeClienteInput.readOnly = false;
        nomeClienteInput.required = true;
    } finally {
        btnConsultarNf.disabled = false;
    }
}

function updateFotoPreview() {
    fotosPreview.innerHTML = "";
    if (!fotosSelecionadas.length) {
        syncFotoFeedback();
        return;
    }
    syncFotoFeedback();
    fotosSelecionadas.forEach(file => {
        const url = URL.createObjectURL(file);
        const card = document.createElement("div");
        card.className = "exp-preview-card";
        card.innerHTML = `<img src="${url}" alt="${file.name}"><span>${file.name}</span>`;
        fotosPreview.appendChild(card);
    });
}

function renderHistorico(data) {
    cacheRegistros(data);
    document.getElementById("resumo-total").textContent = data?.resumo?.total || 0;
    document.getElementById("resumo-pendentes").textContent = data?.resumo?.pendentes || 0;
    document.getElementById("resumo-expedidos").textContent = data?.resumo?.expedidos || 0;
    document.getElementById("resumo-aguardando").textContent = data?.resumo?.aguardando_estorno || 0;
    const registros = data?.registros || [];
    if (!registros.length) {
        historicoLista.innerHTML = `<div class="empty-state"><strong>Nenhuma conferência encontrada.</strong><span>Abra a primeira conferência acima ou ajuste os filtros.</span></div>`;
        return;
    }
    historicoLista.innerHTML = registros.map(registro => {
        const pending = registro.status_slug === "pendente_expedicao";
        const aguardando = registro.status_slug === "aguardando_estorno";
        const expedido = registro.status === "Expedido";
        const finalizado = registro.status === "Finalizado";
        const badgeClass = pending ? "pending" : (aguardando ? "pending" : (expedido ? "expedido" : "done"));
        const fotosHtml = (registro.fotos || []).length
            ? `<div class="exp-preview-grid">${registro.fotos.map(foto => `<a class="exp-photo" href="${foto.url}" target="_blank" rel="noopener noreferrer"><img src="${foto.url}" alt="${foto.nome}"><span>${foto.nome}</span></a>`).join("")}</div>`
            : `<div class="exp-caption">Sem fotos anexadas.</div>`;
        
        // Box de canhoto - aparece quando expedido
        let canhotoHtml = "";
        if (expedido && !registro.canhoto_url) {
            canhotoHtml = `
                <div class="exp-canhoto-box">
                    <div class="exp-canhoto-title">📸 Foto do Canhoto Pendente</div>
                    <p style="margin:0 0 10px;font-size:12px;color:#1e40af;">Para finalizar este registro, tire uma foto do canhoto assinado pelo motorista.</p>
                    <div class="exp-canhoto-actions">
                        <input type="file" accept="image/*" capture="environment" data-action="upload-canhoto" data-id="${registro.id}" style="display:none;" id="canhoto-input-${registro.id}">
                        <button class="exp-btn" onclick="document.getElementById('canhoto-input-${registro.id}').click()">📷 Tirar foto do canhoto</button>
                    </div>
                </div>`;
        } else if (finalizado && registro.canhoto_url) {
            canhotoHtml = `
                <div class="exp-canhoto-box done">
                    <div class="exp-canhoto-title">✅ Canhoto anexado</div>
                    <div class="exp-canhoto-actions">
                        <a href="${registro.canhoto_url}" target="_blank"><img src="${registro.canhoto_url}" class="exp-canhoto-thumb" alt="Canhoto"></a>
                        <span style="font-size:12px;color:#166534;">Anexado em ${registro.canhoto_uploaded_at || "---"} por ${registro.canhoto_uploaded_by || "---"}</span>
                    </div>
                </div>`;
        }
        
        return `
            <article class="exp-record">
                <div class="exp-record-head">
                    <div class="exp-record-title">
                        <strong>Orçamento ${registro.orcamento}</strong>
                        <span>${registro.data_conferencia} · Conferente ${registro.conferente}</span>
                    </div>
                    <span class="exp-status ${badgeClass}">${registro.status}</span>
                </div>
                <div class="exp-record-grid">
                    <div class="exp-record-field"><span>NF</span><strong>${registro.numero_nf || "Não informada"}</strong></div>
                    <div class="exp-record-field"><span>Cliente</span><strong>${registro.nome_cliente || "Não informado"}</strong></div>
                    <div class="exp-record-field"><span>Transportadora</span><strong>${registro.transportadora || "Não informada"}</strong></div>
                    <div class="exp-record-field"><span>Placa</span><strong>${registro.placa || "Não informada"}</strong></div>
                    <div class="exp-record-field"><span>Motorista</span><strong>${registro.motorista || "Não informado"}</strong></div>
                    <div class="exp-record-field"><span>Origem NF</span><strong>${registro.nf_origem || "Manual"}</strong></div>
                </div>
                ${fotosHtml}
                ${canhotoHtml}
                <div class="exp-record-actions">
                    <div class="exp-caption">${finalizado ? `Finalizado em ${registro.finalizado_at} por ${registro.finalizado_by || "sistema"}.` : (registro.expedido_at ? `Expedido em ${registro.expedido_at} por ${registro.expedido_by || "sistema"}. Aguardando foto do canhoto.` : (aguardando ? `Estorno solicitado por ${registro.estorno_pendente?.solicitante || "---"} em ${registro.estorno_pendente?.data || "---"}: ${registro.estorno_pendente?.motivo || ""}` : `Origem do cliente: ${registro.cliente_origem || "Manual"}.`))}</div>
                    <div style="display:flex; gap:12px; flex-wrap:wrap;">
                        ${pending ? `<button class="exp-btn" data-action="completar" data-id="${registro.id}" data-orcamento="${registro.orcamento}" data-nf="${registro.numero_nf}" data-cliente="${registro.nome_cliente}" data-transportadora="${registro.transportadora || ''}" data-placa="${registro.placa || ''}" data-motorista="${registro.motorista || ''}" data-has-fotos="${(registro.fotos || []).length ? '1' : ''}" data-was-expedido="${registro.expedido_at ? '1' : ''}">${(registro.fotos || []).length ? 'Editar conferência' : 'Completar conferência'}</button>` : ""}
                        ${aguardando ? "" : (pending ? `<button class="exp-btn" data-action="status" data-id="${registro.id}" data-status-slug="expedido" data-status-label="Expedido">Marcar expedido</button>` : "")}
                        ${(expedido || finalizado) && !aguardando ? (isAdmin ? `<button class="exp-btn-secondary" data-action="status" data-id="${registro.id}" data-status-slug="pendente_expedicao" data-status-label="Pendente de expedição">Voltar para pendente</button>` : `<button class="exp-btn-secondary" style="border-color:#ef4444;color:#ef4444;" data-action="solicitar-estorno" data-id="${registro.id}" data-orcamento="${registro.orcamento}">Solicitar estorno</button>`) : ""}
                        ${aguardando && isAdmin && registro.estorno_pendente ? `<button class="exp-btn" style="background:#16a34a;" data-action="admin-estorno" data-estorno-id="${registro.estorno_pendente.id}" data-orcamento="${registro.orcamento}" data-motivo="${registro.estorno_pendente.motivo}" data-solicitante="${registro.estorno_pendente.solicitante}">Aprovar / Rejeitar estorno</button>` : ""}
                        ${aguardando && !isAdmin ? `<span style="font-size:13px;color:#f59e0b;font-weight:600;">&#9200; Aguardando aprovação do administrador</span>` : ""}
    }).join("");
}

async function loadHistorico() {
    const params = new URLSearchParams();
    if (buscaHistorico.value.trim()) params.set("q", buscaHistorico.value.trim());
    if (filtroStatus.value) params.set("status", filtroStatus.value);
    historicoLista.innerHTML = `<div class="empty-state"><strong>Atualizando hist├│rico...</strong><span>Buscando as confer├¬ncias registradas.</span></div>`;
    try {
        const response = await fetch(`/api/expedicao/conferencia-simples?${params.toString()}`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "N├úo foi poss├¡vel carregar o hist├│rico.");
        renderHistorico(data);
    } catch (error) {
        historicoLista.innerHTML = `<div class="empty-state"><strong>N├úo foi poss├¡vel carregar o hist├│rico.</strong><span>${error.message}</span></div>`;
    }
}

async function atualizarStatus(id, statusSlug, statusLabel) {
    try {
        const response = await fetch(`/api/expedicao/conferencia-simples/${id}/status`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status_slug: statusSlug }),
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "N├úo foi poss├¡vel atualizar o status.");
        if (data.aguardando_admin) {
            showToast("Solicita├º├úo de estorno enviada. Aguarde aprova├º├úo do admin.");
        } else {
            showToast(`Confer├¬ncia atualizada para ${statusLabel}.`);
        }
        loadHistorico();
    } catch (error) {
        showToast(error.message, "error");
    }
}

async function excluirConferencia(id, orcamento) {
    const confirmado = window.confirm(`Deseja excluir a confer├¬ncia do or├ºamento ${orcamento}? Essa a├º├úo n├úo poder├í ser desfeita.`);
    if (!confirmado) return;

    try {
        const response = await fetch(`/api/expedicao/conferencia-simples/${id}`, {
            method: "DELETE",
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "N├úo foi poss├¡vel excluir a confer├¬ncia.");
        showToast(`Confer├¬ncia do or├ºamento ${orcamento} exclu├¡da com sucesso.`);
        loadHistorico();
    } catch (error) {
        showToast(error.message, "error");
    }
}

form.addEventListener("submit", async event => {
    event.preventDefault();
    if (!fotosSelecionadas.length) {
        showToast("Inclua pelo menos uma foto da confer├¬ncia.", "error");
        return;
    }
    btnSalvar.disabled = true;
    btnSalvar.textContent = "Salvando...";
    try {
        const formData = new FormData(form);
        fotosSelecionadas.forEach(file => formData.append("fotos", file));
        const response = await fetch("/api/expedicao/conferencia-simples", { method: "POST", body: formData });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "N├úo foi poss├¡vel salvar a confer├¬ncia.");
        showToast("Confer├¬ncia criada com sucesso.");
        form.reset();
        resetNfState();
        fotosSelecionadas = [];
        fotosPreview.innerHTML = "";
        syncFotoFeedback();
        loadHistorico();
    } catch (error) {
        showToast(error.message, "error");
    } finally {
        btnSalvar.disabled = false;
        btnSalvar.textContent = "Salvar confer├¬ncia";
    }
});

document.getElementById("btn-limpar-form").addEventListener("click", () => {
    resetNfState();
    fotosSelecionadas = [];
    fotosCameraInput.value = "";
    fotosGaleriaInput.value = "";
    fotosPreview.innerHTML = "";
    syncFotoFeedback();
});
btnConsultarNf.addEventListener("click", consultarNf);
nfInput.addEventListener("blur", () => { if (!nfInput.value.trim()) resetNfState(); });
btnAbrirCamera.addEventListener("click", () => fotosCameraInput.click());
btnEscolherFotos.addEventListener("click", () => fotosGaleriaInput.click());
fotosCameraInput.addEventListener("change", event => {
    addFotos(event.target.files);
    event.target.value = "";
});
fotosGaleriaInput.addEventListener("change", event => {
    addFotos(event.target.files);
    event.target.value = "";
});
buscaHistorico.addEventListener("input", debounce(loadHistorico, 250));
filtroStatus.addEventListener("change", loadHistorico);
historicoLista.addEventListener("click", event => {
    const button = event.target.closest("[data-action='status']");
    if (button) {
        atualizarStatus(button.dataset.id, button.dataset.statusSlug, button.dataset.statusLabel);
        return;
    }

    const romaneioButton = event.target.closest("[data-action='romaneio']");
    if (romaneioButton) {
        abrirRomaneio(romaneioButton.dataset.id);
        return;
    }

    const deleteButton = event.target.closest("[data-action='delete']");
    if (deleteButton) {
        excluirConferencia(deleteButton.dataset.id, deleteButton.dataset.orcamento);
        return;
    }

    const estornoButton = event.target.closest("[data-action='solicitar-estorno']");
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
    if (completarButton) {
        abrirCompletarConferencia(
            completarButton.dataset.id,
            completarButton.dataset.orcamento,
            completarButton.dataset.nf,
            completarButton.dataset.cliente,
            completarButton.dataset.transportadora,
            completarButton.dataset.placa,
            completarButton.dataset.motorista,
            completarButton.dataset.hasFotos,
            completarButton.dataset.wasExpedido,
        );
    }
});

// Listener para upload de canhoto
historicoLista.addEventListener("change", async event => {
    const input = event.target;
    if (input.getAttribute("data-action") !== "upload-canhoto") return;
    const registroId = input.getAttribute("data-id");
    const file = input.files?.[0];
    if (!file) return;
    
    const formData = new FormData();
    formData.append("canhoto", file);
    
    try {
        showToast("Enviando foto do canhoto...");
        const response = await fetch(`/api/expedicao/conferencia-simples/${registroId}/canhoto`, {
            method: "POST",
            body: formData
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Não foi possível enviar o canhoto.");
        showToast("Canhoto anexado com sucesso! Registro finalizado.", "success");
        loadHistorico();
    } catch (error) {
        showToast(error.message, "error");
    }
    input.value = "";
});

/* ========== ESTORNO ========== */
let estornoConferenciaId = null;
let adminEstornoId = null;

function abrirModalEstorno(id, orcamento) {
    estornoConferenciaId = id;
    document.getElementById("modal-estorno-info").textContent = `Or├ºamento ${orcamento || id}. A solicita├º├úo ser├í enviada para o administrador aprovar.`;
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
        if (!response.ok) throw new Error(data.error || "N├úo foi poss├¡vel solicitar o estorno.");
        if (data.aguardando_admin) {
            showToast("Solicita├º├úo de estorno enviada. Aguarde aprova├º├úo do administrador.");
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
    document.getElementById("modal-admin-estorno-info").innerHTML = `<strong>Or├ºamento:</strong> ${orcamento || "---"}<br><strong>Solicitante:</strong> ${solicitante || "---"}<br><strong>Motivo:</strong> ${motivo || "---"}`;
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
        showToast(acao === "aprovar" ? "Estorno aprovado. Confer├¬ncia voltou para pendente." : "Estorno rejeitado. Confer├¬ncia permanece expedida.");
        fecharModalAdminEstorno();
        loadHistorico();
    } catch (error) {
        showToast(error.message, "error");
    } finally {
        btnA.disabled = false;
        btnR.disabled = false;
    }
}

/* ========== BUSCA R├üPIDA DE NF ========== */
const buscaNfInput = document.getElementById("busca-nf-input");
const btnBuscarNf = document.getElementById("btn-buscar-nf");
const buscaNfResultado = document.getElementById("busca-nf-resultado");

async function buscarNfRapida() {
    const numero = (buscaNfInput.value || "").replace(/\D/g, "").trim();
    if (!numero) {
        buscaNfInput.focus();
        return;
    }
    btnBuscarNf.disabled = true;
    btnBuscarNf.textContent = "Buscando...";
    buscaNfResultado.style.display = "block";
    buscaNfResultado.innerHTML = `<div style="padding:10px;color:#64748b;">Buscando NF ${numero} na Consyste...</div>`;

    try {
        const response = await fetch("/api/expedicao/conferencia-simples/buscar-nf", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ numero_nf: numero }),
        });
        const data = await response.json();

        if (data.error) {
            buscaNfResultado.innerHTML = `<div class="empty-state" style="padding:12px;"><strong style="color:#ef4444;">${data.error}</strong></div>`;
            return;
        }

        if (data.ja_existe) {
            buscaNfResultado.innerHTML = `
                <div style="padding:12px;background:#fef3c7;border-radius:8px;border:1px solid #f59e0b;">
                    <strong style="color:#b45309;">ÔÜá NF ${numero} j├í possui confer├¬ncia</strong>
                    <p style="margin:6px 0 0;font-size:13px;color:#78350f;">ID: ${data.conferencia_id} ÔÇö Status: ${data.status || "---"}</p>
                </div>`;
            return;
        }

        if (data.encontrada && data.conferencia_criada) {
            buscaNfResultado.innerHTML = `
                <div style="padding:12px;background:#d1fae5;border-radius:8px;border:1px solid #10b981;">
                    <strong style="color:#065f46;">Ô£à Confer├¬ncia criada para NF ${numero}</strong>
                    <p style="margin:6px 0 0;font-size:13px;color:#064e3b;">Status: Pendente de expedi├º├úo. Acesse o hist├│rico abaixo para completar.</p>
                </div>`;
            buscaNfInput.value = "";
            loadHistorico();
            return;
        }

        if (!data.encontrada) {
            /* NF n├úo encontrada - oferecer op├º├Áes */
            buscaNfResultado.innerHTML = `
                <div style="padding:14px;background:#fef2f2;border-radius:8px;border:1px solid #ef4444;">
                    <strong style="color:#991b1b;">NF ${numero} n├úo encontrada na Consyste</strong>
                    <p style="margin:8px 0 12px;font-size:13px;color:#7f1d1d;">O que deseja fazer?</p>
                    <div style="display:flex;gap:10px;flex-wrap:wrap;">
                        <button class="exp-btn" onclick="incluirManualComNf('${numero}')" style="background:#2563eb;">Incluir manualmente</button>
                        <button class="exp-btn-secondary" onclick="corrigirNumeroNf()" style="min-height:36px;">Corrigir n├║mero</button>
                    </div>
                </div>`;
            return;
        }
    } catch (error) {
        buscaNfResultado.innerHTML = `<div class="empty-state" style="padding:12px;"><strong style="color:#ef4444;">Erro: ${error.message}</strong></div>`;
    } finally {
        btnBuscarNf.disabled = false;
        btnBuscarNf.textContent = "Buscar";
    }
}

function incluirManualComNf(numero) {
    /* Preenche o formul├írio manual e rola at├® ele */
    const nfField = document.getElementById("numero_nf");
    if (nfField) nfField.value = numero;
    buscaNfResultado.style.display = "none";
    buscaNfInput.value = "";
    const form = document.getElementById("expedicao-form");
    if (form) form.scrollIntoView({ behavior: "smooth", block: "center" });
    const orcField = document.getElementById("orcamento");
    if (orcField) orcField.focus();
    showToast(`NF ${numero} preenchida no formul├írio. Complete os dados e salve.`);
}

function corrigirNumeroNf() {
    buscaNfResultado.style.display = "none";
    buscaNfInput.value = "";
    buscaNfInput.focus();
}

btnBuscarNf.addEventListener("click", buscarNfRapida);
buscaNfInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); buscarNfRapida(); }
});



/* ========== ROMANEIO ========== */
let romaneioRegistros = [];

function cacheRegistros(data) {
    romaneioRegistros = data?.registros || [];
}

function abrirRomaneio(id) {
    const reg = romaneioRegistros.find(r => String(r.id) === String(id));
    if (!reg) { showToast("Confer├¬ncia n├úo encontrada.", "error"); return; }
    const overlay = document.getElementById("romaneio-overlay");
    const fotosHtml = (reg.fotos || []).length
        ? `<div class="romaneio-fotos"><h3>Fotos da confer├¬ncia</h3><div class="romaneio-fotos-grid">${reg.fotos.map(f => `<img src="${f.url}" alt="${f.nome}">`).join("")}</div></div>`
        : "";
    overlay.innerHTML = `
        <div class="romaneio-sheet">
            <div class="romaneio-toolbar">
                <button onclick="window.print()" style="background:#0f62c9;color:#fff;border:none;">Imprimir</button>
                <button onclick="document.getElementById('romaneio-overlay').classList.remove('show')" style="background:#fff;border:1px solid #d1d5db;color:#374151;">Fechar</button>
            </div>
            <div class="romaneio-content">
                <div class="romaneio-header">
                    <h2>Romaneio de Expedi├º├úo</h2>
                    <p>Confer├¬ncia #${reg.id} ┬À ${reg.data_conferencia || "---"}</p>
                </div>
                <div class="romaneio-grid">
                    <div class="romaneio-cell"><label>Or├ºamento</label><span>${reg.orcamento || "---"}</span></div>
                    <div class="romaneio-cell"><label>Nota Fiscal</label><span>${reg.numero_nf || "N├úo informada"}</span></div>
                    <div class="romaneio-cell full"><label>Cliente</label><span>${reg.nome_cliente || "N├úo informado"}</span></div>
                    <div class="romaneio-cell"><label>Transportadora</label><span>${reg.transportadora || "N├úo informada"}</span></div>
                    <div class="romaneio-cell"><label>Placa</label><span>${reg.placa || "N├úo informada"}</span></div>
                    <div class="romaneio-cell"><label>Motorista</label><span>${reg.motorista || "N├úo informado"}</span></div>
                    <div class="romaneio-cell"><label>Conferente</label><span>${reg.conferente || "---"}</span></div>
                    <div class="romaneio-cell"><label>Status</label><span>${reg.status || "---"}</span></div>
                    <div class="romaneio-cell"><label>Origem NF</label><span>${reg.nf_origem || "Manual"}</span></div>
                    ${reg.expedido_at ? `<div class="romaneio-cell"><label>Expedido em</label><span>${reg.expedido_at}</span></div><div class="romaneio-cell"><label>Expedido por</label><span>${reg.expedido_by || "---"}</span></div>` : `<div class="romaneio-cell"><label>Origem cliente</label><span>${reg.cliente_origem || "Manual"}</span></div>`}
                    ${reg.consyste_chave ? `<div class="romaneio-cell full"><label>Chave de acesso</label><span style="font-size:12px;word-break:break-all;">${reg.consyste_chave}</span></div>` : ""}
                </div>
                ${fotosHtml}
                <div class="romaneio-footer">
                    <div><div class="sig-line">Conferente</div></div>
                    <div><div class="sig-line">Motorista / Transportadora</div></div>
                </div>
            </div>
        </div>
    `;
    overlay.classList.add("show");
    overlay.addEventListener("click", function handler(e) {
        if (e.target === overlay) { overlay.classList.remove("show"); overlay.removeEventListener("click", handler); }
    });
}

/* ========== COMPLETAR CONFER├èNCIA (modal inline) ========== */
let completarConferenciaId = null;

function abrirCompletarConferencia(id, orcamento, nf, cliente, transportadora, placa, motorista, hasFotos, wasExpedido) {
    completarConferenciaId = id;
    const isEditing = !!hasFotos;
    const wasExp = !!wasExpedido;
    const tituloForm = isEditing ? 'Editar confer├¬ncia' : 'Completar confer├¬ncia';
    const fotoLabel = isEditing ? 'Fotos adicionais (opcional)' : 'Fotos da confer├¬ncia *';
    const html = `
        <article class="exp-record" id="completar-form-wrapper" style="border:2px solid rgba(15,98,201,0.4);background:rgba(247,251,255,0.98);">
            <div class="exp-record-head">
                <div class="exp-record-title">
                    <strong>${tituloForm} #${id}</strong>
                    <span>NF ${nf || "N├úo informada"} ┬À ${cliente || "Cliente n├úo informado"}</span>
                </div>
            </div>
            <form id="completar-form">
                <div class="exp-form-grid">
                    <div class="exp-field">
                        <label for="compl-orcamento">Or├ºamento${wasExp ? ' (n├úo edit├ível)' : ''}</label>
                        <input id="compl-orcamento" name="orcamento" type="text" value="${orcamento || ""}" placeholder="N├║mero do or├ºamento" ${wasExp ? 'readonly' : ''}>
                    </div>
                    <div class="exp-field">
                        <label for="compl-nf">N┬║ NF${wasExp ? ' (n├úo edit├ível)' : ' (corrigir se necess├írio)'}</label>
                        <input id="compl-nf" name="numero_nf" type="text" value="${nf || ""}" placeholder="Manter ou corrigir" ${wasExp ? 'readonly' : ''}>
                    </div>
                    <div class="exp-field">
                        <label for="compl-cliente">Nome do cliente</label>
                        <input id="compl-cliente" name="nome_cliente" type="text" value="${cliente || ""}" placeholder="Corrigir raz├úo social se precisar">
                    </div>
                    <div class="exp-field">
                        <label for="compl-transportadora">Transportadora</label>
                        <input id="compl-transportadora" name="transportadora" type="text" value="${transportadora || ""}" placeholder="Opcional">
                    </div>
                    <div class="exp-field">
                        <label for="compl-placa">Placa</label>
                        <input id="compl-placa" name="placa" type="text" value="${placa || ""}" placeholder="Opcional">
                    </div>
                    <div class="exp-field">
                        <label for="compl-motorista">Motorista</label>
                        <input id="compl-motorista" name="motorista" type="text" value="${motorista || ""}" placeholder="Opcional">
                    </div>
                    <div class="exp-field full">
                        <label>${fotoLabel}</label>
                        <div class="exp-upload" style="min-height:100px;">
                            <div>
                                <strong>Tire a foto ou escolha da galeria</strong>
                                <div class="exp-upload-actions" style="margin-top:10px;">
                                    <button class="exp-btn" type="button" id="compl-btn-camera">Tirar foto</button>
                                    <button class="exp-btn-secondary" type="button" id="compl-btn-galeria">Escolher fotos</button>
                                </div>
                                <input id="compl-fotos-camera" class="exp-file-input-hidden" type="file" accept="image/*" capture="environment">
                                <input id="compl-fotos-galeria" class="exp-file-input-hidden" type="file" accept="image/*" multiple>
                            </div>
                        </div>
                        <div id="compl-fotos-feedback" class="exp-subtle">Nenhuma foto selecionada.</div>
                        <div id="compl-fotos-preview" class="exp-preview-grid"></div>
                    </div>
                </div>
                <div class="exp-actions" style="margin-top:14px;">
                    <button class="exp-btn-secondary" type="button" id="compl-cancelar">Cancelar</button>
                    <button class="exp-btn" type="submit" id="compl-salvar">Salvar confer├¬ncia</button>
                </div>
            </form>
        </article>
    `;
    // Insert at top of historico list
    const wrapper = document.createElement("div");
    wrapper.id = "completar-overlay";
    wrapper.innerHTML = html;
    historicoLista.prepend(wrapper);

    // Wire up the completar form
    let complFotos = [];
    const complForm = document.getElementById("completar-form");
    const complCamera = document.getElementById("compl-fotos-camera");
    const complGaleria = document.getElementById("compl-fotos-galeria");
    const complPreview = document.getElementById("compl-fotos-preview");
    const complFeedback = document.getElementById("compl-fotos-feedback");

    function updateComplPreview() {
        complPreview.innerHTML = "";
        complFeedback.textContent = complFotos.length ? `${complFotos.length} foto(s) pronta(s).` : "Nenhuma foto selecionada.";
        complFotos.forEach(file => {
            const url = URL.createObjectURL(file);
            const card = document.createElement("div");
            card.className = "exp-preview-card";
            card.innerHTML = `<img src="${url}" alt="${file.name}"><span>${file.name}</span>`;
            complPreview.appendChild(card);
        });
    }

    document.getElementById("compl-btn-camera").addEventListener("click", () => complCamera.click());
    document.getElementById("compl-btn-galeria").addEventListener("click", () => complGaleria.click());
    complCamera.addEventListener("change", e => { complFotos = complFotos.concat(Array.from(e.target.files)); updateComplPreview(); e.target.value = ""; });
    complGaleria.addEventListener("change", e => { complFotos = complFotos.concat(Array.from(e.target.files)); updateComplPreview(); e.target.value = ""; });
    document.getElementById("compl-cancelar").addEventListener("click", () => { document.getElementById("completar-overlay")?.remove(); });

    complForm.addEventListener("submit", async e => {
        e.preventDefault();
        if (!complFotos.length && !isEditing) { showToast("Inclua pelo menos uma foto.", "error"); return; }
        const btn = document.getElementById("compl-salvar");
        btn.disabled = true; btn.textContent = "Salvando...";
        try {
            const fd = new FormData(complForm);
            complFotos.forEach(f => fd.append("fotos", f));
            const resp = await fetch(`/api/expedicao/conferencia-simples/${completarConferenciaId}/completar`, { method: "POST", body: fd });
            const data = await resp.json();
            if (!resp.ok) throw new Error(data.error || "Erro ao completar.");
            showToast("Confer├¬ncia completada com sucesso.");
            document.getElementById("completar-overlay")?.remove();
            loadHistorico();
        } catch (err) {
            showToast(err.message, "error");
        } finally {
            btn.disabled = false; btn.textContent = "Salvar confer├¬ncia";
        }
    });

    // Scroll to the form
    document.getElementById("completar-form-wrapper")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

document.addEventListener("DOMContentLoaded", () => { loadHistorico(); });
