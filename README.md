# Jersey Radar BR

Buscador de camisas de clubes brasileiros, clubes do mundo inteiro e seleções,
com score por preço, raridade e sinais de coleção.

## Interface web

O painel mostra imagem, preço, score, clube e link do anúncio quando a API do
Mercado Livre está disponível. Com ou sem a API, ele também cria rankings por
marketplace para Mercado Livre, Enjoei, Adidas, Nike, Brechó do Futebol e
Netshoes. Os filtros atuam sobre todas as buscas de clubes brasileiros, clubes
internacionais e seleções nas categorias:

- `small_club_cheap`
- `cult_beautiful`
- `light_collectible`

Cada busca pode ser adicionada a uma shortlist. A seleção fica codificada no
endereço do painel, sobrevive a recargas e pode ser exportada como JSON para uma
rotina de monitoramento. Alertas automáticos de queda de preço exigem uma fonte
autorizada de preços e um canal de notificação.

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
