import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MessageSquareText, Send, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { api } from "../lib/api";
import { formatDate } from "../lib/format";

interface ObservationModalProps {
  orderNumber: string;
  itemAuxCode: number;
  itemCode: string;
  onClose: () => void;
}

export function ObservationModal({
  orderNumber,
  itemAuxCode,
  itemCode,
  onClose
}: ObservationModalProps) {
  const [text, setText] = useState("");
  const dialogRef = useRef<HTMLDialogElement>(null);
  const queryClient = useQueryClient();
  const queryKey = ["observations", orderNumber, itemAuxCode];
  const observations = useQuery({
    queryKey,
    queryFn: () => api.listObservations(orderNumber, itemAuxCode)
  });
  const create = useMutation({
    mutationFn: () => api.createObservation(orderNumber, itemAuxCode, text.trim()),
    onSuccess: async () => {
      setText("");
      await queryClient.invalidateQueries({ queryKey });
      await queryClient.invalidateQueries({
        queryKey: ["item", orderNumber, itemAuxCode]
      });
    }
  });

  useEffect(() => {
    dialogRef.current?.showModal();
  }, []);

  return (
    <dialog ref={dialogRef} className="observation-dialog" onClose={onClose}>
      <div className="dialog-header">
        <div>
          <MessageSquareText size={21} />
          <div>
            <h2>Observações</h2>
            <small>{itemCode}</small>
          </div>
        </div>
        <button type="button" className="icon-button" onClick={onClose} aria-label="Fechar">
          <X size={20} />
        </button>
      </div>
      <div className="observation-list">
        {observations.isLoading && <div className="empty-state">Carregando...</div>}
        {observations.data?.length === 0 && (
          <div className="empty-state">Nenhuma observação cadastrada.</div>
        )}
        {observations.data?.map((observation) => (
          <article key={observation.id}>
            <p>{observation.text}</p>
            <footer>
              <strong>{observation.author}</strong>
              <span>{formatDate(observation.created_at)}</span>
            </footer>
          </article>
        ))}
      </div>
      <form
        className="observation-form"
        onSubmit={(event) => {
          event.preventDefault();
          if (text.trim()) create.mutate();
        }}
      >
        <label htmlFor="observation-text">Nova observação</label>
        <textarea
          id="observation-text"
          value={text}
          onChange={(event) => setText(event.target.value)}
          maxLength={4000}
          rows={4}
          placeholder="Registre uma informação para a montagem..."
        />
        <div>
          <small>{text.length}/4000</small>
          <button
            className="primary-button"
            type="submit"
            disabled={!text.trim() || create.isPending}
          >
            <Send size={17} />
            {create.isPending ? "Salvando..." : "Salvar observação"}
          </button>
        </div>
        {create.isError && <p className="form-error">Não foi possível salvar.</p>}
      </form>
    </dialog>
  );
}

