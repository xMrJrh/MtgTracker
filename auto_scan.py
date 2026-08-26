# --- FICHIER auto_scan.py ---
# C'est le script que le Robot GitHub va exécuter tous les jours à 12h

import pandas as pd
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import re
import requests
import urllib.parse
from streamlit_gsheets import GSheetsConnection

# --- CONFIGURATIONS IDENTIQUES ---
SCRYFALL_HEADERS = {"User-Agent": "MTGFinanceTracker/1.0", "Accept": "application/json"}
WEB_HEADERS = {"User-Agent": "Mozilla/5.0"}
BASIC_LANDS = ["Plains", "Island", "Swamp", "Mountain", "Forest"]

# --- FONCTIONS D'ACCÈS BASE DE DONNÉES ---
def get_gsheets_connection():
    return st.connection("gsheets", type=GSheetsConnection)

# --- FONCTIONS DE SCRAPING (Copiées depuis ton app principale) ---
# Note : @st.cache_data est remplacé par rien ici car le robot tourne une seule fois puis s'éteint.

def get_edhtop16_staples():
    filters = "?tab=staples&sortBy=TOP&staplesSortBy=MOST_PLAYED&timePeriod=ONE_MONTH&maxStanding=16&minEventSize=30"
    url = f"https://edhtop16.com/staples{filters}"
    try:
        resp = requests.get(url, headers=WEB_HEADERS, timeout=15)
        if resp.status_code != 200:
            print("❌ Erreur EDHTop16")
            return []
            
        clean_text = resp.text.replace('\\"', '"').replace('\\n', '')
        card_stats = {}
        pattern1 = r'"name"\s*:\s*"([^"]+)"(?:(?!"name").)*?"playRate[A-Za-z]*"\s*:\s*([\d.]+)'
        pattern2 = r'"playRate[A-Za-z]*"\s*:\s*([\d.]+)(?:(?!"playRate").)*?"name"\s*:\s*"([^"]+)"'
        for match in re.finditer(pattern1, clean_text, re.IGNORECASE):
            name, val = match.group(1), float(match.group(2))
            if val <= 1.0 and val > 0: val *= 100
            card_stats[name] = max(card_stats.get(name, 0.0), val)
        for match in re.finditer(pattern2, clean_text, re.IGNORECASE):
            val, name = float(match.group(1)), match.group(2)
            if val <= 1.0 and val > 0: val *= 100
            card_stats[name] = max(card_stats.get(name, 0.0), val)
                
        results = []
        for name, inc in card_stats.items():
            if name not in BASIC_LANDS and len(name) > 2 and "{" not in name:
                results.append({"name": name, "inclusion": inc})
        return results
    except Exception as e:
        print(f"Erreur EDHTop16 : {e}")
        return []

def get_mtgtop8_archetypes(format_code):
    url = f"https://www.mtgtop8.com/format?f={format_code}"
    try:
        session = requests.Session()
        resp = session.get(url, headers=WEB_HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, 'html.parser')
        select_meta = soup.find('select', {'name': 'meta'})
        if select_meta:
            for opt in select_meta.find_all('option'):
                val = opt.get('value')
                if val and val.isdigit() and int(val) > 0:
                    url = f"https://www.mtgtop8.com/format?f={format_code}&meta={val}"
                    resp = session.get(url, headers=WEB_HEADERS, timeout=15)
                    soup = BeautifulSoup(resp.text, 'html.parser')
                    break
        archetypes = {}
        for link in soup.find_all('a', href=True):
            if 'archetype?a=' in link['href']:
                name = link.get_text(strip=True)
                if not name and link.parent: name = link.parent.get_text(strip=True)
                name = re.sub(r'\d+(?:\.\d+)?\s*%', '', name).strip().lower()
                if name and "budget" not in name and "compare" not in name:
                    if name not in archetypes: archetypes[name] = link['href']
        return archetypes
    except Exception as e:
        return {}

def get_all_deck_ids(archetype_url):
    if not archetype_url.startswith("http"): url = f"https://www.mtgtop8.com/{archetype_url}"
    else: url = archetype_url
    try:
        resp = requests.get(url, headers=WEB_HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, 'html.parser')
        deck_ids = set()
        for link in soup.find_all('a', href=True):
            if 'event?e=' in link['href'] and '&d=' in link['href']:
                match = re.search(r'&d=(\d+)', link['href'])
                if match: deck_ids.add(match.group(1))
        return list(deck_ids)
    except Exception: return []

def fetch_aggregated_decks(deck_ids):
    card_stats = {}
    total_decks = len(deck_ids)
    if total_decks == 0: return []
    for d_id in deck_ids:
        url = f"https://www.mtgtop8.com/mtgo?d={d_id}"
        try:
            resp = requests.get(url, headers=WEB_HEADERS, timeout=10)
            if resp.status_code == 200:
                seen_in_this_deck = set()
                for line in resp.text.split('\n'):
                    line = line.strip()
                    if line and line.lower() != "sideboard":
                        parts = line.split(" ", 1)
                        if len(parts) == 2 and parts[0].isdigit():
                            name = parts[1].strip('\r')
                            if name not in BASIC_LANDS:
                                if name not in card_stats: card_stats[name] = {"decks_count": 0}
                                if name not in seen_in_this_deck:
                                    card_stats[name]["decks_count"] += 1
                                    seen_in_this_deck.add(name)
        except Exception: continue
    results = []
    for name, stats in card_stats.items():
        results.append({"name": name, "inclusion": (stats["decks_count"] / total_decks) * 100})
    return results

def get_recent_sets():
    resp = requests.get("https://api.scryfall.com/sets", headers=SCRYFALL_HEADERS).json()
    cutoff = (datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d')
    return [s['code'] for s in resp.get("data", []) if s.get('released_at') >= cutoff]

def fetch_scryfall_batch(card_names):
    if not card_names: return []
    url = "https://api.scryfall.com/cards/collection"
    results = []
    for i in range(0, len(card_names), 75):
        batch = card_names[i:i+75]
        payload = {"identifiers": [{"name": n} for n in batch]}
        resp = requests.post(url, headers=SCRYFALL_HEADERS, json=payload)
        if resp.status_code == 200:
            results.extend(resp.json().get("data", []))
    return results

# --- LOGIQUE D'ENREGISTREMENT ---

def update_price_history(scryfall_data, format_name="Global"):
    today = datetime.now().strftime('%Y-%m-%d')
    conn = get_gsheets_connection()

    def _write_to_sheet(sheet_name):
        try:
            df = conn.read(worksheet=sheet_name, usecols=[0, 1, 2], ttl=0)
        except:
            df = pd.DataFrame(columns=["Date", "Card Name", "Price"])
        
        # SÉCURITÉ COLONNES
        if df.empty:
             df = pd.DataFrame(columns=["Date", "Card Name", "Price"])
        elif len(df.columns) != 3:
            df = pd.DataFrame(columns=["Date", "Card Name", "Price"])
        else:
            df.columns = ["Date", "Card Name", "Price"]
            
        new_rows = []
        for c in scryfall_data:
            name = c.get("name")
            price = float(c.get("prices", {}).get("eur") or 0.0)
            if price > 0:
                if not ((df["Date"] == today) & (df["Card Name"] == name)).any():
                    new_rows.append({"Date": today, "Card Name": name, "Price": price})

        if new_rows:
            df_new = pd.DataFrame(new_rows)
            df_updated = pd.concat([df, df_new], ignore_index=True)
            conn.update(worksheet=sheet_name, data=df_updated)
            print(f"📝 {sheet_name}: {len(new_rows)} prix mis à jour.")

    _write_to_sheet("Global")
    if format_name and format_name != "Global":
        _write_to_sheet(format_name)

# --- LE CŒUR DU ROBOT ---
def run_daily_scan():
    print("\n⏰ Début du scan global...")
    
    FORMAT_MAP = {"Standard": "ST", "Pioneer": "PI", "Modern": "MO", "Pauper": "PAU", "Legacy": "LE", "Vintage": "VI"}
    
    # 1. SCAN GLOBAL (MTGTOP8)
    global_cards = []
    for fmt_name, fmt_code in FORMAT_MAP.items():
        print(f"--- Analyse {fmt_name} ---")
        archs = get_mtgtop8_archetypes(fmt_code)
        top_archs = list(archs.values())[:3]
        top_card_list = []
        
        for arch_url in top_archs:
            ids = get_all_deck_ids(arch_url)
            decks = fetch_aggregated_decks(ids[:5])
            for d in decks: top_card_list.append(d)
        
        # On récupère les noms des cartes trouvées
        names_set = list(set([x["name"] for x in top_card_list]))
        scry_data = fetch_scryfall_batch(names_set)
        update_price_history(scry_data, fmt_name)

    # 2. SCAN cEDH (EDHTOP16)
    print("\n--- Analyse cEDH Staples Globales ---")
    cedh_cards = get_edhtop16_staples("ALL")
    if cedh_cards:
        names_cedh = [c["name"] for c in cedh_cards]
        scry_cedh = fetch_scryfall_batch(names_cedh)
        update_price_history(scry_cedh, "cEDH")

    print(f"\n✅ Terminé à {datetime.now()}")

if __name__ == "__main__":
    run_daily_scan()