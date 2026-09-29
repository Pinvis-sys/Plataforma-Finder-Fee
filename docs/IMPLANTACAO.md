# Implantação da Plataforma Finder Fee

Roteiro para colocar a plataforma no ar num servidor Linux próprio, com Docker, PostgreSQL e nginx com HTTPS.
Todos os comandos abaixo foram testados (Docker 29, PostgreSQL 16, nginx 1.30). Troque `portal.exemplo.com.br`
pelo endereço real.

```
Internet ──443──▶ nginx (HTTPS) ──127.0.0.1:8000──▶ container finder-fee (gunicorn) ──▶ PostgreSQL
                                                          └─ volume finderfee-data (NFs, relatórios)
```

## 1. Pré-requisitos

- Servidor Linux com Docker, nginx, certbot e `postgresql-client` (para o backup).
- PostgreSQL 16: no próprio servidor ou gerenciado (RDS, Cloud SQL etc.).
- Domínio apontando (DNS) para o servidor.
- Firewall liberando só as portas 22, 80 e 443. O PostgreSQL e a porta 8000 **não** podem ficar abertos para a internet.

## 2. Banco de dados

```bash
sudo -u postgres psql -c "CREATE ROLE finder LOGIN PASSWORD 'troque-esta-senha'"
sudo -u postgres psql -c "CREATE DATABASE finder_fee OWNER finder"
```

As tabelas são criadas pelo próprio app na primeira subida (migrações).

## 3. Configuração

```bash
git clone https://github.com/Pinvis-sys/Plataforma-Finder-Fee.git && cd Plataforma-Finder-Fee
cp .env.exemplo .env && chmod 600 .env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"      # cole em FF_SECRET_KEY
nano .env                                                          # preencha FF_DATABASE_URL, FF_PUBLIC_URL etc.
```

O app **não sobe** em produção com `FF_SECRET_KEY` vazia, padrão ou com menos de 32 caracteres.

## 4. Imagem e container

```bash
docker build -t finder-fee .
docker run -d --name finder-fee --restart unless-stopped --network host \
  --env-file .env -v finderfee-data:/srv/finder_fee/instance finder-fee
docker logs finder-fee            # deve mostrar "Banco pronto." e "Listening at: http://127.0.0.1:8000"
curl -s http://127.0.0.1:8000/saude     # {"status":"ok"}
```

- `--network host` deixa o container alcançar o PostgreSQL do próprio servidor em `localhost`. Com
  `FF_BIND=127.0.0.1:8000` (já no `.env.exemplo`), só o nginx alcança o app.
- `--restart unless-stopped`: se o banco estiver fora do ar na subida, o container para e o Docker tenta de novo.
- O app roda sem root (usuário `finder`, uid 10001) e só escreve no volume.
- `docker ps` mostra `(healthy)` quando o `/saude` responde.

**Volume criado por uma versão anterior da imagem** (que rodava como root): ajuste o dono uma vez, senão o app não
consegue gravar NFs:

```bash
docker run --rm --user 0 -v finderfee-data:/dados finder-fee chown -R 10001:10001 /dados
```

## 5. HTTPS com nginx

```bash
sudo cp docs/nginx.conf.exemplo /etc/nginx/sites-available/finder-fee
sudo nano /etc/nginx/sites-available/finder-fee                    # troque portal.exemplo.com.br
sudo ln -s /etc/nginx/sites-available/finder-fee /etc/nginx/sites-enabled/
sudo certbot certonly --nginx -d portal.exemplo.com.br
sudo nginx -t && sudo systemctl reload nginx
```

O exemplo redireciona HTTP para HTTPS, aceita NF de até 8 MB e **substitui** o `X-Forwarded-For` pelo IP real
do cliente. Isso importa: com `FF_TRUSTED_PROXIES=1`, o limite de tentativas usa esse cabeçalho, e o nginx impede
que alguém o forje.

## 6. Primeiro acesso

```bash
docker exec -it finder-fee flask create-user voce@empresa.com.br "Seu Nome" --roles admin,manager,commercial,finance
```

Entre em `https://portal.exemplo.com.br`. Com `FF_REQUIRE_2FA=1`, o portal pede para cadastrar a verificação em
duas etapas antes de liberar a gestão.

Depois, em **Feriados**, cadastre os feriados nacionais do ano (e os do estado e da cidade da empresa). Sem isso, os
prazos em dias úteis só descontam fins de semana. Repita no começo de cada ano.

## 7. Conferência depois de subir

| Verificação | Como | Esperado |
|---|---|---|
| App no ar | `curl -s https://portal.exemplo.com.br/saude` | `{"status":"ok"}` |
| HTTP vai para HTTPS | `curl -sI http://portal.exemplo.com.br` | `301` para `https://` |
| HSTS | `curl -sI https://portal.exemplo.com.br/entrar \| grep -i strict` | `max-age=31536000` |
| Cookie seguro | `curl -sI https://portal.exemplo.com.br/entrar \| grep -i set-cookie` | `Secure; HttpOnly` |
| Porta 8000 fechada | de outra máquina: `curl http://IP-DO-SERVIDOR:8000/saude` | sem resposta |
| Duas etapas | entrar com o admin | pede o cadastro do aplicativo |

## 8. Rotinas (cron)

`crontab -e` do usuário que administra o servidor (na pasta do projeto):

```cron
# diária: expira proteções, limpa tentativas e sessões vencidas, envia avisos por e-mail
10 6 * * *   docker exec finder-fee flask run-jobs >> /var/log/finder-fee-jobs.log 2>&1
# semanal (segunda-feira): relatório do piloto
20 6 * * 1   docker exec finder-fee flask weekly-report >> /var/log/finder-fee-jobs.log 2>&1
# backup diário, guardado por 30 dias
30 2 * * *   cd /caminho/Plataforma-Finder-Fee && scripts/backup.sh /var/backups/finder-fee >> /var/log/finder-fee-backup.log 2>&1
```

**Backup fora do servidor:** copie `/var/backups/finder-fee` para outro lugar (outro servidor, armazenamento em
nuvem). Backup só no mesmo disco não protege contra perda do servidor. Os arquivos ficam com permissão só do dono
(têm dados pessoais e bancários).

## 9. Restaurar um backup

Teste a restauração logo depois da primeira implantação, e de tempos em tempos.

```bash
docker stop finder-fee
# banco
sudo -u postgres psql -c "DROP DATABASE finder_fee" -c "CREATE DATABASE finder_fee OWNER finder"
pg_restore --no-owner -d "postgresql://finder:SENHA@localhost/finder_fee" /var/backups/finder-fee/banco-AAAAMMDD-HHMM.dump
# arquivos (NFs e relatórios)
docker run --rm --user 0 -v finderfee-data:/dados -v /var/backups/finder-fee:/backup:ro finder-fee \
  sh -c "rm -rf /dados/* && tar xzf /backup/instance-AAAAMMDD-HHMM.tar.gz -C /dados && chown -R 10001:10001 /dados"
docker start finder-fee
```

## 10. Ligar o e-mail pela primeira vez

1. Veja o que está na fila: `docker exec finder-fee flask email-backlog`.
2. Se não quiser que avisos acumulados saiam por e-mail: `docker exec finder-fee flask email-backlog --discard`.
   Eles continuam visíveis no portal. De qualquer forma, avisos com mais de `FF_EMAIL_MAX_AGE_DAYS` dias (padrão 7)
   nunca saem por e-mail.
3. Preencha `FF_SMTP_*` e `FF_MAIL_FROM` no `.env` e recrie o container (passo 11, sem o `git pull`).
4. Gere um aviso de teste (por exemplo, crie um convite ou aprove algo com um usuário seu) e rode
   `docker exec finder-fee flask run-jobs`. Confira a chegada e a auditoria (`email.refused` indica endereço recusado).

## 11. Atualizar para uma versão nova

```bash
scripts/backup.sh /var/backups/finder-fee          # sempre antes: a versão nova pode mudar o banco
docker tag finder-fee finder-fee:anterior         # guarda a imagem atual, para poder voltar atrás
git pull
docker build -t finder-fee .
docker stop finder-fee && docker rm finder-fee
docker run -d --name finder-fee --restart unless-stopped --network host \
  --env-file .env -v finderfee-data:/srv/finder_fee/instance finder-fee
docker logs finder-fee                               # "Running upgrade ..." aparece quando há migração nova
```

A subida aplica as migrações pendentes sozinha. Quem estiver logado continua logado, exceto quando a versão nova
disser o contrário.

**Voltar atrás:** se a versão nova aplicou migração, restaure o backup do banco (passo 9) e suba a imagem anterior.
Sem migração nova, basta subir a imagem anterior
(o mesmo `docker run` do passo 4, trocando `finder-fee` no final por `finder-fee:anterior`).

## 12. Segurança da operação

- `.env` com `chmod 600`, fora do Git e fora de backups sem criptografia. Nele estão a chave de sessão e as senhas
  do banco e do SMTP.
- Trocar `FF_SECRET_KEY` desconecta todos os usuários. Faça isso se suspeitar que ela vazou.
- Mantenha o sistema operacional, o Docker e o nginx atualizados. Para atualizar as bibliotecas do app, rode
  `scripts/atualizar-dependencias.sh`, confira os testes e siga o passo 11.
- Antes de abrir para parceiros, vale um teste de segurança independente contra este ambiente.
