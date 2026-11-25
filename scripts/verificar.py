#!/usr/bin/env python3
"""Verifica, de fora do Pi-hole, se as listas estao a bloquear o que deviam.

Pergunta directamente ao resolvedor (por omissao 192.168.88.5) e compara:
  - canarios de cada categoria devem devolver 0.0.0.0 (bloqueado)
  - o controlo negativo deve devolver um IP real (a casa continua a funcionar)

Uso:
    ./scripts/verificar.py
    ./scripts/verificar.py --servidor 192.168.88.5 --json
"""

from __future__ import annotations

import argparse
import json
import random
import socket
import struct
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def consultar(dominio: str, servidor: str, timeout: float = 5.0) -> list[str]:
    """Faz uma query A em UDP e devolve os IPs da secção de resposta.

    Lê os RRs a sério (nome com compressão, tipo, rdlength). Ler os últimos 4 bytes
    do pacote dava resultados falsos: com EDNS/OPT na secção adicional, o "IP" que
    saía era lixo — e um teste de bloqueio que mente é pior do que não existir.
    """
    ident = random.randrange(0, 65535)
    cabecalho = struct.pack(">HHHHHH", ident, 0x0100, 1, 0, 0, 0)
    pergunta = b"".join(bytes([len(p)]) + p.encode() for p in dominio.split(".")) + b"\x00"
    pacote = cabecalho + pergunta + struct.pack(">HH", 1, 1)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        s.sendto(pacote, (servidor, 53))
        dados, _ = s.recvfrom(4096)

    ancount = struct.unpack(">H", dados[6:8])[0]
    rcode = dados[3] & 0x0F
    if rcode != 0 and ancount == 0:
        return []

    def saltar_nome(off: int) -> int:
        while True:
            tam = dados[off]
            if tam == 0:
                return off + 1
            if tam & 0xC0 == 0xC0:          # ponteiro de compressão: 2 bytes e fim
                return off + 2
            off += 1 + tam

    off = saltar_nome(12) + 4               # pergunta: nome + qtype + qclass
    ips: list[str] = []
    for _ in range(ancount):
        off = saltar_nome(off)
        tipo, _classe, _ttl, rdlen = struct.unpack(">HHIH", dados[off:off + 10])
        off += 10
        if tipo == 1 and rdlen == 4:        # A
            ips.append(socket.inet_ntoa(dados[off:off + 4]))
        off += rdlen
    if not ips and ancount:
        return ["sem-registo-A"]
    return ips or ["sem-resposta"]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--servidor", default="192.168.88.5")
    p.add_argument("--json", action="store_true")
    a = p.parse_args()

    canarios = json.loads((RAIZ / "listas.json").read_text(encoding="utf-8"))["canarios"]
    relatorio: list[dict] = []
    falhas = 0

    for categoria, dominios in canarios.items():
        esperado_bloqueado = categoria != "controle_negativo"
        for d in dominios:
            try:
                ips = consultar(d, a.servidor)
                bloqueado = all(ip in ("0.0.0.0", "::") for ip in ips)
            except Exception as e:
                ips, bloqueado = [f"ERRO {e.__class__.__name__}"], None
            ok = (bloqueado == esperado_bloqueado) if bloqueado is not None else False
            falhas += 0 if ok else 1
            relatorio.append({"dominio": d, "categoria": categoria, "ips": ips,
                              "esperado": "bloqueado" if esperado_bloqueado else "resolvido",
                              "ok": ok})
            if not a.json:
                marca = "ok  " if ok else "FALHA"
                alvo = "0.0.0.0" if esperado_bloqueado else "IP real"
                print(f"{marca} {categoria:<17} {d:<26} -> {', '.join(ips):<28} (esperado {alvo})")

    if a.json:
        print(json.dumps({"servidor": a.servidor, "falhas": falhas, "resultados": relatorio},
                         ensure_ascii=False, indent=2))
    else:
        print(f"\n{falhas} falha(s) em {len(relatorio)} verificacoes"
              + ("" if falhas else " — tudo como esperado"))

    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
