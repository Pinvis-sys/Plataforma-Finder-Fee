#!/bin/sh
# Backup diário: banco (pg_dump) e pasta instance/ (NFs anexadas e relatórios), com retenção.
# Uso (na pasta do projeto, onde está o .env):  scripts/backup.sh /var/backups/finder-fee
# Variáveis opcionais: VOLUME (padrão finderfee-data), IMAGEM (padrão finder-fee), DIAS (retenção, padrão 30).
# Precisa do pg_dump no servidor (pacote postgresql-client, mesma versão do banco ou mais nova).
set -eu
DEST=${1:?informe a pasta de destino}
VOLUME=${VOLUME:-finderfee-data}
IMAGEM=${IMAGEM:-finder-fee}
DIAS=${DIAS:-30}
URL=$(grep '^FF_DATABASE_URL=' .env | cut -d= -f2- | sed 's/+psycopg//')
[ -n "$URL" ] || { echo "FF_DATABASE_URL não encontrado no .env" >&2; exit 1; }
DATA=$(date +%Y%m%d-%H%M)
umask 077                                   # backup tem dados pessoais e bancários: só o dono lê
mkdir -p "$DEST"
pg_dump --format=custom --file="$DEST/banco-$DATA.dump" "$URL"
docker run --rm --user 0 -v "$VOLUME":/dados:ro -v "$DEST":/backup "$IMAGEM" \
    sh -c "umask 077 && tar czf /backup/instance-$DATA.tar.gz -C /dados ."
find "$DEST" -name 'banco-*.dump' -mtime +"$DIAS" -delete
find "$DEST" -name 'instance-*.tar.gz' -mtime +"$DIAS" -delete
echo "Backup concluído: $DEST/banco-$DATA.dump e $DEST/instance-$DATA.tar.gz"
