/* Impressao direta na Zebra pelo Zebra Browser Print (programa oficial e
 * gratuito da Zebra instalado no computador que imprime). E' o caminho
 * padrao de TODA etiqueta do Sync - ver .claude/skills/etiqueta-zebra.
 *
 * O servidor gera o ZPL com o tamanho da etiqueta embutido (^PW/^LL), entao
 * a mesma Zebra imprime qualquer tamanho sem configurar papel no Windows.
 * A resolucao (203/300 dpi) e' perguntada pra propria impressora e vai pro
 * servidor, pra etiqueta sair no tamanho certo em qualquer modelo.
 *
 * Sem o Browser Print (ou se a impressora nao responder), abre o PDF da
 * mesma etiqueta - a impressao nunca fica travada.
 *
 * Uso:
 *   ZebraPrint.imprimir({ zplUrl: "/api/.../etiqueta-x.zpl", pdfUrl: "/api/.../etiqueta-x.pdf" })
 *     .then((r) => r.via === "zebra" ? "impresso" : "abriu o PDF");
 */
(function (global) {
    "use strict";

    // O Browser Print escuta nessas portas locais (https primeiro: a pagina
    // do Sync e' https).
    const BASES = ["https://localhost:9101/", "http://localhost:9100/"];
    let cache = null;

    async function _req(url, opcoes, tempoMs) {
        const ctrl = new AbortController();
        const t = setTimeout(() => ctrl.abort(), tempoMs || 4000);
        try {
            // text/plain: e' o que o Browser Print espera e evita o preflight de CORS.
            const resp = await fetch(url, Object.assign({ signal: ctrl.signal, headers: { "Content-Type": "text/plain;charset=UTF-8" } }, opcoes || {}));
            if (!resp.ok) throw new Error("Browser Print respondeu " + resp.status);
            return await resp.text();
        } finally {
            clearTimeout(t);
        }
    }

    async function impressoraPadrao() {
        if (cache) return cache;
        for (const base of BASES) {
            try {
                const texto = await _req(base + "default?type=printer", { method: "GET" }, 2500);
                if (texto && texto.trim()) {
                    cache = { base, device: JSON.parse(texto) };
                    return cache;
                }
            } catch (e) { /* tenta a proxima porta */ }
        }
        return null;
    }

    async function _enviar(bp, dados) {
        await _req(bp.base + "write", { method: "POST", body: JSON.stringify({ device: bp.device, data: dados }) }, 15000);
    }

    async function resolucao(bp) {
        // Comando SGD da Zebra; a resposta vem como "203" ou "300".
        try {
            await _enviar(bp, '! U1 getvar "head.resolution.in_dpi"\r\n');
            for (let i = 0; i < 4; i++) {
                await new Promise((r) => setTimeout(r, 250));
                const texto = await _req(bp.base + "read", { method: "POST", body: JSON.stringify({ device: bp.device }) }, 2500);
                const dpi = parseInt(String(texto || "").replace(/\D/g, ""), 10);
                if ([152, 203, 300, 600].includes(dpi)) return dpi;
            }
        } catch (e) { /* cai no padrao */ }
        return 203;
    }

    // Abrir o PDF depois de esperar a deteccao pode cair no bloqueio de
    // pop-up (ja' nao e' mais "o clique"); bloqueado, abre na mesma aba.
    function _abrirPdf(url) {
        if (!url) return;
        const janela = global.open(url, "_blank");
        if (!janela) global.location.assign(url);
    }

    async function imprimir(opcoes) {
        const bp = await impressoraPadrao();
        if (!bp) {
            _abrirPdf(opcoes.pdfUrl);
            return { via: "pdf", motivo: "Zebra Browser Print não encontrado neste computador." };
        }
        const dpi = await resolucao(bp);
        const sep = opcoes.zplUrl.includes("?") ? "&" : "?";
        const resp = await fetch(`${opcoes.zplUrl}${sep}dpi=${dpi}`);
        if (!resp.ok) {
            let msg = "Falha ao gerar a etiqueta.";
            try { msg = (await resp.json()).error || msg; } catch (e) { /* texto */ }
            throw new Error(msg);
        }
        const zpl = await resp.text();
        try {
            await _enviar(bp, zpl);
        } catch (e) {
            cache = null;
            _abrirPdf(opcoes.pdfUrl);
            return { via: "pdf", motivo: "A Zebra não respondeu - abrindo o PDF." };
        }
        return { via: "zebra", impressora: bp.device && bp.device.name, dpi };
    }

    global.ZebraPrint = { imprimir, impressoraPadrao };
})(window);
