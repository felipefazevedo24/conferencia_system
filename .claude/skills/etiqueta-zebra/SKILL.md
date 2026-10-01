---
name: etiqueta-zebra
description: Padrao obrigatorio para QUALQUER etiqueta impressa pelo Sync (expedicao, recebimento, inventario, WMS, producao...). Use ao criar uma etiqueta nova, mudar layout/tamanho de uma existente, ou quando o usuario reclamar de etiqueta saindo pequena, cortada ou desconfigurada na Zebra. Gera ZPL com o tamanho embutido (^PW/^LL), imprime direto na Zebra pelo Zebra Browser Print e mantem o PDF so como alternativa.
---

# Etiqueta Zebra (ZPL) - padrao do Sync

Toda etiqueta do Sync, atual ou futura, sai em **ZPL direto na Zebra**, com o
tamanho dentro da propria etiqueta (`^PW` largura / `^LL` comprimento). Foi
decidido assim (01/10/2026) porque o caminho PDF -> dialogo do navegador
depende do papel configurado no driver: a etiqueta saia reduzida e uma mesma
Zebra nao imprimia dois tamanhos sem trocar configuracao. O ERP faz igual.

## Como funciona hoje (use como molde)

| Peca | Arquivo |
|---|---|
| Medidas e fontes de cada modelo (`Modelo`: largura, faixas em mm, fontes em pt) | `conferencia_app/services/expedicao_etiqueta_pdf.py` (`RED_MOLDS`, `COLUMBIA`) |
| Gerador ZPL (mm/pt -> pontos pela resolucao, `^GFA` dos pictogramas, `^CI28`) | `conferencia_app/services/expedicao_etiqueta_zpl.py` |
| Gerador PDF (alternativa, mesmo `Modelo`) | `expedicao_etiqueta_pdf.gerar_etiqueta_pdf` |
| Rotas `.zpl` e `.pdf` + coleta dos dados (`_dados_etiqueta`) | `conferencia_app/routes/expedicao_fat_routes.py` |
| Envio pra Zebra no navegador (Browser Print, dpi, fallback PDF) | `static/js/zebra_print.js` -> `ZebraPrint.imprimir({zplUrl, pdfUrl})` |
| Botao na tela | `templates/expedicao_conf_cega.html` (`imprimirEtiqueta`) |

## Checklist pra uma etiqueta nova

1. **Pergunte antes de desenhar** (o usuario manda imagem com as faixas):
   largura x altura do rolo em mm, altura de cada faixa, o que vai em cada
   uma, se o topo tem logo **pre-impresso** (fica em branco), quais campos,
   de onde vem cada dado, em que tela/menu aparece o botao.
2. **Modelo**: declare um `Modelo` novo (ou um equivalente pro layout, se as
   faixas forem diferentes) - medidas em **mm**, fontes em **pt**. Nunca
   coordenada solta em pontos: o ZPL converte pela resolucao da impressora.
3. **ZPL** (regras que ja' quebraram etiqueta se esquecidas):
   - Cada etiqueta/volume e' um `^XA ... ^XZ` com `^CI28` (UTF-8: Ç, Ã, É),
     `^PW<largura em pontos>`, `^LL<altura em pontos>`, `^LH0,0`.
   - Pontos = mm x dpi / 25,4. A resolucao vem da tela (`?dpi=`): 203 ou 300
     (aceitos: 152/203/300/600; padrao 203).
   - Texto com `^A0N` (fonte escalavel). Quebra de linha automatica com
     `^FB largura,linhas,espaco,alinhamento`; quebra forcada com `\&`.
   - **Limpe `^` e `~` dos dados** (sao comandos ZPL) - ver `_limpar`.
   - A fonte 0 e' mais estreita que a Helvetica: medida com `stringWidth`
     vai x `FATOR_FONTE_0` (0,80).
   - Imagem/pictograma: PNG -> `^GFA` 1 bit (ver `_grafico`), com cache.
4. **Rotas**: `.../etiqueta-<modelo>.zpl` (texto, `?dpi=`) e
   `.../etiqueta-<modelo>.pdf` (alternativa, com
   `setViewerPreference("PrintScaling", "None")`). Os dois usam a MESMA
   funcao de coleta de dados. Rota com `@permission_required` do modulo.
5. **Tela**: carregue `static/js/zebra_print.js` e chame
   `ZebraPrint.imprimir({ zplUrl, pdfUrl })`; mostre o resultado
   (`r.via === "zebra"` imprimiu; `"pdf"` abriu o PDF com `r.motivo`).
6. **Dados de integracao** (NF, ERP, bridge) nunca derrubam a etiqueta: se a
   consulta falhar, sai com o campo vazio/"—" (ver `dados_da_nf`).

## Verificacao (antes de entregar)

- Teste com `test_client()`: `^PW`/`^LL` certos pra 203 **e** 300 dpi, um
  `^XA` por volume, `^CI28`, dados presentes, `^`/`~` dos dados removidos.
- **Veja a etiqueta renderizada**: o servico publico Labelary desenha ZPL
  (`POST http://api.labelary.com/v1/printers/8dpmm/labels/<larg_pol>x<alt_pol>/0/`
  com `Accept: image/png`; 12dpmm = 300 dpi). **Use so' dado ficticio** -
  e' um servico externo.
- Os testes novos ficam so' locais (`.git/info/exclude`) - regra do projeto.

## Instalacao no computador que imprime (passar pro usuario)

1. Instalar o **Zebra Browser Print** (gratuito, site da Zebra) no
   computador onde a Zebra esta' ligada (USB ou rede).
2. Abrir o Browser Print, conferir a Zebra como **impressora padrao**.
3. Na primeira impressao pelo Sync o Browser Print pergunta se aceita o site
   `sync.columbiamachine.com.br` -> **Aceitar** (uma vez).
4. Se a pagina disser que nao achou o Browser Print com ele aberto: abrir
   `https://localhost:9101/ssl_support` no navegador e aceitar o certificado.
5. Trocou o rolo de tamanho? Calibrar a Zebra (segurar **Feed** ate piscar 2
   vezes) - o ZPL manda o tamanho, mas a impressora precisa medir o gap.

Sem o Browser Print a tela abre o PDF (mesma etiqueta) - funciona, mas volta
a depender do papel do driver. O objetivo e' todo computador de impressao ter
o Browser Print.
