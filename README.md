Markdown
# PCI Concursos Automation Tracker

A resilient Python automation pipeline that extracts public civil service exam notifications, calculates geodesic distances to regional hubs, and synchronizes the optimized data with the Google Sheets API.

This project streamlines exam monitoring for the southern region of Brazil (RS, SC, PR) by filtering opportunities based on location, custom distance constraints, and specific career roles.

## 🛠️ Tech Stack & Architecture

- **Language:** Python 3.x
- **APIs & Storage:** Google Sheets API, Nominatim (OpenStreetMap) API
- **Key Engineering Features:**
  - **3-Tier Geocoding Cache:** Minimizes external API overhead by checking a local JSON cache and an offline municipal reference table before querying Nominatim.
  - **Concurrency Control:** Utilizes a native file-lock system (`.lock`) to prevent duplicate overlapping script executions.
  - **Data Integrity:** Employs deterministic `MD5` hashing to identify duplicate entries and gracefully archives expired posts to a separate historical tab.

## 📦 Setup & Installation

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/soaresmatmon/PCIConcursosGit.git](https://github.com/soaresmatmon/PCIConcursosGit.git)
   cd PCIConcursosGit
Install dependencies:

Bash
pip install -r requirements.txt
API Configuration:

Place your Google Service Account .json credentials file into your local project directory (ensure it is kept out of version control).

Configure your local paths for the lock file and credentials inside Concursos.py.

🛑 Disclaimer
This tool was developed strictly for personal data organization and portfolio demonstration. It handles entirely public domain, uncopyrightable official government notices (atos oficiais). The script is designed to be highly respectful of host infrastructure through defensive caching and strict execution pacing.


## ⚖️ License & Intellectual Property

Copyright © 2026 Mateus Cardoso. All rights reserved.

This repository and its foundational automation scripts are completely proprietary. Unauthorized duplication, modification, system distribution, or commercial exploitation via any execution medium is strictly prohibited. Access to the public codebase is granted exclusively for human portfolio evaluation, software engineering code audits, and recruiting review purposes.