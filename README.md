# Plataforma Finder Fee — All Targets

Portal do Programa Finder Fee (Piloto v1): indicação de clientes por parceiros PJ, comissão por
parcela recebida, bônus de recrutamento (Camada 2), fluxo de nota fiscal e pagamento, painel de gestão
e relatório semanal do piloto. Construção própria, sem plataforma de terceiros.

Python 3.12 · Flask · SQLite (piloto) · sem build de front-end.

## Rodar no computador (teste)

```bash
pip install -r requirements.txt
export FLASK_APP=app:create_app
flask init-db                       # cria o banco, as regras "Piloto v1" e os termos pendentes do jurídico
flask create-user marco@alltargets.example "Marco" --roles admin,manager,commercial,finance
flask run                           # http://127.0.0.1:5000
```

Para ver o sistema já com dados de exemplo (apenas em ambiente de teste):

```bash
flask demo-data                     # gestor@demo.test / demo-senha-123 ; parceiro3@demo.test / senha-segura-123
flask simulate-bonus                # confere a Camada 2 em banco temporário: R$ 34.560 + R$ 17.280 = 22,5%
pytest -q                           # 71 testes (2 só rodam com PostgreSQL); também rodam no GitHub Actions a cada push
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

1. Servidor com HTTPS (o cookie de sessão só trafega seguro).
2. Variáveis: `FF_SECRET_KEY` (texto longo e aleatório), `FF_ENV=production`, `FF_COOKIE_SECURE=1`,
   `FF_PUBLIC_URL` (endereço público), `FF_REQUIRE_2FA=1` (exige duas etapas da equipe interna).
   Atrás de proxy reverso (nginx, balanceador): `FF_TRUSTED_PROXIES=1` (número de proxies), senão o limite
   por IP enxerga só o endereço do proxy. **Não** ligue sem proxy: qualquer um poderia forjar o IP.
   Limite por IP (opcional): `FF_IP_MAX_FAILURES` (padrão 20 erros) em `FF_IP_WINDOW_MINUTES` (padrão 15).
   Banco PostgreSQL (recomendado em produção): `FF_DATABASE_URL=postgresql+psycopg://usuario:senha@host/finder_fee`.
   E-mail (opcional): `FF_SMTP_HOST`, `FF_SMTP_PORT`, `FF_SMTP_USER`, `FF_SMTP_PASS`, `FF_MAIL_FROM`.
3. `docker build -t finder-fee . && docker run -p 8000:8000 -v finderfee-data:/srv/finder_fee/instance --env-file .env finder-fee`
4. Rotina diária (cron): `flask run-jobs` (expira proteções vencidas, apaga o registro de tentativas de acesso com mais de 1 dia e envia avisos por e-mail).
5. Rotina semanal (segunda-feira): `flask weekly-report`, ou pelo botão em "Relatório semanal".
6. **Backup** da pasta `instance/` (banco, NFs anexadas e relatórios) todos os dias.

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
- Sem ferramenta de migração: `flask init-db` só cria tabelas novas. Mudança em coluna existente exige ajuste manual no banco.
- Interface conferida em Chromium (desktop e celular). Safari e Firefox não foram testados.
- Envio de e-mail (SMTP) implementado e **não testado** contra um servidor real. Sem SMTP, os avisos ficam só no portal.
- A NF é armazenada como arquivo; o sistema não lê o XML nem valida a nota na Receita.
- A situação ativa do CNPJ é confirmada manualmente pela equipe (não há consulta automática).
- Acesso: bloqueio da conta após 5 erros (senha ou código de duas etapas) e limite de erros por IP. O limite fica
  no banco, então vale para todos os processos do gunicorn.
- Sem política de retenção/eliminação de dados (LGPD). Recomenda-se uma revisão de segurança independente antes de
  abrir para parceiros.
- Feriados não vêm de fábrica e ainda não há campo na tela para cadastrá-los: hoje os prazos em dias úteis só descontam fins de semana. A lista fica em `holidays` (arquivo `app/rules.py`).
