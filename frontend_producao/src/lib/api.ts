import type {
  AssemblyStructure,
  ItemDetail,
  ItemLiveStatus,
  MaterialList,
  Observation,
  OrderDependencyMap,
  OrderSearchResult
} from "../types";

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
