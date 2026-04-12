import streamlit as st
import time
import gspread
import pandas as pd
import difflib

from datetime import datetime
from groq import Groq
from google.oauth2.service_account import Credentials


# --- GOOGLE SHEETS - KOPPLING OCH DATASPARING ---
def get_worksheet():
    """ Anslut till Google Sheets - fungerar både lokalt och i Streamlit Cloud"""
    try:
        # Hämta autentiseringsuppgifter från Streamlit Secrets
        credentials = Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive"
            ]
        )

        # Auktorisera klienten och öppna det namngivna kalkylbladet
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
        # AI-rader inkluderar original AI-förslag och eventuell diff
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
                data.get("original_text", ""),  # AI:s ursprungliga förslag
                data.get("diff_text", "")       # Vad testaren ändrade
        ])
        else:
             # Manuella rader och SUMMARY-rader har inga diff-kolumner
            ws.append_row([
                data.get("created_at"),
                data.get("type"),
                data.get("participant_id", st.session_state.get("user_title", "Vårdpersonal")),
                data.get("scenario"),
                data.get("category", ""),
                data.get("text", ""),
                data.get("keywords", ""),
                data.get("time_seconds", 0),
                "", # Tomt: original_text
                ""  # Tomt: diff_text
        ])                   
    except Exception as e:
        # Logga felet utan att krascha appen
        print(f"[SHEETS ERROR] {e}")


# --- GROQ API - KONFIGURATION OCH TEXTGENERERING ---


# Säker Groq API-nyckel hantering
if "GROQ_API_KEY" in st.secrets:
    GROQ_API_KEY = st.secrets["GROQ_API_KEY"]
else:
    GROQ_API_KEY = st.text_input("Ange din Groq API-nyckel:", type="password")
    if not GROQ_API_KEY:
        st.warning("Väntar på Groq API-nyckel...")
        st.stop()

client = Groq(api_key=GROQ_API_KEY)


def validate_output(text: str) -> bool:
    """
    Kontrollerar att den genererade texten inte innehåller förbjudna ord
    som kan signalera spekulation, värdering eller bristande objektivitet.
    
    Returnerar True om texten godkänns, annars False.
    """
    forbidden_words = ["kanske", "troligen", "verkar", "antar", "tyvärr", "lyckligvis"] 
    for word in forbidden_words:
        if word in text.lower():
            return False
    return True


# --- ERROR RATE OCH TASK SUCCESS ---
 
def calculate_error_rate(text: str, doc_type: str = "manual") -> dict:
    """
    Kontrollerar strukturella fel i en journalanteckning.
 
    För manuell text kontrolleras:
    - Om texten är för kort (under 20 ord)
    - Om förbjudna subjektiva ord förekommer
    - Om texten saknar beskrivning av åtgärd
 
    För AI-text kontrolleras:
    - Om IBIC-rubrikerna finns (Observation, Insats/Åtgärd)
 
    Returnerar dict med fel och antal fel.
    """
    errors = []
    words = text.strip().split()
 
    if len(words) < 20:
        errors.append("För kort text (under 20 ord)")
 
    subjective_words = ["kanske", "troligen", "verkar", "antar",
                        "tyvärr", "lyckligvis", "bra", "dåligt", "tråkigt"]
    found = [w for w in subjective_words if w in text.lower()]
    if found:
        errors.append(f"Subjektiva/förbjudna ord: {', '.join(found)}")
 
    if doc_type == "manual":
        action_words = ["hjälpte", "assisterade", "gav", "utförde",
                        "kontaktade", "informerade", "dokumenterade", "åtgärd"]
        if not any(w in text.lower() for w in action_words):
            errors.append("Ingen åtgärd beskriven")
 
    if doc_type == "ai":
        if "observation" not in text.lower():
            errors.append("Saknar IBIC-rubrik: Observation")
        if "insats" not in text.lower() and "åtgärd" not in text.lower():
            errors.append("Saknar IBIC-rubrik: Insats/Åtgärd")
 
    return {
        "error_count": len(errors),
        "errors": errors,
        "has_errors": len(errors) > 0
    }
 
 
def calculate_task_success(text: str, category: str, scenario: int) -> dict:
    """
    Bedömer om en dokumentationsuppgift anses godkänd.
 
    Kriterier:
    - Texten är tillräckligt lång (minst 20 ord)
    - Rätt kategori vald för scenariot
    - Inga förbjudna subjektiva ord
 
    Returnerar dict med success (bool) och anledning.
    """
    expected_categories = {
        1: "Utförda insatser",
        2: "Utförda insatser",
        3: "Avvikelser eller problem"
    }
 
    reasons = []
    success = True
 
    if len(text.strip().split()) < 20:
        success = False
        reasons.append("Text för kort")
 
    expected = expected_categories.get(scenario)
    if expected and category != expected:
        success = False
        reasons.append(f"Fel kategori (valde '{category}', förväntad '{expected}')")
 
    subjective_words = ["kanske", "troligen", "verkar", "antar", "tyvärr", "lyckligvis"]
    if any(w in text.lower() for w in subjective_words):
        success = False
        reasons.append("Innehåller subjektiva ord")
 
    return {
        "success": success,
        "reason": ", ".join(reasons) if reasons else "Godkänd",
    }


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
    4. Använd ALDRIG brukarens namn. Ersätt alltid med "Brukaren".
    5. Var strikt objektiv. Inga värderingar eller spekulationer.
        Förbjudna ord: tyvärr, lyckligtvis, verkade, kanske, troligen, antar, verkar
        
        Om användaren anger ett känslotillstånd (t.ex. "ledsen", "upprörd", "glad")
        Skriv INTE bort det — formulera om det som en observation:
        - "ledsen" → "Brukaren uppvisade tecken på nedstämdhet"
        - "upprörd" → "Brukaren uppvisade ett upprörd beteende"
        - "glad" → "Brukaren uppvisade ett positivt sinnesstämning"
    6. Basera anteckningen UTESLUTANDE på nyckelorden i användarens input.
    Scenariotexten är ENBART bakgrundsinformation för kontext — kopiera
    aldrig meningar eller detaljer därifrån som användaren inte nämnt.
    Om ett nyckelord saknar detaljer, skriv kortfattat utifrån det som finns.
    7. Max 5 meningar totalt.
    8. Inga avslutande rekommendationer eller förslag till åtgärder.

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
            model = "llama-3.3-70b-versatile", # Bäst presterande modell i test
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3, # Låg temperatur för mer konsekvent och faktabaserad output
            max_tokens=300,  # Begränsar svarslängden
            top_p=0.9        # Nucleus sampling för viss variation utan att tappa precision
        )
        text = response.choices[0].message.content.strip()
        return text if validate_output(text) else None
    except Exception as e:
        return None   
    
# --- SESSION STATE - INITIERING --- 

def init_session_state():
    defaults = {
        
            # Manuellt
            "started": False,               # Har användaren startat övningen?
            "start_time": None,             # Tidstämpel när manuell del startade
            "scenario": 1,                  # Aktuellt scenario (1–3)
            "finished": False,              # Är manuell del klar?
            "manual_answers": {},           # Sparade svar per scenario  
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
            "participant_id": None          # Används ej aktivt, men reserverad för framtida behov
    }

    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

init_session_state()

# Säkerställ att yrkesroll alltid är en sträng (aldrig None)
if "user_title" not in st.session_state:
    st.session_state.user_title = ""

if not st.session_state.user_title or st.session_state.user_title.strip() == "":
    st.session_state.user_title = ""


# --- # HJÄLPFUNKTIONER ---


def show_progress_bar(is_ai=False):
    """
    Visar en progressbar som indikerar hur långt användaren kommit
    i scenarierna. Hanterar både manuell och AI-assisterad del.
    """
    prefix = "ai_" if is_ai else ""
    current = st.session_state.get(f"{prefix}scenario", 1)
    total = len(scenarios)
    progress = current / total
    st.progress(progress, text=f"Scenario {current} av {total} - {get_scenario_title(current)}")


def compute_diff(original: str, final: str) -> str:
    """
    Beräknar och returnerar en unified diff-sträng som visar
    vad användaren ändrade i AI-förslaget.
    
    Returnerar 'Inga ändringar' om texterna är identiska.
    """
    if original.strip() == final.strip():
        return "Inga ändringar"
    diff = difflib.unified_diff(
        original.splitlines(),
        final.splitlines(),
        lineterm="",
        n=0
    )
    return "\n".join(list(diff)[2:])


def get_current_scenario_time(is_ai=False):
    """
    Beräknar hur lång tid (sekunder) som spenderats på aktuellt scenario.
    Initierar tidstämpeln om det är första gången scenariot öppnas.
 
    Returnerar:
        tuple: (elapsed_seconds, current_scenario_number)
    """
    prefix = "ai_" if is_ai else ""
    key = f"{prefix}scenario_start_times"
    scenario = st.session_state[f"{prefix}scenario"]

    # Sätt starttid om scenariot öppnas för första gången
    if scenario not in st.session_state[key]:
        st.session_state[key][scenario] = time.time()
    
    elapsed = int(time.time() - st.session_state[key][scenario])
    return elapsed, scenario


# ---SCENARION - INNEHÅLL OCH TITLAR ---

# Lista med de tre scenariotexterna som visas för användarna
scenarios = [
"""Scenario 1

Du har precis avslutat Brittas, 84 år, morgonstund.
Hon klarade personlig hygien självständigt men behövde assistans vid påklädning på grund av stelhet i händerna.
Hon åt hela frukosten men klagade på ont i vänster knä vid förflyttning.
Dokumentera insatsen.
""",

"""Scenario 2

Du är mitt i ett hektiskt eftermiddagspass. Du har precis gett Erik, 79 år,
sin ordinerade kvällsmedicin. Han uppger att han känner
sig ovanligt trött och har svårt att hålla ögonen öppna, trots att han sovit
under dagen. Du noterar att han rör sig långsammare än vanligt.
Du har bara några minuter. Dokumentera det viktigaste.
""",

"""Scenario 3

Gunnel, 91 år, vägrade för tredje gången denna vecka att ta sin ordinerade
blodtrycksmedicin. Hon uppgav att "tabletterna gör mig illamående".
Vid ditt försök att förklara vikten av medicinen blev hon upprörd och bad dig lämna rummet.
Du kontaktade ansvarig sjuksköterska per telefon kl. 14:32.
Dokumentera avvikelsen.
"""
]

def get_scenario_title(scenario_number: int) -> str:
    """Returnerar en kort och tydlig titel för varje scenario"""
    titles = {
        1: "Rutindokumentation efter vårdinsats",
        2: "Dokumentation under ett arbetspass",
        3: "Dokumentation av avvikelse"
    }
    return titles.get(scenario_number, f"Scenario {scenario_number}")


# --- ADMIN-VY (SIDOPANEL) ---

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

                 # Bygg DataFrame från kalkylbladsdata
                df = pd.DataFrame(data_rows, columns=headers)

                 # Ta bort eventuella namnlösa kolumner
                df = df.loc[:, df.columns.notna() & (df.columns != "")]

                st.success(f"Totalt {len(df)} rader hämtade")

                 # Filtrera på radtyp (manual / ai / SUMMARY)
                typ_filter = st.selectbox("Visa typ", ["Alla", "manual", "ai", "SUMMARY"])
                if typ_filter != "Alla":
                    df_filtered = df[df.iloc[:,1] == typ_filter]
                else:
                    df_filtered = df

                 # Visa hela tabellen
                st.dataframe(
                    df,
                    use_container_width=True,
                    hide_index=True
                )

                # --- Detaljvy för AI-svar med jämförelse ---
                st.divider()
                st.subheader("AI-genererade texter (med redigering)")

                ai_df = df[df.iloc[:,1] == "ai"].copy() if not df.empty else pd.DataFrame()

                if not ai_df.empty:
                    for idx, row in ai_df.iterrows():
                        # Extrahera kolumnvärden med fallback om kolumner saknas
                        scenario = row.iloc[3] if len(row) > 3 else ""
                        participant = row.iloc[2] if len(row) > 2 else ""
                        final_text = row.iloc[5] if len(row) > 5 else ""       # Kolumn för "text"
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
                            
                             # Visa ändringsvarning och diff om texten modifierats
                            if original.strip() != final_text.strip() and original.strip() != "":
                                st.warning("**Testaren har ändrat texten**")
                                st.markdown("**Skillnad:**")
                                st.text_area("Vad som ändrades", value=diff, height=100, disabled=True)
                            else:
                                st.success("Inga ändringar – testaren godkände AI-förslaget direkt")
                            
                            st.caption(f"Tid använd: {time_sec} sekunder")
                else:
                    st.info("Inga AI-svar har sparats ännu.")
                
                # SUS-sammanfattning 
                st.divider()
                summary_df = df[df.iloc[:,1] == "SUMMARY"]
                if not summary_df.empty:
                    st.subheader("SUS resultat och tid")
                    st.dataframe(summary_df[["participant_id", "text", "keywords"]], use_container_width=True)

        except Exception as e:
            st.error(f"Kunde inte hämta data från Google Sheets: {e}")


# --- STARTSKÄRM - SAMTYCKE OCH YRKESROLL ---

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
    """)

    st.info("""
    **Viktig information innan du börjar:**
    - Denna studie är anonym. Inga personuppgifter samlas in.
    - Din yrkesroll används enbart för att särskilja svar.
    - Data används enbart i examensarbetet.
    - Du kan avbryta när som helst.
    """)

    # Samtyckesbockning — måste kryssas i för att fortsätta
    consent = st.checkbox("Jag förstår och godkänner ovanstående")

    st.subheader("Instruktion")
    st.markdown("""
    Du kommer få **tre olika scenarier** som du ska dokumentera på två sätt:                
    1. **Manuellt** - Liknande som i journalsystemet idag
    2. **Med AI-hjälp** - efteråt
    """)

    # Yrkesroll används som anonym identifierare i kalkylbladet
    user_title = st.text_input(
        "**Din yrkesroll/titel**",
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


# --- MANUELL DEL - SCENARIO-FORMULÄR ---

if st.session_state.started and not st.session_state.finished and not st.session_state.ai_started:
    elapsed, current_scenario = get_current_scenario_time(is_ai=False)

    st.subheader(f"Scenario {current_scenario} - {get_scenario_title(current_scenario)}")
    show_progress_bar(is_ai=False)
    st.markdown(scenarios[current_scenario - 1])

    st.subheader("Händelsedatum och tid")

    # Datum och tidsinmatning i två kolumner
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
        ["Utförda insatser", "Avvikelser eller problem", "Kommunikation"],
        key=f"man_cat_{current_scenario}"
    )
    
    text = st.text_area("Beskrivning av händelse + åtgärd",
                        placeholder="Beskriv vad som hände och vilka åtgärder som vidtogs...", 
                        key=f"manual_text_{current_scenario}",
                        height=200
    )

    if st.button("Nästa scenario", type="primary"):
        if not text.strip():
            st.warning("Skriv något i textfältet innan du går vidare.")
        else:
            event_datetime = datetime.combine(event_date, event_time)
            event_datetime_str = event_datetime.strftime("%Y-%m-%d %H:%M")
            time_spent = elapsed

            # Beräkna fel och task success för manuell text
            error_result = calculate_error_rate(text.strip(), doc_type="manual")
            success_result = calculate_task_success(text.strip(), cat, current_scenario)

            # Spara svaret lokalt i session state
            st.session_state.manual_answers[current_scenario] = {
                "category": cat, 
                "text": text.strip(),
                "event_datetime": event_datetime_str
            }
            st.session_state.scenario_times[current_scenario] = time_spent

           # Spara svaret i Google Sheets
            save_to_sheets({
                "created_at": datetime.now().isoformat(),
                "type": "manual",
                "participant_id": st.session_state.get("user_title", "Vårdpersonal"),
                "scenario": current_scenario,
                "category": cat,
                "text": f"{event_datetime_str} - {text.strip()}",
                "keywords": (
                    f"Fel: {error_result['error_count']} | "
                    f"Feltyper: {'; '.join(error_result['errors']) if error_result['errors'] else 'Inga'} | "
                    f"Success: {success_result['success']} | "
                    f"Anledning: {success_result['reason']}"
                ),
                "time_seconds": time_spent
            })

            # Gå till nästa scenario eller markera manuell del som klar
            if current_scenario < len(scenarios):
                st.session_state.scenario +=1
            else:
                st.session_state.finished = True
                st.session_state.end_time = time.time()
            st.rerun()


# --- ÖVERGÅNGSSKÄRM - MANUELL DEL KLAR ---

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
    
# --- AI-ASSISTERAD DEL - SCENARIO-FORMULÄR MED TEXTGENERERING ---

if st.session_state.ai_started and not st.session_state.ai_finished:
    elapsed, current_scenario = get_current_scenario_time(is_ai=True)

    st.subheader(f"Scenario {current_scenario} - {get_scenario_title(current_scenario)}")
    show_progress_bar(is_ai=True)
    st.markdown(scenarios[current_scenario - 1])

    st.divider()

    st.subheader("Händelsedatum och tid")

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

    # Förval av kategori per scenario baserat på scenariets natur
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
    
    # Separata fält för observation och åtgärd för tydligare struktur
    observation = st.text_area(
        "Beskrivning av händelse/Observation",
        placeholder="t.ex. Brukaren fick hjälp med lunch. Brukaren åt ungefär halva portionen...",
        key=f"ai_obs_{current_scenario}",
        height=100
    )

    åtgärd = st.text_area(
        "Åtgärd/Insats",
        placeholder="t.ex. Hjälpte till att skära maten, uppmuntrade att äta, serverade extra dryck...",
        key=f"ai_åtgärd_{current_scenario}",
        height=100
    )
    
    # Räknare för regenerering används för att tvinga fram ny widget-nyckel
    # och undvika att Streamlit cachar det gamla textvärdet
    if f"regen_count_{current_scenario}" not in st.session_state:
        st.session_state[f"regen_count_{current_scenario}"] = 0

     # Knapp: Generera förstaförslag
    if st.button("Generera dokumentationstext", type="primary", use_container_width=True):
        if not observation.strip():
            st.warning("Fyll i beskrivning av händelse/nyckelord först")
        else:
            event_datetime_str = datetime.combine(event_date, event_time).strftime("%Y-%m-%d %H:%M")
            
            # LLM
            with st.spinner("Genererar journalanteckning med Groq..."):
                generated = query_groq(
                    keywords=f"Observation: {observation}\nÅtgärd: {åtgärd}", 
                    category=category, 
                    scenario_text=scenarios[current_scenario - 1],
                    event_datetime=event_datetime_str
                )

            if generated:
                # Spara både redigerbar version och original för diff-beräkning
                st.session_state[f"ai_result_{current_scenario}"] = generated
                st.session_state[f"ai_result_{current_scenario}_original"] = generated
                
                st.session_state[f"ai_show_{current_scenario}"] = True
            else:
                st.error("Texten uppfyllde inte reglerna. Försök igen.")
    
    # Sektion: Visa och redigera AI-förslaget
    if st.session_state.get(f"ai_show_{current_scenario}", False):
        st.divider()
        st.subheader("2. Journalanteckning")

        regen_count = st.session_state.get(f"regen_count_{current_scenario}", 0)
        
        # Textfält för granskning och redigering; regen_count ingår i nyckeln
        # för att återskapa widgeten när nytt förslag genereras
        edited = st.text_area(
            "AI-förslag - redigera vid behov",
            value = st.session_state.get(f"ai_result_{current_scenario}", ""),
            key=f"ai_edit_{current_scenario}_{regen_count}",
            height=200
        )
        
        # Synkronisera redigerat värde till session state
        st.session_state[f"ai_result_{current_scenario}"] = edited

        if not edited.strip():
            st.warning("Texten är raderad. Fyll i nyckelorden ovan och generera en ny text")
            st.session_state[f"ai_show_{current_scenario}"] = False
        else:
            if st.button("Generera ny text", key=f"regenerate_{current_scenario}"):
                event_datetime_str = datetime.combine(event_date, event_time).strftime("%Y-%m-%d %H:%M")
                with st.spinner("Genererar nytt förslag..."):
                    generated = query_groq(
                        keywords=f"Observation: {observation}\nÅtgärd: {åtgärd}",
                        category=category,
                        scenario_text=scenarios[current_scenario - 1],
                        event_datetime=event_datetime_str
                    )
                if generated:
                    st.session_state[f"ai_result_{current_scenario}"] = generated
                    st.session_state[f"ai_result_{current_scenario}_original"] = generated

                    # Öka räknaren för att tvinga fram ny widget-instans
                    st.session_state[f"regen_count_{current_scenario}"] += 1
                    st.rerun()
                else:
                    st.error("Texten uppfyllde inte reglerna. Försök igen.")

    # Knapp: Godkänn och gå vidare
    if st.button("Godkänn och nästa scenario", type="primary", use_container_width=True):
        final_text = st.session_state.get(f"ai_result_{current_scenario}", "").strip()
        
        if not final_text:
            st.warning("Generera och/eller redigera texten först")
        else:
            original = st.session_state.get(f"ai_result_{current_scenario}_original", "")
            was_edited = original.strip() != final_text # True om användaren ändrat något

            # Beräkna vad som ändrades jämfört med AI-originalet
            if was_edited:
                diff_text = compute_diff(original, final_text)
            else:
                diff_text = "Inga ändringar"
            
            time_spent = elapsed

            # Beräkna fel och task success för AI-text
            error_result = calculate_error_rate(final_text, doc_type="ai")
            success_result = calculate_task_success(final_text, category, current_scenario)


            st.session_state.ai_answers[current_scenario] = final_text
            st.session_state.ai_scenario_times[current_scenario] = elapsed

            # Spara till Google Sheets med original och diff för analysändamål
            save_to_sheets({
                "created_at": datetime.now().isoformat(),
                "type": "ai",
                "participant_id": st.session_state.get("user_title", "Vårdpersonal"),
                "scenario": current_scenario,
                "category": category,
                "text": final_text,
                "keywords": (
                    f"Obs: {observation} | Åtgärd: {åtgärd} | "
                    f"Redigerad: {was_edited} | "
                    f"Fel: {error_result['error_count']} | "
                    f"Feltyper: {'; '.join(error_result['errors']) if error_result['errors'] else 'Inga'} | "
                    f"Success: {success_result['success']} | "
                    f"Anledning: {success_result['reason']}"
                ),
                "time_seconds": time_spent,
                "original_text": original,
                "diff_text": diff_text 
            })

            # Gå till nästa scenario eller markera AI-delen som klar
            if current_scenario < len(scenarios):
                st.session_state.ai_scenario +=1
            else:
                st.session_state.ai_finished = True
                st.session_state.ai_end_time = time.time()
            st.rerun()


# --- AVSLUTNING - SUS-ENKÄT OCH TACK-SIDA ---

if st.session_state.ai_finished:
    total_ai = int(st.session_state.ai_end_time - st.session_state.ai_start_time)
    total_manual = int(st.session_state.end_time - st.session_state.start_time)

    st.title("SUS (System Usability Scale) - standardiserat användbarhetstest")

    st.markdown("---")
    st.subheader("Hur upplevde du AI-assisterad dokumentation?")

    # --- SUS (System Usability Scale) - standardiserat användbarhetstest ---
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
            "time_seconds": total_manual,
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

        st.balloons()
        st.markdown("### Tack för ditt deltagande!")
        st.markdown("Dina svar har sparats. Du kan nu stänga denna sida.")
