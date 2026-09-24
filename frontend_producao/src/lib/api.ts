import type {
  AssemblyStructure,
  ItemDetail,
  ItemLiveStatus,
  MaterialList,
  Observation,
  OrderDependencyMap,
  OrderSearchResult
} from "../types";

export interface DeliveryOrder {
  numero: string;
  descricao: string;
  principal: boolean;
  status: string;
}

export interface ScheduledDelivery {
  orcamento: string;
  versao: string;
  cliente: string;
  descricao: string;
  classificacoes: string[];
  data_entrega: string | null;
  status: string;
  percentual: number | null;
  os: DeliveryOrder[];
}

export interface DeliveryStructureNode {
  id: string;
  aux_code: number;
  codigo: string;
  descricao: string;
  quantidade: number;
  parent_id: string | null;
  estado: "bloqueado" | "concluido" | "disponivel" | "montagem" | "fabricacao" | "nao_iniciado";
  estado_label: string;
  estado_motivo: string;
  operacoes_total: number;
  operacoes_concluidas: number;
  operacoes: Array<{ codigo: string; nome: string; sequencia: number | null; finalizada: boolean; travada: boolean; maquina: string; }>;
}

export interface DeliveryStructure { nos: DeliveryStructureNode[]; }

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "/api/v1";

class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers
    }
  });
  if (!response.ok) {
    let message = `Falha na comunicação (${response.status})`;
    try {
      const payload = (await response.json()) as { detail?: string };
      message = payload.detail ?? message;
    } catch {
      // Keep the safe generic message.
    }
    throw new ApiError(response.status, message);
  }
  return response.json() as Promise<T>;
}

export const api = {
  getScheduledDeliveries(month: number, year: number, classification = "", search = "", signal?: AbortSignal) {
    const params = new URLSearchParams({ mes: String(month), ano: String(year) });
    if (classification) params.set("classificacao", classification);
    if (search) params.set("pesquisa", search);
    return fetch(`/api/producao/cronograma-entregas?${params}`, { signal }).then(async (response) => {
      const payload = await response.json() as { entregas?: ScheduledDelivery[]; error?: string };
      if (!response.ok) throw new ApiError(response.status, payload.error ?? "Falha ao consultar o cronograma.");
      return payload.entregas ?? [];
    });
  },

  getDeliveryStructure(orderNumber: string, signal?: AbortSignal) {
    return fetch(`/api/producao/os/${encodeURIComponent(orderNumber)}`, { signal }).then(async (response) => {
      const payload = await response.json() as DeliveryStructure & { error?: string };
      if (!response.ok) throw new ApiError(response.status, payload.error ?? "Falha ao carregar a estrutura da OS.");
      return payload;
    });
  },

  searchOrders(query: string, signal?: AbortSignal) {
    return request<OrderSearchResult[]>(
      `/orders/search?q=${encodeURIComponent(query)}&limit=20`,
      { signal }
    );
  },

  getStructure(orderNumber: string, signal?: AbortSignal) {
    return request<AssemblyStructure>(
      `/orders/${encodeURIComponent(orderNumber)}/structure`,
      { signal }
    );
  },

  getOrderDependencies(orderNumber: string, signal?: AbortSignal) {
    return request<OrderDependencyMap>(
      `/orders/${encodeURIComponent(orderNumber)}/dependencies`,
      { signal }
    );
  },

  getItem(orderNumber: string, itemAuxCode: number, signal?: AbortSignal) {
    return request<ItemDetail>(
      `/orders/${encodeURIComponent(orderNumber)}/items/${itemAuxCode}`,
      { signal, cache: "no-store" }
    );
  },

  getItemLiveStatus(orderNumber: string, itemAuxCode: number, signal?: AbortSignal) {
    return request<ItemLiveStatus>(
      `/orders/${encodeURIComponent(orderNumber)}/items/${itemAuxCode}/operations/live`,
      { signal }
    );
  },

  getItemMaterials(orderNumber: string, itemAuxCode: number, signal?: AbortSignal) {
    return request<MaterialList>(
      `/orders/${encodeURIComponent(orderNumber)}/items/${itemAuxCode}/materials`,
      { signal }
    );
  },

  listObservations(orderNumber: string, itemAuxCode: number) {
    return request<Observation[]>(
      `/orders/${encodeURIComponent(orderNumber)}/items/${itemAuxCode}/observations`
    );
  },

  createObservation(orderNumber: string, itemAuxCode: number, text: string) {
    return request<Observation>(
      `/orders/${encodeURIComponent(orderNumber)}/items/${itemAuxCode}/observations`,
      {
        method: "POST",
        body: JSON.stringify({ text })
      }
    );
  },

  absoluteUrl(path: string | null) {
    if (!path) return null;
    if (path.startsWith("http")) return path;
    return `${window.location.origin}${path}`;
  }
};

export { ApiError };
