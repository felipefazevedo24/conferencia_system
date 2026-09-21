import dagre from "@dagrejs/dagre";
import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  Panel,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps
} from "@xyflow/react";
import { Network } from "lucide-react";
import { memo, useMemo } from "react";

import type { OrderDependencyMap as DependencyMap, OrderDependencyNode } from "../types";

interface OrderDependencyMapProps {
  data: DependencyMap | undefined;
  selectedOrder: string;
  loading: boolean;
  error: boolean;
  onSelectOrder: (number: string) => void;
  showAll: boolean;
  onShowAllChange: (showAll: boolean) => void;
}

type DependencyCardData = {
  order: OrderDependencyNode;
  selected: boolean;
  onSelectOrder: (number: string) => void;
};

type DependencyFlowNode = Node<DependencyCardData, "dependency">;

const DependencyCard = memo(function DependencyCard({
  data
}: NodeProps<DependencyFlowNode>) {
  const { order, selected, onSelectOrder } = data;
  return (
    <button
      type="button"
      className={`dependency-node${selected ? " selected" : ""}`}
      onClick={() => onSelectOrder(order.number)}
      aria-label={`OS ${order.number}: ${order.title}`}
    >
      <Handle type="target" position={Position.Left} />
      <div className="dependency-node-heading">
        <strong>OS {order.number}</strong>
        <span className={`order-status ${statusClass(order.source_status)}`}>
          {order.source_status ?? "Sem status"}
        </span>
      </div>
      <span className="dependency-node-title">{order.title}</span>
      <small>{order.is_budget_order ? "OS do orçamento" : "OS dependente"}</small>
      <Handle type="source" position={Position.Right} />
    </button>
  );
});

const nodeTypes = { dependency: DependencyCard };

export function OrderDependencyMap({
  data,
  selectedOrder,
  loading,
  error,
  onSelectOrder,
  showAll,
  onShowAllChange
}: OrderDependencyMapProps) {
  const visibleNumbers = useMemo(
    () =>
      data
        ? showAll
          ? new Set(data.nodes.map((node) => node.number))
          : connectedOrderNumbers(data, selectedOrder)
        : new Set<string>(),
    [data, selectedOrder, showAll]
  );
  const { flowNodes, edges } = useMemo(
    () => layoutDependencies(data, visibleNumbers, selectedOrder, onSelectOrder),
    [data, onSelectOrder, selectedOrder, visibleNumbers]
  );

  if (loading) {
    return <DependencyMessage text="Carregando relações entre as OS..." />;
  }
  if (error) {
    return <DependencyMessage text="Não foi possível consultar as dependências no GRV." />;
  }
  if (!data?.budget_number) {
    return (
      <DependencyMessage text={`A OS ${selectedOrder} não possui orçamento relacionado no GRV.`} />
    );
  }

  const budgetOrders = data.nodes.filter((node) => node.is_budget_order).length;
  const hasSelectedConnections = data.edges.some(
    (edge) =>
      edge.dependent_order_number === selectedOrder ||
      edge.prerequisite_order_number === selectedOrder
  );

  return (
    <div className="flow-canvas dependency-canvas">
      <ReactFlow
        key={`${showAll ? "all" : "chain"}-${selectedOrder}`}
        nodes={flowNodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.28, maxZoom: 1.05 }}
        minZoom={0.15}
        maxZoom={1.8}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        onlyRenderVisibleElements
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#dce4ed" />
        <Controls showInteractive={false} position="top-right" />
        <Panel position="top-left" className="dependency-toolbar">
          <div>
            <strong>Orçamento {data.budget_number}</strong>
            <span>
              {budgetOrders} OS vinculadas · {data.edges.length} dependências
            </span>
          </div>
          <div className="dependency-filter" aria-label="Filtro do mapa de dependências">
            <button
              type="button"
              className={!showAll ? "active" : ""}
              onClick={() => onShowAllChange(false)}
            >
              Cadeia da OS
            </button>
            <button
              type="button"
              className={showAll ? "active" : ""}
              onClick={() => onShowAllChange(true)}
            >
              Todas as OS
            </button>
          </div>
          {!showAll && !hasSelectedConnections && (
            <small>Esta OS não possui dependências registradas.</small>
          )}
        </Panel>
      </ReactFlow>
    </div>
  );
}

function DependencyMessage({ text }: { text: string }) {
  return (
    <div className="dependency-message">
      <Network size={34} />
      <p>{text}</p>
    </div>
  );
}

export function connectedOrderNumbers(
  data: Pick<DependencyMap, "nodes" | "edges">,
  selectedOrder: string
): Set<string> {
  const visible = new Set([selectedOrder]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const edge of data.edges) {
      if (
        visible.has(edge.dependent_order_number) ||
        visible.has(edge.prerequisite_order_number)
      ) {
        const previousSize = visible.size;
        visible.add(edge.dependent_order_number);
        visible.add(edge.prerequisite_order_number);
        changed ||= visible.size !== previousSize;
      }
    }
  }
  return visible;
}

function layoutDependencies(
  data: DependencyMap | undefined,
  visibleNumbers: ReadonlySet<string>,
  selectedOrder: string,
  onSelectOrder: (number: string) => void
): { flowNodes: DependencyFlowNode[]; edges: Edge[] } {
  if (!data) return { flowNodes: [], edges: [] };
  const visibleNodes = data.nodes.filter((node) => visibleNumbers.has(node.number));
  const graph = new dagre.graphlib.Graph();
  graph.setDefaultEdgeLabel(() => ({}));
  graph.setGraph({
    rankdir: "LR",
    ranksep: 92,
    nodesep: 25,
    marginx: 32,
    marginy: 76
  });
  for (const order of visibleNodes) {
    graph.setNode(order.number, { width: 226, height: 112 });
  }
  const edges: Edge[] = [];
  for (const relation of data.edges) {
    if (
      !visibleNumbers.has(relation.dependent_order_number) ||
      !visibleNumbers.has(relation.prerequisite_order_number)
    ) {
      continue;
    }
    graph.setEdge(relation.dependent_order_number, relation.prerequisite_order_number);
    edges.push({
      id: `${relation.dependent_order_number}-depends-on-${relation.prerequisite_order_number}`,
      source: relation.dependent_order_number,
      target: relation.prerequisite_order_number,
      type: "smoothstep",
      markerEnd: { type: MarkerType.ArrowClosed, color: "#b54708" },
      style: { stroke: "#d46b16", strokeWidth: 1.8 }
    });
  }
  dagre.layout(graph);
  return {
    flowNodes: visibleNodes.map((order) => {
      const position = graph.node(order.number);
      return {
        id: order.number,
        type: "dependency",
        position: { x: position.x - 113, y: position.y - 56 },
        data: {
          order,
          selected: order.number === selectedOrder,
          onSelectOrder
        }
      };
    }),
    edges
  };
}

function statusClass(status: string | null): string {
  const normalized = (status ?? "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("pt-BR");
  if (normalized.includes("conclu")) return "completed";
  if (normalized.includes("produ") || normalized.includes("execu")) return "active";
  return "pending";
}
