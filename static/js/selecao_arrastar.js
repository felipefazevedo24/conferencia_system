/* Selecao por arrasto nas listagens com caixinha: clica numa caixinha e,
 * segurando o botao, passa por cima das outras linhas - todas ficam no mesmo
 * estado da primeira (marca ou desmarca). Clique simples continua igual.
 *
 * Feito pra <tbody> montado por JS (Inventario): os eventos ficam no proprio
 * <tbody> e a caixinha e' achada pela linha na hora, entao sobrevive a tela
 * re-renderizar a lista no meio do arrasto. Cada caixinha marcada dispara o
 * "change" normal, e a tela trata como se fosse um clique.
 */
(function (global) {
    "use strict";

    const SELETOR = 'input[type="checkbox"]';

    function caixaDaLinha(alvo) {
        const linha = alvo && alvo.closest ? alvo.closest("tr") : null;
        const caixa = linha ? linha.querySelector(SELETOR) : null;
        return caixa && !caixa.disabled ? caixa : null;
    }

    function ativar(tbody) {
        if (!tbody || tbody.dataset.selecaoArrastar) return;
        tbody.dataset.selecaoArrastar = "1";
        let valor = null;          // estado que o arrasto aplica; null = sem arrasto
        let vistas = new WeakSet(); // cada caixinha uma vez por arrasto (a tela pode recusar)
        let engolirClique = false;

        function aplicar(caixa) {
            if (vistas.has(caixa)) return;
            vistas.add(caixa);
            if (caixa.checked === valor) return;
            caixa.checked = valor;
            caixa.dispatchEvent(new Event("change", { bubbles: true }));
        }

        tbody.addEventListener("mousedown", (e) => {
            if (e.button !== 0 || !e.target.matches(SELETOR) || e.target.disabled) return;
            e.preventDefault(); // sem isso o arrasto seleciona o texto da tabela
            valor = !e.target.checked;
            vistas = new WeakSet();
            engolirClique = true;
            aplicar(e.target);
        });

        tbody.addEventListener("mouseover", (e) => {
            if (valor === null) return;
            const caixa = caixaDaLinha(e.target);
            if (caixa) aplicar(caixa);
        });

        // A primeira caixinha ja mudou no mousedown: o clique nativo que vem
        // no mouseup desfaria.
        tbody.addEventListener("click", (e) => {
            if (engolirClique && e.target.matches && e.target.matches(SELETOR)) e.preventDefault();
        }, true);

        document.addEventListener("mouseup", () => {
            valor = null;
            setTimeout(() => { engolirClique = false; }, 0);
        });
    }

    global.SelecaoArrastar = { ativar };
})(window);
