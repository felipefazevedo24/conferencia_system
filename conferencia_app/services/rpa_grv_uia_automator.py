"""Controles determinísticos da M83 sobre o automator legado do GRV."""

from __future__ import annotations

import os
import time
from typing import Any


def _clipboard(text: str) -> None:
    """Usa a API Win32 sem criar PowerShell, CMD ou qualquer janela auxiliar."""
    if os.name != "nt":
        raise RuntimeError("O clipboard do RPA requer Windows.")
    import win32clipboard
    import win32con

    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(str(text), win32con.CF_UNICODETEXT)
    finally:
        win32clipboard.CloseClipboard()


def criar_automator_verificado(base_class: type[Any]) -> type[Any]:
    """Estende o executor existente mantendo sua descoberta de janelas e erros."""

    class AutomatorVerificado(base_class):
        def _form(self, hwnd: int):
            from pywinauto import Desktop

            root = Desktop(backend="win32").window(handle=hwnd)
            try:
                m83 = next(c for c in root.descendants() if c.class_name() == "TFMApontAgrupado")
            except StopIteration as exc:
                raise RuntimeError("A tela M83 não foi localizada dentro do CPS.") from exc
            self._active_m83_hwnd = hwnd
            return Desktop(backend="uia").window(handle=m83.handle)

        @staticmethod
        def _buttons(form) -> list[str]:
            return [b.window_text() for b in form.descendants(control_type="Button")]

        def novo_apontamento(self, hwnd: int) -> None:
            form = self._form(hwnd)
            if "Grava-F3" in self._buttons(form):
                next(b for b in form.descendants(control_type="Button") if b.window_text() == "Cancelar-F12").click_input()
                time.sleep(1)
                form = self._form(hwnd)
            try:
                next(b for b in form.descendants(control_type="Button") if b.window_text() == "Novo-F2").click_input()
            except StopIteration as exc:
                raise RuntimeError("O botão Novo-F2 da M83 não foi localizado.") from exc
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                time.sleep(0.25)
                if "Grava-F3" in self._buttons(self._form(hwnd)):
                    return
            raise RuntimeError("A M83 não entrou no modo de edição após Novo-F2.")

        def selecionar_status_liberado(self, hwnd: int) -> None:
            from pywinauto import mouse

            form = self._form(hwnd)
            status = next(c for c in form.descendants() if c.element_info.class_name == "TCPScxDBImageComboBox")
            rect = status.rectangle()
            abrir = next(
                (b for b in form.descendants(control_type="Button")
                 if b.window_text() == "Open" and abs(b.rectangle().top - rect.top) <= 5),
                None,
            )
            if abrir is None:
                raise RuntimeError("A lista de status da M83 não foi localizada.")
            abrir.click_input()
            time.sleep(0.4)
            mouse.click(coords=((rect.left + rect.right) // 2, rect.bottom + 22))
            time.sleep(max(0.7, self.delay * 2))
            value = str(status.iface_value.CurrentValue or "")
            if not value.casefold().startswith("liberado para produ"):
                raise RuntimeError(f"Status da M83 não selecionado corretamente: {value!r}.")

        def preencher_descricao(self, hwnd: int, descricao: str) -> None:
            from pywinauto import keyboard

            form = self._form(hwnd)
            edits = [c for c in form.descendants(control_type="Edit") if c.element_info.class_name == "TcxCustomInnerTextEdit"]
            if not edits:
                raise RuntimeError("Campo Descrição da M83 não localizado.")
            field = max(edits, key=lambda c: c.rectangle().width())
            field.click_input()
            keyboard.send_keys("^a{BACKSPACE}")
            _clipboard(descricao)
            keyboard.send_keys("^v")
            time.sleep(max(0.6, self.delay))
            if str(field.iface_value.CurrentValue or "").strip().casefold() != descricao.strip().casefold():
                raise RuntimeError("A descrição não foi preenchida integralmente na M83.")
            self._description_validated = True

        def selecionar_empresa_columbia(self, hwnd: int) -> None:
            from pywinauto import keyboard

            form = self._form(hwnd)
            combos = [c for c in form.descendants(control_type="ComboBox") if c.element_info.class_name == "TCPScxDBComboBox"]
            if not combos:
                raise RuntimeError("Campo Empresa para Saída da M83 não localizado.")
            field = min(combos, key=lambda c: c.rectangle().top)
            field.click_input()
            keyboard.send_keys("^a{BACKSPACE}")
            _clipboard("COLUMBIA MACHINE BRASIL")
            keyboard.send_keys("^v{ENTER}")
            time.sleep(max(0.6, self.delay))
            if str(field.iface_value.CurrentValue or "").strip().casefold() != "columbia machine brasil":
                raise RuntimeError("A empresa de saída não foi selecionada na M83.")

        def inserir_codigos_processos(self, hwnd: int, codigos: list[str]) -> None:
            from pywinauto import keyboard

            form = self._form(hwnd)
            grids = [c for c in form.descendants() if c.element_info.class_name == "TcxGridSite"]
            if not grids:
                raise RuntimeError("Grade de Processos Produtivos da M83 não localizada.")
            grid = max(grids, key=lambda c: c.rectangle().width())
            for code in codigos:
                grid.click_input(coords=(100, 98))
                _clipboard(code)
                keyboard.send_keys("^v{DOWN}")
                time.sleep(max(1.2, self.delay * 3))
            self._inserted_codes = list(codigos)

        def tecla(self, vk: int) -> None:
            if vk == self.VK_F3 and getattr(self, "_active_m83_hwnd", None):
                form = self._form(self._active_m83_hwnd)
                try:
                    next(b for b in form.descendants(control_type="Button") if b.window_text() == "Grava-F3").click_input()
                    return
                except StopIteration as exc:
                    raise RuntimeError("O botão Grava-F3 da M83 não foi localizado.") from exc
            super().tecla(vk)

        def executar_apontamento_agrupamento(self, payload, dry_run):
            self._description_validated = False
            self._inserted_codes = []
            result = super().executar_apontamento_agrupamento(payload, dry_run)
            if dry_run or result.get("gravado") is not True:
                return result
            if result.get("gravacao_confirmada") is True:
                return result
            if not getattr(self, "_active_m83_hwnd", None):
                return result
            form = self._form(self._active_m83_hwnd)
            buttons = self._buttons(form)
            ended = "Novo-F2" in buttons and "Grava-F3" not in buttons
            if not ended:
                raise RuntimeError("O F3 foi enviado, mas a M83 continuou em edição.")
            result.update(
                gravacao_confirmada=True,
                descricao_validada=self._description_validated,
                modo_edicao_encerrado=True,
                codigos_inseridos=list(self._inserted_codes),
            )
            return result

    return AutomatorVerificado
