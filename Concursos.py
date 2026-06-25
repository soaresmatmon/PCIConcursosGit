import gspread
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import pickle
from datetime import datetime
import os
import requests
from bs4 import BeautifulSoup
import re
import hashlib
import unicodedata
import time
from math import radians, sin, cos, sqrt, atan2

# =====================================
# 0) Helpers
# =====================================
def normalize_text(text):
    text = text.lower().replace("\xa0", " ")
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))

def haversine(lat1, lon1, lat2, lon2):
    """Calculate distance in km between two lat/lon points."""
    R = 6371.0
    dlon = radians(lon2 - lon1)
    dlat = radians(lat2 - lat1)
    a = sin(dlat / 2)**2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c

# =====================================
# 1) Google Sheets Authentication
# =====================================
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

if os.path.exists("token.pickle"):
    with open("token.pickle", "rb") as token:
        creds = pickle.load(token)
else:
    flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
    creds = flow.run_local_server(port=0)
    with open("token.pickle", "wb") as token:
        pickle.dump(creds, token)

gc = gspread.authorize(creds)

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

ws_concursos = get_or_create_worksheet(sheet, "concursos", 14)
ws_logs = get_or_create_worksheet(sheet, "logs", 5)
ws_geo = get_or_create_worksheet(sheet, "geocodes_cache", 4)

# =====================================
# 3) Load geocode cache
# =====================================
geo_records = ws_geo.get_all_records()
geo_cache = {r["cidade"].lower(): (r["lat"], r["lon"]) for r in geo_records if "cidade" in r}

def get_city_coords(city):
    """Get lat/lon for a city, from cache or via Nominatim."""
    city_norm = city.lower().strip()
    if city_norm in geo_cache:
        return geo_cache[city_norm]

    print(f"🌍 Geocoding: {city}")
    url = f"https://nominatim.openstreetmap.org/search"
    params = {"q": f"{city}, Brasil", "format": "json", "limit": 1}
    try:
        resp = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200 and resp.json():
            data = resp.json()[0]
            lat, lon = float(data["lat"]), float(data["lon"])
            geo_cache[city_norm] = (lat, lon)
            ws_geo.append_row([city, lat, lon, datetime.now().isoformat()], value_input_option="USER_ENTERED")
            time.sleep(1)  # be gentle with Nominatim
            return lat, lon
    except Exception as e:
        print(f"⚠️ Erro ao geocodificar {city}: {e}")
    return None

# =====================================
# 4) Scraper for RS + SC
# =====================================
URL = "https://www.pciconcursos.com.br/concursos/sul/"
response = requests.get(URL)
response.encoding = "utf-8"
soup = BeautifulSoup(response.text, "html.parser")

# Find both sections
sections = {"RS": soup.find("div", {"id": "RS"}), "SC": soup.find("div", {"id": "SC"})}

concursos = []
today = datetime.today()

for estado, div_start in sections.items():
    if not div_start:
        continue

    next_state = None
    if estado == "RS":
        next_state = sections["SC"]
    elif estado == "SC":
        next_state = soup.find("div", {"id": "PR"})  # stop before Paraná section

    region_content = []
    for el in div_start.next_siblings:
        if el == next_state:
            break
        region_content.append(str(el))

    region_soup = BeautifulSoup("".join(region_content), "html.parser")

    for na_div in region_soup.find_all("div", class_="na"):
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

        vagas_info = cd_div.get_text(" ", strip=True) if cd_div else ""
        ca_text = ca_div.get_text(" ", strip=True) if ca_div else ""
        full_text = " ".join([ca_text, vagas_info])

        ca_text_norm = normalize_text(full_text)

        is_varios_cargos_true = "varios cargos" in ca_text_norm or "diversos cargos" in ca_text_norm
        is_escolaridade_EMouSuperior_e_Cargo_nao_Professor = (
            (
                "medio" in ca_text_norm
                or "medio / superior" in ca_text_norm
                or "superior" in ca_text_norm
            )
            and ("professor" not in ca_text_norm)
        )

        # Extract prazo final
        prazo = ""
        if ce_div:
            prazo_span = ce_div.find("span")
            prazo_raw = prazo_span.get_text(" ", strip=True) if prazo_span else ""
            dates = re.findall(r"\d{1,2}/\d{1,2}/\d{4}", prazo_raw)
            if dates:
                prazo = dates[-1]

        # Filter expired concursos
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

print(f"✅ Encontradas {len(concursos)} prefeituras (RS + SC, válidas até hoje ou posteriores)")

# =====================================
# 5) Save to Google Sheets (geocode + distance)
# =====================================
lat_osorio, lon_osorio = -29.8884, -50.2668
lat_floripa, lon_floripa = -27.5954, -48.5480

columns = [
    "id_hash", "coletado_em", "fonte_url", "estado",
    "vagas",
    "is_varios_cargos_true",
    "is_escolaridade_EMouSuperior&Cargo_não_Professor_truer",
    "cidade",
    "orgao",
    "salario",
    "titulo",
    "prazo_de_inscricao",
    "link_edital",
    "dist_km_osorio",
    "dist_km_floripa"
]

existing_hashes = set(ws_concursos.col_values(columns.index("id_hash") + 1))
added = 0

for concurso in concursos:
    if concurso["id_hash"] in existing_hashes:
        continue  # skip duplicates before geocoding

    coords = get_city_coords(concurso["cidade"])
    if coords:
        lat_cidade, lon_cidade = coords
        concurso["dist_km_osorio"] = round(haversine(lat_osorio, lon_osorio, lat_cidade, lon_cidade), 1)
        concurso["dist_km_floripa"] = round(haversine(lat_floripa, lon_floripa, lat_cidade, lon_cidade), 1)
    else:
        concurso["dist_km_osorio"] = ""
        concurso["dist_km_floripa"] = ""

    row = [concurso.get(col, "") for col in columns]
    ws_concursos.append_row(row, value_input_option="USER_ENTERED")
    added += 1

# =====================================
# 6) Log run
# =====================================
ws_logs.append_row(
    [datetime.now().isoformat(), f"{added} novos concursos adicionados", "RS+SC"],
    value_input_option="USER_ENTERED"
)

print(f"📝 {added} novos concursos adicionados (RS+SC).")
print("✅ Processo concluído com sucesso!")
