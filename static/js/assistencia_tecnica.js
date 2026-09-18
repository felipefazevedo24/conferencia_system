/* Modulo Assistencia Tecnica: separacao, faturamento e retorno das
 * Solicitacoes de NF (garantia, bonificacao, teste, conserto, atendimento
 * tecnico). Veio da aba "Faturamento avulso" que vivia dentro da Conferencia
 * de Expedicao (cega) — mesmo fluxo e mesmas rotas, agora em modulo proprio.
 *
 * Quem emite a NF continua sendo o Fiscal; a separacao segue com a Logistica.
 */
(function () {
    "use strict";
    const $ = (id) => document.getElementById(id);
    const IS_ADMIN = ($("at-root")?.dataset.isAdmin === "true");

    const escapeHtml = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

    const fmtDataHora = (v) => {
        if (!v) return "—";
        const d = new Date(v);
        if (isNaN(d.getTime())) return escapeHtml(String(v).slice(0, 16).replace("T", " "));
        return d.toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
    };

    function toast(msg, type) {
        const t = $("at-toast");
        if (!t) return;
        t.textContent = msg;
        t.className = "ce-toast" + (type ? " " + type : "");
        t.style.display = "block";
        clearTimeout(t._t);
        t._t = setTimeout(() => { t.style.display = "none"; }, 4200);
    }

    function criarControladorAvulso() {
        let ordens = [];
        let currentFilter = null;
        let busca = "";

        async function apiAvulso(url, opts) {
            const resp = await fetch(url, Object.assign({ headers: { "Content-Type": "application/json" } }, opts || {}));
            let data = {};
            try { data = await resp.json(); } catch (e) {}
            if (!resp.ok || !data.sucesso) throw new Error(data.erro || ("Erro " + resp.status));
            return data;
        }

        function renderMetrics(resumo) {
            resumo = resumo || {};
            Object.keys(resumo).forEach((k) => {
                const el = $("avulso-m-" + k);
                if (el) el.textContent = resumo[k] || 0;
            });
            const total = $("at-total"); if (total) total.textContent = ordens.length;
        }

        function statusFiltro(o) {
            if (o.status_slug === "estoque_terceiros" || o.status_slug === "estoque_assistencia") return true;
            return false;
        }

        function ordensFiltradas() {
            let lista = ordens;
            if (currentFilter === "pendencia_estoque") {
                lista = lista.filter(statusFiltro);
            } else if (currentFilter) {
                lista = lista.filter((o) => o.status_slug === currentFilter);
            }
            if (busca) {
                const termo = busca.toLowerCase();
                lista = lista.filter((o) =>
                    (o.protocolo || "").toLowerCase().includes(termo) ||
                    (o.cliente_nome || "").toLowerCase().includes(termo) ||
                    (o.solicitante_nome || "").toLowerCase().includes(termo));
            }
            return lista;
        }

        function renderAcoesAdmin(o) {
            if (!IS_ADMIN) return "";
            const podeEstornar = o.status_slug !== "em_separacao";
            return `
                <div class="avl-admin-actions">
                    ${podeEstornar ? `<button type="button" class="eui-btn eui-btn--warning eui-btn--sm" data-action="estornar"><i class="fas fa-rotate-left"></i> Estornar etapa</button>` : ""}
                    <button type="button" class="eui-btn eui-btn--danger eui-btn--sm" data-action="excluir"><i class="fas fa-trash"></i> Excluir solicitação</button>
                </div>`;
        }

        const STEP_LABEL = {
            em_separacao: "Separação",
            expedido_sem_nf: "Expedido s/ NF",
            aguardando_faturamento: "Aguard. faturamento",
            nf_emitida: "NF emitida",
            estoque_terceiros: "Poder de terceiros",
            estoque_assistencia: "Assistência",
            estoque_retornado: "Retornado",
        };
        const ACCENT = {
            em_separacao: "acc-warning", expedido_sem_nf: "acc-info", aguardando_faturamento: "acc-warning",
            nf_emitida: "acc-violet", estoque_terceiros: "acc-primary", estoque_assistencia: "acc-danger", estoque_retornado: "acc-success",
        };
        const TIPOS_AGUARDA = ["Remessa para Conserto", "Remessa de retorno de demonstração"];
        const TIPOS_RETORNO = ["Remessa para Teste", "Materiais para atendimento técnico no cliente", "Remessa para Conserto"];

        function fluxoSlugs(o) {
            const aguarda = o.status_slug === "aguardando_faturamento" || TIPOS_AGUARDA.includes(o.tipo_operacao);
            const comRetorno = ["estoque_terceiros", "estoque_assistencia", "estoque_retornado"].includes(o.status_slug)
                || TIPOS_RETORNO.includes(o.tipo_operacao);
            const seg2 = aguarda ? "aguardando_faturamento" : "expedido_sem_nf";
            if (!comRetorno) return ["em_separacao", seg2, "nf_emitida"];
            const finalStock = o.tipo_operacao === "Materiais para atendimento técnico no cliente" ? "estoque_assistencia" : "estoque_terceiros";
            return ["em_separacao", seg2, finalStock, "estoque_retornado"];
        }

        function renderSteps(o) {
            const slugs = fluxoSlugs(o);
            let cur = slugs.indexOf(o.status_slug);
            if (cur < 0) cur = 0;
            return `<div class="avl-steps">` + slugs.map((sl, i) => {
                const cls = i < cur ? "is-done" : (i === cur ? "is-current" : "");
                const bar = i > 0 ? `<span class="avl-step__bar ${i <= cur ? "is-done" : ""}"></span>` : "";
                const dot = i < cur ? '<i class="fas fa-check"></i>' : (i + 1);
                return `${bar}<div class="avl-step ${cls}"><span class="avl-step__dot">${dot}</span>${STEP_LABEL[sl] || sl}</div>`;
            }).join("") + `</div>`;
        }

        function renderItens(o, comCheckbox) {
            const rows = o.itens.map((it) => {
                const local = it.material_local
                    ? `<span class="avl-local"><i class="fas fa-location-dot"></i>${escapeHtml(it.material_local)}</span>`
                    : `<span class="avl-local is-empty"><i class="fas fa-location-dot"></i>sem local</span>`;
                const naoSep = (o.status_slug !== "em_separacao") && !it.separado;
                const chk = comCheckbox ? `<td><input type="checkbox" class="avl-chk-item" value="${it.id}" checked></td>` : "";
                return `<tr class="${naoSep ? "is-nao-separado" : ""}">
                    ${chk}
                    <td class="avl-td-cod">${escapeHtml(it.material_codigo)}</td>
                    <td>${escapeHtml(it.material_nome || "")}${naoSep ? ' <span style="color:var(--eui-danger);font-size:11px;">(não separado)</span>' : ""}</td>
                    <td>${local}</td>
                    <td class="avl-td-qtde">${it.quantidade}</td>
                </tr>`;
            }).join("");
            const thChk = comCheckbox ? '<th style="width:34px;"></th>' : "";
            return `<table class="avl-table"><thead><tr>${thChk}<th>Código</th><th>Descrição</th><th>Local de estoque</th><th style="text-align:right;">Qtde</th></tr></thead><tbody>${rows}</tbody></table>`;
        }

        function renderOfBox(o) {
            const info = o.ordem_faturamento_info;
            if (o.ordem_faturamento) {
                const detalhe = (info && info.encontrada)
                    ? `<div class="avl-nf-info">
                        <div><span class="k">Ordem de faturamento</span><span class="v">#${o.ordem_faturamento}</span></div>
                        ${info.cliente ? `<div><span class="k">Cliente</span><span class="v">${escapeHtml(info.cliente)}</span></div>` : ""}
                        ${info.status ? `<div><span class="k">Status da OF</span><span class="v">${escapeHtml(info.status)}</span></div>` : ""}
                        <div><span class="k">NF da OF</span><span class="v">${info.numero_nf ? escapeHtml(info.numero_nf) : "aguardando faturamento da OF…"}</span></div>
                       </div>`
                    : `<div class="avl-nf-info"><div><span class="k">Ordem de faturamento</span><span class="v">#${o.ordem_faturamento} (não localizada na base)</span></div></div>`;
                return `<div class="avl-section-title"><i class="fas fa-link"></i> Ordem de faturamento vinculada</div>
                    ${detalhe}
                    <p class="eui-caption" style="margin-top:6px;"><i class="fas fa-circle-info"></i> Quando a OF for faturada, a solicitação avança automaticamente.</p>
                    <div class="avl-nf-row" style="margin-top:8px;">
                        <input type="number" class="eui-input avl-of" placeholder="Trocar nº da OF" value="${o.ordem_faturamento}">
                    </div>
                    <div class="avl-detail-actions">
                        <button type="button" class="eui-btn eui-btn--subtle" data-action="vincular-of"><i class="fas fa-link"></i> Atualizar vínculo</button>
                    </div>`;
            }
            return `<div class="avl-section-title"><i class="fas fa-link"></i> Vincular ordem de faturamento</div>
                <p class="eui-caption"><i class="fas fa-circle-info"></i> Vincule a OF do ERP correspondente a esta expedição. Quando ela for faturada, a solicitação avança sozinha para o status final.</p>
                <div class="avl-nf-row">
                    <input type="number" class="eui-input avl-of" placeholder="Número da ordem de faturamento">
                </div>
                <div class="avl-detail-actions">
                    <button type="button" class="eui-btn eui-btn--subtle" data-action="vincular-of"><i class="fas fa-link"></i> Vincular OF</button>
                </div>`;
        }

        function renderDetalhe(o) {
            const steps = renderSteps(o);
            const emSeparacao = o.status_slug === "em_separacao";
            const itensBloco = `<div class="avl-section-title"><i class="fas fa-boxes-stacked"></i> Materiais</div>${renderItens(o, emSeparacao)}`;
            const vendaInfo = o.venda_posterior
                ? `<div class="eui-callout eui-callout--warning" style="margin-top:12px;"><i class="fas fa-triangle-exclamation"></i> Solicitante indicou venda posterior — acompanhar orçamento manualmente.</div>`
                : "";
            const parceiroBox = (o.nf_parceiro_nome || o.nf_parceiro_endereco)
                ? `<div class="avl-nf-info">
                    ${o.nf_parceiro_nome ? `<div><span class="k">Destinatário (NF)</span><span class="v">${escapeHtml(o.nf_parceiro_nome)}</span></div>` : ""}
                    ${o.nf_parceiro_endereco ? `<div><span class="k">Endereço</span><span class="v">${escapeHtml(o.nf_parceiro_endereco)}</span></div>` : ""}
                   </div>` : "";
            const nfEmitidaLinha = o.numero_nf
                ? `<div class="eui-caption" style="margin-top:12px;"><i class="fas fa-file-invoice"></i> NF ${escapeHtml(o.numero_nf)} emitida por ${escapeHtml(o.faturado_por || "—")} em ${fmtDataHora(o.faturado_at)}</div>`
                : "";

            if (emSeparacao) {
                return `${steps}${vendaInfo}${itensBloco}
                    <textarea class="avl-obs" placeholder="Observações da separação (opcional)"></textarea>
                    <div class="avl-detail-actions">
                        <button type="button" class="eui-btn eui-btn--primary" data-action="separar"><i class="fas fa-check"></i> Confirmar separação</button>
                    </div>
                    ${renderAcoesAdmin(o)}`;
            }
            if (o.status_slug === "expedido_sem_nf" || o.status_slug === "aguardando_faturamento") {
                const dica = o.tipo_operacao === "Remessa para Conserto"
                    ? `<p class="eui-caption" style="margin-top:8px;"><i class="fas fa-circle-info"></i> Ao informar a NF, o destinatário e o endereço serão puxados automaticamente da nota.</p>`
                    : "";
                return `${steps}${vendaInfo}${itensBloco}
                    ${renderOfBox(o)}
                    <div class="avl-section-title"><i class="fas fa-file-invoice-dollar"></i> Faturamento manual</div>
                    <div class="avl-nf-row">
                        <input type="text" class="eui-input avl-nf" placeholder="Número da NF">
                        <textarea class="avl-obs" placeholder="Observações do faturamento (opcional)" style="margin-top:0;"></textarea>
                    </div>${dica}
                    <div class="avl-detail-actions">
                        <button type="button" class="eui-btn eui-btn--primary" data-action="faturar"><i class="fas fa-check"></i> Confirmar faturamento</button>
                    </div>
                    ${renderAcoesAdmin(o)}`;
            }
            if (o.status_slug === "estoque_terceiros" || o.status_slug === "estoque_assistencia") {
                return `${steps}${vendaInfo}${nfEmitidaLinha}${parceiroBox}${itensBloco}
                    <div class="avl-section-title"><i class="fas fa-rotate-left"></i> Retorno do material</div>
                    <div class="avl-nf-row">
                        <input type="text" class="eui-input avl-nf-retorno" placeholder="Número da NF de retorno">
                        <textarea class="avl-obs" placeholder="Observações do retorno (opcional)" style="margin-top:0;"></textarea>
                    </div>
                    <div class="avl-detail-actions">
                        <button type="button" class="eui-btn eui-btn--primary" data-action="retorno"><i class="fas fa-rotate-left"></i> Registrar retorno</button>
                    </div>
                    ${renderAcoesAdmin(o)}`;
            }
            // nf_emitida | estoque_retornado — somente leitura
            let extra = "";
            if (o.status_slug === "estoque_retornado") {
                extra = `<div class="eui-caption" style="margin-top:8px;"><i class="fas fa-rotate-left"></i> Retorno: NF ${escapeHtml(o.numero_nf_retorno || "—")} registrado por ${escapeHtml(o.retorno_por || "—")} em ${fmtDataHora(o.retorno_at)}</div>`;
            }
            return `${steps}${vendaInfo}${nfEmitidaLinha}${parceiroBox}${extra}${itensBloco}
                ${renderAcoesAdmin(o)}`;
        }

        function renderCard(o) {
            const acc = ACCENT[o.status_slug] || "";
            const nfMeta = o.numero_nf ? `<span><i class="fas fa-file-invoice"></i>NF ${escapeHtml(o.numero_nf)}</span>` : "";
            return `
                <article class="avl-card ${acc}" data-id="${o.id}">
                    <div class="avl-card__head" data-action="toggle-detail">
                        <div class="avl-card__accent"></div>
                        <div class="avl-card__id">
                            <div class="avl-card__title">
                                <b>${escapeHtml(o.protocolo)}</b><span class="sep">·</span>
                                <span class="avl-cliente">${escapeHtml(o.cliente_nome)}</span>
                                <span class="avl-chip-tipo">${escapeHtml(o.tipo_operacao)}</span>
                                <span class="eui-badge ${o.status_badge}"><span class="eui-badge__dot"></span>${escapeHtml(o.status)}</span>
                            </div>
                            <div class="avl-card__meta">
                                <span><i class="far fa-user"></i>${escapeHtml(o.solicitante_nome)}</span>
                                <span><i class="far fa-clock"></i>${fmtDataHora(o.created_at)}</span>
                                <span><i class="fas fa-boxes"></i>${o.itens.length} ${o.itens.length === 1 ? "item" : "itens"}</span>
                                ${o.ordem_faturamento ? `<span><i class="fas fa-link"></i>OF #${o.ordem_faturamento}</span>` : ""}
                                ${nfMeta}
                            </div>
                        </div>
                        <button type="button" class="avl-card__toggle" title="Detalhes"><i class="fas fa-chevron-down"></i></button>
                    </div>
                    <div class="avl-card__detail">${renderDetalhe(o)}</div>
                </article>`;
        }

        function renderLista() {
            const container = $("avulso-lista");
            const filtradas = ordensFiltradas();
            if (!filtradas.length) {
                container.innerHTML = `<div class="eui-empty"><i class="fas fa-inbox"></i><strong>Nenhuma solicitação encontrada.</strong></div>`;
                return;
            }
            container.innerHTML = filtradas.map(renderCard).join("");
        }

        async function carregarDashboard() {
            try {
                const data = await apiAvulso("/api/expedicao/conf-cega-avulso/ordens");
                ordens = data.ordens || [];
                renderMetrics(data.resumo);
                renderLista();
            } catch (e) {
                $("avulso-lista").innerHTML = `<div class="eui-empty"><i class="fas fa-triangle-exclamation"></i><strong>Falha ao carregar solicitações.</strong></div>`;
                toast(e.message, "error");
            }
        }

        function aplicarFiltro(filtro, elemento) {
            currentFilter = (currentFilter === filtro) ? null : filtro;
            $("avulso-metrics").querySelectorAll(".eui-stat").forEach((el) => el.classList.remove("is-active"));
            if (currentFilter && elemento) elemento.classList.add("is-active");
            $("avulso-btn-limpar-filtro").classList.toggle("hidden", !currentFilter);
            renderLista();
        }

        async function executarAcao(id, endpoint, body, sucessoMsg) {
            try {
                await apiAvulso(`/api/expedicao/conf-cega-avulso/ordens/${id}/${endpoint}`, {
                    method: "POST",
                    body: JSON.stringify(body),
                });
                toast(sucessoMsg);
                await carregarDashboard();
            } catch (e) {
                toast(e.message, "error");
            }
        }

        $("avulso-lista").addEventListener("click", (e) => {
            const toggle = e.target.closest('[data-action="toggle-detail"]');
            if (toggle) { toggle.closest(".avl-card").classList.toggle("is-open"); return; }

            const row = e.target.closest(".avl-card");
            if (!row) return;
            const id = row.dataset.id;

            if (e.target.closest('[data-action="separar"]')) {
                const itensSeparados = Array.from(row.querySelectorAll(".avl-chk-item:checked")).map((el) => parseInt(el.value, 10));
                const observacao = row.querySelector(".avl-obs").value;
                executarAcao(id, "separar", { itens_separados: itensSeparados, observacao }, "Solicitação separada com sucesso.");
                return;
            }
            if (e.target.closest('[data-action="faturar"]')) {
                const numeroNf = row.querySelector(".avl-nf").value;
                if (!numeroNf.trim()) { toast("Informe o número da NF.", "error"); return; }
                const observacao = row.querySelector(".avl-obs").value;
                executarAcao(id, "faturar", { numero_nf: numeroNf, observacao }, "Faturamento confirmado com sucesso.");
                return;
            }
            if (e.target.closest('[data-action="vincular-of"]')) {
                const codOf = row.querySelector(".avl-of").value;
                if (!String(codOf).trim()) { toast("Informe o número da ordem de faturamento.", "error"); return; }
                executarAcao(id, "vincular-of", { cod_ordem_fat: codOf }, "Ordem de faturamento vinculada.");
                return;
            }
            if (e.target.closest('[data-action="retorno"]')) {
                const numeroNfRetorno = row.querySelector(".avl-nf-retorno").value;
                if (!numeroNfRetorno.trim()) { toast("Informe o número da NF de retorno.", "error"); return; }
                const observacao = row.querySelector(".avl-obs").value;
                executarAcao(id, "retorno", { numero_nf_retorno: numeroNfRetorno, observacao }, "Retorno registrado com sucesso.");
                return;
            }
            if (e.target.closest('[data-action="estornar"]')) {
                pedirMotivo({
                    title: "Estornar etapa",
                    sub: "A solicitação volta para a etapa anterior, desfazendo os dados registrados nesta etapa.",
                    okLabel: "Estornar",
                    placeholder: "Motivo do estorno…",
                }).then((motivo) => {
                    if (motivo === null) return;
                    executarAcao(id, "estornar", { motivo }, "Etapa estornada com sucesso.");
                });
                return;
            }
            if (e.target.closest('[data-action="excluir"]')) {
                pedirMotivo({
                    title: "Excluir solicitação",
                    sub: "Essa ação remove a solicitação permanentemente e não pode ser desfeita.",
                    okLabel: "Excluir definitivamente",
                    placeholder: "Motivo da exclusão…",
                }).then((motivo) => {
                    if (motivo === null) return;
                    excluirOrdemAvulso(id, motivo);
                });
                return;
            }
        });

        async function excluirOrdemAvulso(id, motivo) {
            try {
                await apiAvulso(`/api/expedicao/conf-cega-avulso/ordens/${id}`, {
                    method: "DELETE",
                    body: JSON.stringify({ motivo }),
                });
                toast("Solicitação excluída.");
                await carregarDashboard();
            } catch (e) {
                toast(e.message, "error");
            }
        }

        $("avulso-metrics").querySelectorAll(".eui-stat.is-clickable").forEach((elemento) => {
            elemento.addEventListener("click", () => aplicarFiltro(elemento.dataset.filter, elemento));
        });
        $("avulso-btn-limpar-filtro").addEventListener("click", () => aplicarFiltro(currentFilter, null));
        $("avulso-btn-atualizar").addEventListener("click", carregarDashboard);

        let buscaTimer = null;
        $("avulso-busca").addEventListener("input", (e) => {
            clearTimeout(buscaTimer);
            buscaTimer = setTimeout(() => { busca = e.target.value; renderLista(); }, 250);
        });

        return { carregarDashboard, atualizarSePossivel: carregarDashboard };
    }
    const ctrl = criarControladorAvulso();
    ctrl.carregarDashboard();

    // Atualizacao automatica, como na tela de origem.
    const AUTO_REFRESH_MS = 4 * 60 * 1000;
    setInterval(() => { ctrl.atualizarSePossivel(); }, AUTO_REFRESH_MS);
    document.addEventListener("visibilitychange", () => {
        if (!document.hidden) ctrl.atualizarSePossivel();
    });
})();
