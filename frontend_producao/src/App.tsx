import { useQuery } from "@tanstack/react-query";
import {
  AlertCircle,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  RefreshCw,
  Search
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { AssemblyMap } from "./components/AssemblyMap";
import { DetailsPanel } from "./components/DetailsPanel";
import { DeliveryExplosion, type DeliveryExplosionRequest } from "./components/DeliveryExplosion";
import { Header } from "./components/Header";
import { LoadingState } from "./components/LoadingState";
import { SequencePanel } from "./components/SequencePanel";
import { TreePanel } from "./components/TreePanel";
import { api } from "./lib/api";
import { expandPath, indexNodes } from "./lib/tree";
import type { AssemblyNode } from "./types";

const defaultOrder = import.meta.env.VITE_DEFAULT_OS ?? "7807";
const navigationDepthKey = "assemblyNavigationDepth";
const mapViewKey = "assemblyMapView";
const dependencyScopeKey = "assemblyDependencyShowAll";

function browserHistoryState(): Record<string, unknown> {
  const state: unknown = window.history.state;
  return state && typeof state === "object" ? (state as Record<string, unknown>) : {};
}

function currentNavigationDepth(): number {
  const depth = browserHistoryState()[navigationDepthKey];
  return typeof depth === "number" && depth >= 0 ? depth : 0;
}

function currentMapView(): "structure" | "dependencies" {
  return browserHistoryState()[mapViewKey] === "dependencies" ? "dependencies" : "structure";
}

function currentDependencyScope(): boolean {
  return browserHistoryState()[dependencyScopeKey] === true;
}

export default function App() {
  const initialSearchParams = new URLSearchParams(window.location.search);
  const [selectedOrder, setSelectedOrder] = useState(
    () => initialSearchParams.get("os") ?? defaultOrder
  );
  const [requestedItemCode, setRequestedItemCode] = useState<string | null>(
    () => initialSearchParams.get("item")
  );
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [sequenceCollapsed, setSequenceCollapsed] = useState(false);
  const [treeCollapsed, setTreeCollapsed] = useState(false);
  const [detailsCollapsed, setDetailsCollapsed] = useState(false);
  const [navigationDepth, setNavigationDepth] = useState(currentNavigationDepth);
  const [mapView, setMapView] = useState<"structure" | "dependencies">(currentMapView);
  const [showAllDependencies, setShowAllDependencies] = useState(currentDependencyScope);
  const [deliveryExplosion, setDeliveryExplosion] = useState<DeliveryExplosionRequest | null>(null);
  const loadedStructureOrder = useRef<string | null>(null);

  const structure = useQuery({
    queryKey: ["structure", selectedOrder],
    queryFn: ({ signal }) => api.getStructure(selectedOrder, signal),
    enabled: Boolean(selectedOrder),
    staleTime: 20_000,
    retry: 1
  });

  const dependencies = useQuery({
    queryKey: ["order-dependencies", selectedOrder],
    queryFn: ({ signal }) => api.getOrderDependencies(selectedOrder, signal),
    enabled: Boolean(selectedOrder),
    staleTime: 20_000,
    retry: 1
  });

  const cpm = useQuery({
    queryKey: ["cpm-budget", dependencies.data?.budget_number],
    queryFn: ({ signal }) => api.getCpmBudget(String(dependencies.data!.budget_number), signal),
    enabled: Boolean(dependencies.data?.budget_number),
    staleTime: 20_000,
    retry: 1
  });

  const nodesById = useMemo(
    () => indexNodes(structure.data?.nodes ?? []),
    [structure.data?.nodes]
  );
  const selectedNode = selectedId ? nodesById.get(selectedId) ?? null : null;

  useEffect(() => {
    const openDeliveryExplosion = (event: Event) => {
      setDeliveryExplosion((event as CustomEvent<DeliveryExplosionRequest>).detail);
    };
    window.addEventListener("delivery-explosion:open", openDeliveryExplosion);
    return () => window.removeEventListener("delivery-explosion:open", openDeliveryExplosion);
  }, []);

  useEffect(() => {
    const state = browserHistoryState();
    window.history.replaceState(
      {
        ...state,
        [navigationDepthKey]: currentNavigationDepth(),
        [mapViewKey]: currentMapView(),
        [dependencyScopeKey]: currentDependencyScope()
      },
      "",
      window.location.href
    );

    const restoreLocation = () => {
      const searchParams = new URLSearchParams(window.location.search);
      setSelectedOrder(searchParams.get("os") ?? defaultOrder);
      setRequestedItemCode(searchParams.get("item"));
      setSelectedId(null);
      setNavigationDepth(currentNavigationDepth());
      setMapView(currentMapView());
      setShowAllDependencies(currentDependencyScope());
    };

    window.addEventListener("popstate", restoreLocation);
    return () => window.removeEventListener("popstate", restoreLocation);
  }, []);

  useEffect(() => {
    window.history.replaceState(
      {
        ...browserHistoryState(),
        [mapViewKey]: mapView,
        [dependencyScopeKey]: showAllDependencies
      },
      "",
      window.location.href
    );
  }, [mapView, showAllDependencies]);

  useEffect(() => {
    if (!structure.data) return;
    const orderChanged = loadedStructureOrder.current !== structure.data.order.number;
    loadedStructureOrder.current = structure.data.order.number;
    const root = structure.data.nodes.find((node) => node.id === structure.data.roots[0]);
    const requested = requestedItemCode
      ? structure.data.nodes.find(
          (node) =>
            node.code.trim().toLocaleLowerCase("pt-BR") ===
            requestedItemCode.trim().toLocaleLowerCase("pt-BR")
        )
      : undefined;
    const initialSelection = requested ?? root ?? structure.data.nodes[0];
    setExpanded((current) => orderChanged
      ? new Set(structure.data.nodes.filter((node) => node.has_children).map((node) => node.id))
      : requested ? expandPath(current, requested) : current
    );
    setSelectedId((current) => {
      if (requested) return requested.id;
      return !orderChanged && current && structure.data.nodes.some((node) => node.id === current)
        ? current
        : initialSelection?.id ?? null;
    });
  }, [requestedItemCode, structure.data]);

  const selectOrder = useCallback((number: string, itemCode?: string) => {
    const url = new URL(window.location.href);
    const nextItemCode = itemCode ?? null;
    const currentOrder = url.searchParams.get("os") ?? defaultOrder;
    if (currentOrder === number && url.searchParams.get("item") === nextItemCode) return;

    url.searchParams.set("os", number);
    if (itemCode) url.searchParams.set("item", itemCode);
    else url.searchParams.delete("item");
    const nextDepth = navigationDepth + 1;
    window.history.pushState(
      {
        ...browserHistoryState(),
        [navigationDepthKey]: nextDepth,
        [mapViewKey]: mapView,
        [dependencyScopeKey]: showAllDependencies
      },
      "",
      url
    );
    setNavigationDepth(nextDepth);
    setSelectedOrder(number);
    setRequestedItemCode(nextItemCode);
    setSelectedId(null);
  }, [mapView, navigationDepth, showAllDependencies]);

  const goBack = useCallback(() => {
    if (navigationDepth > 0) window.history.back();
  }, [navigationDepth]);

  const selectNode = useCallback((node: AssemblyNode) => {
    setRequestedItemCode(null);
    setSelectedId(node.id);
    setExpanded((current) => expandPath(current, node));
    const url = new URL(window.location.href);
    url.searchParams.delete("item");
    window.history.replaceState(browserHistoryState(), "", url);
  }, []);

  return (
    <div className="app-shell">
      <Header
        structure={structure.data}
        onSelectOrder={selectOrder}
        canGoBack={navigationDepth > 0}
        onBack={goBack}
      />

      {deliveryExplosion && (
        <DeliveryExplosion
          {...deliveryExplosion}
          onClose={() => setDeliveryExplosion(null)}
          onSelectOrder={(number, itemCode) => {
            setDeliveryExplosion(null);
            selectOrder(number, itemCode);
          }}
        />
      )}

      {!deliveryExplosion && !selectedOrder && (
        <main className="welcome-state">
          <Search size={42} />
          <h1>Selecione uma Ordem de Serviço</h1>
          <p>Utilize a pesquisa no cabeçalho para localizar a OS.</p>
        </main>
      )}

      {!deliveryExplosion && selectedOrder && structure.isLoading && <LoadingState />}

      {!deliveryExplosion && selectedOrder && structure.isError && (
        <main className="error-state">
          <AlertCircle size={42} />
          <h1>Não foi possível carregar a OS {selectedOrder}</h1>
          <p>{structure.error.message}</p>
          <button className="primary-button" type="button" onClick={() => structure.refetch()}>
            <RefreshCw size={17} />
            Tentar novamente
          </button>
        </main>
      )}

      {!deliveryExplosion && structure.data && (
        <main
          className={`workspace${treeCollapsed ? " tree-collapsed" : ""}${
            detailsCollapsed ? " details-collapsed" : ""
          }`}
        >
          <TreePanel
            nodes={structure.data.nodes}
            roots={structure.data.roots}
            selectedId={selectedId}
            expanded={expanded}
            onToggle={(nodeId) =>
              setExpanded((current) => {
                const next = new Set(current);
                if (next.has(nodeId)) next.delete(nodeId);
                else next.add(nodeId);
                return next;
              })
            }
            onSelect={selectNode}
            onCollapseAll={() => setExpanded(new Set(structure.data.roots))}
          />
          <div className="workspace-divider tree-divider">
            <button
              type="button"
              aria-controls="tree-panel"
              aria-expanded={!treeCollapsed}
              aria-label={treeCollapsed ? "Expandir estrutura da OS" : "Recolher estrutura da OS"}
              title={treeCollapsed ? "Expandir estrutura da OS" : "Ampliar mapa visual"}
              onClick={() => setTreeCollapsed((current) => !current)}
            >
              {treeCollapsed ? <ChevronRight size={18} /> : <ChevronLeft size={18} />}
            </button>
          </div>
          <div
            className={`center-column${sequenceCollapsed ? " sequence-collapsed" : ""}`}
          >
            <AssemblyMap
              nodes={structure.data.nodes}
              selectedId={selectedId}
              expanded={expanded}
              onSelect={selectNode}
              dependencyMap={dependencies.data}
              dependenciesLoading={dependencies.isLoading}
              dependenciesError={dependencies.isError}
              selectedOrder={selectedOrder}
              onSelectOrder={selectOrder}
              view={mapView}
              onViewChange={setMapView}
              showAllDependencies={showAllDependencies}
              onShowAllDependenciesChange={setShowAllDependencies}
              cpmActivities={cpm.data?.activities ?? []}
            />
            <div className="map-sequence-divider">
              <button
                type="button"
                aria-controls="sequence-panel"
                aria-expanded={!sequenceCollapsed}
                aria-label={
                  sequenceCollapsed
                    ? "Expandir sequência de montagem"
                    : "Recolher sequência de montagem"
                }
                title={
                  sequenceCollapsed
                    ? "Expandir sequência de montagem"
                    : "Ampliar mapa visual"
                }
                onClick={() => setSequenceCollapsed((current) => !current)}
              >
                {sequenceCollapsed ? <ChevronUp size={18} /> : <ChevronDown size={18} />}
              </button>
            </div>
            <SequencePanel
              orderNumber={selectedOrder}
              selectedNode={selectedNode}
            />
          </div>
          <div className="workspace-divider details-divider">
            <button
              type="button"
              aria-controls="details-panel"
              aria-expanded={!detailsCollapsed}
              aria-label={
                detailsCollapsed ? "Expandir detalhes da peça" : "Recolher detalhes da peça"
              }
              title={detailsCollapsed ? "Expandir detalhes da peça" : "Ampliar mapa visual"}
              onClick={() => setDetailsCollapsed((current) => !current)}
            >
              {detailsCollapsed ? <ChevronLeft size={18} /> : <ChevronRight size={18} />}
            </button>
          </div>
          <DetailsPanel
            orderNumber={selectedOrder}
            selectedNode={selectedNode}
            onLocate={selectNode}
            cpmActivities={cpm.data?.activities ?? []}
          />
        </main>
      )}
    </div>
  );
}
