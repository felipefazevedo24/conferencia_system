"""APK do app do motorista: guarda a versão publicada e a entrega ao app.

O app pergunta a versão ao abrir; se a publicada for maior que a instalada,
baixa e abre o instalador do Android (1 toque do motorista). A versão é lida
de dentro do próprio APK (AndroidManifest binário), nunca digitada: um número
errado faria o app pedir atualização para sempre.

O arquivo fica em instance/motorista_app (fora do git e do banco: um blob de
~50 MB no MySQL arrisca o max_allowed_packet).
"""
from __future__ import annotations

import json
import os
import struct
import zipfile

from flask import current_app

from ..tempo import agora_br

_ANDROID_VERSION_CODE = 0x0101021B
_ANDROID_VERSION_NAME = 0x0101021C


def _pasta() -> str:
    pasta = os.path.join(current_app.instance_path, "motorista_app")
    os.makedirs(pasta, exist_ok=True)
    return pasta


def caminho_apk() -> str:
    return os.path.join(_pasta(), "motorista_app.apk")


def _caminho_info() -> str:
    return os.path.join(_pasta(), "versao.json")


# ----------------------------------------------------------------------------- AndroidManifest binário
def _strings(buf: bytes, ini: int) -> list[str]:
    _, header_size, _, qtd, _, flags, inicio_str, _ = struct.unpack_from("<HHIIIIII", buf, ini)
    utf8 = bool(flags & 0x100)
    offsets = struct.unpack_from(f"<{qtd}I", buf, ini + header_size)
    base = ini + inicio_str
    saida = []
    for off in offsets:
        p = base + off
        if utf8:
            # comprimento em UTF-16 e depois em bytes, cada um com 1 ou 2 bytes
            p += 2 if buf[p] & 0x80 else 1
            n = buf[p]
            if n & 0x80:
                n = ((n & 0x7F) << 8) | buf[p + 1]
                p += 2
            else:
                p += 1
            saida.append(buf[p:p + n].decode("utf-8", errors="replace"))
        else:
            n = struct.unpack_from("<H", buf, p)[0]
            if n & 0x8000:
                n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", buf, p + 2)[0]
                p += 4
            else:
                p += 2
            saida.append(buf[p:p + n * 2].decode("utf-16-le", errors="replace"))
    return saida


def ler_versao_apk(caminho: str) -> tuple[int, str]:
    """(versionCode, versionName) do <manifest>. ValueError se não for APK."""
    try:
        with zipfile.ZipFile(caminho) as z:
            buf = z.read("AndroidManifest.xml")
    except (zipfile.BadZipFile, KeyError):
        raise ValueError("O arquivo não é um APK válido.")
    strings: list[str] = []
    recursos: list[int] = []
    p = 8  # cabeçalho do XML binário
    while p + 8 <= len(buf):
        tipo, header_size, tamanho = struct.unpack_from("<HHI", buf, p)
        if tamanho <= 0:
            break
        if tipo == 0x0001:
            strings = _strings(buf, p)
        elif tipo == 0x0180:
            recursos = list(struct.unpack_from(f"<{(tamanho - header_size) // 4}I", buf, p + header_size))
        elif tipo == 0x0102:  # primeiro elemento = <manifest>
            ext = p + header_size
            attr_ini, attr_tam, attr_qtd = struct.unpack_from("<HHH", buf, ext + 8)
            codigo, nome = None, ""
            for i in range(attr_qtd):
                a = ext + attr_ini + i * attr_tam
                _, nome_idx, bruto, _, _, tipo_dado, dado = struct.unpack_from("<IIIHBBI", buf, a)
                rid = recursos[nome_idx] if nome_idx < len(recursos) else None
                attr = strings[nome_idx] if nome_idx < len(strings) else ""
                if rid == _ANDROID_VERSION_CODE or attr == "versionCode":
                    codigo = dado
                elif rid == _ANDROID_VERSION_NAME or attr == "versionName":
                    idx = bruto if bruto != 0xFFFFFFFF else (dado if tipo_dado == 0x03 else None)
                    nome = strings[idx] if idx is not None and idx < len(strings) else ""
            if codigo is None:
                raise ValueError("Não encontrei a versão (versionCode) dentro do APK.")
            return int(codigo), nome
        p += tamanho
    raise ValueError("APK sem manifest legível.")


# ----------------------------------------------------------------------------- publicação
def versao_atual() -> dict | None:
    try:
        with open(_caminho_info(), encoding="utf-8") as f:
            info = json.load(f)
    except (FileNotFoundError, ValueError):
        return None
    return info if os.path.isfile(caminho_apk()) else None


def publicar_apk(arquivo, usuario: str) -> dict:
    """Grava o APK enviado se a versão for maior que a publicada."""
    temporario = caminho_apk() + ".novo"
    arquivo.save(temporario)
    try:
        codigo, nome = ler_versao_apk(temporario)
        atual = versao_atual()
        if atual and codigo <= int(atual["versao_codigo"]):
            raise ValueError(
                f"Esse APK é a versão {nome} (build {codigo}), e a publicada já é a "
                f"{atual['versao_nome']} (build {atual['versao_codigo']}). Aumente o build no pubspec.yaml."
            )
        os.replace(temporario, caminho_apk())
    except Exception:
        if os.path.exists(temporario):
            os.remove(temporario)
        raise
    info = {
        "versao_codigo": codigo,
        "versao_nome": nome,
        "tamanho": os.path.getsize(caminho_apk()),
        "publicado_por": usuario,
        "publicado_em": agora_br().isoformat(timespec="seconds"),
    }
    with open(_caminho_info(), "w", encoding="utf-8") as f:
        json.dump(info, f)
    return info

