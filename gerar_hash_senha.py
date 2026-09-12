"""
Gera o hash de uma senha pra colar no secrets.toml.

Rode localmente (nunca no app publicado):
    python gerar_hash_senha.py

Isso NÃO precisa estar no requirements.txt do deploy nem rodar na nuvem —
é só uma ferramenta pra você usar uma vez por usuário, na sua máquina.
"""

import getpass

import bcrypt

senha = getpass.getpass("Digite a senha que quer usar (não aparece na tela): ")
hash_gerado = bcrypt.hashpw(senha.encode(), bcrypt.gensalt()).decode()

print()
print("Cole essa linha em [auth_users] no secrets.toml, trocando 'usuario' pelo nome de login:")
print(f'usuario = "{hash_gerado}"')
