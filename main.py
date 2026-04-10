import streamlit as st
import time
import gspread
import pandas as pd
import difflib

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

        # För AI svar sparar vi extra kolumner
        if data.get("type") == "ai":
            ws.append_row([
                data.get("created_at"),
                data.get("type"),
                data.get("participant_id", st.session_state.get("user_title", "Vårdpersonal")),
                data.get("scenario"),
                data.get("category", ""),
                data.get("text", ""),
                data.get("keywords", ""),
                data.get("time_seconds", 0),
                data.get("original_text", ""),
                data.get("diff_text", "")
        ])
        else:
            # Vanlig manual eller Summary
            ws.append_row([
                data.get("created_at"),
                data.get("type"),
                data.get("participant_id", st.session_state.get("user_title", "Vårdpersonal")),
                data.get("scenario"),
                data.get("category", ""),
                data.get("text", ""),
                data.get("keywords", ""),
                data.get("time_seconds", 0),
                "",
                ""
        ])
                        
    except Exception as e:
        print(f"[SHEETS ERROR] {e}")

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
    return True
        
    # Kontroll – minst två av strukturerna ska finnas
    # text_lower = text.lower()
    # required = ["observation", "insats", "åtgärd", "effekt"]
    # matches = sum(1 for word in required if word in text_lower)
    # has_structure = matches >= 2

    # return has_structure

# --- Kolla till denna bit imorgon varför utskriften är konstiga ---
def query_groq(keywords: str, category: str , scenario_text: str, 
               event_datetime: str = None) -> str:
    
    # Regel 1: Struktur (hard constraint via prompt)
    """Genererar professionell journalanteckning med Groq"""
    
    system_prompt ="""
    Du är ett dokumentationsstöd för äldreomsorg. Din enda uppgift är att formulera 
    korrekta journalanteckningar baserat på den information du får.

    HÅRDA REGLER - bryt aldrig dessa:
    1. Skriv ENDAST på svenska.
    2. Använd IBIC struktur med tydliga rubriker:
        - Observation: Vad observerades objektivt (fakta inte tolkningar)
        - Insats/Åtgärd: Vad personalen konkret utförde
        - Effekt: Mätbart eller observerbart resultat - om ingen effekt angetts, utelämna hela avsnittet helt och skriv ingenting
    3. Skriv i tredje person om personalen ("Personalen", "Undersköterskan").
    4. Var strikt objektiv. Inga värderingar, känslor eller spekulationer.
        Förbjudna ord: tyvärr, lyckligtvis, verkade, kanske, troligen, antar, verkar
    5. Hitta INTE på information som inte finns i nyckelorden. Om något saknas, utelämna det.
    6. Max 5 meningar totalt.
    7. Inga avslutande rekommendationer eller förslag till åtgärder.

    Exempel på fel vs rätt:
    Fel - "Brukaren verkade nöjd och mådde troligen bra efter måltiden"
    Rätt - "Brukaren uppgav att hen mådde bra efter måltiden"

    Fel - "Det kan vara bra att följa upp blodtrycket framöver"
    Rätt - (Ingen mening alls - rekommendationer utelämnas)
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
            model = "meta-llama/llama-4-scout-17b-16e-instruct", # Bäst i test efter att ha testat andra
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3,
            max_tokens=300,
            top_p=0.9
        )
        text = response.choices[0].message.content.strip()
        return text if validate_output(text) else None
    except Exception as e:
        return None
# ------------------------------------------------------------------------------------------    
    
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
    # Standardvärde
    st.session_state.user_title = ""

# --- Säkerställ att det alltid finns ett värde ---
if not st.session_state.user_title or st.session_state.user_title.strip() == "":
    st.session_state.user_title = ""

# --- Progress bar funktion ---
def show_progress_bar(is_ai=False):
    prefix = "ai_" if is_ai else ""
    current = st.session_state.get(f"{prefix}scenario", 1)
    total = len(scenarios)

    progress = current / total

    st.progress(progress, text=f"Scenario {current} av {total} - {get_scenario_title(current)}")

def compute_diff(original: str, final: str) -> str:
    if original.strip() == final.strip():
        return "Inga ändringar"
    diff = difflib.unified_diff(
        original.splitlines(),
        final.splitlines(),
        lineterm="",
        n=0
    )
    return "\n".join(list(diff)[2:])

# ---Scenarion --- kolla till om de kan förbättras för de känns dåliga på något sätt
scenarios = [
"""Scenario 1

Du har precis hjälpt Britta, 84 år, med hennes morgonrutiner.
Hon tvättade sig själv till stor del men behövde hjälp med påklädning.
Hon åt en hel frukost och var i gott humör. Dokumentera insatsen.
""",

"""Scenario 2

Du är mitt i ett hektiskt eftermiddagspass. Du har precis hjälpt Erik, 79 år, med sin
kvällsmedicin och noterat att han verkar mer trött än vanligt. Du har bara några minuter,
skriv en kort dokumentation med de viktigaste punkterna.
""",

"""Scenario 3

Gunnel, 91 år, vägrade idag ta sin ordinerade blodtrycksmedicin och blev
upprörd när du försökte hjälpa henne. Detta är tredje gången i veckan.
Dokumentera avvikelsen noggrant och sakligt. Anteckningen kan komma att granskas.
"""
]
#----------------------------------------------------------------------------------------

def get_scenario_title(scenario_number: int) -> str:
    """Returnerar en kort och tydlig titel för varje scenario"""
    titles = {
        1: "Rutindokumentation efter vårdinsats",
        2: "Dokumentation under ett arbetspass",
        3: "Dokumentation av avvikelse"
    }
    return titles.get(scenario_number, f"Scenario {scenario_number}")

# --- Admin vy ---
with st.sidebar:
    admin_password = st.text_input("Admin", type="password", label_visibility="collapsed")

    if admin_password == st.secrets["ADMIN_PASSWORD"]:
        st.title("Admin - Ändringslogg")

        try:
            ws = get_worksheet()
            all_values = ws.get_all_values()

            if not  all_values or len(all_values) < 2:
                st.info("Ingen data sparad ännu.")
            else:
                headers = all_values[0]
                data_rows = all_values[1:]

                # Skapa Dataframe
                df = pd.DataFrame(data_rows, columns=headers)

                # Rensa eventuella tomma kolumner
                df = df.loc[:, df.columns.notna() & (df.columns != "")]

                st.success(f"Totalt {len(df)} rader hämtade")

                # Filter
                typ_filter = st.selectbox("Visa typ", ["Alla", "manual", "ai", "Summary"])
                if typ_filter != "Alla":
                    df_filtered = df[df.iloc[:,1] == typ_filter]
                else:
                    df_filtered = df

                # Visa tabell
                st.dataframe(
                    df,
                    use_container_width=True,
                    hide_index=True
                )

                # Separata sektioner för AI-svar
                st.divider()
                st.subheader("AI-genererade texter (med redigering)")

                ai_df = df[df.iloc[:,1] == "ai"].copy() if not df.empty else pd.DataFrame()

                if not ai_df.empty:
                    for idx, row in ai_df.iterrows():
                        scenario = row.iloc[3] if len(row) > 3 else ""
                        participant = row.iloc[2] if len(row) > 2 else ""
                        final_text = row.iloc[5] if len(row) > 5 else ""      # Kolumn för "text"
                        original = row.iloc[8] if len(row) > 8 else ""         # Kolumn för "original_text"
                        diff = row.iloc[9] if len(row) > 9 else ""             # Kolumn för "diff_text"
                        category = row.iloc[4] if len(row) > 4 else ""
                        time_sec = row.iloc[7] if len(row) > 7 else 0
                        
                        with st.expander(f"Scenario {scenario} — {participant}"):
                            st.caption(f"**Kategori:** {category}")
                            
                            col1, col2 = st.columns(2)
                            with col1:
                                st.markdown("**Original AI-förslag:**")
                                st.text_area("Original", value=original, height=140, disabled=True, key=f"orig_{idx}")
                            with col2:
                                st.markdown("**Slutlig text efter redigering:**")
                                st.text_area("Redigerad", value=final_text, height=140, disabled=True, key=f"edit_{idx}")
                            
                            if original.strip() != final_text.strip() and original.strip() != "":
                                st.warning("**Testaren har ändrat texten**")
                                st.markdown("**Skillnad:**")
                                st.text_area("Vad som ändrades", value=diff, height=100, disabled=True)
                            else:
                                st.success("Inga ändringar – testaren godkände AI-förslaget direkt")
                            
                            st.caption(f"Tid använd: {time_sec} sekunder")
                else:
                    st.info("Inga AI-svar har sparats ännu.")
                
                # Sus sammanfattning
                st.divider()
                summary_df = df[df.iloc[:,1] == "Summary"]
                if not summary_df.empty:
                    st.subheader("SUS resultat och tid")
                    st.dataframe(summary_df[["participant_id", "text", "keywords"]], use_container_width=True)

        except Exception as e:
            st.error(f"Kunde inte hämta data från Google Sheets: {e}")


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

    # Datum och tid
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
    "Kategori/Rubrik",
    ["Observationer", "Utförda insatser", "Avvikelser eller problem", "Kommunikation"],
    key=f"man_cat_{current_scenario}")
    
    text = st.text_area("Beskrivning av händelse + åtgärd",
                        placeholder="Beskriv vad som hände och vilka åtgärder som vidtogs...", 
                        key=f"manual_text_{current_scenario}",
                        height=200)

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

    # Datum och tid
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
        height=100
    )

    effekt = st.text_area(
        "Effekt / Resultat (valfritt)",
        placeholder="t.ex. Brukaren åt upp halva portionen och verkade nöjd efteråt...",
        key=f"ai_effekt_{current_scenario}",
        height=100
    )
    
    # 2. Generera och räknare för regenerering (säkerställer ny widget-nyckel)
    if f"regen_count_{current_scenario}" not in st.session_state:
        st.session_state[f"regen_count_{current_scenario}"] = 0

    if st.button("Generera dokumentationstext", type="primary", use_container_width=True):
        if not observation.strip():
            st.warning("Fyll i observation/nyckelord först")
        else:
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

        regen_count = st.session_state.get(f"regen_count_{current_scenario}", 0)
        edited = st.text_area(
            "AI-förslag - redigera vid behov",
            value = st.session_state.get(f"ai_result_{current_scenario}", ""),
            key=f"ai_edit_{current_scenario}_{regen_count}",
            height=180
        )
        
        # Uppdatera vid ändring
        st.session_state[f"ai_result_{current_scenario}"] = edited

        if not edited.strip():
            st.warning("Texten är raderad. Fyll i nyckelorden ovan och generera en ny text")
            st.session_state[f"ai_show_{current_scenario}"] = False
        else:
            if st.button("Generera ny text", key=f"regenerate_{current_scenario}"):
                event_datetime_str = datetime.combine(event_date, event_time).strftime("%Y-%m-%d %H:%M")
                with st.spinner("Genererar nytt förslag..."):
                    generated = query_groq(
                        keywords=f"Observation: {observation}\nÅtgärd: {åtgärd}\nEffekt: {effekt}",
                        category=category,
                        scenario_text=scenarios[current_scenario - 1],
                        event_datetime=event_datetime_str
                    )
                if generated:
                    st.session_state[f"ai_result_{current_scenario}"] = generated
                    st.session_state[f"ai_result_{current_scenario}_original"] = generated
                    st.session_state[f"regen_count_{current_scenario}"] += 1
                    st.rerun()
                else:
                    st.error("Texten uppfyllde inte reglerna. Försök igen.")

    #Knapp för nästa
    if st.button("Godkänn och nästa scenario", type="primary", use_container_width=True):
        final_text = st.session_state.get(f"ai_result_{current_scenario}", "").strip()
        if not final_text:
            st.warning("Generera och/eller redigera texten först")
        else:
            original = st.session_state.get(f"ai_result_{current_scenario}_original", "")
            was_edited = original.strip() != final_text #  <- Jämför

            # Beräkna vad som ändrades
            if was_edited:
                diff_text = compute_diff(original, final_text)
            else:
                diff_text = "Inga ändringar"
            
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
                "time_seconds": time_spent,
                "original_text": original,
                "diff_text": diff_text 
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
        st.balloons()
        st.markdown("### Tack för ditt deltagande!")
        st.markdown("Dina svar har sparats. Du kan nu stänga denna sida.")
