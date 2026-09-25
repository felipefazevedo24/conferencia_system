import { useQueries, useQuery } from "@tanstack/react-query";
import { AlertTriangle, Box, CalendarDays, CheckCircle2, ChevronDown, ChevronRight, CircleDot, Clock3, PackageOpen, RefreshCw, X } from "lucide-react";
import { useMemo, useState } from "react";
import { api, type CpmActivity, type CpmBudget, type DeliveryStructureNode, type ScheduledDelivery } from "../lib/api";

export interface DeliveryExplosionRequest { month: number; year: number; classification: string; search: string; }
interface Props extends DeliveryExplosionRequest { onClose: () => void; onSelectOrder: (number: string, itemCode?: string) => void; }
const monthNames = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"];
const stateClass: Record<DeliveryStructureNode["estado"], string> = { bloqueado: "danger", concluido: "success", disponivel: "ready", montagem: "info", fabricacao: "warning", nao_iniciado: "neutral" };
const budgetKey = (delivery: ScheduledDelivery) => `${delivery.orcamento}/${delivery.versao}`;
const dateLabel = (value: string | null) => value ? value.slice(0, 10).split("-").reverse().join("/") : "Sem data";
const dateTimeLabel = (value?: string | null) => value ? new Intl.DateTimeFormat("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value)) : "—";
const minutesLabel = (value?: number | null) => value == null ? "—" : `${value < 0 ? "-" : ""}${Math.floor(Math.abs(value) / 60)}h${Math.abs(value) % 60 ? ` ${Math.abs(value) % 60}min` : ""}`;
const statusLabel = (status?: string) => ({ ATRASO_PROJETADO: "Atraso projetado", CRITICO: "Crítico", RISCO_ALTO: "Risco alto", ATENCAO: "Atenção", NORMAL: "No prazo", RISCO_NAO_CALCULAVEL: "Risco não calculável" }[status ?? ""] ?? status ?? "Calculando");
const componentCpm = (node: DeliveryStructureNode, cpm?: CpmBudget) => cpm?.activities.filter((activity) => activity.kind === "process" && activity.metadata.component_code === node.codigo) ?? [];

export function DeliveryExplosion({ month, year, classification, search, onClose, onSelectOrder }: Props) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [simulations, setSimulations] = useState<Record<string, { loading?: boolean; projected?: string; status?: string; error?: string }>>({});
  const deliveries = useQuery({ queryKey: ["delivery-explosion", month, year, classification, search], queryFn: ({ signal }) => api.getScheduledDeliveries(month, year, classification, search, signal), staleTime: 20_000 });
  const orders = useMemo(() => (deliveries.data ?? []).flatMap((delivery) => delivery.os.map((order) => ({ order, delivery }))), [deliveries.data]);
  const structures = useQueries({ queries: orders.map(({ order }) => ({ queryKey: ["delivery-structure", order.numero], queryFn: ({ signal }: { signal: AbortSignal }) => api.getDeliveryStructure(order.numero, signal), staleTime: 20_000, retry: 1 })) });
  const cpmQueries = useQueries({ queries: (deliveries.data ?? []).map((delivery) => ({ queryKey: ["delivery-cpm", delivery.orcamento], queryFn: ({ signal }: { signal: AbortSignal }) => api.getCpmBudget(delivery.orcamento, signal), staleTime: 20_000, retry: 1 })) });
  const byOrder = new Map(orders.map(({ order }, index) => [order.numero, structures[index]]));
  const cpmByBudget = new Map((deliveries.data ?? []).map((delivery, index) => [budgetKey(delivery), cpmQueries[index]]));
  const cpmResults = cpmQueries.flatMap((query) => query.data ? [query.data] : []);
  const totals = {
    onTime: cpmResults.filter((item) => item.summary.status === "NORMAL").length,
    attention: cpmResults.filter((item) => ["ATENCAO", "RISCO_ALTO"].includes(item.summary.status)).length,
    critical: cpmResults.filter((item) => item.summary.status === "CRITICO").length,
    delayed: cpmResults.filter((item) => item.summary.status === "ATRASO_PROJETADO").length,
    purchases: cpmResults.reduce((sum, item) => sum + item.summary.critical_purchase_count, 0)
  };

  async function runSimulation(key: string, budget: string, activity: CpmActivity) {
    setSimulations((current) => ({ ...current, [key]: { loading: true } }));
    try {
      const result = await api.simulateCpm(budget, activity.id, 480);
      setSimulations((current) => ({ ...current, [key]: { projected: result.projected_finish, status: result.status } }));
    } catch {
      setSimulations((current) => ({ ...current, [key]: { error: "Simulação indisponível" } }));
    }
  }

  return <main className="delivery-explosion" aria-label="Caminho crítico das entregas">
    <header className="delivery-explosion-header"><div><span className="delivery-eyebrow"><PackageOpen size={15} /> Planejamento CPM</span><h1>Caminho Crítico das Entregas</h1><p>{monthNames[month - 1]} de {year}{classification ? ` · Setor ${classification}` : " · Todos os setores"}</p></div><div className="delivery-header-actions"><button type="button" onClick={() => deliveries.refetch()}><RefreshCw size={17} /> Atualizar</button><button type="button" className="delivery-close" onClick={onClose} aria-label="Fechar"><X size={19} /></button></div></header>
    <section className="delivery-kpis" aria-label="Resumo CPM"><div><CalendarDays /><span><strong>{deliveries.data?.length ?? 0}</strong> orçamentos</span></div><div><CheckCircle2 /><span><strong>{totals.onTime}</strong> no prazo</span></div><div><Clock3 /><span><strong>{totals.attention}</strong> em atenção</span></div><div className={totals.critical ? "critical" : ""}><AlertTriangle /><span><strong>{totals.critical}</strong> críticos</span></div><div className={totals.delayed ? "critical" : ""}><AlertTriangle /><span><strong>{totals.delayed}</strong> atraso projetado</span></div><div className={totals.purchases ? "critical" : ""}><PackageOpen /><span><strong>{totals.purchases}</strong> compras críticas</span></div></section>
    {deliveries.isLoading && <div className="delivery-explosion-message"><RefreshCw className="spin" /> Carregando cronograma...</div>}
    {deliveries.isError && <div className="delivery-explosion-message error"><AlertTriangle /> Não foi possível carregar o cronograma.</div>}
    {!deliveries.isLoading && !deliveries.isError && !deliveries.data?.length && <div className="delivery-explosion-message">Nenhuma entrega prevista para este período.</div>}
    {!!deliveries.data?.length && <div className="delivery-process-map"><div className="delivery-month-node"><PackageOpen /><span>ENTREGAS</span><strong>{monthNames[month - 1].toUpperCase()} {year}</strong></div><div className="delivery-trunk" /><div className="delivery-budget-grid">
      {deliveries.data.map((delivery) => {
        const key = budgetKey(delivery); const isCollapsed = collapsed.has(key); const cpmQuery = cpmByBudget.get(key); const cpm = cpmQuery?.data; const simulation = simulations[key];
        const criticalActivities = cpm?.activities.filter((activity) => activity.kind === "process" && activity.is_critical && !activity.completed) ?? [];
        return <article className="delivery-budget-branch" key={key}>
          <button className={`delivery-budget-node cpm-${(cpm?.summary.status ?? "loading").toLowerCase()}`} type="button" onClick={() => setCollapsed((current) => { const next = new Set(current); if (next.has(key)) next.delete(key); else next.add(key); return next; })}><span className="delivery-budget-icon"><Box /></span><span><small>ORÇAMENTO</small><strong>{delivery.orcamento}{delivery.versao ? ` · ${delivery.versao}` : ""}</strong><em>{delivery.cliente || delivery.descricao || "Cliente não informado"}</em></span><span className="delivery-budget-meta"><b>{dateLabel(delivery.data_entrega)}</b><i><AlertTriangle size={13} /> {statusLabel(cpm?.summary.status)}</i></span>{isCollapsed ? <ChevronRight /> : <ChevronDown />}</button>
          {!isCollapsed && <div className="delivery-orders-map">
            {cpmQuery?.isLoading && <p className="delivery-order-loading">Calculando CPM...</p>}{cpmQuery?.isError && <p className="delivery-order-loading error">CPM indisponível</p>}
            {cpm && <CpmSummary cpm={cpm} simulation={simulation} criticalActivities={criticalActivities} onSimulate={(activity) => runSimulation(key, delivery.orcamento, activity)} />}
            {delivery.os.length === 0 && <p className="delivery-empty-order">Orçamento sem OS vinculada.</p>}
            {delivery.os.map((order) => { const structure = byOrder.get(order.numero); return <section className="delivery-order-group" key={order.numero}><button type="button" className="delivery-order-label" onClick={() => onSelectOrder(order.numero)}>OS {order.numero}{order.principal && <span>Principal</span>}</button>{structure?.isLoading && <p className="delivery-order-loading">Carregando componentes...</p>}{structure?.isError && <p className="delivery-order-loading error">Estrutura indisponível</p>}<div className="delivery-component-grid">
              {(structure?.data?.nos ?? []).map((node) => <ComponentCard key={node.id} node={node} cpm={cpm} onSelect={() => onSelectOrder(order.numero, node.codigo)} />)}
            </div></section>; })}
          </div>}
        </article>;
      })}
    </div></div>}
    <footer className="delivery-source">Fonte: CPM calculado no backend com estrutura produtiva do GRV</footer>
  </main>;
}

function CpmSummary({ cpm, simulation, criticalActivities, onSimulate }: { cpm: CpmBudget; simulation?: { loading?: boolean; projected?: string; status?: string; error?: string }; criticalActivities: CpmActivity[]; onSimulate: (activity: CpmActivity) => void }) {
  return <section className="delivery-cpm-summary"><div><small>Conclusão projetada</small><strong>{dateTimeLabel(simulation?.projected ?? cpm.summary.projected_finish)}</strong></div><div><small>Índice de folga</small><strong>{minutesLabel(cpm.summary.total_float_minutes)}</strong></div><div><small>Processos críticos</small><strong>{cpm.summary.critical_process_count}</strong></div><div><small>Compras críticas</small><strong>{cpm.summary.critical_purchase_count}</strong></div><span className={`delivery-cpm-status cpm-${(simulation?.status ?? cpm.summary.status).toLowerCase()}`}>{statusLabel(simulation?.status ?? cpm.summary.status)}</span>{criticalActivities.length > 0 && <div className="delivery-cpm-path"><small>CAMINHO CRÍTICO</small>{criticalActivities.slice(0, 8).map((activity) => <span key={activity.id}><b>{activity.name}</b><em>{activity.resource_id || activity.metadata.component_code} · folga {minutesLabel(activity.total_float_minutes)}</em>{activity.critical_reason && <i>{activity.critical_reason}</i>}<button type="button" disabled={simulation?.loading} onClick={() => onSimulate(activity)}>Simular +8h</button></span>)}</div>}{cpm.purchases.some((purchase) => purchase.status !== "NO_PRAZO") && <div className="delivery-purchase-list"><small>COMPRAS CRÍTICAS</small>{cpm.purchases.filter((purchase) => purchase.status !== "NO_PRAZO").slice(0, 8).map((purchase) => <span key={purchase.activity_id}><b>{purchase.purchase_order ? `PC ${purchase.purchase_order}` : "Solicitação"}</b><em>{purchase.material_code} · {purchase.supplier || "Fornecedor não confirmado"}</em><i>Necessário {dateTimeLabel(purchase.needed_date)} · prometido {dateTimeLabel(purchase.promised_date)} · folga {minutesLabel(purchase.float_minutes)}</i><strong>{statusLabel(purchase.status)}</strong></span>)}</div>}{simulation?.error && <p className="delivery-order-loading error">{simulation.error}</p>}{cpm.warnings.map((warning) => <p className="delivery-cpm-warning" key={warning}>{warning}</p>)}</section>;
}

function ComponentCard({ node, cpm, onSelect }: { node: DeliveryStructureNode; cpm?: CpmBudget; onSelect: () => void }) {
  const remaining = node.operacoes.filter((operation) => !operation.finalizada);
  const activities = componentCpm(node, cpm);
  const critical = activities.find((activity) => activity.is_critical && !activity.completed);
  const totalFloat = activities.length ? Math.min(...activities.map((activity) => activity.total_float_minutes)) : null;
  return <button type="button" className={`delivery-component-card${critical ? " is-critical" : ""}`} onClick={onSelect}><span className="delivery-component-top"><span className="delivery-component-icon"><Box /></span><span className={`delivery-state ${stateClass[node.estado]}`}><CircleDot /> {node.estado_label}</span></span><strong>{node.descricao || "Componente"}</strong><span className="delivery-component-code">{node.codigo || `Item ${node.aux_code}`} · Qtde. {node.quantidade}</span><span className="delivery-cpm-chip">CPM: {critical ? "CRÍTICO" : activities.length ? statusLabel(activities[0].status) : "Sem cálculo"} · Folga {minutesLabel(totalFloat)}</span><span className="delivery-pending-title">Processos pendentes</span><span className="delivery-pending-list">{remaining.length ? remaining.slice(0, 4).map((operation) => <span key={`${operation.codigo}-${operation.sequencia}`}><Clock3 /> {operation.nome || operation.codigo}{operation.maquina ? ` · ${operation.maquina}` : ""}</span>) : <span className="done"><CheckCircle2 /> Todos concluídos</span>}{remaining.length > 4 && <span>+ {remaining.length - 4} processo(s)</span>}</span>{critical && <span className="delivery-critical-reason"><AlertTriangle /> {critical.critical_reason || "Folga nula: atraso impacta a entrega"}</span>}</button>;
}
