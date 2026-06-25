import os
import sys
import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import requests
from bs4 import BeautifulSoup
import re
import hashlib
import unicodedata
import time
from math import radians, sin, cos, sqrt, atan2
import urllib.parse
from googleapiclient.discovery import build

print(">>> RUNNING concursos.py v2026-06-09-col-H <<<")

# =====================================
# 0) Single-instance lock (interactive for manual runs)
# =====================================
LOCK_FILE = r"C:\Users\mateu\OneDrive\Área de Trabalho\Concursos\pci_concursos.lock"


def acquire_lock_interactive():
    """Try to acquire the lock; if a stale lock exists, ask user whether to delete and retry."""
    if os.path.exists(LOCK_FILE):
        print(f"🚫 Lock file already exists: {LOCK_FILE}")
        answer = input("Delete existing lock and continue? [y/N]: ").strip().lower()
        if answer == "y":
            try:
                os.remove(LOCK_FILE)
                print("🔧 Old lock removed, retrying acquire...")
            except Exception as e:
                print(f"⚠️ Could not remove existing lock: {e}")
                sys.exit(1)
        else:
            print("❌ Exiting to avoid possible double run.")
            sys.exit(0)

    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        os.close(fd)
        print(f"🔒 Lock acquired: {LOCK_FILE}")
    except FileExistsError:
        print("🚫 Another instance just acquired the lock. Exiting.")
        sys.exit(0)
    except Exception as e:
        print(f"⚠️ Could not create lock file {LOCK_FILE}: {e}. Exiting to avoid duplicates.")
        sys.exit(1)


acquire_lock_interactive()

# =====================================
# 1) Helpers
# =====================================
def normalize_text(text):
    text = text.lower().replace("\xa0", " ")
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlon = radians(lon2 - lon1)
    dlat = radians(lat2 - lat1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c


def make_maps_link(cidade, estado):
    query = urllib.parse.quote(f"{cidade}, {estado}, Brasil")
    return f"https://www.google.com/maps/search/?api=1&query={query}"

# =====================================
# Safe write retry system
# =====================================
def safe_sheet_write(action_desc, func, *args, **kwargs):
    delays = [5, 15, 30]
    for attempt, delay in enumerate(delays, start=1):
        try:
            print(f"📝 Attempt {attempt} — {action_desc} ...")
            result = func(*args, **kwargs)
            print(f"✅ Success: {action_desc}")
            return result
        except Exception as e:
            print(f"⚠️ Error during {action_desc} (attempt {attempt}): {e}")
            if attempt < len(delays):
                print(f"⏳ Retrying in {delay} seconds...")
                time.sleep(delay)
            else:
                print(f"❌ Failed after {len(delays)} attempts: {action_desc}")
                return None
    return None

# =====================================
# 2) Google Sheets Authentication (Service Account)
# =====================================
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
SERVICE_ACCOUNT_FILE = r"C:\Users\mateu\OneDrive\Área de Trabalho\Concursos\pci-concursos-tracker-1eedab4e1dbd.json"

try:
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)
    print("✅ Google Sheets authentication successful (service account mode)")
except Exception as e:
    print(f"❌ Google authentication failed: {e}")
    raise SystemExit(1)

# =====================================
# 3) Open target sheet
# =====================================
SHEET_ID = "1lFQFIs6Y6t2lhwb6SE88QiC-X4Rnsmh7hyCze23fM4Y"
sheet = gc.open_by_key(SHEET_ID)


def get_or_create_worksheet(sheet_obj, title, cols):
    try:
        return sheet_obj.worksheet(title)
    except gspread.WorksheetNotFound:
        return sheet_obj.add_worksheet(title=title, rows="1000", cols=str(cols))


ws_concursos = get_or_create_worksheet(sheet, "concursos", 20)
ws_logs = get_or_create_worksheet(sheet, "logs", 5)
ws_geo = get_or_create_worksheet(sheet, "geocodes_cache", 4)

# =====================================
# 4) Load geocode cache + municipios_ref
# =====================================
# FIX: expected_headers evita crash quando geocodes_cache tem colunas vazias no cabeçalho
geo_records = ws_geo.get_all_records(expected_headers=["cidade", "lat", "lon"])
geo_cache = {}

for r in geo_records:
    cidade = normalize_text(str(r.get("cidade", "")).strip())

    try:
        lat = float(r.get("lat"))
        lon = float(r.get("lon"))
    except:
        continue

    geo_cache[cidade] = (lat, lon)

# Carrega a tabela de municípios brasileiros (lat/lon offline, sem depender do Nominatim)
try:
    ws_mun = sheet.worksheet("municipios_ref")
    mun_records = ws_mun.get_all_records(expected_headers=["cidade", "estado", "codigo_ibge", "lat", "lon"])
    # Índice duplo: (cidade_lower, estado_upper) → (lat, lon)
    # e cidade_lower sozinha como fallback (pega o primeiro encontrado)
    municipios_ref: dict[tuple, tuple] = {}
    municipios_ref_cidade: dict[str, tuple] = {}
    for m in mun_records:
        c = normalize_text(str(m.get("cidade", "")).strip())
        e = str(m.get("estado", "")).strip().upper()
        lat = m.get("lat")
        lon = m.get("lon")
        if c and lat and lon:
            municipios_ref[(c, e)] = (float(lat), float(lon))
            if c not in municipios_ref_cidade:
                municipios_ref_cidade[c] = (float(lat), float(lon))
    print(f"✅ municipios_ref carregado: {len(municipios_ref)} municípios")
except Exception as e:
    municipios_ref = {}
    municipios_ref_cidade = {}
    print(f"⚠️ Não foi possível carregar municipios_ref: {e}")

# ─────────────────────────────────────────────────────────────────────
# FIX 1: get_city_coords — include estado in query, log empty
#         responses, handle 429 rate-limits, use policy-compliant UA
# ─────────────────────────────────────────────────────────────────────
NOMINATIM_UA = "PCI-Concursos-Tracker/2.0 (mateus.concursos@example.com)"
# ↑ Replace the email with a real contact address — Nominatim's terms
#   require a unique, identifying User-Agent. Using "Mozilla/5.0"
#   (the old value) violates their policy and can trigger soft-blocks
#   that return 200 + empty JSON silently.


def get_city_coords(city, estado=None):
    """
    Geocode `city` com três camadas de prioridade:

    1. geo_cache  — resultado de runs anteriores (já gravado no geocodes_cache)
    2. municipios_ref — tabela offline com todos os municípios brasileiros;
                        elimina chamadas ao Nominatim para a imensa maioria dos casos.
    3. Nominatim  — fallback online apenas para cidades não encontradas nas duas
                    fontes acima (municípios muito novos, grafias alternativas, etc.)
    """
    city_norm = normalize_text(city.strip())
    estado_norm = (estado or "").strip().upper()

    # Camada 1 — cache de runs anteriores
    if city_norm in geo_cache:
        return geo_cache[city_norm]

    # Camada 2 — municipios_ref (lookup offline, instantâneo)
    coords = municipios_ref.get((city_norm, estado_norm))
    if coords is None and city_norm in municipios_ref_cidade:
        coords = municipios_ref_cidade[city_norm]
        if estado_norm:
            print(f"ℹ️ municipios_ref: '{city}' encontrado sem confirmar estado '{estado_norm}'")
    if coords:
        geo_cache[city_norm] = coords
        return coords

    # Camada 2.5 — Split + fuzzy (nomes compostos como "Timbó, SAMAE e TIMBOPREV")
    #
    # Estratégia E:
    #   a) Pega o trecho antes da primeira vírgula (ex: "Timbó") e tenta lookup exato.
    #   b) Se ainda falhar, roda difflib.get_close_matches contra todos os municípios
    #      do mesmo estado com threshold 0.85 — evita falsos positivos.
    #   c) Só chega no Nominatim se as duas etapas acima falharem.
    if "," in city:
        city_before_comma = normalize_text(city.split(",")[0].strip())
        # (a) lookup exato com o trecho antes da vírgula
        coords = municipios_ref.get((city_before_comma, estado_norm))
        if coords is None and city_before_comma in municipios_ref_cidade:
            coords = municipios_ref_cidade[city_before_comma]
        if coords:
            print(f"✂️ Split-match: '{city}' resolvido como '{city.split(',')[0].strip()}'")
            geo_cache[city_norm] = coords
            return coords

        # (b) fuzzy matching restrito ao mesmo estado
        import difflib
        candidates = [c for (c, e) in municipios_ref if e == estado_norm] if estado_norm else list(municipios_ref_cidade.keys())
        matches = difflib.get_close_matches(city_before_comma, candidates, n=1, cutoff=0.85)
        if matches:
            matched_key = matches[0]
            coords = municipios_ref.get((matched_key, estado_norm)) or municipios_ref_cidade.get(matched_key)
            if coords:
                print(f"🔍 Fuzzy-match: '{city}' → '{matched_key}' (estado={estado_norm})")
                geo_cache[city_norm] = coords
                return coords

    # Camada 3 — Nominatim (fallback online)
    query = f"{city}, {estado}, Brasil" if estado else f"{city}, Brasil"
    print(f"🌍 Nominatim fallback: {query}")

    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": query, "format": "json", "limit": 1, "countrycodes": "br"}
    req_headers = {"User-Agent": NOMINATIM_UA}

    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, headers=req_headers, timeout=10)

            if resp.status_code == 429:
                wait_s = 30 * (attempt + 1)
                print(f"⏳ Nominatim rate-limited (429). Aguardando {wait_s}s (tentativa {attempt + 1}/3)…")
                time.sleep(wait_s)
                continue

            if resp.status_code == 200:
                data = resp.json()
                if data:
                    lat, lon = float(data[0]["lat"]), float(data[0]["lon"])
                    geo_cache[city_norm] = (lat, lon)
                    safe_sheet_write(
                        f"cache Nominatim → geocodes_cache: {city}",
                        ws_geo.append_row,
                        [city, lat, lon, datetime.now().isoformat()],
                        value_input_option="RAW",
                    )
                    time.sleep(1.5)
                    return lat, lon
                else:
                    print(f"⚠️ Nominatim sem resultados para: '{query}'.")
                    break
            else:
                print(f"⚠️ Nominatim HTTP {resp.status_code} para '{query}'.")

        except Exception as e:
            print(f"⚠️ Erro ao geocodificar '{city}' (tentativa {attempt + 1}/3): {e}")

        if attempt < 2:
            time.sleep(5 * (attempt + 1))

    return None

# =====================================
# 5) Scraper
# =====================================
URL = "https://www.pciconcursos.com.br/concursos/sul/"
response = requests.get(URL)
response.encoding = "utf-8"
soup = BeautifulSoup(response.text, "html.parser")

concursos = []
today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

for na_div in soup.find_all("div", class_="na"):
    ca_div = na_div.find("div", class_="ca")
    if not ca_div:
        continue
    a = ca_div.find("a", href=True)
    if not a:
        continue

    text = a.get_text(strip=True)
    PREFIXES = ("Prefeitura de ", "Câmara de Vereadores de ", "Câmara de ")
    if not text.startswith(PREFIXES):
        continue

    for prefix in PREFIXES:
        if text.startswith(prefix):
            cidade = text[len(prefix):].strip()
            break

    link = a["href"]
    titulo = a.get("title", "")
    fonte_url = URL

    cd_div = na_div.find("div", class_="cd")
    ce_div = na_div.find("div", class_="ce")
    cc_div = na_div.find("div", class_="cc")

    estado = cc_div.get_text(strip=True) if cc_div else ""
    vagas_info = cd_div.get_text(" ", strip=True) if cd_div else ""
    ca_text = ca_div.get_text(" ", strip=True) if ca_div else ""
    full_text = " ".join([ca_text, vagas_info])
    ca_text_norm = normalize_text(full_text)

    is_varios_cargos_true = "varios cargos" in ca_text_norm or "diversos cargos" in ca_text_norm
    is_escolaridade_EMouSuperior_e_Cargo_nao_Professor = (
        ("medio" in ca_text_norm or "medio / superior" in ca_text_norm or "superior" in ca_text_norm)
        and ("professor" not in ca_text_norm)
    )
    # Conservative rule: True only when the normalized text explicitly indicates concurso público.
    # Anything else, including processo seletivo or no clear wording, defaults to False.
    is_concurso_publico = "concurso" in titulo

    prazo = ""
    if ce_div:
        prazo_span = ce_div.find("span")
        prazo_raw = prazo_span.get_text(" ", strip=True) if prazo_span else ""
        dates = re.findall(r"\d{1,2}/\d{1,2}/\d{4}", prazo_raw)
        if dates:
            prazo = dates[-1]

    if prazo:
        try:
            prazo_date = datetime.strptime(prazo, "%d/%m/%Y")
            if prazo_date.date() < today.date():
                continue
        except ValueError:
            continue
    else:
        continue

    vagas_match = re.search(r"(\d+)\s+vagas?", vagas_info)
    salario_match = re.search(r"R\$\s*[\d\.,]+", vagas_info)
    vagas = vagas_match.group(1) if vagas_match else ""
    salario = salario_match.group(0) if salario_match else ""

    concurso = {
        "orgao": text,
        "cidade": cidade,
        "estado": estado,
        "titulo": titulo,
        "vagas": vagas,
        "salario": salario,
        "prazo_de_inscricao": prazo,
        "link_edital": link,
        "fonte_url": fonte_url,
        "coletado_em": datetime.now().isoformat(),
        "is_varios_cargos_true": is_varios_cargos_true,
        "is_escolaridade_EMouSuperior&Cargo_não_Professor_true": is_escolaridade_EMouSuperior_e_Cargo_nao_Professor,
        "is_concurso_publico": is_concurso_publico,
    }

    raw_key = f"{concurso['orgao'].strip().lower()}_{concurso['titulo'].strip().lower()}"
    
    hash_core = hashlib.md5(raw_key.encode("utf-8")).hexdigest()[:10]
    concurso["id_hash"] = f"h_{hash_core}"

    concursos.append(concurso)

print(f"✅ Encontradas {len(concursos)} prefeituras válidas")

# =====================================
# 6) Save to Google Sheets (Enhanced duplicate prevention)
# =====================================
lat_osorio, lon_osorio = -29.8884, -50.2668
lat_floripa, lon_floripa = -27.5954, -48.5480
lat_curitiba, lon_curitiba = -25.4284, -49.2733

# Column order aligned to the manually shifted sheet:
# A  id_hash
# B  coletado_em
# C  fonte_url
# D  estado
# E  vagas
# F  is_varios_cargos_true
# G  is_escolaridade_EMouSuperior&Cargo_não_Professor_true
# H  is_concurso_publico
# I  cidade
# J  orgao
# K  salario
# L  Review
# M  Not Interested
# N  titulo
# O  prazo_de_inscricao
# P  link_edital
# Q  dist_km_osorio
# R  dist_km_floripa
# S  dist_km_curitiba
# T  maps_link
columns = [
    "id_hash",
    "coletado_em",
    "fonte_url",
    "estado",
    "vagas",
    "is_varios_cargos_true",
    "is_escolaridade_EMouSuperior&Cargo_não_Professor_true",
    "is_concurso_publico",
    "cidade",
    "orgao",
    "salario",
    "Review",
    "Not Interested",
    "titulo",
    "prazo_de_inscricao",
    "link_edital",
    "dist_km_osorio",
    "dist_km_floripa",
    "dist_km_curitiba",
    "maps_link",
]

# Distance column positions in the sheet (1-based, letters for A1 notation)
# Matches the `columns` list above: index 16→Q, 17→R, 18→S
DIST_OSORIO_COL = "Q"
DIST_FLORIPA_COL = "R"
DIST_CURITIBA_COL = "S"
CIDADE_COL_IDX = columns.index("cidade")
ESTADO_COL_IDX = columns.index("estado")

# 1) Local dedupe (pre-run)
unique_concursos = {c["id_hash"]: c for c in concursos}
deduped_concursos = list(unique_concursos.values())

# 2) Load existing sheet hashes
existing_hashes = set(ws_concursos.col_values(columns.index("id_hash") + 1))
written_hashes = set()  # per-run memory

added = 0
failed_rows = []


def hash_exists_in_last_rows(ws, hid, rows_to_check=30):
    """Fast duplicate detection to prevent ghost double-writes."""
    try:
        all_values = ws.get_all_values()
        if not all_values:
            return False
        data_rows = all_values[1:]
        for row in data_rows[-rows_to_check:]:
            if len(row) > 0 and row[0] == hid:
                return True
        return False
    except Exception as e:
        print(f"⚠️ Real-time duplicate check failed: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────
# FIX 2: safe_append_concurso_row — mutable default argument removed.
#         Using `attempt_delays=None` + in-body default avoids the
#         Python pitfall where the default list is shared across calls.
#         Also moved the duplicate-check OUTSIDE the retry loop — the
#         original called get_all_values() on every retry attempt,
#         which was expensive and redundant.
# ─────────────────────────────────────────────────────────────────────
def safe_append_concurso_row(concurso_hash, row, ws_concursos_obj, attempt_delays=None):
    if attempt_delays is None:
        attempt_delays = [5, 15, 30]

    # FIX 2b — check for duplicate once, before the retry loop
    if hash_exists_in_last_rows(ws_concursos_obj, concurso_hash):
        print(f"⛔ Real-time duplicate detected before write → skipping hash {concurso_hash}")
        return True

    for attempt, delay in enumerate(attempt_delays, start=1):
        try:
            safe_sheet_write(
                f"append concurso row (try {attempt})",
                ws_concursos_obj.append_row,
                row,
                value_input_option="RAW",
            )
            print(f"✅ Row success on attempt {attempt}")
            return True
        except Exception as e:
            print(f"⚠️ Failed attempt {attempt}, waiting {delay}s before retry. Error: {e}")
            if attempt < len(attempt_delays):
                time.sleep(delay)

    print(f"❌ All attempts failed for hash {concurso_hash}, skipping.")
    return False


print("About to write the following hashes:", [c["id_hash"] for c in deduped_concursos])

for concurso in deduped_concursos:
    hid = concurso["id_hash"]

    if hid in existing_hashes or hid in written_hashes:
        print(f"⚠️ Skipping duplicate id_hash: {hid}")
        continue

    written_hashes.add(hid)

    # FIX 1 — pass estado so the query is unambiguous
    coords = get_city_coords(concurso["cidade"], concurso.get("estado"))
    if coords:
        lat_cidade, lon_cidade = coords
        concurso["dist_km_osorio"] = round(haversine(lat_osorio, lon_osorio, lat_cidade, lon_cidade), 1)
        concurso["dist_km_floripa"] = round(haversine(lat_floripa, lon_floripa, lat_cidade, lon_cidade), 1)
        concurso["dist_km_curitiba"] = round(haversine(lat_curitiba, lon_curitiba, lat_cidade, lon_cidade), 1)
    else:
        concurso["dist_km_osorio"] = concurso["dist_km_floripa"] = concurso["dist_km_curitiba"] = ""

    concurso["maps_link"] = make_maps_link(concurso["cidade"], concurso["estado"])

    row = [
        concurso.get(col, False if col in ["Review", "Not Interested"] else "")
        for col in columns
    ]

    success = safe_append_concurso_row(hid, row, ws_concursos)

    if success:
        added += 1
    else:
        failed_rows.append(hid)

print(f"📝 {added} novos concursos adicionados (sem duplicatas).")
print(f"⚠️ Rows not saved after 3 attempts: {failed_rows}")

# =====================================
# 7) Log run
# =====================================
safe_sheet_write(
    "append log row",
    ws_logs.append_row,
    [datetime.now().isoformat(), f"{added} novos concursos adicionados", "Sul (RS/SC/PR)"],
    value_input_option="USER_ENTERED",
)

# -----------------------------
# Extra: update Filter Views
# -----------------------------
def update_filter_views(spreadsheet_id, tab_name, creds_obj, end_row_index=1000000, end_col_index=20):
    try:
        service = build("sheets", "v4", credentials=creds_obj)
        meta = service.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="sheets(properties(sheetId,title),filterViews)",
        ).execute()
        target_sheet = next(
            (s for s in meta.get("sheets", []) if s["properties"]["title"] == tab_name),
            None,
        )
        if not target_sheet:
            print(f"⚠️ update_filter_views: sheet/tab '{tab_name}' not found.")
            return 0
        sheet_id_num = target_sheet["properties"]["sheetId"]
        filter_views = target_sheet.get("filterViews", [])
        if not filter_views:
            print(f"ℹ️ update_filter_views: no filter views in '{tab_name}'.")
            return 0
        requests_list = []
        for fv in filter_views:
            fv_id = fv.get("filterViewId")
            fv_title = fv.get("title", "")
            new_range = {
                "sheetId": sheet_id_num,
                "startRowIndex": 0,
                "endRowIndex": end_row_index,
                "startColumnIndex": 0,
                "endColumnIndex": end_col_index,
            }
            requests_list.append(
                {
                    "updateFilterView": {
                        "filter": {
                            "filterViewId": fv_id,
                            "title": fv_title,
                            "range": new_range,
                        },
                        "fields": "range",
                    }
                }
            )
        if requests_list:
            body = {"requests": requests_list}
            service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id, body=body
            ).execute()
            print(f"✅ update_filter_views: Updated {len(requests_list)} filter view(s) in '{tab_name}'.")
            return len(requests_list)
        else:
            return 0
    except Exception as e:
        print(f"⚠️ update_filter_views error: {e}")
        return 0


try:
    updated_filters_count = update_filter_views(SHEET_ID, "concursos", creds)
    safe_sheet_write(
        "log filter views update",
        ws_logs.append_row,
        [
            datetime.now().isoformat(),
            f"Filter views updated in 'concursos': {updated_filters_count}",
        ],
        value_input_option="USER_ENTERED",
    )
except Exception as e:
    print(f"⚠️ Failed to update filter views or to log the event: {e}")

# =====================================
# 8) Check and move expired concursos (NO OVERWRITE)
# =====================================

def _sheet_id_from_ws(ws):
    return ws._properties["sheetId"]


def _chunked(iterable, size):
    for i in range(0, len(iterable), size):
        yield iterable[i : i + size]


def batch_delete_rows(service, spreadsheet_id, sheet_id, row_numbers_1based, chunk_size=200):
    """Delete rows in a single sheet using Sheets API."""
    if not row_numbers_1based:
        return 0
    rows_desc = sorted(set(row_numbers_1based), reverse=True)
    deleted = 0
    for chunk in _chunked(rows_desc, chunk_size):
        requests_body = []
        for r in chunk:
            requests_body.append({
                "deleteDimension": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": r - 1,
                        "endIndex": r
                    }
                }
            })
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests_body}
        ).execute()
        deleted += len(chunk)
    return deleted


def append_many_rows(ws, rows, value_input_option="USER_ENTERED"):
    if not rows:
        return
    if hasattr(ws, "append_rows"):
        ws.append_rows(rows, value_input_option=value_input_option)
    else:
        for row in rows:
            ws.append_row(row, value_input_option=value_input_option)


def move_expired_concursos_inplace(ws_source, ws_expired, ws_logs_sheet, spreadsheet_id, creds_obj):
    print("\n🔍 Checking concursos for expiration status (in-place delete/move)...")
    service = build("sheets", "v4", credentials=creds_obj)

    data = ws_source.get_all_values()
    if not data or len(data) < 2:
        print("⚠️ No data found in source sheet.")
        return

    headers = data[0]
    rows = data[1:]

    try:
        prazo_idx = headers.index("prazo_de_inscricao")
    except ValueError:
        print("❌ Column 'prazo_de_inscricao' not found. Skipping expiration step.")
        return

    today_local = datetime.today().date()
    expired_to_move = []
    expired_row_numbers = []

    for i, row in enumerate(rows, start=2):
        if len(row) <= prazo_idx:
            continue
        prazo_str = row[prazo_idx].strip()
        try:
            prazo_date = datetime.strptime(prazo_str, "%d/%m/%Y").date()
        except ValueError:
            continue
        if prazo_date < today_local:
            row_norm = row + [""] * (len(headers) - len(row))
            expired_to_move.append(row_norm)
            expired_row_numbers.append(i)

    print(f"📊 Total concursos found: {len(rows)}")
    print(f"❌ Expired to move: {len(expired_to_move)}")

    if not expired_to_move:
        print("ℹ️ No expired concursos found.")
        return

    expired_values = ws_expired.get_all_values()
    if not expired_values:
        safe_sheet_write(
            "write header concursos_expirados",
            ws_expired.update,
            "A1",
            [headers],
            value_input_option="USER_ENTERED",
        )

    safe_sheet_write(
        f"append {len(expired_to_move)} expired concursos",
        append_many_rows,
        ws_expired,
        expired_to_move,
        "USER_ENTERED",
    )

    deleted = batch_delete_rows(
        service=service,
        spreadsheet_id=spreadsheet_id,
        sheet_id=_sheet_id_from_ws(ws_source),
        row_numbers_1based=expired_row_numbers,
    )

    safe_sheet_write(
        "log expired move",
        ws_logs_sheet.append_row,
        [datetime.now().isoformat(), f"{deleted} concursos expirados movidos para '{ws_expired.title}'"],
        value_input_option="USER_ENTERED",
    )

    print(f"📦 Moved+deleted expired rows: {deleted}")


try:
    ws_expired = sheet.worksheet("concursos_expirados")
except gspread.WorksheetNotFound:
    ws_expired = sheet.add_worksheet(title="concursos_expirados", rows="1000", cols="20")

move_expired_concursos_inplace(ws_concursos, ws_expired, ws_logs, SHEET_ID, creds)

# =====================================
# 9) Automatic dedupe by id_hash (NO OVERWRITE)
# =====================================
def dedupe_concursos_by_hash_inplace(ws_concursos_obj, ws_logs_sheet, spreadsheet_id, creds_obj):
    print("\n🧹 Running automatic dedupe by id_hash (in-place delete)...")
    service = build("sheets", "v4", credentials=creds_obj)

    data = ws_concursos_obj.get_all_values()
    if not data or len(data) < 2:
        print("⚠️ Nothing to dedupe (empty or header only).")
        return

    headers = data[0]
    rows = data[1:]

    try:
        id_hash_idx = headers.index("id_hash")
    except ValueError:
        print("❌ Column 'id_hash' not found, skipping dedupe.")
        return

    seen = set()
    duplicate_row_numbers = []

    for i, row in enumerate(rows, start=2):
        hid = row[id_hash_idx].strip() if len(row) > id_hash_idx else ""
        if not hid:
            continue
        if hid in seen:
            duplicate_row_numbers.append(i)
        else:
            seen.add(hid)

    if not duplicate_row_numbers:
        print("✅ No duplicate id_hash values found.")
        return

    deleted = batch_delete_rows(
        service=service,
        spreadsheet_id=spreadsheet_id,
        sheet_id=_sheet_id_from_ws(ws_concursos_obj),
        row_numbers_1based=duplicate_row_numbers,
    )

    safe_sheet_write(
        "log dedupe",
        ws_logs_sheet.append_row,
        [datetime.now().isoformat(), f"Dedupe removed {deleted} duplicate rows by id_hash"],
        value_input_option="USER_ENTERED",
    )

    print(f"✅ Dedupe finished. Removed {deleted} rows.")


dedupe_concursos_by_hash_inplace(ws_concursos, ws_logs, SHEET_ID, creds)

# ─────────────────────────────────────────────────────────────────────
# FIX 3: backfill_missing_distances — NEW FUNCTION
#
# ROOT CAUSE RECAP:
#   When get_city_coords() returns None at insert-time (e.g. because
#   Nominatim returned an empty result or was rate-limiting), the row
#   is written with empty distance columns. Because the deduplication
#   is hash-based, subsequent runs skip those rows entirely — the
#   distances remain empty forever.
#
# This function scans the live sheet for rows where all three distance
# columns are blank, re-geocodes each city (now with estado included),
# and patches the cells via a single Sheets API batchUpdate call.
# It runs AFTER the dedupe step so the sheet is clean.
# ─────────────────────────────────────────────────────────────────────
def backfill_missing_distances(ws_source, ws_logs_sheet, spreadsheet_id, creds_obj, tab_name="concursos"):
    print("\n🔧 Backfill: scanning for rows with missing distances…")
    service = build("sheets", "v4", credentials=creds_obj)

    data = ws_source.get_all_values()
    if not data or len(data) < 2:
        print("⚠️ Sheet empty — nothing to backfill.")
        return

    headers = data[0]
    rows = data[1:]

    # Locate columns by name (tolerant of the header renaming visible in the ODS)
    def _find_col(candidates):
        """Return the first matching 0-based index from a list of candidate names."""
        for name in candidates:
            try:
                return headers.index(name)
            except ValueError:
                pass
        return None

    cidade_idx = _find_col(["cidade"])
    estado_idx = _find_col(["estado"])
    osorio_idx = _find_col(["dist_km_osorio", "Distancia Osório Km)", "Distância Osório (Km)"])
    floripa_idx = _find_col(["dist_km_floripa", "Distância Floripa (Km)", "Distancia Floripa (Km)"])
    curitiba_idx = _find_col(["dist_km_curitiba", "Distancia Curitiba", "Distância Curitiba (Km)"])

    missing_cols = [
        n for n, i in [
            ("cidade", cidade_idx),
            ("estado", estado_idx),
            ("dist_km_osorio", osorio_idx),
            ("dist_km_floripa", floripa_idx),
            ("dist_km_curitiba", curitiba_idx),
        ] if i is None
    ]
    if missing_cols:
        print(f"❌ Backfill aborted — could not find columns: {missing_cols}")
        return

    # Convert 0-based column index → A1 letter (A=0, P=15, etc.)
    def col_letter(idx):
        result = ""
        n = idx
        while True:
            result = chr(ord("A") + n % 26) + result
            n = n // 26 - 1
            if n < 0:
                break
        return result

    osorio_letter = col_letter(osorio_idx)
    floripa_letter = col_letter(floripa_idx)
    curitiba_letter = col_letter(curitiba_idx)

    updates = []   # list of {"range": ..., "values": [[...]]}
    repaired = 0

    for i, row in enumerate(rows, start=2):   # sheet row 2 = first data row
        # Check if all three distance cells are blank
        def cell(idx):
            return row[idx].strip() if len(row) > idx else ""

        if cell(osorio_idx) != "" and cell(floripa_idx) != "" and cell(curitiba_idx) != "":
            continue   # already has distances

        cidade = cell(cidade_idx)
        estado = cell(estado_idx)

        if not cidade:
            continue

        coords = get_city_coords(cidade, estado)
        if not coords:
            print(f"  ⚠️ Still can't geocode '{cidade}' ({estado}) — skipping row {i}.")
            continue

        lat_c, lon_c = coords
        d_osorio = round(haversine(lat_osorio, lon_osorio, lat_c, lon_c), 1)
        d_floripa = round(haversine(lat_floripa, lon_floripa, lat_c, lon_c), 1)
        d_curitiba = round(haversine(lat_curitiba, lon_curitiba, lat_c, lon_c), 1)

        print(
            f"  ✅ Backfilling row {i}: {cidade} ({estado}) → "
            f"Osório={d_osorio}km, Floripa={d_floripa}km, Curitiba={d_curitiba}km"
        )

        # One update entry per row across the distance columns only
        updates.append({
            "range": f"{tab_name}!{osorio_letter}{i}:{curitiba_letter}{i}",
            "values": [[d_osorio, d_floripa, d_curitiba]]
        })
        repaired += 1

    if not updates:
        print("ℹ️ Backfill: no rows needed repair.")
        return

    # Single batchUpdate — one API call for all repairs
    service.spreadsheets().values().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={
            "valueInputOption": "RAW",
            "data": updates,
        }
    ).execute()

    safe_sheet_write(
        "log backfill distances",
        ws_logs_sheet.append_row,
        [datetime.now().isoformat(), f"Backfill: {repaired} rows with missing distances repaired"],
        value_input_option="USER_ENTERED",
    )

    print(f"📐 Backfill complete — {repaired} rows updated.")


backfill_missing_distances(ws_concursos, ws_logs, SHEET_ID, creds)

# =====================================
# 10) Final summary feedback + safe lock release
# =====================================
try:
    summary_new = (
        f"• {added} new concursos added today."
        if added > 0
        else "• No new concursos were added on this run."
    )
    summary_expired = (
        f"• {len(ws_expired.get_all_values()) - 1} concursos atualmente arquivados (expirados)."
    )
    summary_filters = f"• Filter views updated: {updated_filters_count}"
    summary_failed = (
        f"• Rows not saved after 3 attempts: {failed_rows}"
        if failed_rows
        else "• All rows saved successfully."
    )
    print("\n📈 Summary:")
    print(summary_new)
    print(summary_expired)
    print(summary_filters)
    print(summary_failed)
except Exception as e:
    print(f"⚠️ Could not print final summary: {e}")

try:
    print("🎯 All tasks completed successfully!")
    print("⏳ Keeping script open for 15 minutes for review (Ctrl+C to exit early)...")
    time.sleep(15 * 60)
except KeyboardInterrupt:
    print("⛔ Interrupted by user (Ctrl+C). Cleaning up and exiting...")
finally:
    try:
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
            print(f"🔓 Lock released: {LOCK_FILE}")
    except Exception as e:
        print(f"⚠️ Could not remove lock file {LOCK_FILE}: {e}")