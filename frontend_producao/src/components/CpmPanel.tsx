import { useMutation } from "@tanstack/react-query";
import {
  AlertTriangle,
  Clock3,
  GitBranch,
  LoaderCircle,
  PackageSearch,
  Play,
  RefreshCw,
  Route,
  TriangleAlert
} from "lucide-react";
import { useEffect, useMemo, type ReactNode } from "react";

import { api, type CpmActivity, type CpmOrder, type CpmStatus } from "../lib/api";
import type { AssemblyNode } from "../types";

interface CpmPanelProps {
  orderNumber: string;
  selectedNode: AssemblyNode | null;
  selectedActivityId: string | null;
  onSelectActivity: (activityId: string | null) => void;
  data: CpmOrder | undefined;
  loading: boolean;
  error: boolean;
  onRetry: () => void;
}

const simulationOptions = [120, 240, 480, 1440, 2880];

export function CpmPanel({
  orderNumber,
  selectedNode,
  selectedActivityId,
  onSelectActivity,
  data,
  loading,
  error,
  onRetry
}: CpmPanelProps) {
  const componentActivities = useMemo(
    () => data?.activities.filter(
      (activity) => activity.metadata.component_aux_code === selectedNode?.aux_code
    ) ?? [],
    [data?.activities, selectedNode?.aux_code]
  );
  const selectedActivity = componentActivities.find((item) => item.id === selectedActivityId)
    ?? componentActivities.find((item) => item.is_critical && !item.completed)
    ?? componentActivities.find((item) => !item.completed)
    ?? componentActivities[0]
    ?? null;
  const purchases = data?.purchases.filter(
    (purchase) => purchase.component_aux_code === selectedNode?.aux_code
  ) ?? [];
  const simulation = useMutation({
    mutationFn: (delayMinutes: number) => api.simulateCpm(orderNumber, selectedActivity!.id, delayMinutes)
  });

  useEffect(() => {
    simulation.reset();
  }, [orderNumber, selectedActivity?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <section id="cpm-panel" className="cpm-panel panel" aria-label="Análise CPM">
      <header className="cpm-heading">
        <div>
          <span className="cpm-eyebrow"><Route size={14} /> Execução produtiva</span>
          <h2>ANÁLISE CPM</h2>
        </div>
        {data && <StatusBadge status={selectedActivity?.status ?? data.summary.status} />}
      </header>

      {loading && <PanelMessage icon={<LoaderCircle className="spin" />} text="Calculando caminho crítico…" />}
      {error && (
        <PanelMessage icon={<AlertTriangle />} text="Não foi possível calcular o CPM.">
          <button type="button" onClick={onRetry}><RefreshCw size={14} /> Tentar novamente</button>
        </PanelMessage>
      )}
      {!loading && !error && !data && <PanelMessage icon={<Clock3 />} text="CPM ainda não disponível." />}

      {data && !selectedNode && <OrderSummary data={data} />}
      {data && selectedNode && componentActivities.length === 0 && (
        <PanelMessage icon={<TriangleAlert />} text="Este item não possui processos com dados suficientes para o CPM." />
      )}
      {data && selectedNode && componentActivities.length > 0 && (
        <div className="cpm-content">
          <div className="cpm-selected">
            <small>Elemento selecionado</small>
            <strong>{selectedNode.code}</strong>
            <span>{selectedNode.description}</span>
          </div>

          <div className="cpm-process-picker" role="list" aria-label="Processos do componente">
            {componentActivities.filter((item) => item.kind === "process").map((activity) => (
              <button
                type="button"
                key={activity.id}
                className={`${statusClass(activity.status)}${activity.id === selectedActivity?.id ? " selected" : ""}`}
                onClick={() => onSelectActivity(activity.id)}
              >
                <span>{activity.name}</span>
                <small>{activity.completed ? "Concluído" : formatMinutes(activity.total_float_minutes)}</small>
              </button>
            ))}
          </div>

          {selectedActivity && (
            <>
              <SlackGauge value={selectedActivity.total_float_minutes} status={selectedActivity.status} />
              <div className="cpm-metrics">
                <Metric label="Folga" value={formatMinutes(selectedActivity.total_float_minutes)} />
                <Metric label="Duração restante" value={selectedActivity.duration_minutes === null ? "Não definida" : formatMinutes(selectedActivity.duration_minutes)} />
                <Metric label="Início projetado" value={formatDate(selectedActivity.early_start)} />
                <Metric label="Fim projetado" value={formatDate(selectedActivity.early_finish)} />
              </div>

              <details open className="cpm-section">
                <summary><GitBranch size={15} /> Dependências e impacto</summary>
                <InfoRow label="Predecessores" value={String(selectedActivity.predecessor_ids.length)} />
                <InfoRow label="Sucessores" value={String(selectedActivity.successor_ids.length)} />
                <InfoRow label="Recurso" value={selectedActivity.resource_id ?? "Não informado"} />
                <InfoRow label="Fila do recurso" value={formatQueue(selectedActivity)} />
                <InfoRow label="Causa" value={selectedActivity.critical_reason ?? "Sem causa crítica identificada"} />
                <InfoRow label="Impacto atual" value={selectedActivity.total_float_minutes < 0 ? `+${formatMinutes(-selectedActivity.total_float_minutes)}` : "Sem atraso projetado"} />
              </details>

              <details className="cpm-section" open={purchases.some((item) => item.status !== "NORMAL")}>
                <summary><PackageSearch size={15} /> Materiais e compras ({purchases.length})</summary>
                {purchases.length === 0 && <p>Nenhuma compra vinculada afeta este componente.</p>}
                {purchases.map((purchase) => (
                  <article className={`cpm-purchase ${statusClass(purchase.status)}`} key={purchase.activity_id}>
                    <strong>{purchase.purchase_order ? `PC ${purchase.purchase_order}` : `SC ${purchase.purchase_request}`}</strong>
                    <span>{purchase.material ?? purchase.material_code ?? "Material"}</span>
                    <small>Necessidade: {formatDate(purchase.needed_date)} · Previsão: {formatDate(purchase.promised_date)}</small>
                    <b>{purchase.status.replaceAll("_", " ")} · {formatMinutes(purchase.float_minutes)}</b>
                  </article>
                ))}
              </details>

              {!selectedActivity.completed && selectedActivity.duration_minutes !== null && (
                <details className="cpm-section">
                  <summary><Play size={15} /> Simular atraso</summary>
                  <div className="cpm-simulation-options">
                    {simulationOptions.map((minutes) => (
                      <button type="button" key={minutes} disabled={simulation.isPending} onClick={() => simulation.mutate(minutes)}>
                        +{formatMinutes(minutes)}
                      </button>
                    ))}
                  </div>
                  {simulation.isError && <p className="cpm-error">Não foi possível executar a simulação.</p>}
                  {simulation.data && (
                    <div className="cpm-simulation-result">
                      <InfoRow label="Conclusão atual" value={formatDate(simulation.data.simulation.previous_projected_finish)} />
                      <InfoRow label="Nova conclusão" value={formatDate(simulation.data.projected_finish)} />
                      <InfoRow label="Nova folga" value={formatMinutes(simulation.data.project_float_minutes)} />
                      <InfoRow label="Novo status" value={simulation.data.status.replaceAll("_", " ")} />
                      <small>Simulação temporária; nenhum dado foi gravado.</small>
                    </div>
                  )}
                </details>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}

function OrderSummary({ data }: { data: CpmOrder }) {
  const critical = data.activities.filter((item) => item.is_critical && !item.completed);
  return (
    <div className="cpm-content">
      <div className="cpm-selected"><small>Resumo da OS</small><strong>{data.order.number}</strong><span>{data.order.title}</span></div>
      <div className="cpm-metrics">
        <Metric label="Caminho crítico" value={`${critical.length} atividades`} />
        <Metric label="Folga total" value={formatMinutes(data.summary.total_float_minutes)} />
        <Metric label="Gargalo" value={data.summary.bottleneck ?? "Não identificado"} />
        <Metric label="Compras críticas" value={String(data.summary.critical_purchase_count)} />
        <Metric label="Conclusão projetada" value={formatDate(data.summary.projected_finish)} />
        <Metric label="Processos restantes" value={String(data.summary.pending_process_count)} />
      </div>
      {data.warnings.length > 0 && (
        <details className="cpm-section"><summary><TriangleAlert size={15} /> Dados incompletos ({data.warnings.length})</summary>{data.warnings.map((warning) => <p key={warning}>{warning}</p>)}</details>
      )}
    </div>
  );
}

function SlackGauge({ value, status }: { value: number; status: CpmStatus }) {
  const position = value < 0 ? 5 : value === 0 ? 28 : value <= 480 ? 48 : value <= 1440 ? 70 : 92;
  return (
    <div className="cpm-slack">
      <div><span>Índice de folga</span><strong>{status.replaceAll("_", " ")}</strong></div>
      <div className="cpm-slack-track"><i style={{ left: `${position}%` }} /></div>
      <div className="cpm-slack-labels"><span>Atraso</span><span>Crítico</span><span>Atenção</span><span>Seguro</span></div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div><small>{label}</small><strong>{value}</strong></div>;
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return <div className="cpm-info-row"><span>{label}</span><strong>{value}</strong></div>;
}

function StatusBadge({ status }: { status: CpmStatus }) {
  return <span className={`cpm-status ${statusClass(status)}`}>{status.replaceAll("_", " ")}</span>;
}

function PanelMessage({ icon, text, children }: { icon: ReactNode; text: string; children?: ReactNode }) {
  return <div className="cpm-message">{icon}<span>{text}</span>{children}</div>;
}

function formatQueue(activity: CpmActivity) {
  const count = activity.metadata.queue_count;
  const wait = activity.metadata.estimated_wait_minutes;
  if (!count && !wait) return "Sem espera identificada";
  return `${count ?? 0} processos${wait ? ` · espera ${formatMinutes(wait)}` : ""}`;
}

export function formatMinutes(value: number | null | undefined) {
  if (value === null || value === undefined) return "—";
  const absolute = Math.abs(value);
  const hours = Math.floor(absolute / 60);
  const minutes = absolute % 60;
  return `${value < 0 ? "-" : ""}${hours}h${minutes ? ` ${minutes}min` : ""}`;
}

function formatDate(value: string | null | undefined) {
  if (!value) return "Não informada";
  return new Intl.DateTimeFormat("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

export function statusClass(status: string) {
  return `cpm-${status.toLocaleLowerCase("pt-BR").replaceAll("_", "-")}`;
}
