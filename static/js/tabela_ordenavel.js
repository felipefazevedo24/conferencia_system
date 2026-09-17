/* Ordenacao por clique no cabecalho das listagens.
 *
 * Feito pra tabelas cujo <tbody> e' montado por JS a partir da API (caso do
 * Inventario): a ordem escolhida e' REAPLICADA sempre que o <tbody> e'
 * re-renderizado (filtro, busca, "Atualizar", acao no item) - sem isso a
 * ordenacao se perderia a cada recarga da lista.
 *
 * A chave de cada celula e' inferida do texto exibido, entendendo os formatos
 * que as telas usam: data "16/09/2026 17:09", numero pt-BR com sinal, R$ e
 * unidade ("8 UN", "+3", "R$ -1.250,00", "+R$ 37,50"). Codigo tipo
 * "19-01-00558" continua sendo texto. Celula vazia ("—") vai sempre pro fim,
 * nos dois sentidos. Se o texto exibido nao servir de chave, a celula pode
 * informar a sua com data-sort="...".
 *
 * Coluna fica de fora quando o <th> esta vazio, tem controle (checkbox) ou e'
 * a coluna "Ação" - ou quando marcado com data-nosort.
 */
(function (global) {
    "use strict";

    const ESTILO_ID = "tabela-ordenavel-estilo";
    const RE_DATA = /^(\d{2})\/(\d{2})\/(\d{4})(?:\s+(\d{2}):(\d{2})(?::(\d{2}))?)?/;
    // sinal? R$? sinal? numero (com milhar "1.250" ou simples) decimal? - e
    // nada colado depois que indique que ainda nao acabou (ex.: "19-01-00558").
    const RE_NUMERO = /^([+-])?\s*(?:R\$\s*)?([+-])?\s*(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d+))?(?![\d.,\/-])/;

    function injetarEstilo() {
        if (document.getElementById(ESTILO_ID)) return;
        const estilo = document.createElement("style");
        estilo.id = ESTILO_ID;
        estilo.textContent = [
            "th.to-ordenavel { cursor: pointer; user-select: none; white-space: nowrap; }",
            "th.to-ordenavel:hover, th.to-ordenavel:focus-visible { color: var(--eui-primary, #2563eb); outline: none; }",
            "th.to-ordenavel .to-seta { margin-left: 4px; font-size: 10px; opacity: .4; }",
            "th.to-ordenavel[aria-sort='ascending'] .to-seta, th.to-ordenavel[aria-sort='descending'] .to-seta { opacity: 1; color: var(--eui-primary, #2563eb); }",
        ].join("\n");
        document.head.appendChild(estilo);
    }

    function chaveDaCelula(td) {
        if (!td) return null;
        if (td.dataset && td.dataset.sort !== undefined) {
            const bruto = td.dataset.sort;
            if (bruto === "") return null;
            const numero = Number(bruto);
            return Number.isNaN(numero) ? bruto : numero;
        }
        const texto = (td.textContent || "").replace(/\s+/g, " ").trim();
        if (!texto || texto === "—" || texto === "-") return null;

        let m = texto.match(RE_DATA);
        if (m) {
            return Date.UTC(+m[3], +m[2] - 1, +m[1], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0));
        }
        m = texto.match(RE_NUMERO);
        if (m) {
            const valor = parseFloat(m[3].replace(/\./g, "") + (m[4] ? "." + m[4] : ""));
            return (m[1] === "-" || m[2] === "-") ? -valor : valor;
        }
        return texto;
    }

    function comparar(a, b) {
        if (typeof a === "number" && typeof b === "number") return a - b;
        return String(a).localeCompare(String(b), "pt-BR", { numeric: true, sensitivity: "base" });
    }

    function ativar(tabela) {
        if (!tabela || tabela.dataset.ordenavelAtivo) return;
        const thead = tabela.tHead;
        const tbody = tabela.tBodies[0];
        if (!thead || !thead.rows.length || !tbody) return;
        tabela.dataset.ordenavelAtivo = "1";
        injetarEstilo();

        const ths = Array.from(thead.rows[0].cells);
        const estado = { coluna: -1, direcao: 1 };
        let observador = null;

        function ordenar() {
            if (estado.coluna < 0) return;
            const linhas = Array.from(tbody.rows);
            // Linha de estado ("Carregando…", "Nenhum item") ou de detalhe usa
            // colspan - reordenar ai' so' embaralharia. Deixa quieto.
            if (!linhas.length || linhas.some((tr) => Array.from(tr.cells).some((td) => td.colSpan > 1))) return;

            const chaves = new Map(linhas.map((tr) => [tr, chaveDaCelula(tr.cells[estado.coluna])]));
            linhas.sort((x, y) => {
                const a = chaves.get(x);
                const b = chaves.get(y);
                if (a === null || b === null) return (a === null) - (b === null);  // vazio sempre no fim
                return comparar(a, b) * estado.direcao;
            });
            linhas.forEach((tr) => tbody.appendChild(tr));
            // As mutacoes geradas pela propria reordenacao nao podem disparar
            // outra ordenacao.
            if (observador) observador.takeRecords();
        }

        function atualizarIndicadores() {
            ths.forEach((th, i) => {
                if (!th.classList.contains("to-ordenavel")) return;
                const ativa = i === estado.coluna;
                th.setAttribute("aria-sort", ativa ? (estado.direcao === 1 ? "ascending" : "descending") : "none");
                th.querySelector(".to-seta").textContent = ativa ? (estado.direcao === 1 ? "▲" : "▼") : "↕";
            });
        }

        function alternar(i) {
            estado.direcao = estado.coluna === i ? -estado.direcao : 1;
            estado.coluna = i;
            atualizarIndicadores();
            ordenar();
        }

        ths.forEach((th, i) => {
            const rotulo = (th.textContent || "").trim();
            if (th.dataset.nosort !== undefined || !rotulo || th.querySelector("input, select, button")
                || /^a[çc][ãa]o$/i.test(rotulo)) {
                return;
            }
            th.classList.add("to-ordenavel");
            th.tabIndex = 0;
            th.title = "Clique para ordenar";
            const seta = document.createElement("span");
            seta.className = "to-seta";
            seta.setAttribute("aria-hidden", "true");
            th.appendChild(seta);
            th.addEventListener("click", () => alternar(i));
            th.addEventListener("keydown", (e) => {
                if (e.key === "Enter" || e.key === " ") { e.preventDefault(); alternar(i); }
            });
        });
        atualizarIndicadores();

        observador = new MutationObserver(ordenar);
        observador.observe(tbody, { childList: true });
    }

    global.TabelaOrdenavel = { ativar, chaveDaCelula };
})(window);
