#!/bin/sh
# Regenera as versões exatas (com hash) a partir de requirements.txt e requirements-dev.txt.
# Rodar depois de mudar uma faixa ou para pegar correções de segurança; conferir os testes antes de commitar.
# Precisa do uv (https://docs.astral.sh/uv/): pip install uv
set -e
cd "$(dirname "$0")/.."
uv pip compile requirements.txt --python-version 3.12 --generate-hashes --upgrade -o requirements.lock
uv pip compile requirements-dev.txt -c requirements.lock --python-version 3.12 --generate-hashes -o requirements-dev.lock
