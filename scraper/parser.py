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
_RE_COUNT = re.compile(
    r"([\d.,]+)\s*(?:vehicles?|Fahrzeuge?|entries?|results?)", re.I
)

# ── tooltip helpers (Spritmonitor puts units in onmouseover) ──────────
_RE_TOOLTIP = re.compile(r"showTooltip\('([^']+)'\)")

# Engine pattern
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
# Tooltip extraction helpers
# ══════════════════════════════════════════════════════════════════════

def _extract_tooltip_text(element: Tag) -> str | None:
    """Extract the string argument from ``showTooltip('...')``."""
    tooltip = element.get("onmouseover", "")
    m = _RE_TOOLTIP.search(tooltip)
    return m.group(1) if m else None


def _extract_consumption_from_tooltips(
    element: Tag,
) -> tuple[float | None, str | None]:
    """
    Search *element* and its descendants for an ``onmouseover`` tooltip
    that contains a consumption value with unit.
    Returns ``(value, unit)`` or ``(None, None)``.
    """
    candidates = [element]
    candidates.extend(element.find_all(attrs={"onmouseover": True}))
    for el in candidates:
        tip = _extract_tooltip_text(el)
        if tip:
            cm = _RE_CONSUMPTION.search(tip)
            if cm:
                return _parse_float(cm.group(1)), cm.group(2).strip()
    return None, None


def _extract_fuelings_from_tooltips(element: Tag) -> int | None:
    """
    Search *element* and its descendants for an ``onmouseover`` tooltip
    that contains a fuelings count (e.g. ``'5 Fuelings'``).
    """
    candidates = [element]
    candidates.extend(element.find_all(attrs={"onmouseover": True}))
    for el in candidates:
        tip = _extract_tooltip_text(el)
        if tip:
            fm = re.search(r"(\d+)\s*Fueling", tip, re.I)
            if fm:
                return int(fm.group(1))
    return None


# ══════════════════════════════════════════════════════════════════════
# 1. Parse list of makes from the homepage
# ══════════════════════════════════════════════════════════════════════

def parse_makes(html: str) -> list[dict[str, Any]]:
    """
    Return ``[{make_id, make_name, make_slug, url}, ...]``
    from the homepage ``<select id="manuf">``.
    """
    soup = BeautifulSoup(html, "lxml")
    makes: list[dict[str, Any]] = []
    seen_ids: set[int] = set()

    # Strategy A: <select> for manufacturer (id="manuf")
    for select in soup.find_all("select"):
        select_id = (select.get("id") or select.get("name") or "").lower()
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
                        makes.append({
                            "make_id": mid,
                            "make_name": name,
                            "make_slug": slug,
                            "url": f"/en/overview/{mid}-{slug}.html",
                        })

    # Strategy B: links matching the make URL pattern
    if not makes:
        for a_tag in soup.find_all("a", href=_RE_MAKE_HREF):
            m = _RE_MAKE_HREF.search(a_tag["href"])
            if not m:
                continue
            make_id = int(m.group(1))
            if make_id == 0 or make_id in seen_ids:
                continue
            seen_ids.add(make_id)
            makes.append({
                "make_id": make_id,
                "make_name": a_tag.get_text(strip=True),
                "make_slug": m.group(2),
                "url": a_tag["href"],
            })

    if not makes:
        log.warning(
            "Could not parse any makes. HTML structure may have changed."
        )
    else:
        log.info("Parsed %d makes from page.", len(makes))
    return makes


# ══════════════════════════════════════════════════════════════════════
# 2a. Parse models from AJAX response
# ══════════════════════════════════════════════════════════════════════

def parse_models_ajax(
    response_text: str,
    make_id: int,
    make_slug: str = "",
) -> list[dict[str, Any]]:
    """
    Parse the semicolon-separated CSV returned by
    ``/en/ajaxModel.action?manuf={id}&allowempty=false``.

    Format: ``"1106,181;1988,914-4;452,Golf;"``
    """
    models: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    text = response_text.strip()
    if not text:
        return models

    # Primary: CSV  "id,name;id,name;…"
    parsed_any = False
    for entry in text.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(",", 1)
        if len(parts) != 2:
            continue
        raw_id, raw_name = parts[0].strip(), parts[1].strip()
        try:
            mid = int(raw_id)
        except ValueError:
            continue
        if mid <= 0 or mid in seen_ids or not raw_name:
            continue
        seen_ids.add(mid)
        parsed_any = True
        slug = _slugify(raw_name)
        models.append({
            "model_id": mid,
            "model_name": raw_name,
            "model_slug": slug,
            "url": f"/en/overview/{make_id}-{make_slug}/{mid}-{slug}.html",
        })

    # Fallback: HTML <option> tags
    if not parsed_any:
        soup = BeautifulSoup(text, "lxml")
        for opt in soup.find_all("option"):
            val = opt.get("value", "")
            try:
                mid = int(val)
            except (ValueError, TypeError):
                continue
            if mid <= 0 or mid in seen_ids:
                continue
            name = opt.get_text(strip=True)
            if not name:
                continue
            seen_ids.add(mid)
            slug = _slugify(name)
            models.append({
                "model_id": mid,
                "model_name": name,
                "model_slug": slug,
                "url": f"/en/overview/{make_id}-{make_slug}/{mid}-{slug}.html",
            })

    if not models:
        log.warning(
            "No models for make_id=%d. Response: %.200s", make_id, text
        )
    else:
        log.info("Parsed %d models for make_id=%d via AJAX.", len(models), make_id)
    return models


# ══════════════════════════════════════════════════════════════════════
# 2b. Parse models from page HTML (legacy fallback)
# ══════════════════════════════════════════════════════════════════════

def parse_models(html: str, make_id: int) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    models: list[dict[str, Any]] = []
    seen_ids: set[int] = set()

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
        models.append({
            "model_id": model_id,
            "model_name": a_tag.get_text(strip=True),
            "model_slug": m.group(3),
            "url": a_tag["href"],
        })

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
                            models.append({
                                "model_id": mid,
                                "model_name": name,
                                "model_slug": slug,
                                "url": f"/en/overview/{make_id}-X/{mid}-{slug}.html",
                            })

    if not models:
        log.warning("Could not parse models for make_id=%d.", make_id)
    else:
        log.info("Parsed %d models for make_id=%d.", len(models), make_id)
    return models


# ══════════════════════════════════════════════════════════════════════
# 3. Parse vehicle entries from a model page
# ══════════════════════════════════════════════════════════════════════

def _parse_searchresult_row(tds: list) -> dict[str, Any] | None:
    """
    Parse one ``<tr>`` of the Spritmonitor ``<table class="searchresults">``.

    Columns (by inspection):
        0  picture
        1  description  (class="description") — detail link, name,
           fuel type, power
        2  consumption  (number only; unit in ``onmouseover`` tooltip)
        3  manufacturer-MPG deviation
        4  fuelings     (count in ``onmouseover`` tooltip)
        5  owner        (class="owner")
    """
    # ── find description cell ────────────────────────────────────────
    desc_td = None
    for td in tds:
        if "description" in (td.get("class") or []):
            desc_td = td
            break
    if desc_td is None:
        for td in tds:
            if td.find("a", href=_RE_DETAIL_HREF):
                desc_td = td
                break
    if desc_td is None:
        return None

    # ── detail link & title ──────────────────────────────────────────
    detail_link = desc_td.find("a", href=_RE_DETAIL_HREF)
    if not detail_link:
        return None

    m = _RE_DETAIL_HREF.search(detail_link["href"])
    vehicle: dict[str, Any] = {
        "vehicle_id": int(m.group(1)) if m else None,
        "detail_url": detail_link["href"],
    }

    # title may span two lines via <br>
    raw_title = detail_link.get_text(" ", strip=True)
    vehicle["title"] = re.sub(r"\s+", " ", raw_title).strip()

    desc_text = desc_td.get_text(" ", strip=True)

    # ── consumption from tooltip ─────────────────────────────────────
    consumption, consumption_unit = None, None
    for td in tds:
        if td is desc_td:
            continue                        # skip description column
        tip = _extract_tooltip_text(td)
        if tip:
            cm = _RE_CONSUMPTION.search(tip)
            if cm:
                consumption = _parse_float(cm.group(1))
                consumption_unit = cm.group(2).strip()
                break
    vehicle["consumption"] = consumption
    vehicle["consumption_unit"] = consumption_unit

    # ── fuelings from tooltip ────────────────────────────────────────
    fuelings = None
    for td in tds:
        tip = _extract_tooltip_text(td)
        if tip:
            fm = re.search(r"(\d+)\s*Fueling", tip, re.I)
            if fm:
                fuelings = int(fm.group(1))
                break
    vehicle["fuelings"] = fuelings

    # ── power ────────────────────────────────────────────────────────
    km = _RE_POWER_KW.search(desc_text)
    vehicle["power_kw"] = int(km.group(1)) if km else None
    pm = _RE_POWER_PS.search(desc_text)
    vehicle["power_ps"] = int(pm.group(1)) if pm else None

    # ── displacement ─────────────────────────────────────────────────
    ccm_m = _RE_CCM.search(desc_text)
    vehicle["engine_ccm"] = int(ccm_m.group(1)) if ccm_m else None

    # ── year ─────────────────────────────────────────────────────────
    ym = _RE_YEAR.search(vehicle["title"])
    vehicle["year"] = int(ym.group(1)) if ym else None

    # ── fuel type ────────────────────────────────────────────────────
    vehicle["fuel_type_raw"] = _extract_fuel_type(desc_td)

    # ── transmission ─────────────────────────────────────────────────
    vehicle["transmission"] = _extract_transmission(desc_text)

    return vehicle


def parse_vehicles(html: str) -> list[dict[str, Any]]:
    """
    Parse individual vehicle entries from a model's vehicle-list page.

    Returns a list of raw vehicle dicts.
    """
    soup = BeautifulSoup(html, "lxml")
    vehicles: list[dict[str, Any]] = []

    # ── Strategy A: Spritmonitor <table class="searchresults"> ───────
    #    Consumption values are in onmouseover tooltip attributes.
    results_table = soup.find("table", class_="searchresults")
    if results_table:
        tbody = results_table.find("tbody")
        rows = (
            tbody.find_all("tr", recursive=False)
            if tbody
            else results_table.find_all("tr")
        )
        for tr in rows:
            tds = tr.find_all("td", recursive=False)
            if len(tds) < 3:
                continue
            v = _parse_searchresult_row(tds)
            if v and v.get("consumption") is not None:
                vehicles.append(v)

    if vehicles:
        log.debug(
            "Parsed %d vehicle entries from searchresults table.",
            len(vehicles),
        )
        return vehicles

    # ── Strategy B: detail links + tooltip fallback ──────────────────
    detail_links = soup.find_all("a", href=_RE_DETAIL_HREF)
    if detail_links:
        for link in detail_links:
            vehicle: dict[str, Any] = {}
            m = _RE_DETAIL_HREF.search(link["href"])
            vehicle["vehicle_id"] = int(m.group(1)) if m else None
            vehicle["detail_url"] = link["href"]
            vehicle["title"] = link.get_text(strip=True)

            container = _find_container(link)
            if container is None:
                container = link.parent

            text_block = container.get_text(" ", strip=True)

            # Try tooltips first, then regex on text
            consumption, unit = _extract_consumption_from_tooltips(container)
            if consumption is None:
                cm = _RE_CONSUMPTION.search(text_block)
                if cm:
                    consumption = _parse_float(cm.group(1))
                    unit = cm.group(2).strip()
            vehicle["consumption"] = consumption
            vehicle["consumption_unit"] = unit

            fuelings = _extract_fuelings_from_tooltips(container)
            if fuelings is None:
                fm = _RE_FUELINGS.search(text_block)
                fuelings = _parse_int(fm.group(1)) if fm else None
            vehicle["fuelings"] = fuelings

            km = _RE_POWER_KW.search(text_block)
            vehicle["power_kw"] = int(km.group(1)) if km else None
            pm = _RE_POWER_PS.search(text_block)
            vehicle["power_ps"] = int(pm.group(1)) if pm else None
            ccm_m = _RE_CCM.search(text_block)
            vehicle["engine_ccm"] = int(ccm_m.group(1)) if ccm_m else None
            ym = _RE_YEAR.search(vehicle.get("title", ""))
            vehicle["year"] = int(ym.group(1)) if ym else None
            vehicle["fuel_type_raw"] = _extract_fuel_type(container)
            vehicle["transmission"] = _extract_transmission(text_block)

            if vehicle.get("consumption") is not None:
                vehicles.append(vehicle)

    # ── Strategy C: any <tr> with tooltip consumption ────────────────
    if not vehicles:
        for tr in soup.find_all("tr"):
            consumption, unit = _extract_consumption_from_tooltips(tr)
            if consumption is None:
                text = tr.get_text(" ", strip=True)
                cm = _RE_CONSUMPTION.search(text)
                if cm:
                    consumption = _parse_float(cm.group(1))
                    unit = cm.group(2).strip()
            if consumption is None:
                continue

            text = tr.get_text(" ", strip=True)
            vehicle = {
                "consumption": consumption,
                "consumption_unit": unit,
                "title": text[:200],
                "vehicle_id": None,
                "detail_url": None,
                "fuelings": _extract_fuelings_from_tooltips(tr),
                "power_kw": None,
                "power_ps": None,
                "engine_ccm": None,
                "year": None,
                "fuel_type_raw": _extract_fuel_type(tr),
                "transmission": _extract_transmission(text),
            }
            dl = tr.find("a", href=_RE_DETAIL_HREF)
            if dl:
                dm = _RE_DETAIL_HREF.search(dl["href"])
                vehicle["vehicle_id"] = int(dm.group(1)) if dm else None
                vehicle["detail_url"] = dl["href"]
                vehicle["title"] = dl.get_text(strip=True)
            km = _RE_POWER_KW.search(text)
            vehicle["power_kw"] = int(km.group(1)) if km else None
            ym = _RE_YEAR.search(vehicle.get("title", "") or text)
            vehicle["year"] = int(ym.group(1)) if ym else None
            ccm_m = _RE_CCM.search(text)
            vehicle["engine_ccm"] = int(ccm_m.group(1)) if ccm_m else None
            if vehicle["fuelings"] is None:
                fm = _RE_FUELINGS.search(text)
                vehicle["fuelings"] = _parse_int(fm.group(1)) if fm else None
            vehicles.append(vehicle)

    log.debug("Parsed %d vehicle entries from page.", len(vehicles))
    return vehicles


# ══════════════════════════════════════════════════════════════════════
# 3b. Parse sidebar summary table (model-level aggregates)
# ══════════════════════════════════════════════════════════════════════

def parse_model_summary(html: str) -> list[dict[str, Any]]:
    """
    Parse the sidebar ``<table class="searchsummary">`` which has
    per-fuel-type aggregated consumption for the model.

    Returns ``[{fuel_type_raw, vehicle_count, min_consumption,
    avg_consumption, max_consumption, consumption_unit}, ...]``
    """
    soup = BeautifulSoup(html, "lxml")
    summaries: list[dict[str, Any]] = []

    table = soup.find("table", class_="searchsummary")
    if not table:
        return summaries

    tbody = table.find("tbody")
    rows = tbody.find_all("tr") if tbody else table.find_all("tr")

    for tr in rows:
        tds = tr.find_all("td")
        if len(tds) < 5:
            continue

        # Identify cells by class or position
        count_td = name_td = None
        for td in tds:
            cls = td.get("class") or []
            if "count" in cls:
                count_td = td
            elif "name" in cls:
                name_td = td

        # Fallback to positional: count, name, min, avg, max
        if count_td is None:
            count_td = tds[0]
        if name_td is None:
            name_td = tds[1]

        count_text = count_td.get_text(strip=True)
        vehicle_count = _parse_int(count_text)
        fuel_name = name_td.get_text(strip=True)
        if not fuel_name or vehicle_count is None:
            continue

        # min / avg / max from tooltips (positions 2, 3, 4)
        values: list[tuple[float | None, str | None]] = []
        for td in tds[2:5]:
            tip = _extract_tooltip_text(td)
            if tip:
                cm = _RE_CONSUMPTION.search(tip)
                if cm:
                    values.append(
                        (_parse_float(cm.group(1)), cm.group(2).strip())
                    )
                else:
                    values.append((None, None))
            else:
                values.append((None, None))

        while len(values) < 3:
            values.append((None, None))

        min_c, min_u = values[0]
        avg_c, avg_u = values[1]
        max_c, max_u = values[2]
        unit = avg_u or min_u or max_u

        if avg_c is not None:
            summaries.append({
                "fuel_type_raw": fuel_name,
                "vehicle_count": vehicle_count,
                "min_consumption": min_c,
                "avg_consumption": avg_c,
                "max_consumption": max_c,
                "consumption_unit": unit,
            })

    if summaries:
        log.debug(
            "Parsed %d fuel-type summaries from sidebar.", len(summaries)
        )
    return summaries


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
    route profile, CO₂, fuel cost.
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    ctx: dict[str, Any] = {}

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

    co2 = re.search(r"(\d{2,3})\s*g\s*/?\s*km", text, re.I)
    if co2:
        ctx["co2_g_per_km"] = float(co2.group(1))

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
            # Check for consumption in text OR in tooltips
            if _RE_CONSUMPTION.search(current.get_text(" ", strip=True)):
                return current
            # Also check tooltips in this container
            c, _ = _extract_consumption_from_tooltips(current)
            if c is not None:
                return current
        current = current.parent
    return tag.parent


# Fuel type keywords — sorted longest-first at lookup time so that
# "plug-in hybrid gasoline" is matched before plain "gasoline".
_FUEL_KEYWORDS = {
    "plug-in hybrid gasoline": "hybrid",
    "plug-in hybrid diesel": "hybrid",
    "diesel with adblue": "diesel",
    "hybrid gasoline": "hybrid",
    "hybrid diesel": "hybrid",
    "natural gas": "cng",
    "super e10": "super e10",
    "super plus": "super plus",
    "electricity": "electric",
    "super 95": "super",
    "super 98": "super plus",
    "gasoline": "super",
    "hydrogen": "hydrogen",
    "autogas": "lpg",
    "electric": "electric",
    "benzin": "super",
    "diesel": "diesel",
    "elektro": "electric",
    "erdgas": "cng",
    "hybrid": "hybrid",
    "petrol": "super",
    "plug-in": "hybrid",
    "super": "super",
    "strom": "electric",
    "cng": "cng",
    "lpg": "lpg",
    "e85": "e85",
}


def _extract_fuel_type(container: Tag) -> str | None:
    """Try to identify fuel type from a container's text and images."""
    text = container.get_text(" ", strip=True).lower()
    for keyword in sorted(_FUEL_KEYWORDS, key=len, reverse=True):
        if keyword in text:
            return _FUEL_KEYWORDS[keyword]
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
        "tiptronic", "s tronic", "pdk",
    )):
        return "automatic"
    if any(k in text_lower for k in (
        "manual", "manuell", "schaltgetriebe",
    )):
        return "manual"
    return None


def extract_engine_name(
    title: str, make_name: str = "", model_name: str = "",
) -> str:
    """
    Try to extract a clean engine designation from a vehicle title.
    """
    cleaned = _RE_YEAR.sub("", title).strip()
    for word in (make_name, model_name):
        if word:
            cleaned = re.sub(
                re.escape(word), "", cleaned, flags=re.I
            ).strip()
    cleaned = re.sub(
        r"\b(?:[IVX]{1,4}|Mk\s?\d|[A-Z]\d{1,2}|Facelift|FL|LCI)\b",
        "",
        cleaned,
        flags=re.I,
    ).strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    cleaned = cleaned.strip(" -–—")
    return cleaned if cleaned else "unknown"