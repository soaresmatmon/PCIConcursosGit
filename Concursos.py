import gspread
from google.oauth2.service_account import Credentials
from datetime import datetime
import os
import requests
from bs4 import BeautifulSoup
import re
import hashlib
import unicodedata
import time
from math import radians, sin, cos, sqrt, atan2
import urllib.parse
from googleapiclient.discovery import build

# =====================================
# 0) Helpers
# =====================================
def normalize_text(text):
    text = text.lower().replace("\xa0", " ")
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))

def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlon = radians(lon2 - lon1)
    dlat = radians(lat2 - lat1)
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
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
# 1) Google Sheets Authentication (Service Account)
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
# 2) Open target sheet
# =====================================
SHEET_ID = "1lFQFIs6Y6t2lhwb6SE88QiC-X4Rnsmh7hyCze23fM4Y"
sheet = gc.open_by_key(SHEET_ID)

def get_or_create_worksheet(sheet, title, cols):
    try:
        return sheet.worksheet(title)
    except gspread.WorksheetNotFound:
        return sheet.add_worksheet(title=title, rows="1000", cols=str(cols))

ws_concursos = get_or_create_worksheet(sheet, "concursos", 20)
ws_logs = get_or_create_worksheet(sheet, "logs", 5)
ws_geo = get_or_create_worksheet(sheet, "geocodes_cache", 4)

# =====================================
# 3) Load geocode cache
# =====================================
geo_records = ws_geo.get_all_records()
geo_cache = {r["cidade"].lower(): (r["lat"], r["lon"]) for r in geo_records if "cidade" in r}

def get_city_coords(city):
    city_norm = city.lower().strip()
    if city_norm in geo_cache:
        return geo_cache[city_norm]
    print(f"🌍 Geocoding: {city}")
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": f"{city}, Brasil", "format": "json", "limit": 1}
    try:
        resp = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200 and resp.json():
            data = resp.json()[0]
            lat, lon = float(data["lat"]), float(data["lon"])
            geo_cache[city_norm] = (lat, lon)
            safe_sheet_write("update geocode cache", ws_geo.append_row,
                             [city, lat, lon, datetime.now().isoformat()],
                             value_input_option="USER_ENTERED")
            time.sleep(1)
            return lat, lon
    except Exception as e:
        print(f"⚠️ Erro ao geocodificar {city}: {e}")
    return None

# =====================================
# 4) Scraper
# =====================================
URL = "https://www.pciconcursos.com.br/concursos/sul/"
response = requests.get(URL)
response.encoding = "utf-8"
soup = BeautifulSoup(response.text, "html.parser")

concursos = []
today = datetime.today()
for na_div in soup.find_all("div", class_="na"):
    ca_div = na_div.find("div", class_="ca")
    if not ca_div:
        continue
    a = ca_div.find("a", href=True)
    if not a:
        continue
    text = a.get_text(strip=True)
    if not text.startswith("Prefeitura de "):
        continue
    cidade = text.replace("Prefeitura de ", "").strip()
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
            if prazo_date < today:
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
        "is_escolaridade_EMouSuperior&Cargo_não_Professor_truer": is_escolaridade_EMouSuperior_e_Cargo_nao_Professor,
    }

    concurso["id_hash"] = hashlib.md5(
        f"{concurso['cidade']}_{concurso['estado']}_{concurso['link_edital']}".encode("utf-8")
    ).hexdigest()[:10]

    concursos.append(concurso)

print(f"✅ Encontradas {len(concursos)} prefeituras válidas")

# =====================================
# 5) Save to Google Sheets (Enhanced duplicate prevention)
# =====================================
lat_osorio, lon_osorio = -29.8884, -50.2668
lat_floripa, lon_floripa = -27.5954, -48.5480
lat_curitiba, lon_curitiba = -25.4284, -49.2733

columns = [
    "id_hash", "coletado_em", "fonte_url", "estado",
    "vagas", "is_varios_cargos_true",
    "is_escolaridade_EMouSuperior&Cargo_não_Professor_truer",
    "cidade", "orgao", "salario",
    "Review", "Not Interested",
    "titulo", "prazo_de_inscricao", "link_edital",
    "dist_km_osorio", "dist_km_floripa", "dist_km_curitiba", "maps_link"
]

# 1) Local dedupe (pre-run)
unique_concursos = {c["id_hash"]: c for c in concursos}
deduped_concursos = list(unique_concursos.values())

# 2) Load existing sheet hashes
existing_hashes = set(ws_concursos.col_values(columns.index("id_hash") + 1))
written_hashes = set()  # per-run memory

added = 0
failed_rows = []

# ---------------------------------------------------------------------
# NEW: real-time duplicate check (reads last 30 rows only = fast)
# ---------------------------------------------------------------------
def hash_exists_in_last_rows(ws, hid, rows_to_check=30):
    """Fast duplicate detection to prevent ghost double-writes."""
    try:
        all_values = ws.get_all_values()
        if not all_values:
            return False

        # Protect header
        data_rows = all_values[1:]

        # Check last N rows
        for row in data_rows[-rows_to_check:]:
            if len(row) > 0 and row[0] == hid:
                return True
        return False

    except Exception as e:
        print(f"⚠️ Real-time duplicate check failed: {e}")
        # Fail safe: assume NOT duplicate so script continues
        return False

# ---------------------------------------------------------------------
# NEW: enhanced safe append with real-time dedupe
# ---------------------------------------------------------------------
def safe_append_concurso_row(concurso_hash, row, ws_concursos, attempt_delays=[5, 15, 30]):
    for attempt, delay in enumerate(attempt_delays, start=1):

        # ---- REAL-TIME DUPLICATE CHECK BEFORE RETRYING ----
        if hash_exists_in_last_rows(ws_concursos, concurso_hash):
            print(f"⛔ Real-time duplicate detected before write → skipping hash {concurso_hash}")
            return True  # treat as success (row already exists)

        try:
            safe_sheet_write(
                f"append concurso row (try {attempt})",
                ws_concursos.append_row,
                row,
                value_input_option="USER_ENTERED"
            )
            print(f"✅ Row success on attempt {attempt}")
            return True

        except Exception as e:
            print(f"⚠️ Failed attempt {attempt}, waiting {delay} sec before retry. Error: {e}")

            if attempt < len(attempt_delays):
                time.sleep(delay)

    print(f"❌ All attempts failed for hash {concurso_hash}, skipping.")
    return False

# ---------------------------------------------------------------------
# MAIN WRITE LOOP (unchanged logic, upgraded safety)
# ---------------------------------------------------------------------
print("About to write the following hashes:", [c["id_hash"] for c in deduped_concursos])

for concurso in deduped_concursos:
    hid = concurso["id_hash"]

    # Normal duplicate protections
    if hid in existing_hashes or hid in written_hashes:
        print(f"⚠️ Skipping duplicate id_hash: {hid}")
        continue

    written_hashes.add(hid)

    # Compute distances
    coords = get_city_coords(concurso["cidade"])
    if coords:
        lat_cidade, lon_cidade = coords
        concurso["dist_km_osorio"] = round(haversine(lat_osorio, lon_osorio, lat_cidade, lon_cidade), 1)
        concurso["dist_km_floripa"] = round(haversine(lat_floripa, lon_floripa, lat_cidade, lon_cidade), 1)
        concurso["dist_km_curitiba"] = round(haversine(lat_curitiba, lon_curitiba, lat_cidade, lon_cidade), 1)
    else:
        concurso["dist_km_osorio"] = concurso["dist_km_floripa"] = concurso["dist_km_curitiba"] = ""

    concurso["maps_link"] = make_maps_link(concurso["cidade"], concurso["estado"])

    # Create row data with defaults
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
# 6) Log run
# =====================================
safe_sheet_write(
    "append log row",
    ws_logs.append_row,
    [datetime.now().isoformat(), f"{added} novos concursos adicionados", "Sul (RS/SC/PR)"],
    value_input_option="USER_ENTERED"
)

# -----------------------------
# Extra: update Filter Views to cover all rows (A:S) and log that action
# -----------------------------
def update_filter_views(spreadsheet_id, tab_name, creds, end_row_index=1000000, end_col_index=19):
    try:
        service = build('sheets', 'v4', credentials=creds)
        meta = service.spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields='sheets(properties(sheetId,title),filterViews)'
        ).execute()
        target_sheet = next((s for s in meta.get('sheets', []) if s['properties']['title'] == tab_name), None)
        if not target_sheet:
            print(f"⚠️ update_filter_views: sheet/tab '{tab_name}' not found.")
            return 0
        sheet_id_num = target_sheet['properties']['sheetId']
        filter_views = target_sheet.get('filterViews', [])
        if not filter_views:
            print(f"ℹ️ update_filter_views: no filter views in '{tab_name}'.")
            return 0
        requests = []
        for fv in filter_views:
            fv_id = fv.get('filterViewId')
            fv_title = fv.get('title', '')
            new_range = {
                "sheetId": sheet_id_num,
                "startRowIndex": 0,
                "endRowIndex": end_row_index,
                "startColumnIndex": 0,
                "endColumnIndex": end_col_index
            }
            requests.append({
                "updateFilterView": {
                    "filter": {
                        "filterViewId": fv_id,
                        "title": fv_title,
                        "range": new_range
                    },
                    "fields": "range"
                }
            })
        if requests:
            body = {"requests": requests}
            service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body=body).execute()
            print(f"✅ update_filter_views: Updated {len(requests)} filter view(s) in '{tab_name}'.")
            return len(requests)
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
        [datetime.now().isoformat(), f"Filter views updated in 'concursos': {updated_filters_count}"],
        value_input_option="USER_ENTERED"
    )
except Exception as e:
    print(f"⚠️ Failed to update filter views or to log the event: {e}")

# =====================================
# 7) Check and move expired concursos
# =====================================
def process_concursos_validity(ws_source, ws_expired, ws_logs):
    print("\n🔍 Checking concursos for expiration status...")
    today = datetime.today().date()
    data = ws_source.get_all_values()
    if not data or len(data) < 2:
        print("⚠️ No data found in source sheet.")
        return
    headers = data[0]
    rows = data[1:]
    prazo_index = headers.index("prazo_de_inscricao") if "prazo_de_inscricao" in headers else 13
    valid_rows, expired_rows = [], []
    for row in rows:
        if len(row) <= prazo_index:
            continue
        prazo_str = row[prazo_index].strip()
        try:
            prazo_date = datetime.strptime(prazo_str, "%d/%m/%Y").date()
            (valid_rows if prazo_date >= today else expired_rows).append(row)
        except ValueError:
            continue
    print(f"📊 Total concursos found: {len(rows)}")
    print(f"✅ Valid: {len(valid_rows)} | ❌ Expired: {len(expired_rows)}")
    safe_sheet_write("overwrite valid concursos", ws_source.update,
                     range_name="A1", values=[headers] + valid_rows,
                     value_input_option="USER_ENTERED")
    if expired_rows:
        existing_expired = ws_expired.get_all_values()
        next_row = len(existing_expired) + 1
        safe_sheet_write(f"append {len(expired_rows)} expired concursos",
                         ws_expired.update, range_name=f"A{next_row}", values=expired_rows,
                         value_input_option="USER_ENTERED")
        safe_sheet_write("log expired move", ws_logs.append_row,
                         [datetime.now().isoformat(),
                          f"{len(expired_rows)} concursos expirados movidos para '{ws_expired.title}'"],
                         value_input_option="USER_ENTERED")
        print(f"📦 {len(expired_rows)} expired concursos moved.")
    else:
        print("ℹ️ No expired concursos found.")

try:
    ws_expired = sheet.worksheet("concursos_expirados")
except gspread.WorksheetNotFound:
    ws_expired = sheet.add_worksheet(title="concursos_expirados", rows="1000", cols="20")
process_concursos_validity(ws_concursos, ws_expired, ws_logs)

# =====================================
# 8) Final summary feedback
# =====================================
try:
    summary_new = f"• {added} new concursos added today." if added > 0 else "• No new concursos were added on this run."
    summary_expired = f"• {len(ws_expired.get_all_values()) - 1} concursos currently archived (expired)."
    summary_filters = f"• Filter views updated: {updated_filters_count}"
    summary_failed = f"• Rows not saved after 3 attempts: {failed_rows}" if failed_rows else "• All rows saved successfully."
    print("\n📈 Summary:")
    print(summary_new)
    print(summary_expired)
    print(summary_filters)
    print(summary_failed)
except Exception as e:
    print(f"⚠️ Could not print final summary: {e}")

print("🎯 All tasks completed successfully!")
print("⏳ Keeping script open for 15 minutes for review (Ctrl+C to exit early)...")
time.sleep(15 * 60)