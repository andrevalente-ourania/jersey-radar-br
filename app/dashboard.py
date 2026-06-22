import hashlib
import json
import os
from datetime import datetime
from itertools import zip_longest
from urllib.parse import urlencode

import httpx
import streamlit as st

from main import (
    BUCKETS,
    MAX_API_ATTEMPTS,
    MercadoLivreBlockedError,
    collect_opportunities,
    get_meli_access_token,
    load_yaml,
    search_mercado_livre,
)


SEARCH_PROVIDERS = {
    "mercado_livre": {
        "label": "Mercado Livre",
        "base_url": "https://lista.mercadolivre.com.br/",
        "query_param": "q",
    },
    "enjoei": {
        "label": "Enjoei",
        "base_url": "https://www.enjoei.com.br/s",
        "query_param": "q",
    },
    "adidas": {
        "label": "Adidas",
        "base_url": "https://www.adidas.com.br/search",
        "query_param": "q",
    },
    "nike": {
        "label": "Nike",
        "base_url": "https://www.nike.com.br/nav",
        "query_param": "q",
    },
    "brecho_do_futebol": {
        "label": "Brechó do Futebol",
        "base_url": "https://brechodofutebol.com/search",
        "query_param": "q",
        "extra_params": {"type": "product"},
    },
    "netshoes": {
        "label": "Netshoes",
        "base_url": "https://www.netshoes.com.br/busca",
        "query_param": "q",
        "extra_params": {"nsCat": "Natural"},
    },
}
QUERY_SPECS = (
    ("small_club_cheap", "camisa {target} oficial promoção"),
    ("cult_beautiful", "camisa {target} retrô goleiro terceira"),
    ("light_collectible", "camisa {target} edição especial patch desconto"),
)
BUCKET_LABELS = {
    "small_club_cheap": "Desconto",
    "cult_beautiful": "Cult",
    "light_collectible": "Colecionável",
}
KIND_LABELS = {
    "club": "Clube brasileiro",
    "world_club": "Clube internacional",
    "national_team": "Seleção",
}
SEARCH_CACHE_VERSION = "marketplace-ranking-v1"
MAX_SHORTLIST_ITEMS = 30


def build_dashboard_queries(
    clubs: list[str], national_teams: list[str], world_clubs: list[str]
) -> list[dict[str, str]]:
    queries = []
    for national_team, club, world_club in zip_longest(
        national_teams, clubs, world_clubs
    ):
        for target, kind in (
            (national_team, "national_team"),
            (club, "club"),
            (world_club, "world_club"),
        ):
            if not target:
                continue
            for bucket, template in QUERY_SPECS:
                queries.append(
                    {
                        "bucket": bucket,
                        "query": template.format(target=target),
                        "target": target,
                        "kind": kind,
                    }
                )
    return queries


def build_provider_url(provider_key: str, query: str) -> str:
    provider = SEARCH_PROVIDERS[provider_key]
    params = dict(provider.get("extra_params", {}))
    params[provider["query_param"]] = query
    return f"{provider['base_url']}?{urlencode(params)}"


def query_priority(item: dict[str, str], provider_key: str) -> int:
    score = {
        "small_club_cheap": 88,
        "cult_beautiful": 84,
        "light_collectible": 82,
    }[item["bucket"]]

    if provider_key in {"enjoei", "brecho_do_futebol"}:
        if item["bucket"] == "cult_beautiful":
            score += 10
        elif item["bucket"] == "light_collectible":
            score += 7
    elif provider_key in {"adidas", "nike"}:
        if item["kind"] in {"national_team", "world_club"}:
            score += 6
        if item["bucket"] == "light_collectible":
            score += 4
    elif provider_key in {"mercado_livre", "netshoes"}:
        if item["bucket"] == "small_club_cheap":
            score += 7

    return min(100, score)


def filter_queries(
    queries: list[dict[str, str]],
    search_text: str,
    kinds: list[str],
    buckets: list[str],
) -> list[dict[str, str]]:
    normalized_search = search_text.casefold().strip()
    return [
        item
        for item in queries
        if item["kind"] in kinds
        and item["bucket"] in buckets
        and (
            not normalized_search
            or normalized_search in item["target"].casefold()
            or normalized_search in item["query"].casefold()
        )
    ]


def shortlist_id(provider_key: str, item: dict[str, str]) -> str:
    raw = "|".join((provider_key, item["kind"], item["target"], item["bucket"]))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def load_shortlist_ids() -> set[str]:
    raw = st.query_params.get("shortlist", "[]")
    try:
        values = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return set()
    if not isinstance(values, list):
        return set()
    return {str(value) for value in values[:MAX_SHORTLIST_ITEMS]}


def save_shortlist_ids(values: set[str]) -> None:
    st.query_params["shortlist"] = json.dumps(sorted(values), separators=(",", ":"))


def shortlist_entries(
    queries: list[dict[str, str]], shortlist_ids: set[str]
) -> list[dict]:
    entries = []
    for provider_key, provider in SEARCH_PROVIDERS.items():
        for item in queries:
            item_id = shortlist_id(provider_key, item)
            if item_id not in shortlist_ids:
                continue
            entries.append(
                {
                    "id": item_id,
                    "marketplace": provider_key,
                    "marketplace_label": provider["label"],
                    "target": item["target"],
                    "target_type": KIND_LABELS[item["kind"]],
                    "category": item["bucket"],
                    "category_label": BUCKET_LABELS[item["bucket"]],
                    "query": item["query"],
                    "priority": query_priority(item, provider_key),
                    "url": build_provider_url(provider_key, item["query"]),
                }
            )
    return sorted(
        entries,
        key=lambda entry: (-entry["priority"], entry["marketplace_label"], entry["target"]),
    )


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
def run_dashboard_search(cache_version: str) -> dict:
    _ = cache_version
    load_streamlit_secrets()
    clubs_config = load_yaml("config/clubs.yml")
    clubs = clubs_config["small_clubs"]
    national_teams = clubs_config.get("national_teams", [])
    world_clubs = clubs_config.get("world_clubs", [])
    search_targets = [*clubs, *national_teams, *world_clubs]
    rules = load_yaml("config/rules.yml")
    queries = build_dashboard_queries(clubs, national_teams, world_clubs)
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
        "queries": queries,
        "updated_at": datetime.now().isoformat(timespec="minutes"),
    }


def filtered_items(
    items: list[dict], min_score: int, max_price: int, club_filter: str
) -> list[dict]:
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
            st.markdown(
                f"<div class='price'>R$ {item['price']:,.2f}</div>",
                unsafe_allow_html=True,
            )
            st.progress(item["score"] / 100, text=f"Score {item['score']}/100")
            club = item.get("club") or "Clube não identificado"
            condition = "Novo" if item.get("condition") == "new" else item.get("condition", "")
            st.caption(f"{club} · {condition}" if condition else club)
            if item.get("url"):
                st.link_button("Ver no Mercado Livre", item["url"], use_container_width=True)


def render_results(
    data: dict, min_score: int, max_price: int, club_filter: str
) -> None:
    items = filtered_items(data["opportunities"], min_score, max_price, club_filter)
    if not items:
        st.info("Nenhum anúncio automático encontrado com estes filtros.")
        return
    tabs = st.tabs([BUCKET_LABELS[bucket] for bucket in BUCKETS])
    for bucket, tab in zip(BUCKETS, tabs):
        with tab:
            for item in [entry for entry in items if entry["bucket"] == bucket]:
                render_listing(item)


def toggle_shortlist(
    provider_key: str, item: dict[str, str], shortlist_ids: set[str]
) -> None:
    item_id = shortlist_id(provider_key, item)
    updated = set(shortlist_ids)
    if item_id in updated:
        updated.remove(item_id)
    elif len(updated) < MAX_SHORTLIST_ITEMS:
        updated.add(item_id)
    save_shortlist_ids(updated)
    st.rerun()


def render_marketplace_rankings(
    queries: list[dict[str, str]],
    provider_keys: list[str],
    min_priority: int,
    results_per_marketplace: int,
    shortlist_ids: set[str],
) -> None:
    if not provider_keys:
        st.info("Selecione pelo menos um marketplace no filtro lateral.")
        return
    if not queries:
        st.info("Nenhuma busca corresponde aos filtros escolhidos.")
        return

    tabs = st.tabs([SEARCH_PROVIDERS[key]["label"] for key in provider_keys])
    for provider_key, tab in zip(provider_keys, tabs):
        with tab:
            ranked = sorted(
                (
                    (query_priority(item, provider_key), item)
                    for item in queries
                    if query_priority(item, provider_key) >= min_priority
                ),
                key=lambda pair: (-pair[0], pair[1]["target"]),
            )[:results_per_marketplace]
            st.caption(f"{len(ranked)} buscas priorizadas neste marketplace")
            for rank, (priority, item) in enumerate(ranked, start=1):
                item_id = shortlist_id(provider_key, item)
                with st.container(border=True):
                    title_column, score_column = st.columns([4, 1])
                    with title_column:
                        st.markdown(f"#### #{rank} · {item['target']}")
                        st.caption(
                            f"{KIND_LABELS[item['kind']]} · {BUCKET_LABELS[item['bucket']]}"
                        )
                    with score_column:
                        st.metric("Prioridade", priority)
                    st.markdown(f"**Busca:** {item['query']}")
                    open_column, shortlist_column = st.columns(2)
                    with open_column:
                        st.link_button(
                            f"Abrir na {SEARCH_PROVIDERS[provider_key]['label']}",
                            build_provider_url(provider_key, item["query"]),
                            use_container_width=True,
                        )
                    with shortlist_column:
                        button_label = (
                            "✓ Remover da shortlist"
                            if item_id in shortlist_ids
                            else "☆ Adicionar à shortlist"
                        )
                        if st.button(
                            button_label,
                            key=f"shortlist-{item_id}",
                            use_container_width=True,
                        ):
                            toggle_shortlist(provider_key, item, shortlist_ids)


def render_shortlist(queries: list[dict[str, str]], shortlist_ids: set[str]) -> None:
    entries = shortlist_entries(queries, shortlist_ids)
    if not entries:
        st.info(
            "Sua shortlist está vazia. No ranking, use “Adicionar à shortlist” "
            "nas buscas que você quer acompanhar."
        )
        return

    st.caption(
        "A shortlist fica salva no endereço desta página. Favorite ou compartilhe "
        "este link para recuperar a mesma seleção."
    )
    for entry in entries:
        with st.container(border=True):
            details_column, action_column = st.columns([4, 1])
            with details_column:
                st.markdown(
                    f"**{entry['target']}** · {entry['marketplace_label']} · "
                    f"{entry['category_label']}"
                )
                st.caption(entry["query"])
                st.link_button("Abrir busca", entry["url"])
            with action_column:
                st.metric("Prioridade", entry["priority"])
                if st.button(
                    "Remover",
                    key=f"remove-{entry['id']}",
                    use_container_width=True,
                ):
                    updated = set(shortlist_ids)
                    updated.remove(entry["id"])
                    save_shortlist_ids(updated)
                    st.rerun()

    export_data = {
        "schema_version": 1,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "entries": entries,
    }
    st.download_button(
        "Baixar shortlist para automação diária",
        data=json.dumps(export_data, ensure_ascii=False, indent=2),
        file_name="jersey-radar-shortlist.json",
        mime="application/json",
        use_container_width=True,
    )
    st.warning(
        "A shortlist acompanha buscas. Para disparar alerta real de queda de preço, "
        "a rotina diária ainda precisa de uma fonte autorizada de preços e de um "
        "canal de notificação."
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
        "Ranking de buscas por marketplace para encontrar camisas cult, "
        "colecionáveis e com desconto."
    )

    with st.spinner("Preparando o radar..."):
        data = run_dashboard_search(SEARCH_CACHE_VERSION)

    if st.sidebar.button("Atualizar radar", type="primary", use_container_width=True):
        run_dashboard_search.clear()
        st.rerun()

    st.sidebar.header("Filtros da descoberta")
    search_text = st.sidebar.text_input("Time ou seleção", placeholder="Ex.: Venezia")
    selected_kinds = st.sidebar.multiselect(
        "Tipo",
        options=list(KIND_LABELS),
        default=list(KIND_LABELS),
        format_func=lambda key: KIND_LABELS[key],
    )
    selected_buckets = st.sidebar.multiselect(
        "Ideia",
        options=list(BUCKET_LABELS),
        default=list(BUCKET_LABELS),
        format_func=lambda key: BUCKET_LABELS[key],
    )
    provider_keys = st.sidebar.multiselect(
        "Marketplaces",
        options=list(SEARCH_PROVIDERS),
        default=list(SEARCH_PROVIDERS),
        format_func=lambda key: SEARCH_PROVIDERS[key]["label"],
    )
    min_priority = st.sidebar.slider("Prioridade mínima", 0, 100, 80, 5)
    results_per_marketplace = st.sidebar.slider(
        "Resultados por marketplace", 5, 30, 10, 5
    )

    min_score, max_price, club_filter = 65, 350, "Todos"
    if data["opportunities"]:
        st.sidebar.divider()
        st.sidebar.header("Filtros dos anúncios")
        min_score = st.sidebar.slider("Score do anúncio", 0, 100, 65, 5)
        max_price = st.sidebar.slider("Preço máximo", 50, 1000, 350, 25)
        clubs = sorted(
            {item["club"] for item in data["opportunities"] if item.get("club")}
        )
        club_filter = st.sidebar.selectbox("Clube ou seleção", ["Todos", *clubs])

    filtered_queries = filter_queries(
        data["queries"], search_text, selected_kinds, selected_buckets
    )
    shortlist_ids = load_shortlist_ids()
    valid_shortlist_ids = {
        shortlist_id(provider_key, item)
        for provider_key in SEARCH_PROVIDERS
        for item in data["queries"]
    }
    shortlist_ids &= valid_shortlist_ids

    metric_1, metric_2, metric_3, metric_4 = st.columns(4)
    metric_1.metric("Buscas filtradas", len(filtered_queries))
    metric_2.metric("Marketplaces", len(provider_keys))
    metric_3.metric("Shortlist", len(shortlist_ids))
    metric_4.metric("API Mercado Livre", "Bloqueada" if data["api_blocked"] else "Disponível")
    st.caption(f"Radar atualizado em {data['updated_at'].replace('T', ' ')}")

    if data["api_blocked"]:
        st.info(
            "A API do Mercado Livre está bloqueada, mas os rankings e filtros "
            "multimarketplace abaixo continuam funcionando."
        )

    ranking_tab, shortlist_tab, listings_tab = st.tabs(
        [
            "Ranking por marketplace",
            f"⭐ Shortlist ({len(shortlist_ids)})",
            "Anúncios automáticos",
        ]
    )
    with ranking_tab:
        render_marketplace_rankings(
            filtered_queries,
            provider_keys,
            min_priority,
            results_per_marketplace,
            shortlist_ids,
        )
    with shortlist_tab:
        render_shortlist(data["queries"], shortlist_ids)
    with listings_tab:
        if data["opportunities"]:
            render_results(data, min_score, max_price, club_filter)
        else:
            st.info(
                "Sem anúncios automáticos enquanto a busca da API estiver bloqueada. "
                "Use o ranking por marketplace."
            )


if __name__ == "__main__":
    main()
