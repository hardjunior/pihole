#!/usr/bin/env python3
"""Sincroniza as listas de bloqueio deste repositorio com um Pi-hole v6.

O Pi-hole e a fonte de verdade do que esta activo; este script apenas garante
que o que esta em listas.json la existe (idempotente: correr duas vezes nao
duplica nada).

Uso:
    ./scripts/aplicar.py                      # perfil "familia", aplica e corre gravity
    ./scripts/aplicar.py --ensaio             # mostra o que faria, nao toca em nada
    ./scripts/aplicar.py --perfil familia-max
    ./scripts/aplicar.py --limpar             # remove listas HaGeZi que nao estao no perfil
    ./scripts/aplicar.py --sem-gravity        # subscreve mas nao reconstroi a base
    ./scripts/aplicar.py --dados              # mede as entradas de cada lista e escreve docs/dados.json

A password nunca e escrita no ecra: vem de $PIHOLE_PW, do ficheiro indicado em
--cofre (por omissao o cofre desta casa) ou e pedida interativamente.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
COFRE = Path.home() / ".openclaw/workspace/backups/credenciais/pihole-credentials.txt"
MARCA = "pihole-familia"          # prefixo do comentario das nossas listas
GRAVITY_ESPERA_MAX = 900          # segundos


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def _ctx() -> ssl.SSLContext:
    return ssl.create_default_context()


class Pihole:
    def __init__(self, base: str, password: str) -> None:
        self.base = base.rstrip("/")
        self.sid = ""
        self._login(password)

    def _login(self, password: str) -> None:
        corpo = json.dumps({"password": password}).encode()
        req = urllib.request.Request(
            self.base + "/api/auth", data=corpo,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=15, context=_ctx()) as r:
            dados = json.load(r)
        self.sid = dados["session"]["sid"]
        if not dados["session"].get("valid"):
            raise SystemExit("autenticacao recusada pelo Pi-hole")

    def pedir(self, metodo: str, caminho: str, corpo: dict | None = None) -> tuple[int, dict]:
        dados = json.dumps(corpo).encode() if corpo is not None else None
        req = urllib.request.Request(
            self.base + caminho, data=dados, method=metodo,
            headers={"Content-Type": "application/json", "X-FTL-SID": self.sid})
        try:
            with urllib.request.urlopen(req, timeout=120, context=_ctx()) as r:
                texto = r.read().decode("utf-8", "replace")
                if not texto.strip():
                    return r.status, {}          # 204 / corpo vazio (ex.: /api/action/gravity)
                try:
                    return r.status, json.loads(texto)
                except json.JSONDecodeError:
                    return r.status, {"bruto": texto[:400]}
        except urllib.error.HTTPError as e:
            texto = e.read().decode("utf-8", "replace")
            try:
                return e.code, json.loads(texto)
            except json.JSONDecodeError:
                return e.code, {"bruto": texto[:400]}
    def listas(self) -> list[dict]:
        codigo, dados = self.pedir("GET", "/api/lists")
        if codigo != 200:
            raise SystemExit(f"GET /api/lists devolveu {codigo}: {dados}")
        return dados.get("lists", [])

    def subscrever(self, url: str, comentario: str) -> tuple[int, dict]:
        # O tipo vai em query string (?type=block); no POST o schema não aceita "type" no corpo.
        return self.pedir("POST", "/api/lists?type=block", {
            "address": url, "comment": comentario,
            "enabled": True, "groups": [0],
        })

    def remover(self, url: str) -> tuple[int, dict]:
        return self.pedir("DELETE", "/api/lists/" + urllib.parse.quote(url, safe=""))

    def gravity(self) -> tuple[int, dict]:
        return self.pedir("POST", "/api/action/gravity")

    def sair(self) -> None:
        try:
            self.pedir("DELETE", "/api/auth")
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
def ler_password(cofre: Path) -> str:
    if os.environ.get("PIHOLE_PW"):
        return os.environ["PIHOLE_PW"]
    if cofre and cofre.exists():
        for linha in cofre.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^PIHOLE_ADMIN_PW=(.*)$", linha.strip())
            if m:
                return m.group(1)
    import getpass
    return getpass.getpass("Password de admin do Pi-hole: ")


def carregar_perfil(nome: str) -> dict:
    dados = json.loads((RAIZ / "listas.json").read_text(encoding="utf-8"))
    perfis = dados["perfis"]
    if nome not in perfis:
        raise SystemExit(f"perfil desconhecido: {nome} (existem: {', '.join(perfis)})")
    perfil = dict(perfis[nome])
    perfil["nome"] = nome
    perfil["base_url"] = dados["base_url"]
    return perfil


def comentario(lista: dict, perfil: str) -> str:
    return f"{MARCA}:{perfil} [{lista['categoria']}] {lista['nome']}"


# --------------------------------------------------------------------------- #
# Accoes
# --------------------------------------------------------------------------- #
def aplicar(ph: Pihole, perfil: dict, ensaio: bool, limpar: bool) -> int:
    existentes = {l["address"]: l for l in ph.listas()}
    desejadas = perfil["listas"]
    faltam = [l for l in desejadas if l["url"] not in existentes]
    sobram = [
        l for l in existentes.values()
        if l["address"].startswith(perfil["base_url"]) and l["address"] not in {d["url"] for d in desejadas}
    ]

    print(f"perfil: {perfil['nome']}  ({len(desejadas)} listas)")
    print(f"ja subscritas: {len(desejadas) - len(faltam)}   a subscrever: {len(faltam)}")

    if ensaio:
        for l in faltam:
            print(f"  + {l['id']:<26} {l['url']}")
        if limpar:
            for l in sobram:
                print(f"  - {l['address']}")
        print("ensaio: nada foi alterado")
        return 0

    falhas = 0
    for l in faltam:
        codigo, resp = ph.subscrever(l["url"], comentario(l, perfil["nome"]))
        ok = codigo in (200, 201)
        print(f"  {'+' if ok else 'x'} {l['id']:<26} HTTP {codigo}" + ("" if ok else f"  {resp}"))
        falhas += 0 if ok else 1

    if limpar:
        for l in sobram:
            codigo, resp = ph.remover(l["address"])
            ok = codigo in (200, 204)
            print(f"  {'-' if ok else 'x'} {l['address']} HTTP {codigo}" + ("" if ok else f"  {resp}"))
            falhas += 0 if ok else 1
    elif sobram:
        print(f"  nota: {len(sobram)} lista(s) HaGeZi fora do perfil continuam activas "
              f"(usar --limpar para as remover)")

    return 1 if falhas else 0


def correr_gravity(ph: Pihole) -> int:
    codigo, resp = ph.gravity()
    print(f"gravity: HTTP {codigo} {resp.get('status', '') if isinstance(resp, dict) else ''}")
    if codigo not in (200, 202):
        return 1

    inicio = time.time()
    while time.time() - inicio < GRAVITY_ESPERA_MAX:
        time.sleep(10)
        try:
            listas = ph.listas()
        except Exception as e:                      # container ocupado a importar
            print(f"  ... Pi-hole sem resposta ({e.__class__.__name__}), a esperar")
            continue
        # status: 1 = actualizada, 2 = inalterada desde a ultima gravidade (normal numa
        # repeticao), >=3 = falha de descarga/parsing. So 1 e 2 contam como prontas.
        prontas = [l for l in listas if l.get("status") in (1, 2) and l.get("number", 0) > 0]
        falhadas = [l for l in listas if l.get("status", 0) >= 3]
        pendentes = [l for l in listas if l not in prontas and l not in falhadas]
        print(f"  {len(prontas)}/{len(listas)} listas processadas"
              + (f", {len(falhadas)} com falha" if falhadas else ""))
        if not pendentes:
            total = sum(l.get("number", 0) for l in listas)
            print(f"gravity concluido: {total} dominios em {len(listas)} listas "
                  f"({int(time.time() - inicio)} s)")
            for l in falhadas:
                print(f"  FALHA {l['address']} (status {l.get('status')})")
            return 1 if falhadas else 0
    print("gravity: tempo esgotado (verificar /api/lists na UI)")
    return 1


def medir_listas(perfil: dict) -> dict:
    """Conta as entradas reais de cada lista descarregando-a (para a pagina e o README)."""
    resultado = {"medido_em": time.strftime("%Y-%m-%d"), "perfil": perfil["nome"], "listas": []}
    total = 0
    for l in perfil["listas"]:
        try:
            with urllib.request.urlopen(l["url"], timeout=90, context=_ctx()) as r:
                linhas = sum(1 for _ in r)
        except Exception as e:
            linhas = l.get("entradas_medidas", 0)
            print(f"  ! {l['id']}: nao medido ({e.__class__.__name__}), uso o valor guardado")
        total += linhas
        resultado["listas"].append({**{k: l[k] for k in ("id", "nome", "categoria", "porque", "url")},
                                    "entradas": linhas})
        print(f"  {l['id']:<26} {linhas:>9,} dominios")
    resultado["total"] = total
    return resultado


def escrever_dados(perfil: dict, base: str, cofre: Path) -> None:
    print("a medir listas (descarrega cada ficheiro):")
    dados = medir_listas(perfil)
    # Se o Pi-hole estiver acessivel, os numeros que contam sao os que ele processou
    # (depois de deduplicar contra as outras listas), nao as linhas do ficheiro.
    try:
        ph = Pihole(base, ler_password(cofre))
        try:
            activas = {l["address"]: l for l in ph.listas() if l.get("status") in (1, 2)}
        finally:
            ph.sair()
        total_pihole = 0
        for l in dados["listas"]:
            info = activas.get(l["url"])
            l["no_pihole"] = info.get("number", 0) if info else None
            total_pihole += l["no_pihole"] or 0
        if total_pihole:
            dados["total_pihole"] = total_pihole
            dados["total_listas_pihole"] = sum(
                1 for l in dados["listas"] if l.get("no_pihole"))
    except Exception as e:
        print(f"  ! Pi-hole nao consultado ({e.__class__.__name__}): fico so com as linhas dos ficheiros")

    destino = RAIZ / "docs" / "dados.json"
    destino.parent.mkdir(exist_ok=True)
    destino.write_text(json.dumps(dados, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    real = dados.get("total_pihole")
    print(f"escrito {destino.relative_to(RAIZ)} — {dados['total']:,} entradas nos ficheiros"
          + (f", {real:,} dominios processados pelo Pi-hole" if real else ""))


# --------------------------------------------------------------------------- #
def main() -> int:
    p = argparse.ArgumentParser(description="Aplica as listas do pihole-familia ao Pi-hole")
    p.add_argument("--perfil", default="familia")
    p.add_argument("--base", default=os.environ.get("PIHOLE_URL", "http://192.168.88.5"))
    p.add_argument("--cofre", type=Path, default=COFRE)
    p.add_argument("--ensaio", action="store_true", help="nao altera nada")
    p.add_argument("--limpar", action="store_true", help="remove listas HaGeZi fora do perfil")
    p.add_argument("--sem-gravity", action="store_true")
    p.add_argument("--dados", action="store_true", help="mede as listas e escreve docs/dados.json")
    a = p.parse_args()

    perfil = carregar_perfil(a.perfil)

    if a.dados:
        escrever_dados(perfil, a.base, a.cofre)
        return 0

    ph = Pihole(a.base, ler_password(a.cofre))
    try:
        rc = aplicar(ph, perfil, a.ensaio, a.limpar)
        if rc or a.ensaio or a.sem_gravity:
            return rc
        return correr_gravity(ph)
    finally:
        ph.sair()


if __name__ == "__main__":
    sys.exit(main())
