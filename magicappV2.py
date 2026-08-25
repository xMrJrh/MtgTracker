import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import re
import requests
import os
import urllib.parse

# --- CONFIGURATION DE LA PAGE ---
st.set_page_config(page_title="MTG Finance & Tracker", page_icon="📈", layout="wide")

SCRYFALL_HEADERS = {"User-Agent": "MTGFinanceTracker/1.0", "Accept": "application/json"}
WEB_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}
BASIC_LANDS = ["Plains", "Island", "Swamp", "Mountain", "Forest", "Snow-Covered Plains", "Snow-Covered Island", "Snow-Covered Swamp", "Snow-Covered Mountain", "Snow-Covered Forest", "Wastes"]
HISTORY_FILE = "mtg_prices_history.csv"

# ==========================================
# GESTION DE L'HISTORIQUE DES PRIX
# ==========================================

def update_price_history(scryfall_data):
    today = datetime.now().strftime('%Y-%m-%d')
    if os.path.exists(HISTORY_FILE):
        df = pd.read_csv(HISTORY_FILE)
    else:
        df = pd.DataFrame(columns=["Date", "Card Name", "Price"])
        
    new_rows = []
    for c in scryfall_data:
        name = c.get("name")
        price = float(c.get("prices", {}).get("eur") or 0.0)
        if price > 0:
            already_recorded = ((df['Date'] == today) & (df['Card Name'] == name)).any()
            if not already_recorded:
                new_rows.append({"Date": today, "Card Name": name, "Price": price})
                
    if new_rows:
        df_new = pd.DataFrame(new_rows)
        df = pd.concat([df, df_new], ignore_index=True)
        df.to_csv(HISTORY_FILE, index=False)

def load_price_history(card_names):
    if not os.path.exists(HISTORY_FILE):
        return pd.DataFrame()
    df = pd.read_csv(HISTORY_FILE)
    return df[df['Card Name'].isin(card_names)]

# ==========================================
# MOTEUR cEDH : LECTURE DIRECTE EDHTOP16
# ==========================================

@st.cache_data(ttl=3600)
def get_edhtop16_commanders():
    url = "https://edhtop16.com/?timePeriod=ONE_MONTH"
    try:
        resp = requests.get(url, headers=WEB_HEADERS, timeout=15)
        commanders = {}
        for match in re.finditer(r'href="/commander/([^"]+)"', resp.text):
            raw_url = match.group(1)
            clean_name = urllib.parse.unquote(raw_url).replace('%20', ' ')
            if clean_name not in commanders:
                commanders[clean_name] = raw_url
        return {"error": False, "data": commanders}
    except Exception as e:
        return {"error": True, "msg": str(e)}

@st.cache_data(ttl=3600)
def get_edhtop16_staples(commander_raw_url="ALL"):
    filters = "?tab=staples&sortBy=TOP&staplesSortBy=MOST_PLAYED&timePeriod=ONE_MONTH&maxStanding=16&minEventSize=30"
    
    if commander_raw_url == "ALL":
        url = f"https://edhtop16.com/{filters}"
    else:
        url = f"https://edhtop16.com/commander/{commander_raw_url}{filters}"
        
    try:
        resp = requests.get(url, headers=WEB_HEADERS, timeout=15)
        
        if resp.status_code != 200:
            st.error(f"⚠️ Accès bloqué par EDHTop16 (Erreur {resp.status_code}).")
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
                
        if not results:
            st.warning("⚠️ L'algorithme n'a pas trouvé de données JSON de cartes dans la page.")
            with st.expander("Voir les données brutes (Debug)"):
                st.code(clean_text[:2000], language="html")
                
        return results
    except Exception as e:
        st.error(f"Erreur d'exécution : {e}")
        return []

# ==========================================
# MOTEUR 60 CARTES : MTGTOP8
# ==========================================

@st.cache_data(ttl=3600)
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
                name = re.sub(r'\d+(?:\.\d+)?\s*%', '', name).strip()
                name_lower = name.lower()
                if name and "budget" not in name_lower and "compare" not in name_lower and name_lower != "others":
                    if name not in archetypes: archetypes[name] = link['href']
        return {"error": False, "data": archetypes}
    except Exception as e:
        return {"error": True, "msg": str(e)}

@st.cache_data(ttl=3600)
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

@st.cache_data(ttl=3600)
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

# ==========================================
# ENRICHISSEMENT API SCRYFALL & TRADUCTION
# ==========================================

@st.cache_data(ttl=86400)
def get_recent_sets():
    resp = requests.get("https://api.scryfall.com/sets", headers=SCRYFALL_HEADERS).json()
    cutoff = (datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d')
    return [s['code'] for s in resp.get("data", []) if s.get('set_type') in ['expansion', 'core', 'draft_innovation'] and s.get('released_at') >= cutoff]

@st.cache_data(ttl=86400)
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

# Nouvelle fonction : Traduction FR -> EN via Scryfall pour la barre de recherche
@st.cache_data(ttl=86400, show_spinner=False)
def translate_search_term(search_term):
    if len(search_term) < 3:
        return []
    try:
        query = urllib.parse.quote(search_term)
        # L'API Scryfall cherche automatiquement dans toutes les langues
        url = f"https://api.scryfall.com/cards/search?q={query}"
        resp = requests.get(url, headers=SCRYFALL_HEADERS, timeout=5)
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            # On retourne la liste des noms anglais officiels trouvés
            return [c["name"].lower() for c in data[:15]]
    except:
        pass
    return []

def determine_card_status(scryfall_card, recent_sets):
    set_code = scryfall_card.get("set", "").lower()
    is_reprint = scryfall_card.get("reprint", False)
    if set_code in recent_sets and not is_reprint: return "🔥 NOUVELLE INÉDITE", 1
    elif set_code in recent_sets and is_reprint: return "♻️ RÉÉDITION", 2
    else: return "📈 CLASSIQUE", 3

def extract_card_image(c):
    if 'image_uris' in c: return c['image_uris'].get('normal', '')
    if 'card_faces' in c: return c['card_faces'][0]['image_uris'].get('normal', '')
    return ""

def get_set_and_year(c):
    set_name = c.get("set", "").upper()
    date_str = c.get("released_at", "")
    year = date_str.split("-")[0] if date_str else ""
    return f"{set_name} ({year})" if year else set_name

# ==========================================
# INTERFACE INTERACTIVE (Recherche & Tri)
# ==========================================

def display_interactive_dataframe(df):
    """Fonction maîtresse qui gère la barre de recherche, le tri et l'affichage des grilles."""
    st.markdown("---")
    col1, col2, col3 = st.columns([2, 1, 1])
    
    with col1:
        search_query = st.text_input("🔍 Rechercher par nom (FR ou EN) :", placeholder="ex: Anneau Unique...")
    with col2:
        sort_by = st.selectbox("Trier par :", ["Inclusion (Défaut)", "Prix décroissant", "Prix croissant", "Nom (A-Z)"])
    with col3:
        # st.write("") permet d'aligner le toggle visuellement avec les autres champs
        st.write("")
        show_only_new = st.toggle("🎯 Nouveautés Inédites Uniquement", value=False)

    filtered_df = df.copy()

    # 1. Filtre Nouveautés
    if show_only_new:
        filtered_df = filtered_df[filtered_df["Tri_Statut"] == 1]

    # 2. Filtre Recherche (Bilingue)
    if search_query:
        # Recherche classique (Nom Anglais)
        mask_en = filtered_df['Nom de la Carte'].str.contains(search_query, case=False, na=False)
        
        # Traduction invisible via Scryfall pour le Français
        translated_names = translate_search_term(search_query)
        if translated_names:
            pattern = '|'.join([re.escape(n) for n in translated_names])
            mask_fr = filtered_df['Nom de la Carte'].str.contains(pattern, case=False, na=False)
        else:
            mask_fr = pd.Series(False, index=filtered_df.index)
            
        # On garde les cartes si elles matchent en EN ou en FR
        filtered_df = filtered_df[mask_en | mask_fr]

    # 3. Tri des données
    if sort_by == "Inclusion (Défaut)":
        sort_col = "Inclusion" if "Inclusion" in filtered_df.columns else "Inclusion Max"
        filtered_df = filtered_df.sort_values(by=["Tri_Statut", sort_col, "Prix €"], ascending=[True, False, False])
    elif sort_by == "Prix décroissant":
        filtered_df = filtered_df.sort_values(by=["Prix €"], ascending=False)
    elif sort_by == "Prix croissant":
        filtered_df = filtered_df.sort_values(by=["Prix €"], ascending=True)
    elif sort_by == "Nom (A-Z)":
        filtered_df = filtered_df.sort_values(by=["Nom de la Carte"], ascending=True)

    # 4. Affichage final
    if filtered_df.empty:
        st.warning("Aucune carte ne correspond à ces critères de recherche ou de tri.")
    else:
        # Affiche le compteur de cartes
        st.caption(f"Affichage de **{len(filtered_df)}** cartes.")
        display_card_grid(filtered_df)

def display_card_grid(dataframe):
    cards_list = dataframe.to_dict('records')
    for i in range(0, len(cards_list), 4):
        cols = st.columns(4)
        for j in range(4):
            if i + j < len(cards_list):
                card = cards_list[i + j]
                with cols[j]:
                    if card["Image"]: st.image(card["Image"], use_container_width=True)
                    st.markdown(f"**{card['Nom de la Carte']}**")
                    st.caption(f"📦 {card['Édition']}")
                    
                    if card.get("Format(s)"): st.caption(f"🏆 {card['Format(s)']}")
                    if card.get("Archétype(s)"): st.caption(f"⚔️ {card['Archétype(s)']}")
                        
                    if "Inclusion Max" in card: st.caption(f"📊 Inclusion : **{card['Inclusion Max']:.1f}%**")
                    elif "Inclusion" in card: st.caption(f"📊 Inclusion : **{card['Inclusion']:.1f}%**")
                        
                    st.success(f"**{card['Prix €']:.2f} €**")
                    
                    hist = load_price_history([card['Nom de la Carte']])
                    if not hist.empty and len(hist['Date'].unique()) > 1:
                        chart_data = hist.pivot_table(index='Date', columns='Card Name', values='Price')
                        st.line_chart(chart_data, height=150)
                    else:
                        st.caption("⏳ *Graphique en construction*")
        st.markdown("---")

# ==========================================
# INTERFACE UTILISATEUR (MENU PRINCIPAL)
# ==========================================

RECENT_SETS = get_recent_sets()
FORMAT_MAP = {"Standard": "ST", "Pioneer": "PI", "Modern": "MO", "Pauper": "PAU", "Legacy": "LE", "Vintage": "VI"}

st.sidebar.image("https://upload.wikimedia.org/wikipedia/commons/3/3f/Magicthegathering-logo.svg", width=200)
st.sidebar.title("Navigation")
menu = st.sidebar.radio("Aller à :", ["🏠 Accueil (Toutes Nouveautés)"] + list(FORMAT_MAP.keys()) + ["🐉 cEDH (EDHTop16)"])
st.sidebar.markdown("---")
st.sidebar.success("📊 **Tracker Actif**")

# ----------------------------------------------------
# PAGE : cEDH (EDHTop16)
# ----------------------------------------------------
if menu == "🐉 cEDH (EDHTop16)":
    st.title("🐉 cEDH Meta & Tracker")
    st.markdown("Analyse des Staples directement extraites de **EDHTop16**.")
    st.info("Filtres Actifs : **1 Mois | Top 16 uniquement | Minimum 30 joueurs.**")
    
    with st.spinner("Récupération de la base EDHTop16..."):
        cmd_response = get_edhtop16_commanders()
        
    if cmd_response.get("error"):
        st.error(f"Erreur EDHTop16 : {cmd_response.get('msg')}")
    else:
        commanders = cmd_response.get("data", {})
        arch_list = ["🌟 Aperçu du Format (Staples Globales)"] + sorted(list(commanders.keys()))
        deck_choisi = st.selectbox("Sélectionnez une vue :", arch_list)
        
        if st.button(f"🚀 Scanner : {deck_choisi}"):
            with st.spinner("Recherche des cartes cachées dans EDHTop16..."):
                cmd_raw_url = "ALL" if deck_choisi == "🌟 Aperçu du Format (Staples Globales)" else commanders[deck_choisi]
                deck_cards = get_edhtop16_staples(cmd_raw_url)
                
                if deck_cards:
                    st.info(f"✅ Extraction réussie ! Validation de {len(deck_cards)} cartes via Scryfall...")
                    with st.spinner("Analyse financière et nouveautés en cours..."):
                        card_names = list(set([c["name"] for c in deck_cards]))
                        scryfall_data = fetch_scryfall_batch(card_names)
                        update_price_history(scryfall_data)
                        
                        incl_map = {c["name"]: c["inclusion"] for c in deck_cards}
                        table_data = []
                        for c in scryfall_data:
                            full_name = c.get("name")
                            front_name = full_name.split(" // ")[0].strip()
                            statut_nom, tri_val = determine_card_status(c, RECENT_SETS)
                            table_data.append({
                                "Tri_Statut": tri_val, 
                                "Inclusion": incl_map.get(front_name) or incl_map.get(full_name, 0.0),
                                "Image": extract_card_image(c),
                                "Nom de la Carte": full_name,
                                "Édition": get_set_and_year(c),
                                "Prix €": float(c.get("prices", {}).get("eur") or 0.0),
                            })
                            
                        # Sauvegarde des données en mémoire pour ne pas les perdre lors de la recherche
                        st.session_state['cedh_df'] = pd.DataFrame(table_data)
                        st.session_state['cedh_deck_view'] = deck_choisi

        # Affiche le panneau interactif si on a des données en mémoire pour CE deck précis
        if 'cedh_df' in st.session_state and st.session_state.get('cedh_deck_view') == deck_choisi:
            display_interactive_dataframe(st.session_state['cedh_df'])

# ----------------------------------------------------
# PAGE D'ACCUEIL : NOUVEAUTÉS GLOBALES EN GRILLE
# ----------------------------------------------------
elif menu == "🏠 Accueil (Toutes Nouveautés)":
    st.title("🔥 Nouvelles Cartes du Métagame Global")
    
    if st.button("Lancer le Scanner Global"):
        progress_bar = st.progress(0)
        status_text = st.empty()
        global_new_cards = {}
        formats_list = list(FORMAT_MAP.items())
        
        for i, (fmt_name, fmt_code) in enumerate(formats_list):
            status_text.text(f"Analyse des archétypes en {fmt_name}...")
            arch_resp = get_mtgtop8_archetypes(fmt_code)
            
            if not arch_resp.get("error"):
                top_archs = list(arch_resp["data"].values())[:3]
                for arch_url in top_archs:
                    deck_ids = get_all_deck_ids(arch_url)
                    aggregated_cards = fetch_aggregated_decks(deck_ids[:5])
                    for c in aggregated_cards:
                        name, pct = c["name"], c["inclusion"]
                        if name not in global_new_cards: global_new_cards[name] = {"max_pct": pct, "formats": set()}
                        global_new_cards[name]["max_pct"] = max(global_new_cards[name]["max_pct"], pct)
                        if pct > 0: global_new_cards[name]["formats"].add(fmt_name)
                            
            progress_bar.progress((i + 1) / len(formats_list))
            
        status_text.text("Vérification avec Scryfall...")
        scryfall_data = fetch_scryfall_batch(list(global_new_cards.keys()))
        update_price_history(scryfall_data)
        
        home_cards = []
        for c in scryfall_data:
            full_name = c.get("name")
            front_name = full_name.split(" // ")[0].strip()
            statut_nom, tri_val = determine_card_status(c, RECENT_SETS)
            
            # Pour l'accueil, on ne sauvegarde de base que les nouveautés
            if tri_val == 1:
                stats = global_new_cards.get(front_name) or global_new_cards.get(full_name) or {}
                home_cards.append({
                    "Tri_Statut": tri_val,
                    "Image": extract_card_image(c),
                    "Nom de la Carte": full_name,
                    "Édition": get_set_and_year(c),
                    "Format(s)": ", ".join(list(stats.get("formats", []))),
                    "Inclusion Max": stats.get("max_pct", 0.0),
                    "Prix €": float(c.get("prices", {}).get("eur") or 0.0),
                })
                
        progress_bar.empty()
        status_text.empty()
        st.session_state['home_df'] = pd.DataFrame(home_cards)

    if 'home_df' in st.session_state:
        display_interactive_dataframe(st.session_state['home_df'])

# ----------------------------------------------------
# PAGES DES FORMATS (MTGTOP8) EN GRILLE
# ----------------------------------------------------
else:
    fmt_code = FORMAT_MAP[menu]
    st.title(f"🏆 Meta Decks & Tracker : {menu}")

    with st.spinner(f"Chargement des Archétypes pour le {menu}..."):
        arch_response = get_mtgtop8_archetypes(fmt_code)

    if arch_response.get("error"):
        st.error(f"Impossible de récupérer les données : {arch_response.get('msg')}")
    else:
        archetypes = arch_response.get("data", {})
        arch_list = ["🌟 Aperçu du Format (Nouvelles Cartes)"] + list(archetypes.keys())
        deck_choisi = st.selectbox("Sélectionnez une vue :", arch_list)
        
        if st.button(f"🚀 Scanner : {deck_choisi}"):
            if deck_choisi == "🌟 Aperçu du Format (Nouvelles Cartes)":
                progress_bar = st.progress(0)
                status_text = st.empty()
                format_new_cards = {}
                
                top_archs_names = list(archetypes.keys())[:5]
                for i, arch_name in enumerate(top_archs_names):
                    status_text.text(f"Analyse de l'archétype : {arch_name}...")
                    deck_ids = get_all_deck_ids(archetypes[arch_name])
                    aggregated_cards = fetch_aggregated_decks(deck_ids[:5])
                    
                    for c in aggregated_cards:
                        name, pct = c["name"], c["inclusion"]
                        if name not in format_new_cards: format_new_cards[name] = {"max_pct": pct, "archetypes": set()}
                        format_new_cards[name]["max_pct"] = max(format_new_cards[name]["max_pct"], pct)
                        if pct > 0: format_new_cards[name]["archetypes"].add(arch_name)
                            
                    progress_bar.progress((i + 1) / len(top_archs_names))
                    
                status_text.text("Enrichissement avec Scryfall...")
                scryfall_data = fetch_scryfall_batch(list(format_new_cards.keys()))
                update_price_history(scryfall_data)
                
                format_cards = []
                for c in scryfall_data:
                    full_name = c.get("name")
                    front_name = full_name.split(" // ")[0].strip()
                    statut_nom, tri_val = determine_card_status(c, RECENT_SETS)
                    
                    if tri_val == 1:
                        stats = format_new_cards.get(front_name) or format_new_cards.get(full_name) or {}
                        format_cards.append({
                            "Tri_Statut": tri_val,
                            "Image": extract_card_image(c),
                            "Nom de la Carte": full_name,
                            "Édition": get_set_and_year(c),
                            "Archétype(s)": ", ".join(list(stats.get("archetypes", []))),
                            "Inclusion Max": stats.get("max_pct", 0.0),
                            "Prix €": float(c.get("prices", {}).get("eur") or 0.0),
                        })
                        
                progress_bar.empty()
                status_text.empty()
                st.session_state['format_df'] = pd.DataFrame(format_cards)
                st.session_state['format_deck_view'] = deck_choisi
                
            else:
                with st.spinner(f"Extraction de TOUS les decks pour {deck_choisi}..."):
                    deck_ids = get_all_deck_ids(archetypes[deck_choisi])
                    if deck_ids:
                        st.info(f"✅ L'algorithme croise actuellement **{len(deck_ids)} decks de tournois** en temps réel.")
                        deck_cards = fetch_aggregated_decks(deck_ids)
                        
                        if deck_cards:
                            with st.spinner("Analyse financière avec Scryfall..."):
                                scryfall_data = fetch_scryfall_batch([c["name"] for c in deck_cards])
                                update_price_history(scryfall_data)
                                
                                incl_map = {c["name"]: c["inclusion"] for c in deck_cards}
                                table_data = []
                                for c in scryfall_data:
                                    full_name = c.get("name")
                                    front_name = full_name.split(" // ")[0].strip()
                                    statut_nom, tri_val = determine_card_status(c, RECENT_SETS)
                                    table_data.append({
                                        "Tri_Statut": tri_val, 
                                        "Inclusion": incl_map.get(front_name) or incl_map.get(full_name, 0.0),
                                        "Image": extract_card_image(c),
                                        "Nom de la Carte": full_name,
                                        "Édition": get_set_and_year(c),
                                        "Prix €": float(c.get("prices", {}).get("eur") or 0.0),
                                    })
                                    
                                st.session_state['format_df'] = pd.DataFrame(table_data)
                                st.session_state['format_deck_view'] = deck_choisi

        # Affichage avec panneau de filtres (si on a scanné ce deck)
        if 'format_df' in st.session_state and st.session_state.get('format_deck_view') == deck_choisi:
            display_interactive_dataframe(st.session_state['format_df'])