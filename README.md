# pihole-familia

Subscrições de bloqueio DNS do Pi-hole desta casa — montadas para proteger a família, não para
experimentar listas.

O Pi-hole corre como container no MikroTik (`192.168.88.5`) e serve **toda** a rede: LAN, VLAN IoT e
as máquinas com Tailscale que aceitem o DNS da rede. Este repositório guarda **o quê** está subscrito,
**porquê** cada lista está lá, e os scripts que aplicam e verificam — para a configuração ser
reproduzível em vez de memória de uma sessão.

## Estado actual — medido, não estimado

Perfil activo: **familia** · 8 listas · **629 927 domínios activos** nestas listas, **711 604** na base
total do Pi-hole (inclui a lista que já existia, StevenBlack) — medido a 2026-11-25 com
`./scripts/aplicar.py --dados` e confirmado em `GET /api/lists` (`status: 1`).

| Lista | Categoria | Domínios | O que fecha |
|---|---:|:---:|---|
| HaGeZi Multi PRO | base | 246 391 | Anúncios, rastreadores, telemetria, malware, phishing |
| HaGeZi Threat Intelligence Feeds (medium) | segurança | 230 844 | Malware, phishing, burlas, servidores de comando-e-controlo |
| HaGeZi NSFW | família | 81 432 | Conteúdo adulto |
| HaGeZi Gambling (mini) | família | 55 320 | Apostas e casinos online |
| HaGeZi Fake | família | 10 334 | Lojas falsas, streaming fraudulento, armadilhas de subscrição |
| HaGeZi DoH | evasão | 3 563 | Endpoints de DNS-sobre-HTTPS (a fuga mais fácil) |
| HaGeZi DynDNS | segurança | 1 940 | DNS dinâmico usado em campanhas de phishing |
| HaGeZi NoSafeSearch | família | 103 | Motores de busca sem SafeSearch |

A diferença entre as linhas do ficheiro e os domínios activos (≈0,1 %) é a deduplicação normal do
Pi-hole contra as outras listas.

Consumo medido no container do MikroTik: **210,6 MB de 384 MB** de teto, router com 354 MB livres —
margem suficiente para o dobro das listas antes de ser preciso subir o `memory-high`.

Listas de origem: [hagezi/dns-blocklists](https://github.com/hagezi/dns-blocklists) — projeto de
terceiros, mantido por HaGeZi; aqui só se referenciam os URLs, não se redistribui conteúdo.

## Porque estas e não outras

- **Uma lista base, não cinco empilhadas.** Light/Normal/Pro/Pro++/Ultimate são versões da *mesma*
  lista. Assinar várias é trabalho de gravidade a duplicar sem ganho. Ficou a **PRO** (equilíbrio
  entre cobertura e risco de partir sites).
- **TIF em versão medium.** A completa tem 882 074 entradas. O container do router tem teto de
  memória de 384 MiB e já andou no limite; a medium traz o essencial das fontes de ameaças sem
  empurrar o Pi-hole para o estrangulamento.
- **NSFW antes de tudo o resto que é "família".** É a razão principal do exercício. Não é filtro de
  escola — é um travão; uma VPN no telefone passa por cima dele (ver Limites).
- **Gambling em mini.** A completa tem 669 798 entradas — desproporcionada para o peso.
- **DoH, e não a lista de bypass completa.** A lista `doh-vpn-proxy-bypass` fecha também VPN, Tor e
  proxies, e isso parte o próprio acesso remoto da casa (Tailscale/RustDesk). O perfil `familia-max`
  tem-na, com o aviso escrito, para quem quiser assumir esse risco.
- **DynDNS e Fake são baratas.** Juntas são 12 mil domínios e apanham duas famílias de fraude que
  chegam por mensagem — a que mais atinge quem não é técnico.

## Como aplicar

```bash
git clone https://github.com/hardjunior/pihole.git && cd pihole

./scripts/aplicar.py --ensaio          # mostra o que faria, não altera nada
./scripts/aplicar.py                   # subscreve o que falta e corre a gravidade
./scripts/aplicar.py --perfil familia-max
./scripts/aplicar.py --limpar          # remove listas HaGeZi que não estão no perfil
./scripts/verificar.py                 # mede canários: bloqueado vs. resolvido
```

O script é idempotente: correr duas vezes não duplica listas. A password do Pi-hole vem de
`$PIHOLE_PW`, do ficheiro do cofre indicado em `--cofre`, ou é pedida no terminal — **nunca é escrita
no ecrã nem entra neste repositório**.

Servidor por omissão: `http://192.168.88.5` (mudar com `--base` ou `$PIHOLE_URL`).

## Como verificar (sem acreditar no script)

`./scripts/verificar.py` pergunta directamente ao resolvedor e compara com o esperado:

```
ok    anuncios          doubleclick.net      -> 0.0.0.0          (esperado 0.0.0.0)
ok    adulto            pornhub.com          -> 0.0.0.0          (esperado 0.0.0.0)
ok    controle_negativo github.com           -> 20.208.89.80     (esperado IP real)
```

Se os canários de publicidade bloqueiam mas o controlo negativo também bloqueia, o problema é o
servidor, não as listas. Se os canários de família passam, a lista não foi processada — correr a
gravidade outra vez e ver `/api/lists` (`status: 1`, `number > 0`).

## Reverter

```bash
# remover tudo o que este projeto subscreveu
SID=$(curl -s -X POST http://192.168.88.5/api/auth -H 'Content-Type: application/json' \
      -d "{\"password\":\"$PIHOLE_PW\"}" | jq -r .session.sid)
curl -s "http://192.168.88.5/api/lists" -H "X-FTL-SID: $SID" |
  jq -r '.lists[] | select(.comment | startswith("pihole-familia")) | .address' |
  while read -r url; do
    curl -s -X DELETE "http://192.168.88.5/api/lists/$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=''))" "$url")" \
      -H "X-FTL-SID: $SID"; done
curl -s -X POST http://192.168.88.5/api/action/gravity -H "X-FTL-SID: $SID"
```

Antes de mexer, vale a pena guardar o estado: `GET /api/teleporter` devolve um zip com a configuração
toda do Pi-hole.

## Limites — o que isto não faz

Dizer isto antes de prometer proteção é a parte honesta:

- **Não bloqueia anúncios do YouTube** (nem Spotify, nem Twitch). Saem do mesmo domínio que o vídeo;
  nenhum DNS separa anúncio de conteúdo.
- **Uma VPN ou um DNS privado no telefone passa por cima.** Do lado da casa há duas defesas
  complementares: `dst-nat` de todas as consultas para a porta 53 no MikroTik e bloqueio de DoT/DoQ
  (853). Fora de casa, deixa de haver Pi-hole — aí só o DNS privado do aparelho protegeria.
- **Não é controlo parental.** Bloqueia domínios conhecidos; não vê tráfego, não filtra por palavra,
  não lê mensagens. Para os miúdos isso é uma camada, não um plano.
- **Nada disto substitui autenticação.** Quem está na rede continua a aceder ao que é legítimo; isto
  só remove destinos conhecidos por serem lixo ou armadilha.
- **Falsos positivos acontecem.** Uma loja legítima pode cair no NSFW/Fake e um jogo no Gambling.
  Desbloqueio cirúrgico: `POST /api/domains/allow/exact` com o domínio, em vez de desligar a lista
  toda.

## Manutenção

- A gravidade (reconstrução da base) não corre sozinha: `./scripts/aplicar.py` no fim de cada
  alteração, ou o botão *Tools → Update Gravity* na UI.
- Um vigia local (`~/.hermes/scripts/pihole-db-check.py`) alerta se a base de dados crescer demais ou
  o container passar dos 350 MiB. O RouterOS tem ainda um `pihole-watchdog` a cada 5 minutos que
  reinicia o container se o DNS parar.
- Versões `@latest` do CDN: as listas actualizam no upstream. A gravidade seguinte apanha as
  alterações, sem mudar nada aqui.

## Estrutura

```
listas.json              fonte de verdade: perfis, URLs, porque cada lista existe, canários
scripts/aplicar.py       sincroniza com o Pi-hole (idempotente) + gravidade + docs/dados.json
scripts/verificar.py     canários de bloqueio, com controlo negativo
docs/index.html          página publicada (GitHub Pages)
docs/dados.json          números medidos, gerados por `aplicar.py --dados`
```

## Créditos e licença

Listas: [HaGeZi DNS Blocklists](https://github.com/hagezi/dns-blocklists) — mérito inteiro do autor;
consultar lá as condições de utilização. Código e documentação deste repositório: MIT.
