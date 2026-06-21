import os
import re
import yaml
import httpx
from datetime import datetime
from dotenv import load_dotenv


load_dotenv()


MELI_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"


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


def build_queries(clubs: list[str]) -> list[str]:
    query_templates = [
        "camisa {club} oficial",
        "camisa {club} nova",
        "camisa {club} goleiro",
        "camisa {club} terceira",
        "camisa {club} torcedor",
    ]

    queries = []
    for club in clubs:
        for template in query_templates:
            queries.append(template.format(club=club))

    return queries

def get_meli_access_token() -> str | None:
    client_id = os.getenv("MELI_CLIENT_ID")
    client_secret = os.getenv("MELI_CLIENT_SECRET")

    if not client_id or not client_secret:
        print("MELI_CLIENT_ID or MELI_CLIENT_SECRET not configured.")
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

    with httpx.Client(timeout=20, follow_redirects=True) as client:
        response = client.post(MELI_TOKEN_URL, data=payload, headers=headers)
        response.raise_for_status()
        data = response.json()

    return data.get("access_token")

def search_mercado_livre(query: str, access_token: str | None, limit: int = 10) -> list[dict]:
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
    }

    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"

    with httpx.Client(timeout=20, headers=headers, follow_redirects=True) as client:
        response = client.get(MELI_SEARCH_URL, params=params)
        response.raise_for_status()
        data = response.json()

    return data.get("results", [])


def main() -> None:
    clubs_config = load_yaml("config/clubs.yml")
    rules = load_yaml("config/rules.yml")

    access_token = get_meli_access_token()
    if access_token:
        print("Mercado Livre access token generated successfully.")
    else:
        print("Running without Mercado Livre access token.")

    all_opportunities = []

    print(f"Jersey Radar BR — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Running {len(queries)} Mercado Livre searches...")

    for query in queries[:30]:
        try:
            results = search_mercado_livre(query=query, access_token=access_token, limit=10)
        except Exception as error:
            print(f"Error searching '{query}': {error}")
            continue

        for item in results:
            title = item.get("title", "")
            price = float(item.get("price") or 0)
            url = item.get("permalink", "")
            thumbnail = item.get("thumbnail", "")
            condition = item.get("condition", "")

            club = find_club_in_title(title, small_clubs)
            bucket = infer_bucket(title=title, price=price, club=club, rules=rules)
            score = score_listing(title=title, price=price, bucket=bucket, rules=rules)

            if bucket != "discard" and score >= 65:
                all_opportunities.append(
                    {
                        "score": score,
                        "bucket": bucket,
                        "club": club,
                        "title": title,
                        "price": price,
                        "condition": condition,
                        "url": url,
                        "thumbnail": thumbnail,
                    }
                )

    all_opportunities = sorted(
        all_opportunities,
        key=lambda item: item["score"],
        reverse=True,
    )

    print("\nTop opportunities:")
    for index, item in enumerate(all_opportunities[:15], start=1):
        print(
            f"{index}. [{item['score']}] {item['bucket']} | "
            f"{item['club']} | R${item['price']:.2f} | {item['title']}"
        )
        print(f"   {item['url']}")

    if not all_opportunities:
        print("No opportunities found today.")


if __name__ == "__main__":
    main()
