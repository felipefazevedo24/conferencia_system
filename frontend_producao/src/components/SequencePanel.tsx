import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Clock3,
  Factory,
  Info,
  LoaderCircle,
  Timer,
  UserRound
} from "lucide-react";
import { Fragment, useEffect, useMemo, useRef } from "react";

import { api } from "../lib/api";
import type {
  AssemblyNode,
  OperationDetail,
  OperationLiveStatus
} from "../types";

interface SequencePanelProps {
  orderNumber: string;
  selectedNode: AssemblyNode | null;
}

type OperationState =
  | "blocked"
  | "completed"
  | "in-progress"
  | "paused"
  | "started"
  | "pending";

export function SequencePanel({
  orderNumber,
  selectedNode
}: SequencePanelProps) {
  const queryClient = useQueryClient();
  const previousLiveSignature = useRef<string | null>(null);
  const detail = useQuery({
    queryKey: ["item", orderNumber, selectedNode?.aux_code],
    queryFn: ({ signal }) =>
      api.getItem(orderNumber, selectedNode!.aux_code, signal),
    enabled: Boolean(orderNumber && selectedNode),
    staleTime: 15_000
  });
  const live = useQuery({
    queryKey: ["item-live", orderNumber, selectedNode?.aux_code],
    queryFn: ({ signal }) =>
      api.getItemLiveStatus(orderNumber, selectedNode!.aux_code, signal),
    enabled: Boolean(orderNumber && selectedNode),
    staleTime: 5_000,
    refetchInterval: 10_000,
    refetchIntervalInBackground: false,
    retry: 1
  });
  const liveSignature = useMemo(
    () =>
      live.data
        ? JSON.stringify(
            live.data.operations.map((operation) => ({
              code: operation.code,
              sequence: operation.sequence,
              state: operation.state,
              pointings: operation.pointings.map((pointing) => ({
                operator: pointing.operator_name,
                started_at: pointing.started_at,
                paused: pointing.paused
              }))
            }))
          )
        : null,
    [live.data]
  );

  useEffect(() => {
    previousLiveSignature.current = null;
  }, [orderNumber, selectedNode?.aux_code]);

  useEffect(() => {
    if (liveSignature === null) return;
    const previous = previousLiveSignature.current;
    previousLiveSignature.current = liveSignature;
    if (previous === null || previous === liveSignature) return;

    void detail.refetch();
    void queryClient.invalidateQueries({
      queryKey: ["structure", orderNumber]
    });
  }, [detail, liveSignature, orderNumber, queryClient]);
  const operations = useMemo(
    () =>
      [...(detail.data?.operations ?? [])].sort(
        (left, right) =>
          (left.sequence ?? Number.MAX_SAFE_INTEGER) -
            (right.sequence ?? Number.MAX_SAFE_INTEGER) ||
          left.name.localeCompare(right.name, "pt-BR")
      ),
    [detail.data?.operations]
  );

  return (
    <section id="sequence-panel" className="sequence-panel panel">
      <div className="sequence-heading">
        <div>
          <h2>Sequência de Montagem</h2>
          {selectedNode && (
            <strong className="sequence-item-code">{selectedNode.code}</strong>
          )}
        </div>
        <div className="sequence-source">
          <span className={`live-sync${live.isError ? " unavailable" : ""}`}>
            <i aria-hidden="true" />
            {live.isError
              ? "Atualização indisponível"
              : live.isPending
                ? "Conectando ao GRV"
                : "AO VIVO · 10s"}
          </span>
          <span>
            <Info size={14} />
            Fonte: processo produtivo · GRV
          </span>
        </div>
      </div>

      {!selectedNode && (
        <SequenceMessage text="Selecione um item para visualizar suas operações." />
      )}

      {selectedNode && detail.isLoading && (
        <div className="sequence-cards" aria-label="Carregando sequência">
          {Array.from({ length: 3 }, (_, index) => (
            <div className="sequence-card-skeleton" key={index} />
          ))}
        </div>
      )}

      {selectedNode && detail.isError && (
        <div className="sequence-empty">
          <AlertTriangle size={23} />
          <div>
            <strong>Não foi possível carregar as operações</strong>
            <button type="button" onClick={() => detail.refetch()}>
              Tentar novamente
            </button>
          </div>
        </div>
      )}

      {selectedNode && detail.isSuccess && operations.length === 0 && (
        <SequenceMessage text="Este item não possui operações produtivas cadastradas no GRV." />
      )}

      {selectedNode && operations.length > 0 && (
        <div className="sequence-cards" aria-label={`Operações de ${selectedNode.code}`}>
          {operations.map((operation, index) => (
            <Fragment key={operation.code}>
              <OperationCard
                operation={operation}
                itemCode={selectedNode.code}
                position={index + 1}
                liveStatus={findLiveStatus(live.data?.operations, operation)}
                calculatedAt={live.data?.source.calculated_at}
              />
              {index < operations.length - 1 && (
                <div className="sequence-connector" aria-hidden="true">
                  <ArrowRight size={25} />
                </div>
              )}
            </Fragment>
          ))}
        </div>
      )}
    </section>
  );
}

function OperationCard({
  operation,
  itemCode,
  position,
  liveStatus,
  calculatedAt
}: {
  operation: OperationDetail;
  itemCode: string;
  position: number;
  liveStatus?: OperationLiveStatus;
  calculatedAt?: string;
}) {
  const state = operationState(operation, liveStatus);
  const status = operationStatus(state);
  const activePointings = liveStatus?.pointings ?? [];
  const operatorNames = [...new Set(activePointings.map((pointing) => pointing.operator_name))];
  const startedAt = activePointings
    .map((pointing) => pointing.started_at)
    .filter((value): value is string => Boolean(value))
    .sort()[0];
  const activeMachines = [
    ...new Set(
      activePointings
        .map((pointing) => pointing.machine)
        .filter((value): value is string => Boolean(value))
    )
  ];
  const machine = activeMachines.join(" / ") || operation.machine;
  return (
    <article
      className={`sequence-operation-card ${state}`}
      aria-label={`${position}. ${operation.name}: ${status.label}`}
    >
      <div className="sequence-card-heading">
        <span className="sequence-number">{position}</span>
        <small>Seq. {operation.sequence ?? "—"}</small>
      </div>
      <strong title={operation.name}>{operation.name}</strong>
      <span className="sequence-operation-item">{itemCode}</span>
      <div className="sequence-machine" title={machine ?? undefined}>
        <Factory size={15} />
        <span>{machine ?? "Máquina não informada"}</span>
      </div>
      {activePointings.length > 0 && (
        <div className="sequence-live-details">
          <span title={operatorNames.join(", ")}>
            <UserRound size={15} />
            {operatorNames.length === 1
              ? operatorNames[0]
              : `${operatorNames.length} operadores · ${operatorNames.join(", ")}`}
          </span>
          {startedAt && (
            <span>
              <Timer size={15} />
              Iniciado às {formatTime(startedAt)}
              {calculatedAt ? ` · ${formatElapsed(startedAt, calculatedAt)}` : ""}
            </span>
          )}
        </div>
      )}
      <div className="sequence-status">
        {status.icon}
        <span>{status.label}</span>
      </div>
    </article>
  );
}

function SequenceMessage({ text }: { text: string }) {
  return (
    <div className="sequence-empty">
      <Clock3 size={23} />
      <div>
        <strong>Sem sequência para exibir</strong>
        <span>{text}</span>
      </div>
    </div>
  );
}

function findLiveStatus(
  statuses: OperationLiveStatus[] | undefined,
  operation: OperationDetail
) {
  return statuses?.find(
    (status) =>
      (status.code !== null && status.code === operation.code) ||
      (status.code === null &&
        status.sequence !== null &&
        status.sequence === operation.sequence)
  );
}

function operationState(
  operation: OperationDetail,
  liveStatus?: OperationLiveStatus
): OperationState {
  if (operation.locked) return "blocked";
  if (liveStatus?.state === "paused") return "paused";
  if (liveStatus?.state === "running") return "in-progress";
  if (operation.finalized) return "completed";
  if (operation.started_at || operation.first_report_at) return "started";
  return "pending";
}

function operationStatus(state: OperationState) {
  if (state === "blocked") {
    return {
      label: "Bloqueada",
      icon: <AlertTriangle size={15} />
    };
  }
  if (state === "completed") {
    return {
      label: "Finalizada",
      icon: <CheckCircle2 size={15} />
    };
  }
  if (state === "in-progress") {
    return {
      label: "Em execução agora",
      icon: <LoaderCircle size={15} />
    };
  }
  if (state === "paused") {
    return {
      label: "Apontamento pausado",
      icon: <Clock3 size={15} />
    };
  }
  if (state === "started") {
    return {
      label: "Iniciada · sem apontamento ativo",
      icon: <Clock3 size={15} />
    };
  }
  return {
    label: "Pendente",
    icon: <Clock3 size={15} />
  };
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat("pt-BR", {
    hour: "2-digit",
    minute: "2-digit"
  }).format(new Date(value));
}

function formatElapsed(startedAt: string, calculatedAt: string) {
  const elapsedMinutes = Math.max(
    0,
    Math.floor(
      (new Date(calculatedAt).getTime() - new Date(startedAt).getTime()) / 60_000
    )
  );
  if (elapsedMinutes < 60) return `há ${elapsedMinutes} min`;
  const hours = Math.floor(elapsedMinutes / 60);
  const minutes = elapsedMinutes % 60;
  return `há ${hours}h${String(minutes).padStart(2, "0")}`;
}
