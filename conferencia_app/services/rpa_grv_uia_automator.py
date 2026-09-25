"""Controles determinísticos e instrumentados da M83 sobre o automator legado."""

from __future__ import annotations

import logging
import os
import subprocess
import time
from typing import Any, Callable


_logger = logging.getLogger(__name__)


class _FatalRpaError(RuntimeError):
    """Erro funcional que não deve ser tratado como refresh transitório do UIA."""


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
    """Estende o executor existente sem alterar suas regras de negócio."""

    class AutomatorVerificado(base_class):
        POLL_INTERVAL = 0.10

        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.execution_id = "sem-id"
            self._timings_ms: dict[str, int] = {}
            self._cached_hwnd: int | None = None
            self._cached_form = None
            self._cached_controls: list[Any] | None = None
            self._active_m83_hwnd: int | None = None
            self._description_validated = False
            self._inserted_codes: list[str] = []

        def _timed(self, stage: str, action: Callable[[], Any]) -> Any:
            started = time.perf_counter()
            try:
                return action()
            finally:
                duration = round((time.perf_counter() - started) * 1000)
                self._timings_ms[stage] = self._timings_ms.get(stage, 0) + duration
                _logger.info(
                    "RPA_TIMING execution_id=%s stage=%s duration_ms=%s",
                    self.execution_id,
                    stage,
                    duration,
                )

        def _wait_until(
            self,
            condition: Callable[[], Any],
            *,
            timeout: float,
            message: str,
            poll_interval: float | None = None,
        ) -> Any:
            deadline = time.monotonic() + timeout
            last_error: Exception | None = None
            while time.monotonic() < deadline:
                try:
                    result = condition()
                    if result:
                        return result
                except Exception as exc:
                    if isinstance(exc, _FatalRpaError):
                        raise
                    last_error = exc
                time.sleep(poll_interval or self.POLL_INTERVAL)
            if last_error:
                _logger.debug("Ultima falha durante wait da M83: %s", last_error)
            raise RuntimeError(message)

        def _invalidate_controls(self) -> None:
            self._cached_controls = None

        def _form(self, hwnd: int, *, refresh: bool = False):
            from pywinauto import Desktop

            if not refresh and self._cached_hwnd == hwnd and self._cached_form is not None:
                try:
                    if self._cached_form.exists(timeout=0):
                        return self._cached_form
                except Exception:
                    pass
            root = Desktop(backend="win32").window(handle=hwnd)
            try:
                m83 = next(c for c in root.descendants() if c.class_name() == "TFMApontAgrupado")
            except StopIteration as exc:
                raise RuntimeError("A tela M83 não foi localizada dentro do CPS.") from exc
            self._active_m83_hwnd = hwnd
            self._cached_hwnd = hwnd
            self._cached_form = Desktop(backend="uia").window(handle=m83.handle)
            self._invalidate_controls()
            return self._cached_form

        def _controls(self, form, *, refresh: bool = False) -> list[Any]:
            if refresh or self._cached_controls is None:
                self._cached_controls = list(form.descendants())
            return self._cached_controls

        def _buttons(self, form, *, refresh: bool = False) -> list[Any]:
            controls = self._controls(form, refresh=refresh)
            return [c for c in controls if c.element_info.control_type == "Button"]

        @staticmethod
        def _button_texts(buttons: list[Any]) -> list[str]:
            return [button.window_text() for button in buttons]

        def _button_state_waiter(
            self,
            form,
            *,
            present: str,
            absent: str | None = None,
        ) -> Callable[[], bool]:
            """Reutiliza handles e faz uma única redescoberta se eles forem recriados."""
            buttons = self._buttons(form)
            refresh_at = time.monotonic() + 0.75
            refreshed = False

            def ready() -> bool:
                nonlocal buttons, refreshed
                try:
                    texts = self._button_texts(buttons)
                except Exception:
                    texts = []
                matched = present in texts and (absent is None or absent not in texts)
                if matched:
                    return True
                if not refreshed and time.monotonic() >= refresh_at:
                    buttons = self._buttons(form, refresh=True)
                    refreshed = True
                return False

            return ready

        @staticmethod
        def _value(control) -> str:
            return str(control.iface_value.CurrentValue or "").strip()

        def novo_apontamento(self, hwnd: int) -> None:
            def action() -> None:
                form = self._form(hwnd)
                buttons = self._buttons(form, refresh=True)
                if "Grava-F3" in self._button_texts(buttons):
                    next(b for b in buttons if b.window_text() == "Cancelar-F12").click_input()
                    self._wait_until(
                        self._button_state_waiter(form, present="Novo-F2"),
                        timeout=4,
                        message="A M83 não encerrou o lançamento anterior.",
                    )
                buttons = self._buttons(self._form(hwnd))
                try:
                    next(b for b in buttons if b.window_text() == "Novo-F2").click_input()
                except StopIteration as exc:
                    raise RuntimeError("O botão Novo-F2 da M83 não foi localizado.") from exc
                self._wait_until(
                    self._button_state_waiter(form, present="Grava-F3"),
                    timeout=8,
                    message="A M83 não entrou no modo de edição após Novo-F2.",
                )

            self._timed("novo_f2", action)

        def selecionar_status_liberado(self, hwnd: int) -> None:
            from pywinauto import mouse

            def action() -> None:
                form = self._form(hwnd)
                controls = self._controls(form)
                status = next(
                    c for c in controls
                    if c.element_info.class_name == "TCPScxDBImageComboBox"
                )
                if self._value(status).casefold().startswith("liberado para produ"):
                    return
                rect = status.rectangle()
                abrir = next(
                    (b for b in controls
                     if b.element_info.control_type == "Button"
                     and b.window_text() == "Open"
                     and abs(b.rectangle().top - rect.top) <= 5),
                    None,
                )
                if abrir is None:
                    raise RuntimeError("A lista de status da M83 não foi localizada.")
                abrir.click_input()
                time.sleep(0.15)
                mouse.click(coords=((rect.left + rect.right) // 2, rect.bottom + 22))
                self._wait_until(
                    lambda: self._value(status).casefold().startswith("liberado para produ"),
                    timeout=max(3.0, self.delay * 6),
                    message=f"Status da M83 não selecionado corretamente: {self._value(status)!r}.",
                )

            self._timed("status", action)

        def _campo_descricao(self, form, *, refresh: bool = False):
            """Localiza a Descrição no cabeçalho, fora dos editores da grade."""
            controls = self._controls(form, refresh=refresh)
            grid_tops = [
                c.rectangle().top
                for c in controls
                if c.element_info.class_name == "TcxGridSite"
            ]
            grid_top = min(grid_tops) if grid_tops else float("inf")
            edits = [
                c for c in controls
                if c.element_info.control_type == "Edit"
                and c.element_info.class_name == "TcxCustomInnerTextEdit"
                and c.rectangle().top < grid_top
                and c.rectangle().width() > 500
            ]
            if not edits:
                raise RuntimeError("Campo Descrição da M83 não localizado no cabeçalho.")
            return max(edits, key=lambda c: c.rectangle().width())

        def preencher_descricao(self, hwnd: int, descricao: str) -> None:
            from pywinauto import keyboard

            def action() -> None:
                form = self._form(hwnd)
                field = self._campo_descricao(form, refresh=True)
                expected = descricao.strip().casefold()
                if self._value(field).casefold() != expected:
                    field.click_input()
                    keyboard.send_keys("^a{BACKSPACE}")
                    _clipboard(descricao)
                    keyboard.send_keys("^v{TAB}")
                    self._wait_until(
                        lambda: self._value(
                            self._campo_descricao(self._form(hwnd), refresh=True)
                        ).casefold() == expected,
                        timeout=max(3.0, self.delay * 6),
                        message="A descrição não foi preenchida e confirmada na M83.",
                    )
                self._description_validated = True

            self._timed("descricao", action)

        def selecionar_empresa_columbia(self, hwnd: int) -> None:
            from pywinauto import keyboard

            def action() -> None:
                form = self._form(hwnd)
                combos = [
                    c for c in self._controls(form)
                    if c.element_info.control_type == "ComboBox"
                    and c.element_info.class_name == "TCPScxDBComboBox"
                ]
                if not combos:
                    raise RuntimeError("Campo Empresa para Saída da M83 não localizado.")
                field = min(combos, key=lambda c: c.rectangle().top)
                expected = "columbia machine brasil"
                if self._value(field).casefold() == expected:
                    return
                selected = False
                try:
                    field.select("COLUMBIA MACHINE BRASIL")
                    selected = True
                except Exception:
                    pass
                if selected:
                    try:
                        self._wait_until(
                            lambda: self._value(field).casefold() == expected,
                            timeout=0.6,
                            message="Select não confirmou a empresa.",
                        )
                    except RuntimeError:
                        selected = False
                if not selected:
                    field.click_input()
                    keyboard.send_keys("^a{BACKSPACE}")
                    _clipboard("COLUMBIA MACHINE BRASIL")
                    keyboard.send_keys("^v{ENTER}")
                    self._wait_until(
                        lambda: self._value(field).casefold() == expected,
                        timeout=max(3.0, self.delay * 6),
                        message="A empresa de saída não foi selecionada na M83.",
                    )

            self._timed("empresa", action)

        @staticmethod
        def _inside(inner, outer) -> bool:
            return (
                inner.left >= outer.left and inner.right <= outer.right
                and inner.top >= outer.top and inner.bottom <= outer.bottom
            )

        def _grid_editor_committed(self, form, grid, code: str) -> bool | None:
            """Confirma o commit quando o editor da grade é exposto pelo UIA."""
            grid_rect = grid.rectangle()
            editors = []
            for control in form.descendants(control_type="Edit"):
                try:
                    if self._inside(control.rectangle(), grid_rect):
                        editors.append(control)
                except Exception:
                    continue
            if not editors:
                return None
            return all(self._value(editor) != code for editor in editors)

        def inserir_codigos_processos(self, hwnd: int, codigos: list[str]) -> None:
            from pywinauto import keyboard

            form = self._form(hwnd)
            grids = [
                c for c in self._controls(form)
                if c.element_info.class_name == "TcxGridSite"
            ]
            if not grids:
                raise RuntimeError("Grade de Processos Produtivos da M83 não localizada.")
            grid = max(grids, key=lambda c: c.rectangle().width())
            for code in codigos:
                def insert(code=code) -> None:
                    grid.click_input(coords=(100, 98))
                    _clipboard(code)
                    keyboard.send_keys("^v{DOWN}")
                    time.sleep(0.12)
                    commit_exposed = self._grid_editor_committed(form, grid, code)
                    if commit_exposed is None:
                        time.sleep(max(1.2, self.delay * 3) - 0.12)
                        return
                    stable_observations = 0

                    def committed_twice() -> bool:
                        nonlocal stable_observations
                        if self._grid_editor_committed(form, grid, code):
                            stable_observations += 1
                        else:
                            stable_observations = 0
                        return stable_observations >= 2

                    self._wait_until(
                        committed_twice,
                        timeout=max(4.0, self.delay * 8),
                        message=f"O processo {code} não foi confirmado na grade da M83.",
                    )

                self._timed(f"codigo_{code}", insert)
                self._inserted_codes.append(code)

        def _validar_antes_de_gravar(self, hwnd: int, descricao: str) -> None:
            form = self._form(hwnd)
            field = self._campo_descricao(form, refresh=True)
            if self._value(field).casefold() != descricao.casefold():
                raise RuntimeError(
                    "A descrição não foi preenchida integralmente na M83; gravação cancelada."
                )
            if "Grava-F3" not in self._button_texts(self._buttons(form)):
                raise RuntimeError("A M83 não permaneceu no modo de edição antes da gravação.")

        def _gravar_e_aguardar(self, hwnd: int) -> None:
            form = self._form(hwnd)
            buttons = self._buttons(form)
            try:
                next(b for b in buttons if b.window_text() == "Grava-F3").click_input()
            except StopIteration as exc:
                raise RuntimeError("O botão Grava-F3 da M83 não foi localizado.") from exc

            last_error_check = 0.0
            button_ready = self._button_state_waiter(
                form, present="Novo-F2", absent="Grava-F3"
            )

            def ended() -> bool:
                nonlocal last_error_check
                now = time.monotonic()
                if now - last_error_check >= 0.5:
                    last_error_check = now
                    error = self.encontrar_erro_visivel_cps()
                    if error:
                        raise _FatalRpaError(
                            f"O GRV recusou a gravação e exibiu a janela: {error}. "
                            "Revise os campos da tela M83."
                        )
                return button_ready()

            self._wait_until(
                ended,
                timeout=max(10.0, self.delay * 20),
                message="O comando Grava-F3 foi enviado, mas a M83 continuou em edição.",
                poll_interval=0.15,
            )

        def executar_apontamento_agrupamento(self, payload, dry_run):
            required_backend_methods = (
                "encontrar_janela",
                "ativar_janela",
                "iniciar_monitor_tela_cheia",
                "garantir_janela_tela_cheia",
            )
            if (
                dry_run
                or os.name != "nt"
                or not all(hasattr(self, name) for name in required_backend_methods)
            ):
                return super().executar_apontamento_agrupamento(payload, dry_run)

            started = time.perf_counter()
            self._timings_ms = {}
            self._description_validated = False
            self._inserted_codes = []
            codigos = [str(code).strip() for code in payload["codigos_destacados_para_agrupamento"]]
            descricao = str(payload.get("descricao_agrupamento") or "").strip()
            if not descricao:
                raise ValueError("Descrição do agrupamento não informada.")

            monitor_stop = monitor_thread = None
            hwnd = None
            result: dict[str, Any] | None = None
            try:
                if self.executable:
                    subprocess.Popen([self.executable])
                title = self.window_title or r".*CPS.*COLUMBIA.*"
                hwnd = self._timed("localizar_janela", lambda: self.encontrar_janela(title))
                if not hwnd:
                    raise RuntimeError("Janela do GRV/CPS não encontrada. Abra a M83 e tente novamente.")
                self._timed("ativar_janela", lambda: self.ativar_janela(hwnd))
                monitor_stop, monitor_thread = self.iniciar_monitor_tela_cheia(hwnd)

                self.novo_apontamento(hwnd)
                self.selecionar_status_liberado(hwnd)
                self.selecionar_empresa_columbia(hwnd)
                self.inserir_codigos_processos(hwnd, codigos)
                # A grade DevExpress recria editores durante a inclusão dos
                # processos. Preenche a descrição por último para que o valor
                # seja confirmado no cabeçalho e não se perca ao mudar o foco.
                self.preencher_descricao(hwnd, descricao)
                self._timed(
                    "validacao_pre_gravacao",
                    lambda: self._validar_antes_de_gravar(hwnd, descricao),
                )
                if not self.gravar_sem_confirmar and not self.confirmar_gravacao():
                    return {
                        "dry_run": False,
                        "gravado": False,
                        "cancelado_antes_gravar": True,
                        "quantidade_codigos": len(codigos),
                    }
                self._timed("grava_f3", lambda: self._gravar_e_aguardar(hwnd))
                result = {
                    "dry_run": False,
                    "gravado": True,
                    "gravacao_confirmada": True,
                    "comando_gravar_enviado": True,
                    "descricao_validada": self._description_validated,
                    "modo_edicao_encerrado": True,
                    "codigos_inseridos": list(self._inserted_codes),
                    "cancelado_antes_gravar": False,
                    "quantidade_codigos": len(codigos),
                }
            finally:
                if monitor_stop is not None:
                    monitor_stop.set()
                if monitor_thread is not None:
                    monitor_thread.join(timeout=1)
                if hwnd:
                    self.garantir_janela_tela_cheia(hwnd)
                total = round((time.perf_counter() - started) * 1000)
                self._timings_ms["total"] = total
                summary = " ".join(
                    f"{stage}={duration}ms" for stage, duration in self._timings_ms.items()
                )
                _logger.warning(
                    "RPA_TIMING execution_id=%s quantidade_codigos=%s %s",
                    self.execution_id,
                    len(codigos),
                    summary,
                )
            if result is None:
                raise RuntimeError("A execução da M83 terminou sem resultado.")
            result["timings_ms"] = dict(self._timings_ms)
            return result

        def tecla(self, vk: int) -> None:
            if vk == self.VK_F3 and self._active_m83_hwnd:
                form = self._form(self._active_m83_hwnd)
                try:
                    next(
                        b for b in self._buttons(form)
                        if b.window_text() == "Grava-F3"
                    ).click_input()
                    return
                except StopIteration as exc:
                    raise RuntimeError("O botão Grava-F3 da M83 não foi localizado.") from exc
            super().tecla(vk)

    return AutomatorVerificado
