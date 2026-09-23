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

    const fmtData = (v) => {
        if (!v) return "—";
        const d = new Date(v);
        return isNaN(d.getTime()) ? escapeHtml(String(v).slice(0, 10)) : d.toLocaleDateString("pt-BR");
    };

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
        // Ids das solicitacoes abertas na tela.
        const abertos = new Set();

        async function apiAvulso(url, opts) {
            const resp = await fetch(url, Object.assign({ headers: { "Content-Type": "application/json" } }, opts || {}));
            let data = {};
            try { data = await resp.json(); } catch (e) {}
            if (!resp.ok || !data.sucesso) throw new Error(data.erro || ("Erro " + resp.status));
            return data;
        }

        // Os grupos saem dos ITENS, nao do status do cabecalho: agora que cada
        // item anda no seu ritmo, a pergunta util e' "o que ainda falta fazer
        // nesta solicitacao?", e a mesma pode aparecer em mais de um grupo.
        function itensDe(o) {
            return (o.itens && o.itens.length) ? o.itens : [{ status_slug: o.status_slug }];
        }

        const GRUPOS = {
            em_separacao: (o) => itensDe(o).some((i) => i.status_slug === "em_separacao"),
            a_faturar: (o) => itensDe(o).some((i) => i.pode_faturar
                || i.status_slug === "expedido_sem_nf" || i.status_slug === "aguardando_faturamento"),
            pendencia_estoque: (o) => itensDe(o).some((i) => i.status_slug === "estoque_terceiros"
                || i.status_slug === "estoque_assistencia"),
            concluidas: (o) => itensDe(o).every((i) => i.status_slug === "nf_emitida"
                || i.status_slug === "estoque_retornado"),
        };

        function renderMetrics() {
            Object.keys(GRUPOS).forEach((grupo) => {
                const el = $("avulso-m-" + grupo);
                if (el) el.textContent = ordens.filter(GRUPOS[grupo]).length;
            });
            const total = $("at-total"); if (total) total.textContent = ordens.length;
        }

        function ordensFiltradas() {
            let lista = ordens;
            if (currentFilter && GRUPOS[currentFilter]) {
                lista = lista.filter(GRUPOS[currentFilter]);
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

        // Os tipos que a solicitacao tem DE VERDADE, olhando os itens: o campo
        // do cabecalho guarda so' o do primeiro e mente quando eles diferem.
        function tiposDe(o) {
            const tipos = [...new Set((o.itens || []).map((i) => i.tipo_operacao).filter(Boolean))];
            return tipos.length ? tipos : [o.tipo_operacao].filter(Boolean);
        }

        function chipTipo(o) {
            const tipos = tiposDe(o);
            if (tipos.length <= 1) {
                return `<span class="avl-chip-tipo">${escapeHtml(tipos[0] || "—")}</span>`;
            }
            return `<span class="avl-chip-tipo is-misto" title="${escapeHtml(tipos.join(" · "))}">`
                + `${tipos.length} operações</span>`;
        }

        function fluxoSlugs(o) {
            const tipos = tiposDe(o);
            const slugs = (o.itens || []).map((i) => i.status_slug);
            const aguarda = slugs.includes("aguardando_faturamento")
                || tipos.some((t) => TIPOS_AGUARDA.includes(t));
            const comRetorno = slugs.some((sl) =>
                    ["estoque_terceiros", "estoque_assistencia", "estoque_retornado"].includes(sl))
                || (o.itens || []).some((i) => i.necessita_retorno)
                || tipos.some((t) => TIPOS_RETORNO.includes(t));
            const seg2 = aguarda ? "aguardando_faturamento" : "expedido_sem_nf";
            if (!comRetorno) return ["em_separacao", seg2, "nf_emitida"];
            const finalStock = slugs.includes("estoque_assistencia")
                || tipos.includes("Materiais para atendimento técnico no cliente")
                ? "estoque_assistencia" : "estoque_terceiros";
            return ["em_separacao", seg2, finalStock, "estoque_retornado"];
        }

        function renderSteps(o) {
            const slugs = fluxoSlugs(o);
            // A etapa atual e' a do item MENOS adiantado: e' o que ainda falta
            // fazer. Antes vinha do status do cabecalho, que com itens em
            // etapas diferentes nao casa com nenhuma e voltava para a primeira.
            const posicoes = (o.itens || []).map((i) => slugs.indexOf(i.status_slug))
                .filter((n) => n >= 0);
            let cur = posicoes.length ? Math.min(...posicoes) : slugs.indexOf(o.status_slug);
            if (cur < 0) cur = 0;
            return `<div class="avl-steps">` + slugs.map((sl, i) => {
                const cls = i < cur ? "is-done" : (i === cur ? "is-current" : "");
                const bar = i > 0 ? `<span class="avl-step__bar ${i <= cur ? "is-done" : ""}"></span>` : "";
                const dot = i < cur ? '<i class="fas fa-check"></i>' : (i + 1);
                return `${bar}<div class="avl-step ${cls}"><span class="avl-step__dot">${dot}</span>${STEP_LABEL[sl] || sl}</div>`;
            }).join("") + `</div>`;
        }

        // Tipos de operacao, para o seletor por item. Carregado uma vez.
        let TIPOS = [];

        function renderItens(o, comCheckbox) {
            const rows = o.itens.map((it) => {
                const local = it.material_local
                    ? `<span class="avl-local"><i class="fas fa-location-dot"></i>${escapeHtml(it.material_local)}</span>`
                    : `<span class="avl-local is-empty"><i class="fas fa-location-dot"></i>sem local</span>`;
                const naoSep = (o.status_slug !== "em_separacao") && !it.separado;
                let chk = "";
                if (comCheckbox === "separar") {
                    chk = `<td><input type="checkbox" class="avl-chk-item" value="${it.id}" checked></td>`;
                } else if (comCheckbox === "faturar") {
                    // Itens de operacoes diferentes nao entram na mesma nota:
                    // vem marcado so' o grupo da primeira operacao faturavel.
                    chk = it.pode_faturar
                        ? `<td><input type="checkbox" class="avl-chk-fat" value="${it.id}" data-op="${escapeHtml(it.tipo_operacao || "")}" checked></td>`
                        : '<td></td>';
                }
                // A operacao so' pode mudar enquanto o item nao tem nota: depois
                // dela o tipo ja' definiu como a nota saiu.
                const travado = !!it.numero_nf;
                const opcoes = TIPOS.map((t) =>
                    `<option value="${escapeHtml(t)}" ${t === it.tipo_operacao ? "selected" : ""}>${escapeHtml(t)}</option>`).join("");
                const operacao = travado
                    ? `<span class="avl-op-fixa">${escapeHtml(it.tipo_operacao || "—")}</span>`
                    : `<select class="avl-op" data-item="${it.id}" title="Trocar a operação deste item">${opcoes}</select>`;
                const volta = travado
                    ? (it.necessita_retorno ? "Sim" : "Não")
                    : `<select class="avl-ret" data-item="${it.id}" title="Este material volta?">
                         <option value="sim" ${it.necessita_retorno ? "selected" : ""}>Sim</option>
                         <option value="nao" ${it.necessita_retorno ? "" : "selected"}>Não</option>
                       </select>`;
                const devolvido = Number(it.quantidade_retornada || 0);
                const atraso = Number(it.dias_de_atraso || 0);
                const prazo = it.data_prevista_retorno
                    ? (atraso
                        ? `<span class="avl-atraso">atrasado ${atraso} dia${atraso > 1 ? "s" : ""}</span>`
                        : `<span class="avl-prazo-ok">volta até ${fmtData(it.data_prevista_retorno)}</span>`)
                    : (it.aguardando_retorno ? '<span class="avl-prazo-ok is-vazio">sem prazo</span>' : "");
                const nf = it.numero_nf
                    ? `<span class="avl-item-nf">NF ${escapeHtml(it.numero_nf)}</span>`
                    : '<span class="avl-item-nf is-empty">sem nota</span>';
                return `<tr class="${naoSep ? "is-nao-separado" : ""} ${atraso ? "is-atrasado" : ""}">
                    ${chk}
                    <td class="avl-td-cod">${escapeHtml(it.material_codigo)}</td>
                    <td>${escapeHtml(it.material_nome || "")}${naoSep ? ' <span style="color:var(--eui-danger);font-size:11px;">(não separado)</span>' : ""}</td>
                    <td>${local}</td>
                    <td class="avl-td-op">${operacao}</td>
                    <td class="avl-td-volta">${volta}</td>
                    <td class="avl-td-nf">${nf}${prazo ? `<br>${prazo}` : ""}</td>
                    <td class="avl-td-qtde">${it.quantidade}${devolvido ? `<small> (voltou ${devolvido})</small>` : ""}</td>
                </tr>`;
            }).join("");
            const thChk = comCheckbox ? '<th style="width:34px;"></th>' : "";
            void thChk;
            return `<table class="avl-table"><thead><tr>${thChk}<th>Código</th><th>Descrição</th>`
                + `<th>Local</th><th>Operação</th><th>Volta?</th><th>Nota</th>`
                + `<th style="text-align:right;">Qtde</th></tr></thead><tbody>${rows}</tbody></table>`;
        }

        async function salvarItem(solicitacaoId, itemId, campos) {
            try {
                await apiAvulso(`/api/expedicao/conf-cega-avulso/ordens/${solicitacaoId}/itens/${itemId}`,
                                { method: "PATCH", body: JSON.stringify(campos) });
                toast("Item atualizado.", "ok");
                await carregarDashboard();
            } catch (e) {
                toast(e.message, "err");
                await carregarDashboard();
            }
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

        // O que o solicitante escreveu e anexou. Em garantia, e' quase sempre
        // aqui que esta a explicacao do caso.
        function renderPedidoDoSolicitante(o) {
            const anexos = (o.anexos || []).map((a) =>
                `<a class="avl-anexo" href="/api/expedicao/conf-cega-avulso/anexos/${a.id}"
                    target="_blank" rel="noopener" title="${escapeHtml(a.nome)}">
                   <i class="fas fa-paperclip"></i>${escapeHtml(a.nome)}
                   <small>${(a.tamanho / 1024).toFixed(0)} KB</small></a>`).join("");
            if (!o.observacoes && !anexos) return "";
            return `<div class="avl-section-title"><i class="fas fa-comment"></i> Do solicitante</div>
                ${o.observacoes ? `<p class="avl-obs-solicitante">${escapeHtml(o.observacoes)}</p>` : ""}
                ${anexos ? `<div class="avl-anexos">${anexos}</div>` : ""}`;
        }

        function renderDetalhe(o) {
            const steps = renderSteps(o);
            const doSolicitante = renderPedidoDoSolicitante(o);
            const emSeparacao = o.status_slug === "em_separacao";
            const itensBloco = `<div class="avl-section-title"><i class="fas fa-boxes-stacked"></i> Materiais</div>`
                + renderItens(o, emSeparacao ? "separar" : null);
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
                return `${steps}${doSolicitante}${vendaInfo}${itensBloco}
                    <textarea class="avl-obs" placeholder="Observações da separação (opcional)"></textarea>
                    <div class="avl-detail-actions">
                        <button type="button" class="eui-btn eui-btn--primary" data-action="separar"><i class="fas fa-check"></i> Confirmar separação</button>
                    </div>
                    ${renderAcoesAdmin(o)}`;
            }
            if (itensDe(o).some((i) => i.pode_faturar)) {
                const dica = o.tipo_operacao === "Remessa para Conserto"
                    ? `<p class="eui-caption" style="margin-top:8px;"><i class="fas fa-circle-info"></i> Ao informar a NF, o destinatário e o endereço serão puxados automaticamente da nota.</p>`
                    : "";
                const operacoes = [...new Set(o.itens.filter((i) => i.pode_faturar)
                    .map((i) => i.tipo_operacao))];
                // Prazo de retorno: quem informa e' o Fiscal, junto com a nota, e
                // so' aparece se algum item do grupo realmente volta.
                const temRetorno = o.itens.some((i) => i.pode_faturar && i.necessita_retorno);
                const prazoBox = temRetorno
                    ? `<label class="avl-prazo">Prazo de retorno
                         <input type="date" class="eui-input avl-prazo-input">
                         <small>Vale para os itens marcados que voltam. Depois dele, o item aparece como atrasado.</small>
                       </label>`
                    : "";
                const avisoGrupo = operacoes.length > 1
                    ? `<p class="eui-caption avl-aviso-grupo"><i class="fas fa-circle-info"></i>
                        Esta solicitação tem ${operacoes.length} operações diferentes, que não cabem
                        na mesma nota. Marque os itens de uma delas, informe a NF, e depois repita
                        para as outras.</p>`
                    : "";
                return `${steps}${doSolicitante}${vendaInfo}
                    <div class="avl-section-title"><i class="fas fa-boxes-stacked"></i> Materiais</div>
                    ${renderItens(o, "faturar")}
                    ${renderOfBox(o)}
                    <div class="avl-section-title"><i class="fas fa-file-invoice-dollar"></i> Faturamento manual</div>
                    ${avisoGrupo}
                    <div class="avl-nf-row">
                        <input type="text" class="eui-input avl-nf" placeholder="Número da NF">
                        <textarea class="avl-obs" placeholder="Observações do faturamento (opcional)" style="margin-top:0;"></textarea>
                    </div>${prazoBox}${dica}
                    <div class="avl-detail-actions">
                        <button type="button" class="eui-btn eui-btn--primary" data-action="faturar"><i class="fas fa-check"></i> Confirmar faturamento</button>
                    </div>
                    ${renderAcoesAdmin(o)}`;
            }
            if (itensDe(o).some((i) => i.aguardando_retorno)) {
                const aguardando = o.itens.filter((i) => i.aguardando_retorno);
                const linhas = aguardando.map((it) => {
                    const falta = Number(it.quantidade || 0) - Number(it.quantidade_retornada || 0);
                    return `<tr data-item="${it.id}">
                        <td class="avl-td-cod">${escapeHtml(it.material_codigo)}</td>
                        <td>${escapeHtml(it.material_nome || "")}</td>
                        <td class="avl-td-qtde">${falta}</td>
                        <td><input type="number" step="any" min="0" class="eui-input avl-qtd-ret"
                                   placeholder="quanto voltou" value="${falta}"></td>
                        <td><label class="avl-encerrar">
                              <input type="checkbox" class="avl-chk-encerrar"> encerrar
                            </label></td>
                    </tr>`;
                }).join("");
                return `${steps}${doSolicitante}${vendaInfo}${nfEmitidaLinha}${parceiroBox}${itensBloco}
                    <div class="avl-section-title"><i class="fas fa-rotate-left"></i> Retorno do material</div>
                    <table class="avl-table avl-table-retorno"><thead><tr>
                        <th>Código</th><th>Descrição</th><th style="text-align:right;">Falta voltar</th>
                        <th>Voltou agora</th><th>Encerrar</th>
                    </tr></thead><tbody>${linhas}</tbody></table>
                    <p class="eui-caption avl-aviso-grupo"><i class="fas fa-circle-info"></i>
                       Voltou menos do que saiu e está certo assim? Marque <b>encerrar</b>: o item
                       fecha sem pendência e a diferença fica registrada no histórico.</p>
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
            return `${steps}${doSolicitante}${vendaInfo}${nfEmitidaLinha}${parceiroBox}${extra}${itensBloco}
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
                                ${chipTipo(o)}
                                <span class="eui-badge ${o.status_badge}"><span class="eui-badge__dot"></span>${escapeHtml(o.status)}</span>
                            </div>
                            <div class="avl-card__meta">
                                <span><i class="far fa-user"></i>${escapeHtml(o.solicitante_nome)}</span>
                                <span><i class="far fa-clock"></i>${fmtDataHora(o.created_at)}</span>
                                <span><i class="fas fa-boxes"></i>${o.itens.length} ${o.itens.length === 1 ? "item" : "itens"}</span>
                                ${o.data_necessidade ? `<span><i class="fas fa-calendar-day"></i>Necessário em ${fmtData(o.data_necessidade)}</span>` : ""}
                                ${o.ordem_faturamento ? `<span><i class="fas fa-link"></i>OF #${o.ordem_faturamento}</span>` : ""}
                                ${o.romaneio_id ? `<span title="Gerado fora do horário comercial: a NF ainda não existia quando a solicitação foi criada."><i class="fas fa-triangle-exclamation"></i><a href="/expedicao/romaneio/${o.romaneio_id}/visualizar" target="_blank" rel="noopener" style="color:inherit;">Romaneio sem NF</a></span>` : ""}
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
            // Reabre o que estava aberto: o render troca o HTML inteiro, entao
            // sem isso a solicitacao fechava sozinha a cada atualizacao.
            abertos.forEach((id) => {
                const card = container.querySelector(`[data-id="${id}"]`);
                if (card) card.classList.add("is-open");
            });
        }

        async function carregarDashboard() {
            try {
                if (!TIPOS.length) {
                    const t = await apiAvulso("/api/expedicao/conf-cega-avulso/tipos-operacao");
                    TIPOS = (t.tipos || []).map((x) => x.nome);
                }
                const data = await apiAvulso("/api/expedicao/conf-cega-avulso/ordens");
                ordens = data.ordens || [];
                renderMetrics();
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

        // Trocar operacao/volta de um item salva na hora.
        $("avulso-lista").addEventListener("change", (e) => {
            const op = e.target.closest(".avl-op");
            const ret = e.target.closest(".avl-ret");
            const alvo = op || ret;
            if (!alvo) return;
            const card = alvo.closest("[data-id]");
            if (!card) return;
            salvarItem(card.dataset.id, alvo.dataset.item,
                op ? { tipo_operacao: alvo.value } : { necessita_retorno: alvo.value === "sim" });
        });

        $("avulso-lista").addEventListener("click", (e) => {
            const toggle = e.target.closest('[data-action="toggle-detail"]');
            if (toggle) {
                const card = toggle.closest(".avl-card");
                const aberto = card.classList.toggle("is-open");
                if (aberto) abertos.add(card.dataset.id); else abertos.delete(card.dataset.id);
                return;
            }

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
                const itens = Array.from(row.querySelectorAll(".avl-chk-fat:checked"))
                    .map((el) => parseInt(el.value, 10));
                if (!itens.length) { toast("Marque ao menos um item para esta nota.", "error"); return; }
                const prazoEl = row.querySelector(".avl-prazo-input");
                executarAcao(id, "faturar", {
                    numero_nf: numeroNf, observacao, itens,
                    data_prevista_retorno: prazoEl ? prazoEl.value : null,
                }, "Faturamento confirmado com sucesso.");
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
                const retornos = {};
                const encerrar = [];
                row.querySelectorAll(".avl-table-retorno tbody tr").forEach((tr) => {
                    const item = tr.dataset.item;
                    const qtd = parseFloat(tr.querySelector(".avl-qtd-ret").value || "0");
                    if (qtd > 0) retornos[item] = qtd;
                    if (tr.querySelector(".avl-chk-encerrar").checked) encerrar.push(item);
                });
                if (!Object.keys(retornos).length && !encerrar.length) {
                    toast("Informe quanto voltou, ou marque encerrar.", "error"); return;
                }
                executarAcao(id, "retorno", {
                    numero_nf_retorno: numeroNfRetorno, observacao, retornos, encerrar,
                }, "Retorno registrado com sucesso.");
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

        function atualizarSePossivel() {
            // Com uma solicitacao aberta, quem esta na tela esta no meio de
            // algo (conferindo item, digitando NF). Recarregar ali atrapalha,
            // e o botao Atualizar continua disponivel.
            if (abertos.size) return Promise.resolve();
            return carregarDashboard();
        }

        return { carregarDashboard, atualizarSePossivel };
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
