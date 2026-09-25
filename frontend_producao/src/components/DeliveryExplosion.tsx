import { useQueries, useQuery } from "@tanstack/react-query";
import { AlertTriangle, Box, CalendarDays, CheckCircle2, ChevronDown, ChevronRight, CircleDot, Clock3, Factory, PackageOpen, RefreshCw, X } from "lucide-react";
import { useMemo, useState } from "react";
import { api, type DeliveryStructureNode, type ScheduledDelivery } from "../lib/api";

export interface DeliveryExplosionRequest { month: number; year: number; classification: string; search: string; }
interface Props extends DeliveryExplosionRequest { onClose: () => void; onSelectOrder: (number: string, itemCode?: string) => void; }
const monthNames = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"];
const stateClass: Record<DeliveryStructureNode["estado"], string> = { bloqueado: "danger", concluido: "success", disponivel: "ready", montagem: "info", fabricacao: "warning", nao_iniciado: "neutral" };

function dateLabel(value: string | null) { if (!value) return "Sem data"; const [year, month, day] = value.slice(0, 10).split("-"); return `${day}/${month}/${year}`; }
function daysUntil(value: string | null) { if (!value) return null; const [year, month, day] = value.slice(0, 10).split("-").map(Number); const today = new Date(); return Math.round((Date.UTC(year, month - 1, day) - Date.UTC(today.getFullYear(), today.getMonth(), today.getDate())) / 86400000); }
function criticalReason(node: DeliveryStructureNode, dueDate: string | null) {
  if (node.estado === "bloqueado" || node.operacoes.some((operation) => operation.travada)) return "Processo bloqueado";
  if (node.estado === "concluido") return null;
  const days = daysUntil(dueDate);
  if (days !== null && days < 0) return `${Math.abs(days)} dia(s) em atraso`;
  if (days !== null && days <= 7 && node.estado === "nao_iniciado") return "Prazo próximo e produção não iniciada";
  if (days !== null && days <= 3) return "Prazo crítico";
  return null;
}
const budgetKey = (delivery: ScheduledDelivery) => `${delivery.orcamento}/${delivery.versao}`;

export function DeliveryExplosion({ month, year, classification, search, onClose, onSelectOrder }: Props) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const deliveries = useQuery({ queryKey: ["delivery-explosion", month, year, classification, search], queryFn: ({ signal }) => api.getScheduledDeliveries(month, year, classification, search, signal), staleTime: 20_000 });
  const orders = useMemo(() => (deliveries.data ?? []).flatMap((delivery) => delivery.os.map((order) => ({ order, delivery }))), [deliveries.data]);
  const structures = useQueries({ queries: orders.map(({ order }) => ({ queryKey: ["delivery-structure", order.numero], queryFn: ({ signal }: { signal: AbortSignal }) => api.getDeliveryStructure(order.numero, signal), staleTime: 20_000, retry: 1 })) });
  const byOrder = new Map(orders.map(({ order }, index) => [order.numero, structures[index]]));
  const nodes = structures.flatMap((query) => query.data?.nos ?? []);
  const pending = nodes.filter((node) => node.estado !== "concluido");
  const critical = orders.reduce((total, { delivery, order }) => total + (byOrder.get(order.numero)?.data?.nos ?? []).filter((node) => criticalReason(node, delivery.data_entrega)).length, 0);
  return <main className="delivery-explosion" aria-label="Explosão visual das entregas do mês">
    <header className="delivery-explosion-header"><div><span className="delivery-eyebrow"><PackageOpen size={15} /> Planejamento de entregas</span><h1>Explosão da Entrega do Mês</h1><p>{monthNames[month - 1]} de {year}{classification ? ` · Setor ${classification}` : " · Todos os setores"}</p></div><div className="delivery-header-actions"><button type="button" onClick={() => deliveries.refetch()}><RefreshCw size={17} /> Atualizar</button><button type="button" className="delivery-close" onClick={onClose} aria-label="Fechar explosão visual"><X size={19} /></button></div></header>
    <section className="delivery-kpis" aria-label="Resumo do período"><div><CalendarDays /><span><strong>{deliveries.data?.length ?? 0}</strong> orçamentos</span></div><div><Factory /><span><strong>{orders.length}</strong> ordens de serviço</span></div><div><Clock3 /><span><strong>{pending.length}</strong> componentes pendentes</span></div><div className={critical ? "critical" : ""}><AlertTriangle /><span><strong>{critical}</strong> pontos críticos</span></div></section>
    {deliveries.isLoading && <div className="delivery-explosion-message"><RefreshCw className="spin" /> Carregando entregas e estruturas...</div>}
    {deliveries.isError && <div className="delivery-explosion-message error"><AlertTriangle /> Não foi possível carregar o cronograma.</div>}
    {!deliveries.isLoading && !deliveries.isError && !deliveries.data?.length && <div className="delivery-explosion-message">Nenhuma entrega prevista para este período.</div>}
    {!!deliveries.data?.length && <div className="delivery-process-map"><div className="delivery-month-node"><PackageOpen /><span>ENTREGAS</span><strong>{monthNames[month - 1].toUpperCase()} {year}</strong></div><div className="delivery-trunk" /><div className="delivery-budget-grid">
      {deliveries.data.map((delivery) => { const key = budgetKey(delivery); const isCollapsed = collapsed.has(key); const budgetNodes = delivery.os.flatMap((order) => byOrder.get(order.numero)?.data?.nos ?? []); const budgetCritical = budgetNodes.filter((node) => criticalReason(node, delivery.data_entrega)).length; return <article className="delivery-budget-branch" key={key}>
        <button className="delivery-budget-node" type="button" onClick={() => setCollapsed((current) => { const next = new Set(current); if (next.has(key)) next.delete(key); else next.add(key); return next; })}><span className="delivery-budget-icon"><Box /></span><span><small>ORÇAMENTO</small><strong>{delivery.orcamento}{delivery.versao ? ` · ${delivery.versao}` : ""}</strong><em>{delivery.cliente || delivery.descricao || "Cliente não informado"}</em></span><span className="delivery-budget-meta"><b>{dateLabel(delivery.data_entrega)}</b>{budgetCritical > 0 && <i><AlertTriangle size={13} /> {budgetCritical}</i>}</span>{isCollapsed ? <ChevronRight /> : <ChevronDown />}</button>
        {!isCollapsed && <div className="delivery-orders-map">{delivery.os.length === 0 && <p className="delivery-empty-order">Orçamento sem OS vinculada.</p>}{delivery.os.map((order) => { const structure = byOrder.get(order.numero); return <section className="delivery-order-group" key={order.numero}><button type="button" className="delivery-order-label" onClick={() => onSelectOrder(order.numero)}>OS {order.numero}{order.principal && <span>Principal</span>}</button>{structure?.isLoading && <p className="delivery-order-loading">Carregando componentes...</p>}{structure?.isError && <p className="delivery-order-loading error">Estrutura indisponível</p>}<div className="delivery-component-grid">
          {(structure?.data?.nos ?? []).map((node) => { const remaining = node.operacoes.filter((operation) => !operation.finalizada); const reason = criticalReason(node, delivery.data_entrega); return <button type="button" className={`delivery-component-card${reason ? " is-critical" : ""}`} key={node.id} onClick={() => onSelectOrder(order.numero, node.codigo)}><span className="delivery-component-top"><span className="delivery-component-icon"><Box /></span><span className={`delivery-state ${stateClass[node.estado]}`}><CircleDot /> {node.estado_label}</span></span><strong>{node.descricao || "Componente"}</strong><span className="delivery-component-code">{node.codigo || `Item ${node.aux_code}`} · Qtde. {node.quantidade}</span><span className="delivery-pending-title">Processos pendentes</span><span className="delivery-pending-list">{remaining.length ? remaining.slice(0, 4).map((operation) => <span key={`${operation.codigo}-${operation.sequencia}`}><Clock3 /> {operation.nome || operation.codigo}{operation.maquina ? ` · ${operation.maquina}` : ""}</span>) : <span className="done"><CheckCircle2 /> Todos concluídos</span>}{remaining.length > 4 && <span>+ {remaining.length - 4} processo(s)</span>}</span>{reason && <span className="delivery-critical-reason"><AlertTriangle /> {reason}</span>}</button>; })}
        </div></section>; })}</div>}</article>; })}
    </div></div>}
    <footer className="delivery-source">Fonte: Cronograma de Entrega e estrutura produtiva · GRV</footer>
  </main>;
}
