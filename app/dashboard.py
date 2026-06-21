import os
from datetime import datetime

import httpx
import streamlit as st

from main import (
    BUCKETS,
    MAX_API_ATTEMPTS,
    MAX_FALLBACK_LINKS,
    MercadoLivreBlockedError,
    SEARCH_PROVIDERS,
    build_provider_url,
    build_queries,
    collect_opportunities,
    get_meli_access_token,
    load_yaml,
    search_mercado_livre,
)


BUCKET_LABELS = {
    "small_club_cheap": "Achados baratos",
    "cult_beautiful": "Cult e bonitas",
    "light_collectible": "Colecionáveis leves",
}


def load_streamlit_secrets() -> None:
    for key in ("MELI_CLIENT_ID", "MELI_CLIENT_SECRET"):
        if os.getenv(key):
            continue
        try:
            value = st.secrets.get(key)
        except Exception:
            value = None
        if value:
            os.environ[key] = str(value)


@st.cache_data(ttl=900, show_spinner=False)
def run_dashboard_search() -> dict:
    load_streamlit_secrets()
    clubs_config = load_yaml("config/clubs.yml")
    clubs = clubs_config["small_clubs"]
    national_teams = clubs_config.get("national_teams", [])
    search_targets = [*clubs, *national_teams]
    rules = load_yaml("config/rules.yml")
    queries = build_queries(clubs, national_teams)
    access_token = get_meli_access_token()
    opportunities = []
    api_blocked = False
    failed_searches = 0

    if access_token:
        for query_item in queries[:MAX_API_ATTEMPTS]:
            try:
                results = search_mercado_livre(
                    query=query_item["query"],
                    access_token=access_token,
                    limit=10,
                )
            except MercadoLivreBlockedError:
                api_blocked = True
                break
            except (httpx.HTTPError, ValueError):
                failed_searches += 1
                continue
            opportunities.extend(collect_opportunities(results, search_targets, rules))

    opportunities.sort(key=lambda item: item["score"], reverse=True)
    return {
        "oauth_ok": bool(access_token),
        "api_blocked": api_blocked,
        "failed_searches": failed_searches,
        "opportunities": opportunities,
        "fallback_queries": queries[:MAX_FALLBACK_LINKS],
        "updated_at": datetime.now().isoformat(timespec="minutes"),
    }


def filtered_items(items: list[dict], min_score: int, max_price: int, club_filter: str) -> list[dict]:
    return [
        item
        for item in items
        if item["score"] >= min_score
        and item["price"] <= max_price
        and (club_filter == "Todos" or item["club"] == club_filter)
    ]


def render_listing(item: dict) -> None:
    with st.container(border=True):
        image_column, details_column = st.columns([1, 2], gap="medium")
        with image_column:
            thumbnail = item.get("thumbnail", "").replace("http://", "https://", 1)
            if thumbnail:
                st.image(thumbnail, use_container_width=True)
            else:
                st.markdown("<div class='shirt-placeholder'>👕</div>", unsafe_allow_html=True)

        with details_column:
            st.markdown(f"#### {item['title']}")
            st.markdown(f"<div class='price'>R$ {item['price']:,.2f}</div>", unsafe_allow_html=True)
            st.progress(item["score"] / 100, text=f"Score {item['score']}/100")
            club = item.get("club") or "Clube não identificado"
            condition = "Novo" if item.get("condition") == "new" else item.get("condition", "")
            st.caption(f"{club} · {condition}" if condition else club)
            if item.get("url"):
                st.link_button("Ver no Mercado Livre", item["url"], use_container_width=True)


def render_results(data: dict, min_score: int, max_price: int, club_filter: str) -> None:
    items = filtered_items(data["opportunities"], min_score, max_price, club_filter)
    tabs = st.tabs([BUCKET_LABELS[bucket] for bucket in BUCKETS])
    for bucket, tab in zip(BUCKETS, tabs):
        with tab:
            bucket_items = [item for item in items if item["bucket"] == bucket]
            if not bucket_items:
                st.info("Nenhuma camiseta encontrada com estes filtros.")
            for item in bucket_items:
                render_listing(item)


def render_fallback(queries: list[dict[str, str]], provider_keys: list[str]) -> None:
    tabs = st.tabs([BUCKET_LABELS[bucket] for bucket in BUCKETS])
    for bucket, tab in zip(BUCKETS, tabs):
        with tab:
            bucket_queries = [item for item in queries if item["bucket"] == bucket]
            for item in bucket_queries:
                with st.container(border=True):
                    target_type = "Seleção" if item.get("kind") == "national_team" else "Clube"
                    st.markdown(f"**{item['query']}** · {target_type}")
                    columns = st.columns(3)
                    for index, provider_key in enumerate(provider_keys):
                        provider = SEARCH_PROVIDERS[provider_key]
                        with columns[index % 3]:
                            st.link_button(
                                provider["label"],
                                build_provider_url(provider_key, item["query"]),
                                use_container_width=True,
                            )


def main() -> None:
    st.set_page_config(page_title="Jersey Radar BR", page_icon="👕", layout="wide")
    st.markdown(
        """
        <style>
        .block-container {max-width: 1180px; padding-top: 2rem;}
        .price {font-size: 1.65rem; font-weight: 750; color: #00a650; margin-bottom: .75rem;}
        .shirt-placeholder {font-size: 5rem; text-align: center; padding: 2rem 0;}
        [data-testid="stMetricValue"] {font-size: 1.55rem;}
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.title("👕 Jersey Radar BR")
    st.caption(
        "Camisas de clubes e seleções em várias lojas, organizadas por preço, "
        "beleza e potencial de coleção."
    )

    with st.spinner("Procurando camisetas..."):
        data = run_dashboard_search()

    if st.sidebar.button("Atualizar busca", type="primary", use_container_width=True):
        run_dashboard_search.clear()
        st.rerun()

    st.sidebar.header("Filtros")
    min_score = st.sidebar.slider("Score mínimo", 0, 100, 65, 5)
    max_price = st.sidebar.slider("Preço máximo", 50, 1000, 350, 25)
    clubs = sorted({item["club"] for item in data["opportunities"] if item.get("club")})
    club_filter = st.sidebar.selectbox("Clube ou seleção", ["Todos", *clubs])
    provider_keys = st.sidebar.multiselect(
        "Lojas",
        options=list(SEARCH_PROVIDERS),
        default=list(SEARCH_PROVIDERS),
        format_func=lambda key: SEARCH_PROVIDERS[key]["label"],
    )

    metric_1, metric_2, metric_3 = st.columns(3)
    metric_1.metric("Oportunidades", len(data["opportunities"]))
    metric_2.metric("OAuth", "Ativo" if data["oauth_ok"] else "Indisponível")
    metric_3.metric("API Mercado Livre", "Bloqueada" if data["api_blocked"] else "Disponível")
    st.caption(f"Atualizado em {data['updated_at'].replace('T', ' ')}")

    if data["opportunities"]:
        render_results(data, min_score, max_price, club_filter)

    if not data["opportunities"] and not (data["api_blocked"] or not data["oauth_ok"]):
        st.info("A busca terminou sem oportunidades acima do score mínimo.")

    st.divider()
    st.subheader("Buscar promoções em outras lojas")
    if data["api_blocked"]:
        st.warning(
            "A API do Mercado Livre bloqueou a busca automática, mas os links "
            "multiloja abaixo continuam funcionando."
        )
    if provider_keys:
        render_fallback(data["fallback_queries"], provider_keys)
    else:
        st.info("Selecione pelo menos uma loja no filtro lateral.")


if __name__ == "__main__":
    main()
