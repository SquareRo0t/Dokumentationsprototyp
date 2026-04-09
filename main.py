import streamlit as st
import time
import gspread

from datetime import datetime
from groq import Groq
from google.oauth2.service_account import Credentials


# --- Google Sheets ---
def get_worksheet():
    """ Anslut till Google Sheets - fungerar både lokalt och i Streamlit Cloud"""
    try:
        # För lokal testning (om du har service.account.json i samma mapp)
        credentials = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
        )
        client = gspread.authorize(credentials)
        sheet = client.open("Dokumentationsprototyp - Svar")
        worksheet = sheet.worksheet("Sheet1")
        return worksheet
    except FileNotFoundError as e:
        st.error(f"Kan inte ansluta till Google Sheets: {e}")
        st.stop()
    except Exception as e:
        st.error(f"Kan inte ansluta till Google Sheets: {e}")
        st.stop()

def save_to_sheets(data: dict):
    """Spara data till Google Sheets"""
    try:
        ws = get_worksheet()
        ws.append_row([
            data.get("created_at"),
            data.get("type"),
            data.get("participant_id", st.session_state.get("user_title", "Vårdpersonal")),
            data.get("scenario"),
            data.get("category", ""),
            data.get("text", ""),
            data.get("keywords", ""),
            data.get("time_seconds", 0),
            ""
        ])
    except Exception as e:
        pass # Tyst felhantering för användarupplevelsen


# --- Groq ---
# Säker Groq API-nyckel hantering
if "GROQ_API_KEY" in st.secrets:
    GROQ_API_KEY = st.secrets["GROQ_API_KEY"]
else:
    GROQ_API_KEY = st.text_input("Ange din Groq API-nyckel:", type="password")
    if not GROQ_API_KEY:
        st.warning("Väntar på Groq API-nyckel...")
        st.stop()

client = Groq(api_key=GROQ_API_KEY)

def validate_output(text):
    forbidden_words = ["kanske", "troligen", "verkar", "antar", "tyvärr", "lyckligvis"] 
    for word in forbidden_words:
        if word in text.lower():
            return False
        
    # Kontroll – minst två av strukturerna ska finnas
    text_lower = text.lower()
    required = ["observation", "insats", "åtgärd", "effekt"]
    matches = sum(1 for word in required if word in text_lower)
    has_structure = matches >= 2

    return has_structure

def query_groq(keywords: str, category: str , scenario_text: str, 
               event_datetime: str = None) -> str:
    # Regel 1: Struktur (hard constraint via prompt)

    """Genererar professionell journalanteckning med Groq"""
    
    system_prompt ="""
    Du är en erfaren vårdpersonal inom äldreomsorg med många års erfarenhet av dokumentation.

    FÖLJ ALLTID dess hårda regler när du skriver journalanteckningar:
    1. Skriv **endast på svenska**.
    2. Var **objektiv och faktabaserad** - inga värderingar, antaganden eller spekulationer (undvik ord som "tyvärr", "lyckligtvis", "verkade").
    3. Använd tydlig **IBIC-struktur**: 
    - Observation: Beskriv fakta om vad som observerades.
    - Insats/Åtgärd: Beskriv exakt vad som utfördes av personalen.
    - Effekt: Beskriv resultatet av insatsen (om det är relevant). 
    4. Var **koncis** - max 4-6 meningar.
    5. Använd korrekt vårdterminologi men håll språket lättläst.
    6. Börja alltid med datum och tid för händelsen.
    7. Avsluta aldrig med rekommendationer om det inte är en "Utförda insatser"-kategori.

    Skriv alltid i **professionell, neutral ton**.
    """

    user_prompt = f"""
    Scenario: {scenario_text}
    Kategori: {category}
    Datum och tid för händelsen: {event_datetime}
    Nyckelord/observationer {keywords}

    Skriv en korrekt journalanteckning enligt reglerna ovan.
    """
    try:
        response = client.chat.completions.create(
            model = "llama-3.3-70b-versatile", # Bra och snabb (Gratis version räcker)
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3,
            max_tokens=350,
            top_p=0.95
        )
        text = response.choices[0].message.content.strip()
        return text if validate_output(text) else None
    except Exception as e:
        return None
    
    
# --- Session state --- 
def init_session_state():
    defaults = {
            # Manuellt
            "started": False, 
            "start_time": None, 
            "scenario": 1, 
            "finished": False,
            "manual_answers": {}, 
            "scenario_start_times": {}, 
            "scenario_times": {},
            
            # AI
            "ai_started": False, 
            "ai_start_time": None, 
            "ai_finished": False,
            "ai_scenario": 1, 
            "ai_answers": {}, 
            "ai_scenario_start_times": {},
            "ai_scenario_times": {}, 
            "participant_id": None
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

init_session_state()


# --- Anonym signatur ---
if "user_title" not in st.session_state:
    st.session_state.user_title = "" # Standardvärde

# Säkerställ att det alltid finns ett värde
if not st.session_state.user_title or st.session_state.user_title.strip() == "":
    st.session_state.user_title = ""

# Progress bar
def show_progress_bar(is_ai=False):
    prefix = "ai_" if is_ai else ""
    current = st.session_state.get(f"{prefix}scenario", 1)
    total = len(scenarios)

    progress = current / total

    st.progress(progress, text=f"Scenario {current} av {total} - {get_scenario_title(current)}")


# ---Scenarion --- Fixa till så att de blir bättre
scenarios = [
"""Scenario 1 - Lätt:

Du har precis hjälpt Britta, 84 år, med hennes morgonrutiner.
Hon tvättade sig själv till stor del men behövde hjälp med påklädning.
Hon åt en hel frukost och var i gott humör. Dokumentera insatsen.
""",

"""Scenario 2 - Medel:

Du är mitt i ett hektiskt eftermiddagspass. Du har precis hjälpt Erik, 79 år, med sin
kvällsmedicin och noterat att han verkar mer trött än vanligt. Du har bara några minuter,
skriv en kort dokumentation med de viktigaste punkterna.
""",

"""Scenario 3 - Svårast:

Gunnel, 91 år, vägrade idag ta sin ordinerade blodtrycksmedicin och blev
upprörd när du försökte hjälpa henne. Detta är tredje gången i veckan.
Dokumentera avvikelsen noggrant och sakligt. Anteckningen kan komma att granskas.
"""
]

def get_scenario_title(scenario_number: int) -> str:
    """Returnerar en kort och tydlig titel för varje scenario"""
    titles = {
        1: "Lätt: Rutindokumentation efter vårdinsats",
        2: "Medel: Dokumentation under ett arbetspass",
        3: "Svår: Dokumentation av avvikelse"
    }
    return titles.get(scenario_number, f"Scenario {scenario_number}")

# === ADMIN VY - Robust version ===
with st.sidebar:
    admin_password = st.text_input("Admin-lösenord", type="password", label_visibility="collapsed")
    
    if admin_password == st.secrets.get("ADMIN_PASSWORD", ""):
        st.title("🔧 Admin - Ändringslogg")
        
        try:
            ws = get_worksheet()
            all_values = ws.get_all_values()
            
            if len(all_values) <= 1:
                st.info("Ingen data i arket ännu.")
                st.stop()

            headers = [h.strip().lower() for h in all_values[0]]   # Gör headers case-insensitive
            data_rows = all_values[1:]

            # Skapa dictionarys
            all_data = []
            for row in data_rows:
                row_padded = row + [""] * (len(headers) - len(row))
                record = {headers[i]: str(row_padded[i]).strip() for i in range(len(headers))}
                all_data.append(record)

            # Filtrera (nu case-insensitive)
            ai_rows = [row for row in all_data if row.get("type", "").lower() == "ai"]
            manual_rows = [row for row in all_data if row.get("type", "").lower() == "manual"]
            summary_rows = [row for row in all_data if row.get("type", "").lower() in ("summary", "sus")]

            st.markdown("### Sammanfattning")
            col1, col2, col3 = st.columns(3)
            col1.metric("Deltagare", len(summary_rows))
            col2.metric("Manuella svar", len(manual_rows))
            col3.metric("AI-svar", len(ai_rows))

            edited = sum(1 for row in ai_rows if "redigerad: true" in row.get("keywords", "").lower())
            st.metric("Redigerade AI-texter", f"{edited} av {len(ai_rows)}")

            st.divider()
            st.markdown("### Per deltagare")

            # Hitta unika deltagare
            participants = sorted({row.get("participant_id", "") for row in summary_rows if row.get("participant_id")})

            if not participants:
                st.info("Inga SUMMARY-rader hittades ännu.")
            else:
                for p in participants:
                    with st.expander(f"👤 {p}"):
                        p_ai = [r for r in ai_rows if r.get("participant_id", "") == p]
                        for row in p_ai:
                            scen = row.get("scenario", "?")
                            st.markdown(f"**Scenario {scen}**")
                            st.caption(f"Kategori: {row.get('category', '')}")
                            st.text_area("Journalanteckning", value=row.get("text", ""), height=100, disabled=True)
                            
                            if "redigerad: true" in row.get("keywords", "").lower():
                                st.warning("Texten har redigerats av användaren")
                            else:
                                st.success("AI-texten är oförändrad")
                            st.divider()

        except Exception as e:
            st.error("Kunde inte läsa Google Sheets")
            st.exception(e)


# --- Startskärm ---
if not st.session_state.started:
    st.title("AI-baserat dokumentationsstöd för äldreomsorg (Prototyp)")

    st.warning("""
    **Detta är en forskningsprototyp.**
    Den representerar inte ett färdigt journalsystem och saknar många funktioner som finns
    i verkliga system. Syftet är att utvärdera AI-assisterad dokumentation.
    """)

    st.info("""
    **Bästa upplevelsen får du på en dator eller surfplatta.**
    Denna prototyp fungerar på mobil, men är betydligt smidigare att använda på
    större skärmar
    
    **När du går vidare:**
    Det tar några sekunder att spara ditt svar och ladda nästa scenario.
    Vänligen vänta tills nästa visas.
    """)

    st.info("""
    **Viktig information innan du börjar:**
    - Denna studie är anonym. Inga personuppgifter samlas in.
    - Din yrkesroll används enbart för att särskilja svar.
    - Data används enbart i examensarbetet.
    - Du kan avbryta när som helst.
    """)

    consent = st.checkbox("Jag förstår och godkänner ovanstående")

    st.subheader("Instruktion")
    st.markdown("""
    Du kommer få **tre olika scenarier** som du ska dokumentera på två sätt:                
    1. **Manuellt** - Liknande som i journalsystemet idag
    2. **Med AI-hjälp** - efteråt
    """)

    # Endast yrkesroll
    user_title = st.text_input(
        "**Din yrkesroll / titel**",
        value=st.session_state.get("user_title", "Undersköterska"),
        placeholder="t.ex. Undersköterska, Sjuksköterska, Vårdbiträde",
        help="Endast din yrkesroll används för signering. Appen är anonym."
    )
    
    if st.button("Starta uppgift", type="primary"):
        if not consent:
            st.error("Du måste godkänna villkoren för att fortsätta.")
        else:
            title = user_title.strip() if user_title else ""
            if not title:
                st.error("Du måste ange din yrkesroll innan du börjar.")
            else:
                st.session_state.user_title = title
                st.session_state.started = True
                st.session_state.start_time = time.time()
                st.rerun()
    st.stop()


# --- Gemensam timer-logik (osynlig) ---
def get_current_scenario_time(is_ai=False):
    prefix = "ai_" if is_ai else ""
    key = f"{prefix}scenario_start_times"
    scenario = st.session_state[f"{prefix}scenario"]

    if scenario not in st.session_state[key]:
        st.session_state[key][scenario] = time.time()
    
    elapsed = int(time.time() - st.session_state[key][scenario])
    return elapsed, scenario


# --- Manuella delen ---
if st.session_state.started and not st.session_state.finished and not st.session_state.ai_started:
    elapsed, current_scenario = get_current_scenario_time(is_ai=False)

    st.subheader(f"Scenario {current_scenario} - {get_scenario_title(current_scenario)}")
    show_progress_bar(is_ai=False)
    st.markdown(scenarios[current_scenario - 1])

    st.subheader("Händelsedatum och tid")

    # Datum och tid - visas först
    col1, col2 = st.columns(2)
    with col1:
        event_date = st.date_input(
            "Datum för händelsen",
            value=datetime.now().date(),
            key=f"manual_date_{current_scenario}"
        )
    with col2:
        event_time = st.time_input(
            "Tid för händelsen",
            value=datetime.now().time(),
            key=f"manual_time_{current_scenario}"
        )

    st.subheader("Manuell dokumentation")

    st.caption("Välj den kategori som bäst beskriver händelsen.")

    cat = st.selectbox(
    "Kategori / Rubrik",
    ["Observationer", "Utförda insatser", "Avvikelser eller problem", "Kommunikation"],
    key=f"man_cat_{current_scenario}")
    
    text = st.text_area("Beskrivning av händelse + åtgärd",
                        placeholder="Beskriv vad som hände och vilka åtgärder som vidtogs...", 
                        key=f"manual_text_{current_scenario}",
                        height=150)

    if st.button("Nästa scenario", type="primary"):
        if not text.strip(): # TEST
            st.warning("Skriv något i textfältet innan du går vidare.")
        else:
            event_datetime = datetime.combine(event_date, event_time)
            event_datetime_str = event_datetime.strftime("%Y-%m-%d %H:%M")

            time_spent = elapsed
            # Spara svar
            st.session_state.manual_answers[current_scenario] = {
            "category": cat, 
            "text": text.strip(),
            "event_datetime": event_datetime_str
            }
            st.session_state.scenario_times[current_scenario] = time_spent

           # Spara till Google Sheets
            save_to_sheets({
                "created_at": datetime.now().isoformat(),
                "type": "manual",
                "participant_id": st.session_state.get("user_title", "Vårdpersonal"),
                "scenario": current_scenario,
                "category": cat,
                "text": f"{event_datetime_str} - {text.strip()}",
                "keywords": "",
                "time_seconds": time_spent
            })

            if current_scenario < len(scenarios):
                st.session_state.scenario +=1
            else:
                st.session_state.finished = True
                st.session_state.end_time = time.time()
            st.rerun()


# --- Övergång till AI ---
if st.session_state.finished and not st.session_state.ai_started:
    st.success("Manuell del klar!")
    st.markdown("""
    ### Nu börjar den AI-assisterade delen
    Du får samma tre scenarion igen, men nu hjälper AI dig att formulera journalanteckningen.
                
    **Så här fungerar det:**
    1. Fyll i korta nyckelord om vad som observerades
    2. Klicka "Generera" för att få ett AI-förslag
    3. Granska och redigera texten vid behov
    4. Godkänn och gå vidare
    """)

    if st.button("Starta AI-assisterad del", type="primary"):
        st.session_state.ai_started = True
        st.session_state.ai_start_time = time.time()
        st.session_state.ai_scenario = 1
        st.rerun()
    st.stop()
    

# --- AI-assisterad del ---
if st.session_state.ai_started and not st.session_state.ai_finished:
    elapsed, current_scenario = get_current_scenario_time(is_ai=True)

    # Visa de olika scenario
    st.subheader(f"Scenario {current_scenario} - {get_scenario_title(current_scenario)}")
    show_progress_bar(is_ai=True)
    st.markdown(scenarios[current_scenario - 1])

    st.divider()

    # 1. Strukturerad information
    st.subheader("Händelsedatum och tid")

    # Datum och tid - visas först
    col1, col2 = st.columns(2)
    with col1:
        event_date = st.date_input(
            "Datum för händelsen",
            value=datetime.now().date(),
            key=f"ai_date_{current_scenario}"
        )
    with col2:
        event_time = st.time_input(
            "Tid för händelsen",
            value=datetime.now().time(),
            key=f"ai_time_{current_scenario}"
        )

    st.subheader("AI-assisterad dokumentation")

    scenario_categories = {
        1: "Utförda insatser",
        2: "Utförda insatser",
        3: "Avvikelser eller problem"
    }

    category = st.selectbox(
        "Kategori", 
        ["Utförda insatser", "Avvikelser eller problem", "Kommunikation med anhörig/annan personal"], 
        index=["Utförda insatser", "Avvikelser eller problem",
               "Kommunikation med anhörig/annan personal"]
               .index(scenario_categories[current_scenario]),
        key=f"ai_cat_{current_scenario}"
    )
    
    observation = st.text_area(
        "Observation",
        placeholder="t.ex. Brukaren fick hjälp med lunch. Brukaren åt ungefär halva portionen",
        key=f"ai_obs_{current_scenario}",
        height=100
    )

    åtgärd = st.text_area(
        "Åtgärd / Insats",
        placeholder="Hjälpte till att skära maten, uppmuntrade att äta, serverade extra dryck...",
        key=f"ai_åtgärd_{current_scenario}",
        height=80
    )

    effekt = st.text_area(
        "Effekt / Resultat (valfritt)",
        placeholder="t.ex. Brukaren åt upp halva portionen och verkade nöjd efteråt...",
        key=f"ai_effekt_{current_scenario}",
        height=70
    )

    # 2. Generera
    if st.button("Generera dokumentationstext", type="primary", use_container_width=True):
        if not observation.strip():
            st.warning("Fyll i observation/nyckelord först")
        else:
            # Lägg till datum/tid i början av genererad text
            #event_datetime_obj = datetime.combine(event_date, event_time)
            event_datetime_str = datetime.combine(event_date, event_time).strftime("%Y-%m-%d %H:%M")
            # LLM
            with st.spinner("Genererar journalanteckning med Groq..."):
                generated = query_groq(
                    keywords=f"Observation: {observation}\nÅtgärd: {åtgärd}\nEffekt: {effekt}", 
                    category=category, 
                    scenario_text=scenarios[current_scenario - 1],
                    event_datetime=event_datetime_str
                )
            if generated:
                st.session_state[f"ai_result_{current_scenario}"] = generated
                st.session_state[f"ai_result_{current_scenario}_original"] = generated
                st.session_state[f"ai_show_{current_scenario}"] = True
            else:
                st.error("Texten uppfyllde inte reglerna. Försök igen.")
    
    # 3. Journalanteckning (visas efter generering)
    if st.session_state.get(f"ai_show_{current_scenario}", False):
        st.divider()
        st.subheader("2. Journalanteckning")

        edited = st.text_area(
            "AI-förslag - redigera vid behov",
            value = st.session_state.get(f"ai_result_{current_scenario}", ""),
            key=f"ai_edit_{current_scenario}",
            height=180
        )
        
        # Uppdatera vid ändring
        st.session_state[f"ai_result_{current_scenario}"] = edited

    #Knapp för nästa
    if st.button("Godkänn och nästa scenario", type="primary", use_container_width=True):
        final_text = st.session_state.get(f"ai_result_{current_scenario}", "").strip()
        if not final_text:
            st.warning("Generera och/eller redigera texten först")
        else:
            original = st.session_state.get(f"ai_result_{current_scenario}_original", "")
            was_edited = original.strip() != final_text #  <- Jämför
            
            time_spent = elapsed

            st.session_state.ai_answers[current_scenario] = final_text
            st.session_state.ai_scenario_times[current_scenario] = elapsed

            # Spara till Google Sheets
            save_to_sheets({
                "created_at": datetime.now().isoformat(),
                "type": "ai",
                "participant_id": st.session_state.get("user_title", "Vårdpersonal"),
                "scenario": current_scenario,
                "category": category,
                "text": final_text,
                "keywords": f"Obs: {observation} | Åtgärd: {åtgärd} | Effekt: {effekt} | Redigerad: {was_edited}" ,
                "time_seconds": time_spent
            })

            if current_scenario < len(scenarios):
                st.session_state.ai_scenario +=1
            else:
                st.session_state.ai_finished = True
                st.session_state.ai_end_time = time.time()
            st.rerun()


# --- Slutresultat och SUS ---
if st.session_state.ai_finished:
    total_ai = int(st.session_state.ai_end_time - st.session_state.ai_start_time)
    total_manual = int(st.session_state.end_time - st.session_state.start_time)

    st.title("Tack för ditt deltagande!")
    st.markdown("Du är nu klar med uppgiften.")

    st.markdown("---")
    st.subheader("Hur upplevde du AI-assisterad dokumentation?")

    # --- SUS enkät ---
    st.markdown("### System Usability Scale (SUS)")
    st.caption("Svara på följande 10 påståenden utifrån hur du upplevde **AI-assisterande delen**")

    sus_questions = [
        "Jag tycker att jag skulle vilja använda detta system ofta.",
        "Jag tyckte att systemet var 'onödigt komplex'.",
        "Jag tyckte att systemet var lätt att använda.",
        "Jag tror att jag skulle behöva stöd från en teknisk person för att kunna använda detta system.",
        "Jag tyckte att de olika funktionerna i detta system var väl integrerade.",
        "Jag tyckte att det var för mycket inkonsekvens i detta system.",
        "Jag skulle föreställa mig att de flesta människor skulle lära sig att använda detta system mycket snabbt.",
        "Jag tyckte att systemet var mycket besvärligt att använda.",
        "Jag kände mig mycket självsäker när jag använde systemet.",
        "Jag behövde lära mig en hel del innan jag kunde komma igång med detta system."
    ]

    sus_scores =[]
    for i, question in enumerate(sus_questions):
        score = st.slider(
            f"{i+1}. {question}",
            min_value=1, max_value=5, value=3,
            help="1 = Håller inte med alls, 5 = Håller med helt",
            key=f"sus_{i}"
        )
        sus_scores.append(score)
    
    if st.button("Skicka in svar och avsluta", type="primary"):
        # Beräkna SUS-poäng (standardformel)
        sus_final = 0
        for i, score in enumerate(sus_scores):
            if i % 2 == 0: # Udda frågor 
                sus_final += (score - 1)
            else:          # Jämna frågor
                sus_final += (5 - score)
        sus_score = sus_final * 2.5 # SUS-poäng mellan 0-100

        # Spara till Google Sheets
        save_to_sheets({
            "created_at": datetime.now().isoformat(),
            "type": "SUMMARY",
            "participant_id": st.session_state.get("user_title", "Vårdpersonal"),
            "scenario": "TOTAL",
            "category": "",
            "text": f"Manuell: {total_manual}s | AI: {total_ai}s | Skillnad: {total_manual-total_ai}s",
            "keywords": f"SUS: {sus_score}",
            "time_seconds": total_manual
        })

        try:
            ws = get_worksheet()
            ws.append_row([
                datetime.now().isoformat(),
                "SUS",
                "Testdeltagare",
                "Sammanfattning",
                total_manual,
                total_ai,
                total_manual - total_ai,
                sus_score,
                sus_scores[0], sus_scores[1], sus_scores[2], sus_scores[3], sus_scores[4],
                sus_scores[5], sus_scores[6], sus_scores[7], sus_scores[8], sus_scores[9]
            ])
            st.balloons()
        except Exception as e:
            st.error(f"Fel vid sparning av SUS: {e}")
        
        st.markdown("### Tack för ditt deltagande!")
        st.markdown("Dina svar har sparats. Du kan nu stänga denna sida.")
