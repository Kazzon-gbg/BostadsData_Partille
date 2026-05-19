"""
Hämta och städa riktiga slutpriser från Booli för Partille kommun.

Output:
    data/partille_housing_real_2023_today.csv           <-- STÄDAD ML-fil
    data/partille_housing_real_2023_today_raw_debug.csv <-- råfil för felsökning

Extra i denna version:
    Scriptet lägger till ungefärliga geografiska features baserat på area_name:
        - approximate_latitude
        - approximate_longitude
        - distance_to_partille_center_km
        - distance_to_gothenburg_center_km
        - distance_to_savedalen_center_km
        - area_group

    Dessa är inte exakta koordinater per adress, men ger modellen bättre
    information om läge än bara områdesnamn.

Period:
    2023-01-01 till dagens datum.

Område:
    Partille kommun inklusive Sävedalen.

Bostadstyper:
    Villa, Radhus, Kedjehus, Parhus.

Installera:
    pip install requests beautifulsoup4 pandas

Kör:
    python hamta_partille_bostadsdata_booli.py

Viktigt:
    Använd låg hastighet och kontrollera alltid källans användarvillkor.
"""

from __future__ import annotations

import math
import random
import re
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import requests
from bs4 import BeautifulSoup


# ============================================================
# Inställningar
# ============================================================

START_DATE = date(2023, 1, 1)
END_DATE = date.today()

DATA_DIR = Path("data")
CLEAN_OUTPUT_PATH = DATA_DIR / "partille_housing_real_2023_today.csv"
RAW_DEBUG_OUTPUT_PATH = DATA_DIR / "partille_housing_real_2023_today_raw_debug.csv"

# Partille kommun på Booli.
# objectType=Villa,Kedjehus-Parhus-Radhus ger villor + radhus/parhus/kedjehus.
BASE_URL = (
    "https://www.booli.se/sok/slutpriser"
    "?areaIds=268"
    "&objectType=Villa%2CKedjehus-Parhus-Radhus"
    "&page={page}"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "sv-SE,sv;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
    "Referer": "https://www.booli.se/",
}

REQUEST_TIMEOUT_SECONDS = 45
MAX_RETRIES_PER_PAGE = 5
PAUSE_BETWEEN_PAGES_MIN = 2.5
PAUSE_BETWEEN_PAGES_MAX = 5.5

# Säkerhetsstopp så scriptet inte går oändligt om källans HTML ändras.
# För Partille från 2023 räcker normalt 80-150 sidor beroende på träffmängd.
MAX_PAGES = 1000


# ============================================================
# Geografiska features
# ============================================================
#
# Detta är ungefärliga centrumkoordinater för större områden i Partille.
# De används för att skapa bättre lägesfeatures utan att behöva geokoda varje adress.
# Koordinaterna är ungefärliga och ska därför beskrivas som "approximate".
#
# Vill du senare göra detta mer exakt kan du ersätta dessa med koordinater per adress,
# exempelvis via en geokodningstjänst eller en officiell adressdatabas.

PARTILLE_CENTER = (57.7395, 12.1066)
GOTHENBURG_CENTER = (57.7089, 11.9746)
SAVEDALEN_CENTER = (57.7320, 12.0715)

AREA_COORDINATES = {
    "sävedalen": (57.7320, 12.0715),
    "södra sävedalen": (57.7265, 12.0710),
    "norra sävedalen": (57.7390, 12.0735),
    "partille": (57.7395, 12.1066),
    "partille centrum": (57.7395, 12.1066),
    "furulund": (57.7315, 12.1120),
    "öjersjö": (57.7005, 12.1450),
    "mellby": (57.7440, 12.1180),
    "kåhög": (57.7545, 12.1305),
    "jonsered": (57.7465, 12.1730),
    "lexby": (57.7480, 12.0920),
    "ugglum": (57.7245, 12.0840),
    "skulltorp": (57.7360, 12.1210),
    "björndammen": (57.7165, 12.1280),
    "lillegården": (57.7335, 12.1005),
    "finngösa": (57.7470, 12.1030),
    "ånäs": (57.7420, 12.0960),
}


def haversine_km(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """Beräkna ungefärligt avstånd i kilometer mellan två koordinater."""
    earth_radius_km = 6371.0

    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return earth_radius_km * c


def get_area_coordinate(area_name: object, address: object = "") -> tuple[Optional[float], Optional[float], str]:
    """
    Matcha area_name/adress mot ungefärligt områdescentrum.

    Returnerar:
        latitude, longitude, area_group

    area_group används som en förenklad områdeskategori som kan vara stabilare
    än många små area_name-varianter.
    """
    text = normalize_spaces(f"{area_name} {address}").lower()

    # Matcha längsta områdesnamn först, så "södra sävedalen" hinner före "sävedalen".
    for area_key in sorted(AREA_COORDINATES.keys(), key=len, reverse=True):
        if area_key in text:
            lat, lon = AREA_COORDINATES[area_key]
            return lat, lon, area_key.title()

    # Fallback om området inte matchas.
    lat, lon = PARTILLE_CENTER
    return lat, lon, "Partille kommun"


def add_geographic_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Lägg till geografiska features baserat på area_name/adress.

    Detta ger modellen en ungefärlig lägesinformation:
        - koordinat för områdets centrum
        - avstånd till Partille centrum
        - avstånd till Göteborg centrum
        - avstånd till Sävedalen centrum

    Obs: Detta är inte exakt adressgeokodning.
    """
    if df.empty:
        return df

    df = df.copy()

    latitudes = []
    longitudes = []
    area_groups = []
    partille_distances = []
    gothenburg_distances = []
    savedalen_distances = []

    for _, row in df.iterrows():
        lat, lon, area_group = get_area_coordinate(
            row.get("area_name", ""),
            row.get("address", ""),
        )

        latitudes.append(lat)
        longitudes.append(lon)
        area_groups.append(area_group)

        if lat is None or lon is None:
            partille_distances.append(None)
            gothenburg_distances.append(None)
            savedalen_distances.append(None)
        else:
            partille_distances.append(
                round(haversine_km(lat, lon, PARTILLE_CENTER[0], PARTILLE_CENTER[1]), 2)
            )
            gothenburg_distances.append(
                round(haversine_km(lat, lon, GOTHENBURG_CENTER[0], GOTHENBURG_CENTER[1]), 2)
            )
            savedalen_distances.append(
                round(haversine_km(lat, lon, SAVEDALEN_CENTER[0], SAVEDALEN_CENTER[1]), 2)
            )

    df["approximate_latitude"] = latitudes
    df["approximate_longitude"] = longitudes
    df["area_group"] = area_groups
    df["distance_to_partille_center_km"] = partille_distances
    df["distance_to_gothenburg_center_km"] = gothenburg_distances
    df["distance_to_savedalen_center_km"] = savedalen_distances

    return df


# ============================================================
# Regexar
# ============================================================

DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
PRICE_RE = re.compile(r"(\d[\d\s]{4,})\s*kr\b")
AREA_RE = re.compile(r"(\d+(?:[,.]\d+)?)\s*(?:\+\s*(\d+(?:[,.]\d+)?))?\s*m²(?!\s*tomt)")
PLOT_RE = re.compile(r"(\d[\d\s]*(?:[,.]\d+)?)\s*m²\s*tomt")
ROOMS_RE = re.compile(r"(\d+(?:[,.]\d+)?)\s*rum")
KVM_RE = re.compile(r"(\d[\d\s]*)\s*kr/m²")
TYPE_RE = re.compile(r"\b(Villa|Radhus|Kedjehus|Parhus)\b")
SALE_TYPE_RE = re.compile(r"\b(Slutpris|Lagfart|Sista bud)\b")

# Viktigt: starta en ny rad vid själva försäljningstypen, inte vid procenttecknet.
# Procenten på Booli hör ofta till raden FÖRE nästa "Slutpris".
LISTING_START_RE = re.compile(r"(?:^|\s)(?:Slutpris|Lagfart|Sista bud)\s+")

BAD_ADDRESS_PATTERNS = [
    r"gå till innehåll",
    r"gå till sök",
    r"sök bostad",
    r"till salu",
    r"nyproduktion",
    r"visa karta",
    r"slutpriser för hus",
    r"mäklare i området",
    r"booli är",
    r"visar sida",
    r"vad tycker du",
    r"går du i säljtankar",
    r"cecilia, produktägare",
]


@dataclass
class SaleRow:
    address: str
    property_type: str
    area_name: str
    municipality: str
    is_savedalen: str
    sale_type: str
    sold_date: str
    year: int
    month: int
    final_price_sek: Optional[int]
    asking_price_sek: Optional[int]
    price_change_sek: Optional[int]
    price_change_percent: Optional[float]
    living_area_m2: Optional[float]
    extra_area_m2: Optional[float]
    rooms: Optional[float]
    plot_area_m2: Optional[float]
    price_per_m2: Optional[int]
    bid_change_percent: Optional[float]
    source_url: str
    source_text: str


def normalize_spaces(text: object) -> str:
    return " ".join(str(text).replace("\xa0", " ").split())


def clean_number(value: str | None) -> Optional[float]:
    if not value:
        return None
    value = value.replace(" ", "").replace("\xa0", "").replace(",", ".")
    try:
        return float(value)
    except ValueError:
        return None


def clean_int(value: str | None) -> Optional[int]:
    number = clean_number(value)
    if number is None:
        return None
    return int(round(number))


def parse_percent(text: str) -> Optional[float]:
    """
    Hämta prisförändring i procent.

    I Boolis listvy ligger procenten normalt EFTER säljdatumet, t.ex.
    "2026-05-18 +4,8%". Därför letar vi först efter procent efter datumet.
    Det minskar risken att procenten från föregående rad hamnar på nästa bostad.
    """
    normalized = text.replace("+/-0", "0")
    date_match = DATE_RE.search(normalized)
    search_area = normalized[date_match.end():] if date_match else normalized
    match = re.search(r"([+\-]?\d+(?:[,.]\d+)?)\s*%", search_area)
    if not match:
        return None
    return clean_number(match.group(1))


def estimate_asking_price(
    final_price: Optional[int],
    change_percent: Optional[float],
) -> tuple[Optional[int], Optional[int], Optional[float]]:
    """
    Uppskatta utgångspris från slutpris och procentuell förändring.

    Formel:
        utgångspris = slutpris / (1 + procent / 100)

    Om procent saknas går utgångspris inte att räkna fram från listvyn.
    Vi avrundar till närmaste 1 000 kr.
    """
    if final_price is None or change_percent is None:
        return None, None, None

    factor = 1 + (change_percent / 100)
    if factor <= 0:
        return None, None, change_percent

    asking_price = int(round((final_price / factor) / 1000) * 1000)
    price_change = int(final_price - asking_price)
    return asking_price, price_change, change_percent


def clean_address(address: object) -> str:
    """Tar bort Booli-prefix, procenttecken och skräp runt adressen."""
    address = normalize_spaces(address)

    address = re.sub(r"^[—–-]\s*", "", address)
    address = re.sub(r"^[+\-]?\d+(?:[,.]\d+)?\s*%\s*", "", address)
    address = re.sub(r"^\+/-0\s*%\s*", "", address)
    address = re.sub(r"^[—–-]\s*", "", address)

    # Om hela sidtexten råkat hamna här: ta delen efter sista Slutpris/Lagfart/Sista bud.
    markers = list(SALE_TYPE_RE.finditer(address))
    if markers:
        address = address[markers[-1].end() :].strip()

    address = re.sub(r"^(Slutpris|Lagfart|Sista bud)\s+", "", address, flags=re.IGNORECASE)
    address = re.sub(r"\s+", " ", address).strip(" ,.;:-–—")
    return address


def is_probably_sale_row(text: str) -> bool:
    return (
        "Partille" in text
        and DATE_RE.search(text) is not None
        and PRICE_RE.search(text) is not None
        and TYPE_RE.search(text) is not None
        and SALE_TYPE_RE.search(text) is not None
    )


def segment_listings_from_text(text: str) -> list[str]:
    """
    Klipper ut varje faktisk slutprisrad ur Boolis sidtext.

    Detta är den viktiga fixen:
    tidigare kunde hela sidan bli en enda rad, vilket gav adresser som
    'Gå till innehåll Gå till sök...'.
    """
    text = normalize_spaces(text)
    starts = [m.start() for m in LISTING_START_RE.finditer(text)]
    if not starts:
        return []

    segments: list[str] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        segment = text[start:end].strip(" —")

        dates = list(DATE_RE.finditer(segment))
        if dates:
            # Behåll datum och eventuell procent direkt efter datumet.
            # Exempel: "2026-05-18 +4,8%" eller "2026-05-18 +/-0%".
            date_end = dates[-1].end()
            tail = segment[date_end:]
            percent_after_date = re.match(r"\s*(?:[+\-]?\d+(?:[,.]\d+)?|\+/-0)\s*%", tail)
            if percent_after_date:
                segment = segment[: date_end + percent_after_date.end()].strip()
            else:
                segment = segment[:date_end].strip()

        if is_probably_sale_row(segment):
            segments.append(segment)

    return segments


def extract_candidate_lines(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    candidates: list[str] = []

    full_text = soup.get_text(" ", strip=True)
    candidates.extend(segment_listings_from_text(full_text))

    # Fallback: försök även från separata taggar.
    for tag in soup.find_all(["li", "article", "div", "a"]):
        text = tag.get_text(" ", strip=True)
        if not text:
            continue
        candidates.extend(segment_listings_from_text(text))
        if is_probably_sale_row(text):
            candidates.append(normalize_spaces(text))

    # Deduplicera men behåll ordning.
    seen = set()
    unique = []
    for candidate in candidates:
        key = normalize_spaces(candidate)
        if key not in seen:
            seen.add(key)
            unique.append(key)

    return unique


def parse_sale_line(text: str, source_url: str) -> Optional[SaleRow]:
    text = normalize_spaces(text)

    date_match = DATE_RE.search(text)
    price_match = PRICE_RE.search(text)
    type_match = TYPE_RE.search(text)
    sale_type_match = SALE_TYPE_RE.search(text)

    if not date_match or not price_match or not type_match or not sale_type_match:
        return None

    sold = datetime.strptime(date_match.group(1), "%Y-%m-%d").date()
    if sold < START_DATE or sold > END_DATE:
        return None

    final_price = clean_int(price_match.group(1))
    property_type = type_match.group(1)
    sale_type = sale_type_match.group(1)

    address_part = text[sale_type_match.end() : type_match.start()].strip()
    address = clean_address(address_part)

    after_type = text[type_match.end() :]
    area_name = "Partille kommun"
    area_match = re.search(r"·\s*([^·]+?)\s*·\s*Partille", after_type)
    if area_match:
        area_name = normalize_spaces(area_match.group(1))

    area_match = AREA_RE.search(text)
    living_area = clean_number(area_match.group(1)) if area_match else None
    extra_area = clean_number(area_match.group(2)) if area_match and area_match.group(2) else None

    rooms_match = ROOMS_RE.search(text)
    rooms = clean_number(rooms_match.group(1)) if rooms_match else None

    plot_match = PLOT_RE.search(text)
    plot_area = clean_number(plot_match.group(1)) if plot_match else None

    kvm_matches = KVM_RE.findall(text)
    price_per_m2 = clean_int(kvm_matches[-1]) if kvm_matches else None

    bid_change = parse_percent(text)
    asking_price, price_change, price_change_percent = estimate_asking_price(final_price, bid_change)

    is_savedalen = "yes" if "sävedalen" in f"{area_name} {address}".lower() else "no"

    return SaleRow(
        address=address,
        property_type=property_type,
        area_name=area_name,
        municipality="Partille",
        is_savedalen=is_savedalen,
        sale_type=sale_type,
        sold_date=sold.isoformat(),
        year=sold.year,
        month=sold.month,
        final_price_sek=final_price,
        asking_price_sek=asking_price,
        price_change_sek=price_change,
        price_change_percent=price_change_percent,
        living_area_m2=living_area,
        extra_area_m2=extra_area,
        rooms=rooms,
        plot_area_m2=plot_area,
        price_per_m2=price_per_m2,
        bid_change_percent=bid_change,
        source_url=source_url,
        source_text=text,
    )


def clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Städar rådata till ML-vänlig CSV."""
    if df.empty:
        return df

    df = df.copy()

    # Säkerställ att alla kolumner finns.
    expected_cols = [
        "address", "property_type", "area_name", "municipality", "is_savedalen",
        "sale_type", "sold_date", "year", "month", "final_price_sek",
        "asking_price_sek", "price_change_sek", "price_change_percent",
        "living_area_m2", "extra_area_m2", "rooms", "plot_area_m2",
        "price_per_m2", "bid_change_percent", "source_url", "source_text",
    ]
    for col in expected_cols:
        if col not in df.columns:
            df[col] = None

    for col in ["address", "property_type", "area_name", "municipality", "is_savedalen", "sale_type"]:
        df[col] = df[col].map(normalize_spaces)

    df["address"] = df["address"].map(clean_address)

    # Ta bort skräpadresser.
    address_lower = df["address"].str.lower()
    bad = pd.Series(False, index=df.index)
    for pattern in BAD_ADDRESS_PATTERNS:
        bad |= address_lower.str.contains(pattern, regex=True, na=False)

    bad |= df["address"].str.len().gt(80)
    bad |= df["address"].str.len().lt(2)

    df = df[~bad].copy()

    # Datatyper.
    df["sold_date"] = pd.to_datetime(df["sold_date"], errors="coerce")
    numeric_cols = [
        "final_price_sek", "asking_price_sek", "price_change_sek", "price_change_percent",
        "living_area_m2", "extra_area_m2", "rooms",
        "plot_area_m2", "price_per_m2", "bid_change_percent",
    ]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Om scriptet körs på en äldre råfil räknar vi fram utgångspris här.
    for idx, row in df.iterrows():
        asking, change, pct = estimate_asking_price(
            int(row["final_price_sek"]) if pd.notna(row["final_price_sek"]) else None,
            float(row["bid_change_percent"]) if pd.notna(row["bid_change_percent"]) else None,
        )
        if pd.isna(row["asking_price_sek"]):
            df.at[idx, "asking_price_sek"] = asking
        if pd.isna(row["price_change_sek"]):
            df.at[idx, "price_change_sek"] = change
        if pd.isna(row["price_change_percent"]):
            df.at[idx, "price_change_percent"] = pct

    df = df.dropna(subset=["sold_date", "final_price_sek"])
    df = df[df["property_type"].isin(["Villa", "Radhus", "Kedjehus", "Parhus"])]

    df = df[(df["sold_date"] >= pd.Timestamp(START_DATE)) & (df["sold_date"] <= pd.Timestamp(END_DATE))]

    df["area_name"] = df["area_name"].replace({"": "Partille kommun", "nan": "Partille kommun"})
    df["municipality"] = "Partille"
    df["is_savedalen"] = df.apply(
        lambda row: "yes" if "sävedalen" in f"{row['area_name']} {row['address']}".lower() else "no",
        axis=1,
    )

    # Deduplicering.
    # Först exakt per adress/datum/pris/boarea/typ.
    df["_address_key"] = (
        df["address"]
        .str.lower()
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )

    priority = {"Slutpris": 0, "Sista bud": 1, "Lagfart": 2}
    df["_sale_priority"] = df["sale_type"].map(priority).fillna(9)

    df = df.sort_values(["sold_date", "_sale_priority"], ascending=[False, True])
    df = df.drop_duplicates(
        subset=["_address_key", "sold_date", "final_price_sek", "living_area_m2", "property_type"],
        keep="first",
    )

    # Extra dedupe om samma objekt kommit både som Slutpris/Lagfart med små skillnader.
    df = df.drop_duplicates(
        subset=["_address_key", "final_price_sek", "living_area_m2", "rooms", "plot_area_m2"],
        keep="first",
    )

    df["year"] = df["sold_date"].dt.year.astype(int)
    df["month"] = df["sold_date"].dt.month.astype(int)
    df["sold_date"] = df["sold_date"].dt.strftime("%Y-%m-%d")

    # Outlier-flaggor. Rader tas inte bort, bara markeras.
    df["price_outlier_flag"] = (
        (df["final_price_sek"] < 1_000_000) | (df["final_price_sek"] > 25_000_000)
    ).map({True: "yes", False: "no"})

    df["area_outlier_flag"] = (
        df["living_area_m2"].notna() & ((df["living_area_m2"] < 30) | (df["living_area_m2"] > 400))
    ).map({True: "yes", False: "no"})

    df["plot_outlier_flag"] = (
        df["plot_area_m2"].notna() & ((df["plot_area_m2"] < 20) | (df["plot_area_m2"] > 10_000))
    ).map({True: "yes", False: "no"})

    # Lägg till ungefärliga geografiska features.
    df = add_geographic_features(df)

    output_columns = [
        "address",
        "property_type",
        "area_name",
        "municipality",
        "is_savedalen",
        "sale_type",
        "sold_date",
        "year",
        "month",
        "final_price_sek",
        "asking_price_sek",
        "price_change_sek",
        "price_change_percent",
        "living_area_m2",
        "extra_area_m2",
        "rooms",
        "plot_area_m2",
        "price_per_m2",
        "bid_change_percent",
        "source_url",
        "approximate_latitude",
        "approximate_longitude",
        "area_group",
        "distance_to_partille_center_km",
        "distance_to_gothenburg_center_km",
        "distance_to_savedalen_center_km",
        "price_outlier_flag",
        "area_outlier_flag",
        "plot_outlier_flag",
    ]

    df = df[output_columns]
    df = df.sort_values("sold_date", ascending=False).reset_index(drop=True)
    return df


def fetch_page_with_retries(session: requests.Session, url: str, page: int) -> Optional[str]:
    """
    Hämtar en sida robust.

    Felet du fick:
        RemoteDisconnected('Remote end closed connection without response')

    betyder normalt att servern avbröt anslutningen. Det kan hända om man
    hämtar många sidor, om nätverket tappar kort, eller om servern inte gillar
    requesten. Därför använder vi retry med ökande paus.
    """
    last_error: Optional[Exception] = None

    for attempt in range(1, MAX_RETRIES_PER_PAGE + 1):
        try:
            response = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)

            # Om servern ber oss sakta ner, vänta extra länge och försök igen.
            if response.status_code in {403, 429, 500, 502, 503, 504}:
                wait_seconds = min(90, 8 * attempt + random.uniform(2, 8))
                print(
                    f"  Servern svarade {response.status_code} på sida {page}. "
                    f"Väntar {wait_seconds:.1f} sek och försöker igen "
                    f"({attempt}/{MAX_RETRIES_PER_PAGE})."
                )
                time.sleep(wait_seconds)
                continue

            response.raise_for_status()
            return response.text

        except requests.exceptions.RequestException as exc:
            last_error = exc
            wait_seconds = min(90, 6 * attempt + random.uniform(2, 8))
            print(
                f"  Anslutningen bröts på sida {page}: {exc}.\n"
                f"  Försök {attempt}/{MAX_RETRIES_PER_PAGE}. "
                f"Väntar {wait_seconds:.1f} sek och försöker igen."
            )
            time.sleep(wait_seconds)

            # Skapa ny session ibland. Det hjälper när en keep-alive-anslutning blivit dålig.
            if attempt in {2, 4}:
                session.close()
                session.headers.update(HEADERS)

    print(f"  Kunde inte hämta sida {page} efter {MAX_RETRIES_PER_PAGE} försök. Hoppar över sidan.")
    if last_error:
        print(f"  Sista fel: {last_error}")
    return None


def save_partial_files(rows: list[SaleRow]) -> None:
    """Sparar hittills hämtade rader så att man inte tappar allt vid avbrott."""
    if not rows:
        return
    partial_raw_df = pd.DataFrame([asdict(row) for row in rows])
    partial_clean_df = clean_dataframe(partial_raw_df)
    partial_raw_df.to_csv(RAW_DEBUG_OUTPUT_PATH, index=False, encoding="utf-8-sig")
    partial_clean_df.to_csv(CLEAN_OUTPUT_PATH, index=False, encoding="utf-8-sig")


def download_raw_data() -> pd.DataFrame:
    rows: list[SaleRow] = []
    seen_raw_keys = set()
    session = requests.Session()
    session.headers.update(HEADERS)

    page = 1
    oldest_seen: Optional[date] = None
    empty_pages = 0
    skipped_pages = 0

    while page <= MAX_PAGES:
        url = BASE_URL.format(page=page)
        print(f"Hämtar sida {page}: {url}")

        html = fetch_page_with_retries(session, url, page)
        if html is None:
            skipped_pages += 1
            empty_pages += 1
            page += 1
            time.sleep(random.uniform(PAUSE_BETWEEN_PAGES_MIN, PAUSE_BETWEEN_PAGES_MAX))
            continue

        candidates = extract_candidate_lines(html)
        page_rows: list[SaleRow] = []

        for line in candidates:
            row = parse_sale_line(line, url)
            if row is None:
                continue

            key = (
                row.address.lower(),
                row.sold_date,
                row.final_price_sek,
                row.living_area_m2,
                row.property_type,
                row.sale_type,
            )
            if key in seen_raw_keys:
                continue

            seen_raw_keys.add(key)
            page_rows.append(row)

        if not page_rows:
            empty_pages += 1
            print(f"  Inga tolkade rader på sida {page}.")
        else:
            empty_pages = 0
            rows.extend(page_rows)
            dates = [datetime.strptime(r.sold_date, "%Y-%m-%d").date() for r in page_rows]
            oldest_seen = min(dates) if oldest_seen is None else min(oldest_seen, min(dates))
            print(f"  +{len(page_rows)} råa rader. Äldsta datum hittills: {oldest_seen}")

        # Spara löpande var femte sida, så att en senare avbruten anslutning inte förstör körningen.
        if page % 5 == 0:
            save_partial_files(rows)
            print("  Delresultat sparat.")

        if oldest_seen is not None and oldest_seen < START_DATE:
            print(f"Stoppar: äldsta datum {oldest_seen} är före startdatum {START_DATE}.")
            break

        if empty_pages >= 5:
            print("Stoppar efter 5 tomma/överhoppade sidor. Boolis HTML kan ha ändrats eller servern avbryter.")
            break

        page += 1
        time.sleep(random.uniform(PAUSE_BETWEEN_PAGES_MIN, PAUSE_BETWEEN_PAGES_MAX))

    session.close()
    print(f"Överhoppade sidor på grund av anslutningsfel: {skipped_pages}")
    return pd.DataFrame([asdict(row) for row in rows])

def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    raw_df = download_raw_data()
    clean_df = clean_dataframe(raw_df)

    raw_df.to_csv(RAW_DEBUG_OUTPUT_PATH, index=False, encoding="utf-8-sig")
    clean_df.to_csv(CLEAN_OUTPUT_PATH, index=False, encoding="utf-8-sig")

    print("\nKlart!")
    print(f"Råfil för kontroll: {RAW_DEBUG_OUTPUT_PATH}")
    print(f"Städad ML-fil:     {CLEAN_OUTPUT_PATH}")
    print(f"Antal råa rader:   {len(raw_df)}")
    print(f"Antal städade:     {len(clean_df)}")

    if not clean_df.empty:
        print(f"Datumintervall: {clean_df['sold_date'].min()} till {clean_df['sold_date'].max()}")

        print("\nRader per år:")
        print(clean_df["year"].value_counts().sort_index().to_string())

        print("\nBostadstyper:")
        print(clean_df["property_type"].value_counts().to_string())

        if "area_group" in clean_df.columns:
            print("\nOmrådesgrupper:")
            print(clean_df["area_group"].value_counts().to_string())

        print("\nFörsta 10 raderna i städad fil:")
        print(clean_df.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
