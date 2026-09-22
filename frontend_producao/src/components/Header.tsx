import {
  AlertTriangle,
  ArrowLeft,
  Search,
  Wrench
} from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api } from "../lib/api";
import type { AssemblyStructure, OrderSearchResult } from "../types";

interface HeaderProps {
  structure?: AssemblyStructure;
  onSelectOrder: (orderNumber: string, itemCode?: string) => void;
  canGoBack: boolean;
  onBack: () => void;
}

export function Header({
  structure,
  onSelectOrder,
  canGoBack,
  onBack
}: HeaderProps) {
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query.trim()), 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as HTMLElement)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    return () => document.removeEventListener("mousedown", close);
  }, []);

  const results = useQuery({
    queryKey: ["order-search", debouncedQuery],
    queryFn: ({ signal }) => api.searchOrders(debouncedQuery, signal),
    enabled: debouncedQuery.length > 0,
    staleTime: 30_000
  });

  const choose = (result: OrderSearchResult) => {
    onSelectOrder(result.number, result.matched_item_code ?? undefined);
    setQuery("");
    setOpen(false);
  };

  return (
    <header className="app-header">
      <div className="header-grid" aria-hidden="true" />

      <div className="header-copy">
        <p className="header-eyebrow">
          <span />
          Controle de produção
        </p>
        <h1>Painel Visual de Montagem</h1>
        <p className="header-subtitle">
          Acompanhe a estrutura, o mapa do conjunto e o status de montagem em tempo real.
        </p>
      </div>

      <div className="brand">
        <img src="/columbia-logo.png" alt="Columbia" />
      </div>

      <div className="global-search" ref={containerRef}>
        <button
          className="search-back-button"
          type="button"
          disabled={!canGoBack}
          onClick={onBack}
          aria-label="Voltar para a OS anterior"
          title={canGoBack ? "Voltar para a OS anterior" : "Nenhuma OS anterior"}
        >
          <ArrowLeft size={19} />
        </button>
        <Search size={20} />
        <input
          value={query}
          onChange={(event) => {
            setQuery(event.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && results.data?.[0]) choose(results.data[0]);
          }}
          placeholder="Buscar orçamento, OS, peça, desenho ou descrição..."
          aria-label="Pesquisa global"
        />
        {open && debouncedQuery && (
          <div className="search-results">
            {results.isLoading && <div className="search-message">Pesquisando...</div>}
            {results.isError && (
              <div className="search-message error">Não foi possível pesquisar.</div>
            )}
            {results.data?.length === 0 && (
              <div className="search-message">Nenhum resultado encontrado.</div>
            )}
            {results.data?.map((order) => (
              <button
                key={`${order.number}-${order.matched_item_code ?? "order"}`}
                type="button"
                onClick={() => choose(order)}
              >
                <strong>OS {order.matched_item_code ?? order.number}</strong>
                <span>{order.matched_item_description ?? order.title}</span>
                {order.matched_item_code && (
                  <small>Estrutura da OS {order.number}</small>
                )}
                {order.matched_budget_number != null && (
                  <small>Orçamento {order.matched_budget_number} · OS {order.number}</small>
                )}
                {order.drawing_number && <small>Desenho {order.drawing_number}</small>}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="header-metrics">
        <div className="header-tile progress-tile">
          <div
            className="progress-ring"
            style={
              {
                "--progress": `${structure?.progress.percentage ?? 0}%`
              } as React.CSSProperties
            }
          >
            <span>{Math.round(structure?.progress.percentage ?? 0)}%</span>
          </div>
          <div>
            <strong>{structure?.progress.percentage ?? 0}% concluído</strong>
            <small>
              {structure?.progress.finalized_operations ?? 0}/
              {structure?.progress.total_operations ?? 0} operações
            </small>
          </div>
        </div>

        <div className="header-tile pending-tile">
          <AlertTriangle size={20} />
          <strong>{structure?.pending_count ?? 0} pendências</strong>
        </div>

        <div className="header-tile stage-tile">
          <Wrench size={20} />
          <div>
            <small>Etapa atual:</small>
            <strong title={structure?.current_stage ?? "—"}>
              {structure?.current_stage ?? "—"}
            </strong>
          </div>
        </div>
      </div>
    </header>
  );
}
