"""
HTML parsers for every Spritmonitor page type.

The parsers use multiple selector strategies so they can survive minor
layout changes.  When no strategy succeeds the parser returns an empty
list and logs a warning — it never raises.
"""

import logging
import re
from typing import Any

from bs4 import BeautifulSoup, Tag

log = logging.getLogger(__name__)

# ── regex helpers ─────────────────────────────────────────────────────

_RE_MAKE_HREF = re.compile(r"/en/overview/(\d+)-([^/]+)\.html$")
_RE_MODEL_HREF = re.compile(
    r"/en/overview/(\d+)-[^/]+/(\d+)-([^/]+)\.html$"
)
_RE_DETAIL_HREF = re.compile(r"/en/detail/(\d+)\.html")
_RE_CONSUMPTION = re.compile(
    r"([\d]+[.,]?\d*)\s*(l/100\s*km|kWh/100\s*km|kg/100\s*km)", re.I
)
_RE_FUELINGS = re.compile(r"([\d.,]+)\s*(Fueling|Tankvorgang|fuel-up)", re.I)
_RE_POWER_KW = re.compile(r"(\d+)\s*kW", re.I)
_RE_POWER_PS = re.compile(r"(\d+)\s*(?:PS|hp|bhp)", re.I)
_RE_CCM = re.compile(r"(\d{3,5})\s*(?:ccm|cm³|cc)", re.I)
_RE_YEAR = re.compile(r"\b(19\d{2}|20\d{2})\b")
_RE_PAGINATION = re.compile(r"[?&]page=(\d+)")
_RE_COUNT = re.compile(r"([\d.,]+)\s*(?:vehicles?|Fahrzeuge?|entries?|results?)", re.I)

# Engine pattern: tries to capture e.g. "1.5 TSI", "2.0 TDI", "e-tron 55"
_RE_ENGINE = re.compile(
    r"\b(\d[.,]\d\s*(?:TSI|TFSI|TDI|CDI|HDi|dCi|BlueHDi|CDTI|JTD|"
    r"CRDI|MPI|GDI|T-GDI|FSI|GTI|GTD|GTE|MHEV|PHEV|EV|"
    r"Skyactiv|EcoBoost|PureTech|TCe|SCe|"
    r"Multijet|BlueTEC|d|i|e|D|S|M|x|"
    r"[A-Z]{0,5})(?:\s+\w+){0,3})"
    r"|"
    r"\b((?:e-tron|ID\.\d|EQ[A-Z]?)\s*\w*)",
    re.I,
)

# ══════════════════════════════════════════════════════════════════════
# 1. Parse list of makes from the homepage
# ══════════════════════════════════════════════════════════════════════

def parse_makes(html: str) -> list[dict[str, Any]]:
    """
    Return ``[{make_id, make_name, make_slug, url}, ...]``
    from the main homepage (https://www.spritmonitor.de/en/).

    Primary strategy: parse the ``<select id="manuf">`` dropdown.
    Fallback: links whose href matches the make overview pattern.
    """
    soup = BeautifulSoup(html, "lxml")
    makes: list[dict[str, Any]] = []
    seen_ids: set[int] = set()

    # ── Strategy A (primary): <select> / <option> for manufacturer ──
    #    The homepage has <select name="manuf" id="manuf"> with all makes.
    for select in soup.find_all("select"):
        select_id = (select.get("id") or select.get("name") or "").lower()
        # CHANGED: added "manuf" to match <select id="manuf">
        if any(k in select_id for k in (
            "manuf", "make", "manufacturer", "hersteller", "marke",
        )):
            for opt in select.find_all("option"):
                val = opt.get("value", "")
                if val and val.isdigit() and int(val) > 0:
                    mid = int(val)
                    if mid not in seen_ids:
                        seen_ids.add(mid)
                        name = opt.get_text(strip=True)
                        slug = _slugify(name)
                        makes.append(
                            {
                                "make_id": mid,
                                "make_name": name,
                                "make_slug": slug,
                                # Reference only — make-level overview
                                # pages do not exist on Spritmonitor.
                                "url": f"/en/overview/{mid}-{slug}.html",
                            }
                        )

    # ── Strategy B (fallback): links whose href matches the pattern ──
    if not makes:
        for a_tag in soup.find_all("a", href=_RE_MAKE_HREF):
            m = _RE_MAKE_HREF.search(a_tag["href"])
            if not m:
                continue
            make_id = int(m.group(1))
            if make_id == 0 or make_id in seen_ids:
                continue
            seen_ids.add(make_id)
            makes.append(
                {
                    "make_id": make_id,
                    "make_name": a_tag.get_text(strip=True),
                    "make_slug": m.group(2),
                    "url": a_tag["href"],
                }
            )

    if not makes:
        log.warning(
            "Could not parse any makes from the page. "
            "The HTML structure may have changed."
        )
    else:
        log.info("Parsed %d makes from page.", len(makes))

    return makes


# ══════════════════════════════════════════════════════════════════════
# 2a. Parse models from AJAX response  (NEW — primary approach)
# ══════════════════════════════════════════════════════════════════════

def parse_models_ajax(
    response_text: str,
    make_id: int,
    make_slug: str = "",
) -> list[dict[str, Any]]:
    """
    Parse the semicolon-separated response from Spritmonitor's AJAX
    endpoint ``/en/ajaxModel.action?manuf={make_id}&allowempty=false``.

    Response format: ``"1106,181;1988,914-4;1321,Amarok;452,Golf;"``
    Each entry is ``model_id,model_name`` separated by semicolons.

    Returns ``[{model_id, model_name, model_slug, url}, ...]``.
    """
    models: list[dict[str, Any]] = []
    seen_ids: set[int] = set()

    # Strip whitespace; the response may have a trailing semicolon
    text = response_text.strip()
    if not text:
        log.warning(
            "Empty AJAX response for make_id=%d.", make_id,
        )
        return models

    # ── Try CSV format first: "id,name;id,name;…" ────────────────
    entries = text.split(";")
    parsed_any = False
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(",", 1)  # split on FIRST comma only
        if len(parts) != 2:
            continue
        raw_id, raw_name = parts[0].strip(), parts[1].strip()
        try:
            mid = int(raw_id)
        except ValueError:
            continue
        if mid <= 0 or mid in seen_ids:
            continue
        if not raw_name:
            continue
        seen_ids.add(mid)
        parsed_any = True
        slug = _slugify(raw_name)
        models.append(
            {
                "model_id": mid,
                "model_name": raw_name,
                "model_slug": slug,
                "url": f"/en/overview/{make_id}-{make_slug}/{mid}-{slug}.html",
            }
        )

    # ── Fallback: maybe the response is HTML with <option> tags ──
    if not parsed_any:
        soup = BeautifulSoup(text, "lxml")
        for opt in soup.find_all("option"):
            val = opt.get("value", "")
            if not val:
                continue
            try:
                mid = int(val)
            except ValueError:
                continue
            if mid <= 0 or mid in seen_ids:
                continue
            name = opt.get_text(strip=True)
            if not name:
                continue
            seen_ids.add(mid)
            slug = _slugify(name)
            models.append(
                {
                    "model_id": mid,
                    "model_name": name,
                    "model_slug": slug,
                    "url": f"/en/overview/{make_id}-{make_slug}/{mid}-{slug}.html",
                }
            )

    if not models:
        log.warning(
            "No models found for make_id=%d (AJAX response could not be parsed). "
            "Response preview: %.200s",
            make_id, text,
        )
    else:
        log.info("Parsed %d models for make_id=%d via AJAX.", len(models), make_id)

    return models

# ══════════════════════════════════════════════════════════════════════
# 2b. Parse models from a make page (legacy fallback)
# ══════════════════════════════════════════════════════════════════════

def parse_models(html: str, make_id: int) -> list[dict[str, Any]]:
    """
    Return ``[{model_id, model_name, model_slug, url}, ...]``
    from a make's overview page (or any page containing model links /
    a model ``<select>``).

    NOTE: Make-level overview pages (/en/overview/{id}-{slug}.html) no
    longer exist on Spritmonitor.  Prefer ``parse_models_ajax`` with the
    AJAX endpoint instead.  This function is kept as a fallback in case
    the page HTML contains model links or a model ``<select>``.
    """
    soup = BeautifulSoup(html, "lxml")
    models: list[dict[str, Any]] = []
    seen_ids: set[int] = set()

    # Strategy A: links matching the model URL pattern
    for a_tag in soup.find_all("a", href=_RE_MODEL_HREF):
        m = _RE_MODEL_HREF.search(a_tag["href"])
        if not m:
            continue
        href_make_id = int(m.group(1))
        model_id = int(m.group(2))
        if href_make_id != make_id and make_id != 0:
            continue
        if model_id == 0 or model_id in seen_ids:
            continue
        seen_ids.add(model_id)
        models.append(
            {
                "model_id": model_id,
                "model_name": a_tag.get_text(strip=True),
                "model_slug": m.group(3),
                "url": a_tag["href"],
            }
        )

    # Strategy B: <select> / <option>
    if not models:
        for select in soup.find_all("select"):
            select_id = (select.get("id") or select.get("name") or "").lower()
            if any(k in select_id for k in ("model", "modell")):
                for opt in select.find_all("option"):
                    val = opt.get("value", "")
                    if val and val.isdigit() and int(val) > 0:
                        mid = int(val)
                        if mid not in seen_ids:
                            seen_ids.add(mid)
                            name = opt.get_text(strip=True)
                            slug = _slugify(name)
                            models.append(
                                {
                                    "model_id": mid,
                                    "model_name": name,
                                    "model_slug": slug,
                                    "url": f"/en/overview/{make_id}-X/{mid}-{slug}.html",
                                }
                            )

    if not models:
        log.warning(
            "Could not parse any models for make_id=%d. "
            "The HTML structure may have changed.",
            make_id,
        )
    else:
        log.info("Parsed %d models for make_id=%d.", len(models), make_id)

    return models


# ══════════════════════════════════════════════════════════════════════
# 3. Parse vehicle entries from a model page
# ══════════════════════════════════════════════════════════════════════

def parse_vehicles(html: str) -> list[dict[str, Any]]:
    """
    Parse individual vehicle entries from a model's vehicle-list page.

    Returns a list of raw vehicle dicts with fields:
        vehicle_id, title, fuel_type_raw, consumption, consumption_unit,
        fuelings, power_kw, power_ps, engine_ccm, year, detail_url
    """
    soup = BeautifulSoup(html, "lxml")
    vehicles: list[dict[str, Any]] = []

    # ── Strategy A: look for <div> / <tr> blocks that contain
    #    consumption data (very generic — works across layouts) ────────

    # Find all links to vehicle detail pages
    detail_links = soup.find_all("a", href=_RE_DETAIL_HREF)

    if detail_links:
        # For each detail link, the surrounding container has the data
        for link in detail_links:
            vehicle: dict[str, Any] = {}

            m = _RE_DETAIL_HREF.search(link["href"])
            vehicle["vehicle_id"] = int(m.group(1)) if m else None
            vehicle["detail_url"] = link["href"]
            vehicle["title"] = link.get_text(strip=True)

            # Walk up to find the enclosing container
            container = _find_container(link)
            if container is None:
                container = link.parent

            text_block = container.get_text(" ", strip=True)

            # Consumption
            cm = _RE_CONSUMPTION.search(text_block)
            if cm:
                vehicle["consumption"] = _parse_float(cm.group(1))
                vehicle["consumption_unit"] = cm.group(2).strip()
            else:
                vehicle["consumption"] = None
                vehicle["consumption_unit"] = None

            # Fuelings
            fm = _RE_FUELINGS.search(text_block)
            vehicle["fuelings"] = _parse_int(fm.group(1)) if fm else None

            # Power
            km = _RE_POWER_KW.search(text_block)
            vehicle["power_kw"] = int(km.group(1)) if km else None
            pm = _RE_POWER_PS.search(text_block)
            vehicle["power_ps"] = int(pm.group(1)) if pm else None

            # CCM
            ccm_m = _RE_CCM.search(text_block)
            vehicle["engine_ccm"] = int(ccm_m.group(1)) if ccm_m else None

            # Year
            ym = _RE_YEAR.search(vehicle.get("title", ""))
            vehicle["year"] = int(ym.group(1)) if ym else None

            # Fuel type — look for known keywords
            vehicle["fuel_type_raw"] = _extract_fuel_type(container)

            # Transmission
            vehicle["transmission"] = _extract_transmission(text_block)

            if vehicle.get("consumption") is not None:
                vehicles.append(vehicle)

    # ── Strategy B: table rows with consumption values ────────────────
    if not vehicles:
        for tr in soup.find_all("tr"):
            text = tr.get_text(" ", strip=True)
            cm = _RE_CONSUMPTION.search(text)
            if not cm:
                continue
            vehicle = {
                "consumption": _parse_float(cm.group(1)),
                "consumption_unit": cm.group(2).strip(),
                "title": text[:200],
                "vehicle_id": None,
                "detail_url": None,
                "fuelings": None,
                "power_kw": None,
                "power_ps": None,
                "engine_ccm": None,
                "year": None,
                "fuel_type_raw": _extract_fuel_type(tr),
                "transmission": _extract_transmission(text),
            }
            # Try to find a detail link in this row
            dl = tr.find("a", href=_RE_DETAIL_HREF)
            if dl:
                dm = _RE_DETAIL_HREF.search(dl["href"])
                vehicle["vehicle_id"] = int(dm.group(1)) if dm else None
                vehicle["detail_url"] = dl["href"]
                vehicle["title"] = dl.get_text(strip=True)

            fm = _RE_FUELINGS.search(text)
            vehicle["fuelings"] = _parse_int(fm.group(1)) if fm else None
            km = _RE_POWER_KW.search(text)
            vehicle["power_kw"] = int(km.group(1)) if km else None
            ym = _RE_YEAR.search(vehicle.get("title", "") or text)
            vehicle["year"] = int(ym.group(1)) if ym else None
            ccm_m = _RE_CCM.search(text)
            vehicle["engine_ccm"] = int(ccm_m.group(1)) if ccm_m else None

            vehicles.append(vehicle)

    log.debug("Parsed %d vehicle entries from page.", len(vehicles))
    return vehicles


# ══════════════════════════════════════════════════════════════════════
# 4. Parse pagination
# ══════════════════════════════════════════════════════════════════════

def parse_max_page(html: str) -> int:
    """Return the highest page number found in pagination links."""
    soup = BeautifulSoup(html, "lxml")
    max_page = 1

    for a_tag in soup.find_all("a", href=True):
        m = _RE_PAGINATION.search(a_tag["href"])
        if m:
            max_page = max(max_page, int(m.group(1)))

    # Also look for text like "Page 1 of 19"
    page_of = re.search(
        r"(?:page|Seite)\s+\d+\s+(?:of|von)\s+(\d+)",
        soup.get_text(), re.I,
    )
    if page_of:
        max_page = max(max_page, int(page_of.group(1)))

    return max_page


def parse_total_vehicles(html: str) -> int | None:
    """Try to extract total vehicle count from the page header."""
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    m = _RE_COUNT.search(text)
    if m:
        return _parse_int(m.group(1))
    return None


# ══════════════════════════════════════════════════════════════════════
# 5. Parse contextual / secondary data from model page
# ══════════════════════════════════════════════════════════════════════

def parse_model_context(html: str) -> dict[str, Any]:
    """
    Try to extract contextual data from a model page:
    route profile, monthly consumption, histogram, CO₂, fuel cost.

    Returns a dict with available fields (may be mostly empty).
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    ctx: dict[str, Any] = {}

    # Route profile percentages
    for label, key in [
        ("motorway", "pct_motorway"),
        ("highway", "pct_motorway"),
        ("Autobahn", "pct_motorway"),
        ("city", "pct_city"),
        ("Stadt", "pct_city"),
        ("urban", "pct_city"),
        ("country", "pct_country"),
        ("Land", "pct_country"),
        ("rural", "pct_country"),
    ]:
        pattern = re.compile(
            rf"{label}\s*[:\s]*(\d{{1,3}})[.,]?(\d?)\s*%", re.I
        )
        m = pattern.search(text)
        if m and key not in ctx:
            val = float(f"{m.group(1)}.{m.group(2) or '0'}")
            ctx[key] = val

    # CO₂
    co2 = re.search(r"(\d{2,3})\s*g\s*/?\s*km", text, re.I)
    if co2:
        ctx["co2_g_per_km"] = float(co2.group(1))

    # Fuel cost
    cost = re.search(
        r"(\d{1,3}[.,]\d{1,2})\s*(?:€|EUR)\s*/?\s*100\s*km", text, re.I
    )
    if cost:
        ctx["fuel_cost_eur_per_100km"] = _parse_float(cost.group(1))

    return ctx


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════

def _slugify(name: str) -> str:
    slug = name.strip().replace(" ", "_").replace("/", "_")
    return re.sub(r"[^A-Za-z0-9_.-]", "", slug)


def _parse_float(s: str) -> float | None:
    if not s:
        return None
    s = s.strip().replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _parse_int(s: str) -> int | None:
    if not s:
        return None
    s = s.strip().replace(",", "").replace(".", "")
    try:
        return int(s)
    except ValueError:
        return None


def _find_container(tag: Tag, max_depth: int = 5) -> Tag | None:
    """Walk up from *tag* to find a meaningful container element."""
    current = tag.parent
    for _ in range(max_depth):
        if current is None:
            return None
        if current.name in ("div", "tr", "li", "article", "section"):
            # Check this container has some consumption text
            if _RE_CONSUMPTION.search(current.get_text(" ", strip=True)):
                return current
        current = current.parent
    return tag.parent


_FUEL_KEYWORDS = {
    "diesel": "diesel",
    "super e10": "super e10",
    "super plus": "super plus",
    "super 95": "super",
    "super 98": "super plus",
    "super": "super",
    "benzin": "super",
    "petrol": "super",
    "gasoline": "super",
    "lpg": "lpg",
    "autogas": "lpg",
    "cng": "cng",
    "erdgas": "cng",
    "natural gas": "cng",
    "electric": "electric",
    "elektro": "electric",
    "strom": "electric",
    "hybrid": "hybrid",
    "plug-in": "hybrid",
    "hydrogen": "hydrogen",
    "e85": "e85",
}


def _extract_fuel_type(container: Tag) -> str | None:
    """Try to identify fuel type from a container's text and images."""
    text = container.get_text(" ", strip=True).lower()
    # Check from longest keyword to shortest to avoid partial matches
    for keyword in sorted(_FUEL_KEYWORDS, key=len, reverse=True):
        if keyword in text:
            return _FUEL_KEYWORDS[keyword]
    # Check alt text on images (Spritmonitor uses fuel-type icons)
    for img in container.find_all("img"):
        alt = (img.get("alt") or "").lower()
        src = (img.get("src") or "").lower()
        for keyword in _FUEL_KEYWORDS:
            if keyword in alt or keyword in src:
                return _FUEL_KEYWORDS[keyword]
    return None


def _extract_transmission(text: str) -> str | None:
    text_lower = text.lower()
    if any(k in text_lower for k in (
        "automatic", "automatik", "auto.", "dsg", "dct", "cvt",
        "tiptronic", "s tronic", "pdk", "at",
    )):
        # Be careful with "at" — could be part of other words
        if ("automat" in text_lower or "dsg" in text_lower
                or "dct" in text_lower or "cvt" in text_lower):
            return "automatic"
    if any(k in text_lower for k in (
        "manual", "manuell", "schaltgetriebe", "mt",
    )):
        return "manual"
    return None


def extract_engine_name(
    title: str, make_name: str = "", model_name: str = "",
) -> str:
    """
    Try to extract a clean engine designation from a vehicle title.

    Example:
        "2019 Volkswagen Golf VII 1.5 TSI ACT BlueMotion"
        → "1.5 TSI ACT BlueMotion"
    """
    # Remove year
    cleaned = _RE_YEAR.sub("", title).strip()
    # Remove make and model names
    for word in (make_name, model_name):
        if word:
            cleaned = re.sub(re.escape(word), "", cleaned, flags=re.I).strip()
    # Remove generation designators like "VII", "VIII", "Mk7", "G30"
    cleaned = re.sub(
        r"\b(?:[IVX]{1,4}|Mk\s?\d|[A-Z]\d{1,2}|Facelift|FL|LCI)\b",
        "",
        cleaned,
        flags=re.I,
    ).strip()
    # Remove excess whitespace
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    # Remove leading/trailing dashes
    cleaned = cleaned.strip(" -–—")
    return cleaned if cleaned else "unknown"