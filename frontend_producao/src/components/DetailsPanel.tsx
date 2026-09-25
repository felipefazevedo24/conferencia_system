import { useQuery } from "@tanstack/react-query";
import {
  Box,
  Check,
  ChevronRight,
  Copy,
  ExternalLink,
  FileText,
  Package,
  Route
} from "lucide-react";
import { useEffect, useState } from "react";

import { api, type CpmActivity } from "../lib/api";
import { formatBytes, formatQuantity } from "../lib/format";
import type {
  AssemblyNode,
  ItemDetail,
  MaterialConsumptionStatus,
  MaterialList
} from "../types";
import { StatusBadge } from "./StatusBadge";

interface DetailsPanelProps {
  orderNumber: string;
  selectedNode: AssemblyNode | null;
  onLocate: (node: AssemblyNode) => void;
  cpmActivities?: CpmActivity[];
}

const EMPTY_CPM_ACTIVITIES: CpmActivity[] = [];

type DetailsTab = "details" | "materials";

export function DetailsPanel({ orderNumber, selectedNode, onLocate, cpmActivities = EMPTY_CPM_ACTIVITIES }: DetailsPanelProps) {
  const [copied, setCopied] = useState(false);
  const [activeTab, setActiveTab] = useState<DetailsTab>("details");
  const detail = useQuery({
    queryKey: ["item", orderNumber, selectedNode?.aux_code],
    queryFn: ({ signal }) => api.getItem(orderNumber, selectedNode!.aux_code, signal),
    enabled: Boolean(orderNumber && selectedNode),
    staleTime: 0,
    refetchInterval: 2_000,
    refetchIntervalInBackground: true
  });
  const materials = useQuery({
    queryKey: ["item-materials", orderNumber, selectedNode?.aux_code],
    queryFn: ({ signal }) => api.getItemMaterials(orderNumber, selectedNode!.aux_code, signal),
    enabled: Boolean(orderNumber && selectedNode),
    staleTime: 60_000,
    refetchInterval: false
  });

  if (!selectedNode) {
    return (
      <aside id="details-panel" className="details-panel panel">
        <div className="panel-title"><h2>Detalhes da Peça</h2></div>
        <div className="empty-state details-empty">
          <Box size={34} />
          <p>Selecione um item na árvore ou no mapa.</p>
        </div>
      </aside>
    );
  }

  const data = detail.data;
  return (
    <aside id="details-panel" className="details-panel panel">
      <div className="panel-title">
        <h2>Detalhes da Peça</h2>
        <span className="source-label">{data?.information_origin ?? "GRV"}</span>
      </div>

      <div className="detail-tabs" role="tablist" aria-label="Informações da peça">
        <TabButton id="details" active={activeTab} onSelect={setActiveTab}>Detalhes</TabButton>
        <TabButton id="materials" active={activeTab} onSelect={setActiveTab}>
          Lista de Materiais
        </TabButton>
      </div>

      {detail.isLoading && activeTab !== "materials" && <DetailsSkeleton />}
      {detail.isError && activeTab !== "materials" && (
        <QueryError message="Não foi possível carregar os detalhes." onRetry={detail.refetch} />
      )}
      {activeTab === "details" && data && (
        <DetailsTabContent
          data={data}
          materialUsed={materials.data?.material_used}
          materialLoading={materials.isLoading}
          materialError={materials.isError}
          copied={copied}
          onCopied={setCopied}
          onLocate={onLocate}
          cpmActivities={cpmActivities.filter((activity) => activity.metadata.component_code === selectedNode.code)}
        />
      )}
      {activeTab === "materials" && (
        <MaterialsTab
          data={materials.data}
          isLoading={materials.isLoading}
          isError={materials.isError}
          onRetry={materials.refetch}
        />
      )}
    </aside>
  );
}

function TabButton({ id, active, onSelect, children }: {
  id: DetailsTab;
  active: DetailsTab;
  onSelect: (tab: DetailsTab) => void;
  children: string;
}) {
  const selected = id === active;
  return (
    <button
      type="button"
      role="tab"
      aria-selected={selected}
      aria-controls={`detail-tab-${id}`}
      className={selected ? "active" : ""}
      onClick={() => onSelect(id)}
    >{children}</button>
  );
}

function DetailsTabContent({
  data,
  materialUsed,
  materialLoading,
  materialError,
  copied,
  onCopied,
  onLocate,
  cpmActivities
}: {
  data: ItemDetail;
  materialUsed: boolean | undefined;
  materialLoading: boolean;
  materialError: boolean;
  copied: boolean;
  onCopied: (copied: boolean) => void;
  onLocate: (node: AssemblyNode) => void;
  cpmActivities: CpmActivity[];
}) {
  const allDocuments = [...data.drawings, ...data.documents];
  const primaryDocument = allDocuments.find((document) => document.is_primary)
    ?? data.drawings[0]
    ?? data.documents[0];
  const orderedDocuments = primaryDocument
    ? [primaryDocument, ...allDocuments.filter((document) => document !== primaryDocument)]
    : allDocuments;
  const previewUrl = versionThumbnailUrl(data.node.thumbnail_url, primaryDocument);
  return (
    <div className="details-scroll" id="detail-tab-details" role="tabpanel">
      <PartPreview
        src={previewUrl}
        description={data.node.description}
      />
      <div className="detail-block detail-grid">
        <DetailRow label="Código" value={data.node.code} />
        <DetailRow label="Descrição" value={data.node.description} />
        <DetailRow label="Desenho" value={data.node.drawing_number ?? "—"} />
        <DetailRow label="Quantidade necessária" value={formatQuantity(data.node.quantity)} />
        <div className="detail-row">
          <span>Status calculado</span>
          <StatusBadge state={data.node.state} reason={data.node.state_reason} />
        </div>
        <DetailRow label="Motivo do status" value={data.node.state_reason} />
        <DetailRow label="Status no GRV" value={data.node.source_status ?? "—"} />
        <DetailRow label="Status CPM" value={cpmActivities.find((activity) => activity.is_critical && !activity.completed) ? "CRÍTICO" : cpmActivities[0]?.status.replaceAll("_", " ") ?? "Sem cálculo"} />
        <DetailRow label="Folga CPM" value={formatCpmFloat(cpmActivities)} />
        <DetailRow label="Próximo processo" value={cpmActivities.find((activity) => !activity.completed)?.name ?? "—"} />
        <DetailRow label="Impacto na entrega" value={cpmActivities.find((activity) => activity.is_critical && !activity.completed)?.critical_reason ?? "Sem impacto crítico calculado"} />
        <div className="detail-row material-used-row">
          <span>Material utilizado</span>
          <strong aria-label={
            materialError
              ? "Consumo de material indisponível"
              : materialUsed
                ? "Material utilizado"
                : "Sem consumo registrado"
          }>
            {materialLoading ? "…" : materialUsed ? <Check size={18} /> : "—"}
          </strong>
        </div>
      </div>
      <div className="detail-block">
        <h3><Route size={17} /> Caminho hierárquico</h3>
        <div className="breadcrumb">
          {data.path.map((item, index) => (
            <span key={item.id}>
              {index > 0 && <ChevronRight size={13} />}
              <button type="button" onClick={() => onLocate(item)}>{item.code}</button>
            </span>
          ))}
        </div>
        <DetailRow label="Item pai" value={data.parent?.code ?? "Raiz da OS"} />
        <DetailRow
          label="Predecessoras"
          value={data.predecessors.length ? data.predecessors.map((item) => item.code).join(", ") : "—"}
        />
      </div>
      <div className="detail-block">
        <h3><FileText size={17} /> Documentos</h3>
        {orderedDocuments.map((drawing) => (
          <div className="drawing-row" key={`${drawing.kind}-${drawing.open_url}`}>
            <div><strong>{drawing.filename}</strong><small>{formatBytes(drawing.size_bytes)}</small></div>
            <button
              type="button"
              onClick={() => window.open(drawing.open_url, "_blank", "noopener,noreferrer")}
              aria-label={`Abrir ${drawing.filename}`}
            ><ExternalLink size={16} /></button>
          </div>
        ))}
        {data.drawings.length + data.documents.length === 0 && (
          <div className="empty-inline">Nenhum desenho ou anexo disponível.</div>
        )}
        <DetailRow label="Origem" value={data.information_origin} />
        <DetailRow label="Arquivo" value={data.document_path ?? "—"} />
      </div>
      <div className="detail-actions">
        <button
          className="primary-button"
          type="button"
          disabled={!primaryDocument}
          onClick={() => primaryDocument && window.open(primaryDocument.open_url, "_blank", "noopener,noreferrer")}
        ><FileText size={18} /> Abrir desenho</button>
        <button
          type="button"
          onClick={async () => {
            await navigator.clipboard.writeText(data.node.code);
            onCopied(true);
            window.setTimeout(() => onCopied(false), 1500);
          }}
        >
          {copied ? <Check size={18} /> : <Copy size={18} />}
          {copied ? "Código copiado" : "Copiar código"}
        </button>
      </div>
    </div>
  );
}

function formatCpmFloat(activities: CpmActivity[]) {
  if (!activities.length) return "—";
  const value = Math.min(...activities.map((activity) => activity.total_float_minutes));
  const absolute = Math.abs(value);
  return `${value < 0 ? "-" : ""}${Math.floor(absolute / 60)}h${absolute % 60 ? ` ${absolute % 60}min` : ""}`;
}

function versionThumbnailUrl(
  thumbnailUrl: string | null,
  document: ItemDetail["drawings"][number] | undefined
) {
  if (!thumbnailUrl || !document) return thumbnailUrl;
  const revision = [
    document.kind,
    document.id,
    document.content_revision ?? "unknown",
    document.size_bytes,
    document.filename
  ].join(":");
  const separator = thumbnailUrl.includes("?") ? "&" : "?";
  return `${thumbnailUrl}${separator}document=${encodeURIComponent(revision)}`;
}

function PartPreview({ src, description }: {
  src: string | null;
  description: string;
}) {
  const [failedSrc, setFailedSrc] = useState<string | null>(null);

  useEffect(() => {
    if (src !== failedSrc) setFailedSrc(null);
  }, [src, failedSrc]);

  if (!src || failedSrc === src) {
    return (
      <div className="detail-preview" role="img" aria-label="Pré-visualização indisponível">
        <Box size={56} />
      </div>
    );
  }

  return (
    <div
      className="detail-preview interactive"
      onPointerMove={movePreview}
      onPointerLeave={resetPreview}
      onPointerDown={startPreviewDrag}
      onPointerUp={stopPreviewDrag}
      onPointerCancel={stopPreviewDrag}
      title="Mova o cursor sobre a imagem para visualizar em perspectiva"
    >
      <div className="detail-preview-motion">
        <img
          src={src}
          alt={`Vista de ${description}`}
          draggable={false}
          onError={() => setFailedSrc(src)}
        />
      </div>
    </div>
  );
}

function movePreview(event: React.PointerEvent<HTMLDivElement>) {
  if (event.pointerType === "touch") return;
  const bounds = event.currentTarget.getBoundingClientRect();
  const horizontal = Math.min(1, Math.max(0, (event.clientX - bounds.left) / bounds.width));
  const vertical = Math.min(1, Math.max(0, (event.clientY - bounds.top) / bounds.height));
  event.currentTarget.style.setProperty("--preview-rotate-x", `${(0.5 - vertical) * 10}deg`);
  event.currentTarget.style.setProperty("--preview-rotate-y", `${(horizontal - 0.5) * 14}deg`);
  event.currentTarget.style.setProperty("--preview-pointer-x", `${horizontal * 100}%`);
  event.currentTarget.style.setProperty("--preview-pointer-y", `${vertical * 100}%`);
}

function resetPreview(event: React.PointerEvent<HTMLDivElement>) {
  event.currentTarget.classList.remove("is-dragging");
  event.currentTarget.style.setProperty("--preview-rotate-x", "0deg");
  event.currentTarget.style.setProperty("--preview-rotate-y", "0deg");
  event.currentTarget.style.setProperty("--preview-pointer-x", "50%");
  event.currentTarget.style.setProperty("--preview-pointer-y", "50%");
}

function startPreviewDrag(event: React.PointerEvent<HTMLDivElement>) {
  if (event.pointerType === "touch") return;
  if (event.pointerType === "mouse" && event.button !== 0) return;
  event.currentTarget.classList.add("is-dragging");
  event.currentTarget.setPointerCapture(event.pointerId);
}

function stopPreviewDrag(event: React.PointerEvent<HTMLDivElement>) {
  event.currentTarget.classList.remove("is-dragging");
  if (event.currentTarget.hasPointerCapture(event.pointerId)) {
    event.currentTarget.releasePointerCapture(event.pointerId);
  }
}

function MaterialsTab({ data, isLoading, isError, onRetry }: {
  data: MaterialList | undefined;
  isLoading: boolean;
  isError: boolean;
  onRetry: () => unknown;
}) {
  if (isLoading) return <DetailsSkeleton />;
  if (isError) return <QueryError message="Não foi possível carregar a lista de materiais." onRetry={onRetry} />;
  if (!data || data.materials.length === 0) {
    return (
      <div className="empty-state details-empty" id="detail-tab-materials" role="tabpanel">
        <Package size={34} /><p>Nenhum material encontrado para esta OS/Sub-OS.</p>
      </div>
    );
  }
  return (
    <div className="details-scroll detail-tab-content" id="detail-tab-materials" role="tabpanel">
      <div className="detail-tab-heading"><strong>Lista de Materiais</strong><small>{data.item_code}</small></div>
      {!data.material_used && (
        <div className="materials-notice">Materiais previstos encontrados, porém ainda sem consumo registrado.</div>
      )}
      <div className="material-list">
        {data.materials.map((material) => (
          <article className="material-card" key={material.id}>
            <header>
              <div><strong>{material.code}</strong><small>{material.item_code}</small></div>
              <ConsumptionBadge status={material.consumption_status} />
            </header>
            <p>{material.description}</p>
            <div className="material-quantities">
              <Quantity label="Previsto" value={material.required_quantity} unit={material.unit} />
              <Quantity label="Consumido" value={material.consumed_quantity} unit={material.unit} />
              <Quantity label="Restante" value={material.remaining_quantity} unit={material.unit} />
              <Quantity label="Disponível" value={material.available_quantity} unit={material.unit} />
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}

function ConsumptionBadge({ status }: { status: MaterialConsumptionStatus }) {
  const labels: Record<MaterialConsumptionStatus, string> = {
    not_used: "Não utilizado",
    partial: "Parcial",
    consumed: "Consumido"
  };
  return (
    <span className={`consumption-badge ${status}`}>
      {status !== "not_used" && <Check size={13} />}{labels[status]}
    </span>
  );
}

function Quantity({ label, value, unit }: { label: string; value: number | null; unit: string | null }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value === null ? "—" : `${formatQuantity(value)} ${unit ?? ""}`}</strong>
    </div>
  );
}

function QueryError({ message, onRetry }: { message: string; onRetry: () => unknown }) {
  return (
    <div className="empty-state details-empty">
      <p>{message}</p><button type="button" onClick={() => onRetry()}>Tentar novamente</button>
    </div>
  );
}

function DetailRow({ label, value }: { label: string; value: string }) {
  return <div className="detail-row"><span>{label}</span><strong>{value}</strong></div>;
}

function DetailsSkeleton() {
  return (
    <div className="details-skeleton" aria-label="Carregando detalhes">
      <div />{Array.from({ length: 8 }, (_, index) => <span key={index} />)}
    </div>
  );
}
