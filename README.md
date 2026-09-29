# Plataforma Finder Fee — All Targets

Portal do Programa Finder Fee (Piloto v1): indicação de clientes por parceiros PJ, comissão por
parcela recebida, bônus de recrutamento (Camada 2), fluxo de nota fiscal e pagamento, painel de gestão
e relatório semanal do piloto. Construção própria, sem plataforma de terceiros.

Python 3.12 · Flask · SQLite (piloto) · sem build de front-end.

## Rodar no computador (teste)

```bash
pip install -r requirements-dev.txt    # app + ferramentas de teste
export FLASK_APP=app:create_app
flask init-db                       # cria ou atualiza o banco (migrações), as regras "Piloto v1" e os termos pendentes do jurídico
flask create-user marco@alltargets.example "Marco" --roles admin,manager,commercial,finance
flask run                           # http://127.0.0.1:5000
```

Para ver o sistema já com dados de exemplo (apenas em ambiente de teste):

```bash
flask demo-data                     # gestor@demo.test / demo-senha-123 ; parceiro3@demo.test / senha-segura-123
flask simulate-bonus                # confere a Camada 2 em banco temporário: R$ 34.560 + R$ 17.280 = 22,5%
pytest -q                           # 142 testes (3 só rodam com PostgreSQL); também rodam no GitHub Actions a cada push
# contra PostgreSQL (banco descartável, é apagado a cada teste):
FF_TEST_DATABASE_URL=postgresql+psycopg://usuario:senha@localhost/finder_fee_test pytest -q
```

## Perfis internos

| Perfil | Pode |
|---|---|
| `manager` (gestor) | Validar indicações, aprovar parceiros, emitir o relatório semanal |
| `commercial` | Mesmo que gestor nas indicações e parceiros, sem financeiro |
| `finance` | Conferir comissões, notas, pagamentos e registrar recebimentos |
| `admin` | Tudo, mais regras, termos e usuários |

Parceiros só enxergam os próprios dados. A empresa já indicada por outro parceiro nunca revela quem indicou.

## Colocar no ar

O passo a passo completo, testado de ponta a ponta, está em **[docs/IMPLANTACAO.md](docs/IMPLANTACAO.md)**:
PostgreSQL, container Docker sem root, nginx com HTTPS ([exemplo](docs/nginx.conf.exemplo)), primeiro acesso,
conferência, rotinas no cron, backup e restauração, primeira ativação do e-mail, atualização e volta atrás.
As variáveis de configuração estão comentadas em [.env.exemplo](.env.exemplo).

Em resumo:

```bash
cp .env.exemplo .env && chmod 600 .env        # preencha; FF_SECRET_KEY com 32+ caracteres aleatórios
docker build -t finder-fee .
docker run -d --name finder-fee --restart unless-stopped --network host \
  --env-file .env -v finderfee-data:/srv/finder_fee/instance finder-fee
```

- O container aplica as migrações a cada subida, roda como usuário sem privilégios (uid 10001) e expõe
  `/saude` para o Docker e o balanceador.
- Rotinas: `flask run-jobs` todo dia, `flask weekly-report` às segundas e `scripts/backup.sh` todo dia.
- Antes de ligar o e-mail pela primeira vez: `flask email-backlog` (e `--discard` para não enviar o acumulado).

### Versões das bibliotecas

`requirements.txt` e `requirements-dev.txt` dizem as faixas aceitas. As versões exatas, conferidas por hash, ficam
em `requirements.lock` (usado pelo Docker) e `requirements-dev.lock` (usado pelo CI). Para atualizar, rode
`scripts/atualizar-dependencias.sh` (precisa do [uv](https://docs.astral.sh/uv/)), confira os testes e commite
os dois arquivos `.lock`.

## Mudanças no banco (migrações)

O esquema do banco é versionado com Alembic (Flask-Migrate), na pasta `migrations/`. `flask init-db` aplica as
migrações pendentes e pode rodar a cada implantação (o `Dockerfile` já faz isso ao subir).

- **Faça backup antes de atualizar** um banco com dados.
- Banco criado antes das migrações (pelo `init-db` antigo): o `init-db` atual reconhece as tabelas que já existem,
  cria as que faltam e aplica as mudanças de coluna, sem apagar dados.
- Para mudar o esquema: altere o modelo em `app/models.py` e gere a migração:
  ```bash
  flask db migrate -m "o que mudou"      # cria migrations/versions/<id>_....py a partir da diferença
  ```
  Revise o arquivo gerado antes de commitar: renomeação de coluna, por exemplo, sai como "apaga e cria" e perderia
  os dados. A migração não pode importar código do `app` (o teste `test_migracoes_nao_importam_o_app` confere).
- `flask db current` mostra a versão do banco; `flask db downgrade` desfaz a última migração.
- Os testes conferem que as migrações geram exatamente o esquema dos modelos, no SQLite e no PostgreSQL. Esquecer de
  gerar a migração faz o CI falhar.

## Antes do primeiro parceiro (não é código)

- **Jurídico (Willian):** os seis termos entram como texto pendente e o parceiro **não consegue aceitá-los**
  até o administrador colar o texto final e marcar "liberado pelo jurídico" em *Termos e materiais*.
  Sem isso ninguém é ativado. Pendências: parecer sobre a Lei 4.886/65 (caráter eventual), consentimento LGPD
  do indicado, estrutura para advogados (vedação da OAB) e representantes fora do CORE.
- **Financeiro:** precificação final; percentual por produto/público (campo pronto em regras); prazo mensal da NF;
  retenções sobre pagamento a PJ e enquadramento de MEI.
- **Base comercial:** cadastrar clientes atuais e oportunidades abertas (senão a checagem de duplicidade não os vê).
- **Materiais:** publicar o playbook por perfil em *Termos e materiais*.

## Valores provisórios (aparecem em *Regras e parâmetros*)

| Parâmetro | Valor atual | Situação |
|---|---|---|
| Proteção comercial | 90 dias | Provisório, definir no regulamento |
| Prazo mensal da NF | dia 20 | Provisório, a definir |
| Teto agregado | sem teto | Número fica para depois do piloto |
| Parcela no aniversário de 12 meses | conta | Confirmar a borda da janela |
| Matriz e filiais | mesma empresa | Confirmar tratamento de grupo econômico |
| Alíquota de referência de impostos | 10% | Ilustrativa. O valor real vem de cada NF |
| Alerta de recorrência | 6 aceitas em 12 meses | Ajustar com o parecer jurídico |
| Limite de custo do canal | a definir | Depende da precificação |

Mudar qualquer regra cria uma nova versão; cada indicação guarda a versão da data do aceite.

## Como o cálculo funciona

`comissão = valor recebido × (1 − impostos ÷ valor da NF) × percentual` · `bônus = 50% da comissão da mesma parcela`
(só no primeiro contrato do parceiro recrutado). Parcela com vencimento dentro dos 12 meses conta, mesmo paga depois;
a comissão só é gerada quando o cliente paga. Pagamento no mês seguinte ao recebimento, contra NF do parceiro.

## Limites desta versão

- Testado com SQLite e PostgreSQL 16: a suíte inteira, os comandos `flask`, o servidor gunicorn com 2 processos e envios
  simultâneos (mesma empresa: só um parceiro leva; empresas diferentes: todas gravadas). No PostgreSQL os códigos
  I-001/P01 podem pular números depois de um envio recusado; continuam únicos e estáveis.
- Interface conferida em Chromium (desktop e celular). Safari e Firefox não foram testados.
- E-mail testado contra servidor SMTP local (STARTTLS, SSL na 465, login, certificado inválido, endereço recusado,
  falha temporária). Ainda não foi testado com o provedor que será usado de verdade: antes de ligar, rode
  `flask run-jobs` com um aviso de teste. Endereço recusado em definitivo fica registrado na *Auditoria*
  (`email.refused`) e não trava os demais. Sem SMTP, os avisos ficam só no portal.
- Avisos com mais de `FF_EMAIL_MAX_AGE_DAYS` dias (padrão 7) não saem por e-mail, só aparecem no portal. Os mais novos
  saem em lotes de até 200 por rodada. `flask email-backlog --discard` descarta a fila inteira antes da primeira ativação.
- A NF é armazenada como arquivo; o sistema não lê o XML nem valida a nota na Receita.
- A situação ativa do CNPJ é confirmada manualmente pela equipe (não há consulta automática).
- Acesso: bloqueio da conta após 5 erros (senha ou código de duas etapas) e limite de erros por IP. O limite fica
  no banco, então vale para todos os processos do gunicorn.
- Sessão: fica registrada no banco (tabela `user_session`) e o cookie leva só um token. Sair, trocar a senha ou o
  aplicativo de duas etapas, ter a senha redefinida ou o acesso desativado encerra a sessão de verdade. Trocar o
  aplicativo de duas etapas exige o código do aplicativo atual.
- Teste de intrusão (set/2026) contra o servidor local: controle de acesso entre parceiros e perfis, CSRF, injeção SQL,
  XSS, upload de NF, redirecionamento, sessão, força bruta e cabeçalhos. As falhas encontradas foram corrigidas e têm
  teste em `tests/test_seguranca.py`. Ainda vale uma revisão independente antes de abrir para parceiros.
- O código de duas etapas vale uma vez só: depois de usado, o mesmo código (ou um anterior) é recusado.
- Sem política de retenção/eliminação de dados (LGPD).
- Feriados: cadastrados em *Gestão > Feriados* (administrador ou gestor). Um botão cadastra os nacionais do ano, inclusive
  Sexta-feira Santa, e opcionalmente os pontos facultativos (Carnaval e Corpus Christi); estaduais e municipais entram
  um a um. Sem feriados cadastrados, os prazos em dias úteis só descontam fins de semana. Os feriados ficam fora das
  regras versionadas: cadastrar um não cria versão nova e vale para todas as indicações.
