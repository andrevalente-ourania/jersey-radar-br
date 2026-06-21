import os
from datetime import datetime
from urllib.parse import urlencode

import httpx
import yaml
from dotenv import load_dotenv


load_dotenv()


MELI_SEARCH_URL = "https://api.mercadolibre.com/sites/MLB/search"
MELI_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"
MELI_WEB_SEARCH_URL = "https://lista.mercadolivre.com.br/"
MAX_API_ATTEMPTS = 10
MAX_FALLBACK_LINKS = 20
BUCKETS = ("small_club_cheap", "cult_beautiful", "light_collectible")
QUERY_SPECS = (
    ("small_club_cheap", "camisa {club} oficial promoção"),
    ("cult_beautiful", "camisa {club} goleiro terceira"),
    ("light_collectible", "camisa {club} comemorativa patch"),
)


class MercadoLivreBlockedError(Exception):
    """Raised when Mercado Livre blocks API searches."""


def load_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as file:
        return yaml.safe_load(file)


def normalize_text(value: str) -> str:
    if not value:
        return ""
    return value.lower().strip()


def contains_any(text: str, terms: list[str]) -> bool:
    text = normalize_text(text)
    return any(normalize_text(term) in text for term in terms)


def price_band(price: float, thresholds: dict) -> str:
    if price <= thresholds["very_cheap"]:
        return "very_cheap"
    if price <= thresholds["cheap"]:
        return "cheap"
    if price <= thresholds["good"]:
        return "good"
    if price <= thresholds["max_default"]:
        return "acceptable"
    if price <= thresholds["max_special"]:
        return "special_only"
    return "too_expensive"


def infer_bucket(title: str, price: float, club: str | None, rules: dict) -> str:
    title_norm = normalize_text(title)
    thresholds = rules["price_thresholds"]
    negative_terms = rules["negative_terms"]
    positive_terms = rules["positive_terms"]

    if contains_any(title_norm, negative_terms):
        return "discard"

    band = price_band(price, thresholds)

    has_positive = contains_any(title_norm, positive_terms)
    has_cult_signal = contains_any(
        title_norm,
        ["goleiro", "terceira", "patch", "copa do nordeste", "centenário", "comemorativa"],
    )

    if club and band in ["very_cheap", "cheap", "good"]:
        if has_cult_signal:
            return "cult_beautiful"
        return "small_club_cheap"

    if club and has_positive and band in ["acceptable", "special_only"]:
        return "light_collectible"

    return "discard"


def score_listing(title: str, price: float, bucket: str, rules: dict) -> int:
    thresholds = rules["price_thresholds"]
    band = price_band(price, thresholds)

    score = 0

    if band == "very_cheap":
        score += 35
    elif band == "cheap":
        score += 30
    elif band == "good":
        score += 22
    elif band == "acceptable":
        score += 15
    elif band == "special_only":
        score += 8

    if bucket == "small_club_cheap":
        score += 35
    elif bucket == "cult_beautiful":
        score += 40
    elif bucket == "light_collectible":
        score += 25
    else:
        score -= 30

    title_norm = normalize_text(title)

    if contains_any(title_norm, rules["positive_terms"]):
        score += 15

    if contains_any(title_norm, ["goleiro", "terceira", "patch", "copa do nordeste", "centenário"]):
        score += 10

    if contains_any(title_norm, rules["negative_terms"]):
        score -= 50

    return max(0, min(100, score))


def find_club_in_title(title: str, clubs: list[str]) -> str | None:
    title_norm = normalize_text(title)
    for club in clubs:
        club_norm = normalize_text(club)
        if club_norm in title_norm:
            return club
    return None


def build_queries(clubs: list[str]) -> list[dict[str, str]]:
    queries = []
    for club in clubs:
        for bucket, template in QUERY_SPECS:
            queries.append({"bucket": bucket, "query": template.format(club=club)})
    return queries


def build_fallback_url(query: str) -> str:
    return f"{MELI_WEB_SEARCH_URL}?{urlencode({'q': query})}"


def get_meli_access_token() -> str | None:
    client_id = os.getenv("MELI_CLIENT_ID")
    client_secret = os.getenv("MELI_CLIENT_SECRET")

    if not client_id or not client_secret:
        print("OAuth: not configured; fallback mode will be used.")
        return None

    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
    }

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }

    try:
        with httpx.Client(timeout=20, follow_redirects=True) as client:
            response = client.post(MELI_TOKEN_URL, data=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
    except (httpx.HTTPError, ValueError):
        print("OAuth: failed; fallback mode will be used.")
        return None

    access_token = data.get("access_token") if isinstance(data, dict) else None
    if not access_token:
        print("OAuth: no access token returned; fallback mode will be used.")
        return None

    print("OAuth: access token generated successfully.")
    return access_token


def search_mercado_livre(query: str, access_token: str, limit: int = 10) -> list[dict]:
    params = {
        "q": query,
        "limit": limit,
    }

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        "Authorization": f"Bearer {access_token}",
    }

    with httpx.Client(timeout=20, headers=headers, follow_redirects=True) as client:
        response = client.get(MELI_SEARCH_URL, params=params)
        if response.status_code == 403:
            raise MercadoLivreBlockedError
        response.raise_for_status()
        data = response.json()

    return data.get("results", []) if isinstance(data, dict) else []


def collect_opportunities(results: list[dict], clubs: list[str], rules: dict) -> list[dict]:
    opportunities = []
    for item in results:
        if not isinstance(item, dict):
            continue
        title = item.get("title", "")
        try:
            price = float(item.get("price") or 0)
        except (TypeError, ValueError):
            continue

        club = find_club_in_title(title, clubs)
        bucket = infer_bucket(title=title, price=price, club=club, rules=rules)
        score = score_listing(title=title, price=price, bucket=bucket, rules=rules)

        if bucket != "discard" and score >= 65:
            opportunities.append(
                {
                    "score": score,
                    "bucket": bucket,
                    "club": club,
                    "title": title,
                    "price": price,
                    "condition": item.get("condition", ""),
                    "url": item.get("permalink", ""),
                    "thumbnail": item.get("thumbnail", ""),
                }
            )
    return opportunities


def print_grouped_opportunities(opportunities: list[dict]) -> None:
    print("\nTop opportunities:")
    for bucket in BUCKETS:
        print(f"\n{bucket}")
        bucket_items = [item for item in opportunities if item["bucket"] == bucket][:15]
        if not bucket_items:
            print("  No opportunities found.")
            continue
        for index, item in enumerate(bucket_items, start=1):
            print(
                f"  {index}. [{item['score']}] {item['club']} | "
                f"R${item['price']:.2f} | {item['title']}"
            )
            print(f"     {item['url']}")


def print_fallback_links(queries: list[dict[str, str]]) -> None:
    print(f"\nFallback mode: top {MAX_FALLBACK_LINKS} Mercado Livre searches to inspect manually")
    fallback_queries = queries[:MAX_FALLBACK_LINKS]
    for bucket in BUCKETS:
        print(f"\n{bucket}")
        bucket_queries = [item for item in fallback_queries if item["bucket"] == bucket]
        for index, item in enumerate(bucket_queries, start=1):
            print(f"  {index}. {item['query']}")
            print(f"     {build_fallback_url(item['query'])}")


def main() -> None:
    clubs_config = load_yaml("config/clubs.yml")
    rules = load_yaml("config/rules.yml")

    small_clubs = clubs_config["small_clubs"]
    queries = build_queries(small_clubs)
    access_token = get_meli_access_token()
    fallback_mode = access_token is None
    all_opportunities = []

    print(f"Jersey Radar BR — {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    if access_token:
        print(f"API: trying at most {MAX_API_ATTEMPTS} searches.")
        for attempt, query_item in enumerate(queries[:MAX_API_ATTEMPTS], start=1):
            query = query_item["query"]
            try:
                results = search_mercado_livre(query=query, access_token=access_token, limit=10)
            except MercadoLivreBlockedError:
                print(f"API: blocked with HTTP 403 on attempt {attempt}; stopping API searches.")
                fallback_mode = True
                break
            except (httpx.HTTPError, ValueError):
                print(f"API: search {attempt} failed; continuing within the {MAX_API_ATTEMPTS}-attempt limit.")
                continue

            all_opportunities.extend(collect_opportunities(results, small_clubs, rules))

    all_opportunities.sort(key=lambda item: item["score"], reverse=True)
    print_grouped_opportunities(all_opportunities)

    if fallback_mode:
        print_fallback_links(queries)
    else:
        print("\nAPI: searches completed without an HTTP 403 block.")


if __name__ == "__main__":
    main()
