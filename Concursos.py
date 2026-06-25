import gspread
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import pickle
from datetime import datetime
import os

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# --- 1) Load saved credentials or authenticate ---
if os.path.exists("token.pickle"):
    with open("token.pickle", "rb") as token:
        creds = pickle.load(token)
else:
    # Dynamically find credentials.json in the same folder as the script
    current_dir = os.path.dirname(os.path.abspath(__file__))
    credentials_path = os.path.join(current_dir, "credentials.json")

    if not os.path.exists(credentials_path):
        raise FileNotFoundError(f"credentials.json not found at {credentials_path}")

    flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)

    creds = flow.run_local_server(port=0)
    with open("token.pickle", "wb") as token:
        pickle.dump(creds, token)

gc = gspread.authorize(creds)

# --- 2) Open the sheet ---
SHEET_ID = "1lFQFIs6Y6t2lhwb6SE88QiC-X4Rnsmh7hyCze23fM4Y"
sheet = gc.open_by_key(SHEET_ID)

# --- 3) Ensure worksheets ---
def get_or_create_worksheet(sheet, title, cols):
    try:
        return sheet.worksheet(title)
    except gspread.WorksheetNotFound:
        return sheet.add_worksheet(title=title, rows="1000", cols=str(cols))

ws_concursos = get_or_create_worksheet(sheet, "concursos", 12)
ws_logs = get_or_create_worksheet(sheet, "logs", 5)
ws_geo = get_or_create_worksheet(sheet, "geocodes_cache", 6)

# --- 4) Insert test data ---
concurso = {
    "id_hash": "abc123",
    "coletado_em": datetime.now().isoformat(),
    "fonte_url": "https://www.pciconcursos.com.br/concursos/rs",
    "estado": "RS",
    "cidade": "Porto Alegre",
    "titulo": "Prefeitura de Porto Alegre - Concurso Público",
    "orgao": "Prefeitura de Porto Alegre",
    "vagas": "20",
    "data_publicacao": "2025-08-18",
    "link_edital": "https://www.pciconcursos.com.br/edital/123"
}

columns = [
    "id_hash", "coletado_em", "fonte_url", "estado", "cidade",
    "titulo", "orgao", "vagas", "data_publicacao", "link_edital"
]

# Create header if missing
if not ws_concursos.row_values(1):
    ws_concursos.insert_row(columns, 1)

# Append row
row = [concurso.get(col, "") for col in columns]
ws_concursos.append_row(row, value_input_option="USER_ENTERED")

print("✅ Concurso salvo com sucesso no Google Sheets!")
