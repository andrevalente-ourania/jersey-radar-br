# Jersey Radar BR

Buscador de camisas de clubes brasileiros fora do radar, com score por preço,
raridade e sinais de coleção.

## Interface web

O painel mostra imagem, preço, score, clube e link do anúncio quando a API do
Mercado Livre está disponível. Se a API responder com HTTP 403, ele muda para
uma tela fallback com 20 buscas úteis, separadas nas mesmas categorias:

- `small_club_cheap`
- `cult_beautiful`
- `light_collectible`

### Rodar localmente

```bash
pip install -r requirements.txt
streamlit run app/dashboard.py
```

Crie um arquivo `.env` local com `MELI_CLIENT_ID` e `MELI_CLIENT_SECRET`. Nunca
adicione esse arquivo ao Git.

### Publicar no Streamlit Community Cloud

1. Crie um app apontando para este repositório.
2. Use `app/dashboard.py` como arquivo principal.
3. Em **Settings → Secrets**, configure:

```toml
MELI_CLIENT_ID = "seu-client-id"
MELI_CLIENT_SECRET = "seu-client-secret"
```

O painel guarda o resultado em cache por 15 minutos. O botão **Atualizar busca**
limpa o cache e executa uma nova busca.

## Automação

O workflow `.github/workflows/daily.yml` continua executando o radar diariamente
e também pode ser disparado manualmente no GitHub Actions.
