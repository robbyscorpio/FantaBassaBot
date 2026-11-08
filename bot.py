import os
import asyncio
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import pandas as pd
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# --- 1. SERVER HTTP PER RENDER ---
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Fantacalcio is online!")

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    server.serve_forever()

server_thread = threading.Thread(target=run_server, daemon=True)
server_thread.start()

# --- 2. STATO MEMORIA E ASTE ---
aste = {}                # { "giocatore_key": { ... } }
crediti_squadre = {}     # { "NomeSquadra": crediti_rimanenti }
giocatori_tagliati = {}  # { "giocatore_key": { "squadra": "NomeSquadra", "prezzo": 15 } }
storico_acquisti = []    # Lista di dizionari con i giocatori assegnati
rose_squadre = {}        # { "NomeSquadra": [ {giocatore, ruolo, prezzo}, ... ] }
giocatori_liberi = []    # Lista di dizionari con i giocatori svincolati {giocatore, ruolo, prezzo}

# Limiti massimi per reparto
LIMITI_ROSA = {
    "P": 3,
    "D": 8,
    "C": 8,
    "A": 6
}

TIMER_ASTA_ORE = 24
MAX_ASTE_CONTEMPORANEE = 2

data_inizio_mercato = None
data_fine_mercato = None
mercato_bloccato = False

def get_nome_squadra(user_fullname_or_username) -> str:
    return user_fullname_or_username

# --- CARICAMENTO AUTOMATICO DA EXCEL ---
def carica_dati_da_excel():
    global rose_squadre, crediti_squadre, giocatori_tagliati, giocatori_liberi
    file_excel = "Listone_Fantacalcio.xlsx"
    
    if not os.path.exists(file_excel):
        print(f"❌ ERRORE CRITICO: Il file {file_excel} NON è stato trovato nella cartella del progetto!")
        return

    try:
        xls = pd.ExcelFile(file_excel)
        fogli_disponibili = xls.sheet_names
        print(f"📊 Fogli trovati nel file Excel: {fogli_disponibili}")

        # 1. LETTURA DEL FOGLIO 'Rosa' E DEI LIBERI
        nome_foglio_rosa = 'Rosa' if 'Rosa' in fogli_disponibili else fogli_disponibili[0]
        df_rosa = pd.read_excel(xls, sheet_name=nome_foglio_rosa)
        print(f"📄 Lette {len(df_rosa)} righe dal foglio '{nome_foglio_rosa}'")
        
        for _, row in df_rosa.iterrows():
            nome = str(row.get("NomeGiocatore", "")).strip()
            ruolo = str(row.get("Ruolo", "")).strip().upper()
            squadra = str(row.get("Squadra", "")).strip()
            prezzo = row.get("Prezzo", 0)
            
            if not nome or nome.lower() == "nan":
                continue
                
            try:
                prezzo = int(prezzo)
            except ValueError:
                prezzo = 0

            if squadra and squadra.lower() != "nan" and squadra.lower() != "svincolato" and squadra != "":
                if squadra not in rose_squadre:
                    rose_squadre[squadra] = []
                rose_squadre[squadra].append({
                    "giocatore": nome,
                    "ruolo": ruolo,
                    "prezzo": prezzo
                })
            else:
                giocatori_liberi.append({
                    "giocatore": nome,
                    "ruolo": ruolo,
                    "prezzo": prezzo
                })

        print(f"✅ Caricati {len(giocatori_liberi)} giocatori liberi e {sum(len(v) for v in rose_squadre.values())} giocatori in rosa.")

        # 2. LETTURA DEL FOGLIO DEI CREDITI
        if 'Crediti' in fogli_disponibili:
            df_crediti = pd.read_excel(xls, sheet_name='Crediti')
            for _, row in df_crediti.iterrows():
                squadra = str(row.get("Squadra", "")).strip()
                crediti = row.get("Crediti", 0)
                
                if not squadra or squadra.lower() == "nan":
                    continue
                try:
                    crediti = int(crediti)
                except ValueError:
                    crediti = 0
                
                crediti_squadre[squadra] = crediti
            print(f"✅ Caricati i crediti per {len(crediti_squadre)} squadre.")
        else:
            print("⚠️ Attenzione: Foglio 'Crediti' non trovato nel file Excel.")

        # 3. LETTURA DEL FOGLIO DEI TAGLI
        if 'Tagli' in fogli_disponibili:
            df_tagli = pd.read_excel(xls, sheet_name='Tagli')
            for _, row in df_tagli.iterrows():
                nome = str(row.get("NomeGiocatore", "")).strip()
                squadra = str(row.get("Squadra", "")).strip()
                prezzo = row.get("PrezzoTaglio", 0)
                
                if not nome or nome.lower() == "nan":
                    continue
                try:
                    prezzo = int(prezzo)
                except ValueError:
                    prezzo = 0

                giocatore_key = nome.lower()
                giocatori_tagliati[giocatore_key] = {
                    "squadra": squadra,
                    "prezzo": prezzo
                }
            print(f"✅ Caricati {len(giocatori_tagliati)} giocatori tagliati.")

    except Exception as e:
        print(f"❌ Errore critico durante la lettura del file Excel: {e}")

# --- CONTROLLO AMMINISTRATORE ---
async def verifica_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if update.effective_chat.type == "private":
        return True
    try:
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        member = await context.bot.get_chat_member(chat_id, user_id)
        if member.status in ["creator", "administrator"]:
            return True
    except Exception:
        pass
    
    await update.message.reply_text("⛔ **Comando riservato agli amministratori del gruppo.**")
    return False

# --- 3. GESTORE TIMER ASTA ---
async def timer_asta(context: ContextTypes.DEFAULT_TYPE):
    global giocatori_liberi
    job = context.job
    giocatore_key = job.data["giocatore_key"]
    chat_id = job.data["chat_id"]

    if giocatore_key in aste:
        info = aste[giocatore_key]
        nome = info["nome_originale"]
        vincitore = info["squadra_offerente"]
        offerta = info["offerta_attuale"]

        storico_acquisti.append({
            "giocatore": nome,
            "vincitore": vincitore,
            "prezzo": offerta
        })

        if vincitore not in rose_squadre:
            rose_squadre[vincitore] = []
        
        ruolo_giocatore = "N/D"
        for lib in giocatori_liberi:
            if lib["giocatore"].lower() == giocatore_key:
                ruolo_giocatore = lib["ruolo"]
                break

        rose_squadre[vincitore].append({
            "giocatore": nome,
            "ruolo": ruolo_giocatore,
            "prezzo": offerta
        })

        giocatori_liberi = [l for l in giocatori_liberi if l["giocatore"].lower() != giocatore_key]

        if vincitore in crediti_squadre:
            crediti_squadre[vincitore] = max(0, crediti_squadre[vincitore] - offerta)

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🔴 **ASTA CHIUSA!** 🔴\n\n"
                 f"⚽ **Giocatore:** {nome}\n"
                 f"🏆 **Aggiudicato a:** {vincitore}\n"
                 f"💰 **Prezzo finale:** {offerta} crediti"
        )
        del aste[giocatore_key]

# --- 4. COMANDI DEL BOT ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Benvenuto nel Bot Aste Fantacalcio!\n"
        "Usa `/guida` per consultare le istruzioni e le regole del mercato.",
        parse_mode="Markdown"
    )

async def set_mercato(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await verifica_admin(update, context):
        return

    if len(context.args) < 4:
        await update.message.reply_text("⚠️ Usa: `/setmercato DataInizio OraInizio DataFine OraFine`", parse_mode="Markdown")
        return

    str_inizio = f"{context.args[0]} {context.args[1]}"
    str_fine = f"{context.args[2]} {context.args[3]}"

    try:
        global data_inizio_mercato, data_fine_mercato, mercato_bloccato
        data_inizio_mercato = datetime.strptime(str_inizio, "%d/%m/%Y %H:%M")
        data_fine_mercato = datetime.strptime(str_fine, "%d/%m/%Y %H:%M")
        mercato_bloccato = False

        await update.message.reply_text(
            f"📅 **PERIODO MERCATO IMPOSTATO E APERTO!**\n\n"
            f"🟢 **Apertura:** {data_inizio_mercato.strftime('%d/%m/%Y alle %H:%M')}\n"
            f"🔴 **Chiusura:** {data_fine_mercato.strftime('%d/%m/%Y alle %H:%M')}",
            parse_mode="Markdown"
        )
    except ValueError:
        await update.message.reply_text("⚠️ Data o ora non valide. Usa il formato `GG/MM/AAAA HH:MM`.", parse_mode="Markdown")

async def blocca_mercato(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await verifica_admin(update, context):
        return

    global mercato_bloccato
    mercato_bloccato = not mercato_bloccato
    stato_testo = "🛑 **BLOCCATO**" if mercato_bloccato else "🟢 **APERTO E ATTIVO**"
    
    await update.message.reply_text(f"🔒 **STATO MERCATO AGGIORNATO:** {stato_testo}", parse_mode="Markdown")

async def mostra_crediti(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not crediti_squadre:
        await update.message.reply_text("ℹ️ Non ci sono crediti registrati al momento.", parse_mode="Markdown")
        return

    messaggio = "💰 **BILANCIO CREDITI SQUADRE:**\n\n"
    for squadra, crediti in crediti_squadre.items():
        messaggio += f"• {squadra}: **{crediti}** crediti\n"

    await update.message.reply_text(messaggio, parse_mode="Markdown")

# --- GESTIONE ROSE E LIBERI ---
async def seleziona_rosa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tutte_squadre = list(set(list(rose_squadre.keys()) + list(crediti_squadre.keys())))
    if not tutte_squadre:
        await update.message.reply_text("ℹ️ Nessuna squadra trovata. Controlla che il file Excel sia caricato correttamente.", parse_mode="Markdown")
        return

    keyboard = [[InlineKeyboardButton(s, callback_data=f"rosa_{s}")] for s in sorted(tutte_squadre)]
    await update.message.reply_text("📋 **Seleziona la squadra di cui vuoi visualizzare la rosa:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def bottone_rosa_cliccato(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    squadra = query.data.replace("rosa_", "")

    if squadra not in rose_squadre or not rose_squadre[squadra]:
        await query.edit_message_text(text=f"ℹ️ La rosa della squadra **{squadra}** è attualmente vuota.", parse_mode="Markdown")
        return

    rosa = rose_squadre[squadra]
    conteggio = {"P": 0, "D": 0, "C": 0, "A": 0}
    messaggio = f"📋 **ROSA SQUADRA: {squadra}**\n\n"
    tot_speso = 0
    for p in rosa:
        r = p.get('ruolo', 'N/D')
        if r in conteggio: conteggio[r] += 1
        messaggio += f"• [{r}] **{p['giocatore']}** ({p['prezzo']} crediti)\n"
        tot_speso += p['prezzo']

    messaggio += f"\n📊 **Composizione:** P: {conteggio['P']}/3 | D: {conteggio['D']}/8 | C: {conteggio['C']}/8 | A: {conteggio['A']}/6\n"
    messaggio += f"💸 **Spesa totale:** {tot_speso} crediti"
    await query.edit_message_text(text=messaggio, parse_mode="Markdown")

async def seleziona_liberi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not giocatori_liberi:
        await update.message.reply_text("ℹ️ Non ci sono giocatori liberi al momento. Controlla il file Excel.", parse_mode="Markdown")
        return

    keyboard = [
        [InlineKeyboardButton("🟢 Portieri (P)", callback_data="liberi_P")],
        [InlineKeyboardButton("🔵 Difensori (D)", callback_data="liberi_D")],
        [InlineKeyboardButton("🟡 Centrocampisti (C)", callback_data="liberi_C")],
        [InlineKeyboardButton("🔴 Attaccanti (A)", callback_data="liberi_A")]
    ]
    await update.message.reply_text("🔍 **Seleziona il ruolo dei giocatori liberi:**", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def bottone_liberi_cliccato(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ruolo_scelto = query.data.replace("liberi_", "").upper()
    filtrati = sorted([g for g in giocatori_liberi if g["ruolo"] == ruolo_scelto], key=lambda x: x["giocatore"])

    if not filtrati:
        await query.edit_message_text(text=f"ℹ️ Nessun giocatore libero trovato per il ruolo **{ruolo_scelto}**.", parse_mode="Markdown")
        return

    messaggio = f"🟢 **GIOCATORI LIBERI - RUOLO: {ruolo_scelto}**\n\n"
    for p in filtrati:
        messaggio += f"• **{p['giocatore']}** (Quotazione: {p['prezzo']})\n"

    if len(messaggio) > 4000:
        messaggio = messaggio[:3950] + "\n\n... (elenco troncato)"

    await query.edit_message_text(text=messaggio, parse_mode="Markdown")

async def storico_aste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not storico_acquisti:
        await update.message.reply_text("ℹ️ Nessun giocatore è stato ancora assegnato.", parse_mode="Markdown")
        return

    messaggio = "🏆 **RIEPILOGO GIOCATORI ASSEGNATI:**\n\n"
    for idx, item in enumerate(storico_acquisti, 1):
        messaggio += f"{idx}. **{item['giocatore']}** ➔ {item['vincitore']} ({item['prezzo']} crediti)\n"

    await update.message.reply_text(messaggio, parse_mode="Markdown")

async def guida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    testo_guida = (
        "📖 **GUIDA E REGOLAMENTO ASTE**\n\n"
        "⚙️ **Regole Fondamentali:**\n"
        "1️⃣ **Solo Liberi:** Si può offrire esclusivamente per i giocatori liberi.\n"
        "2️⃣ **Nome Esatto:** Il nome del giocatore deve essere inserito **esattamente** come scritto nell'elenco ufficiale.\n"
        "3️⃣ **Limiti Rosa:** È obbligatorio mantenere i seguenti limiti per reparto:\n"
        "   •  P: max 3 | D: max 8 | C: max 8 | A: max 6\n"
        "4️⃣ **Regola Tagli:** Se si offre per un giocatore presente nel foglio 'Tagli', l'offerta minima deve essere pari al valore del taglio stabilito.\n"
        "5️⃣ **Crediti e Aste Simultanee:** La somma di quanto offerto nelle tue aste simultanee attive non può superare i tuoi **crediti residui** in portafoglio.\n\n"
        "🔍 **Comandi di Consultazione:**\n"
        "`/portafoglio` - Crediti residui\n"
        "`/rosa` - Visualizza le rose delle squadre\n"
        "`/liberi` - Elenca i giocatori svincolati per ruolo\n"
        "`/assegnati` - Storico acquisti\n"
        "`/stato` - Aste attive\n\n"
        f"🟢 **Aste e Rilanci:**\n"
        "`/asta NomeGiocatore Quota` (Max {MAX_ASTE_CONTEMPORANEE} simultanee)\n"
        "`/rilancio NomeGiocatore NuovaOfferta`"
    )
    await update.message.reply_text(testo_guida, parse_mode="Markdown")

async def nuova_asta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        try: await update.message.delete()
        except: pass

    if mercato_bloccato:
        await update.message.reply_text("🛑 **Il mercato è attualmente bloccato dagli amministratori!**")
        return

    utente = get_nome_squadra(update.effective_user.first_name)
    if update.effective_user.username:
        utente = f"@{update.effective_user.username}"

    squadra_utente = None
    for s in crediti_squadre.keys():
        if s.lower() in utente.lower() or utente.lower() in s.lower():
            squadra_utente = s
            break
    if not squadra_utente:
        squadra_utente = utente

    aste_attive_utente = sum(1 for info in aste.values() if info["squadra_offerente"] == squadra_utente)
    if aste_attive_utente >= MAX_ASTE_CONTEMPORANEE:
        await update.message.reply_text(f"⚠️ Hai già {MAX_ASTE_CONTEMPORANEE} aste attive in simultanea.")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text("⚠️ Usa: `/asta NomeGiocatore Quota`\n*(Ricorda di inserire il nome esatto come da elenco!)*", parse_mode="Markdown")
        return

    try:
        quota_massima = int(context.args[-1])
        nome_giocatore = " ".join(context.args[:-1]).replace('"', '').strip()
    except ValueError:
        await update.message.reply_text("⚠️ La quota deve essere un numero intero.", parse_mode="Markdown")
        return

    giocatore_key = nome_giocatore.lower()

    if giocatore_key in aste:
        await update.message.reply_text(f"⚠️ C'è già un'asta in corso per **{nome_giocatore}**!")
        return

    giocatore_trovato = None
    for lib in giocatori_liberi:
        if lib["giocatore"].lower() == giocatore_key:
            giocatore_trovato = lib
            break

    if not giocatore_trovato:
        await update.message.reply_text(
            f"❌ **Errore:** Il giocatore **\"{nome_giocatore}\"** non è disponibile (o è già di proprietà di qualcuno, oppure il nome non corrisponde esattamente all'elenco)."
        )
        return

    ruolo = giocatore_trovato["ruolo"]

    rosa_attuale = rose_squadre.get(squadra_utente, [])
    conti_reparto = sum(1 for p in rosa_attuale if p.get("ruolo") == ruolo)
    
    for k, info in aste.items():
        if info["squadra_offerente"] == squadra_utente:
            for lib in giocatori_liberi:
                if lib["giocatore"].lower() == k and lib["ruolo"] == ruolo:
                    conti_reparto += 1

    limite_max = LIMITI_ROSA.get(ruolo, 99)
    if conti_reparto >= limite_max:
        await update.message.reply_text(
            f"❌ **Rosa Piena:** Hai già raggiunto il limite massimo di {limite_max} giocatori per il ruolo **{ruolo}** ({squadra_utente})."
        )
        return

    crediti_disponibili = crediti_squadre.get(squadra_utente, 500)
    impegno_altre_aste = sum(info["offerta_attuale"] for k, info in aste.items() if info["squadra_offerente"] == squadra_utente)
    
    if (impegno_altre_aste + quota_massima) > crediti_disponibili:
        await update.message.reply_text(
            f"❌ **Crediti Insufficienti:** Hai {crediti_disponibili} crediti residui e {impegno_altre_aste} impegnati in altre aste. La somma supera il tuo budget!"
        )
        return

    offerta_pubblica = 1
    if giocatore_key in giocatori_tagliati:
        prezzo_minimo = giocatori_tagliati[giocatore_key]["prezzo"]
        if quota_massima < prezzo_minimo:
            await update.message.reply_text(
                f"❌ **Regola Tagli:** Questo giocatore è stato tagliato (valore minimo: {prezzo_minimo}). La tua offerta iniziale deve essere di almeno **{prezzo_minimo}** crediti."
            )
            return
        offerta_pubblica = prezzo_minimo

    if quota_massima < offerta_pubblica:
        offerta_pubblica = quota_massima

    ora_attuale = datetime.now()
    scadenza = ora_attuale + timedelta(hours=TIMER_ASTA_ORE)

    job = context.job_queue.run_once(
        timer_asta,
        when=timedelta(hours=TIMER_ASTA_ORE),
        data={"giocatore_key": giocatore_key, "chat_id": update.effective_chat.id},
        name=giocatore_key
    )

    aste[giocatore_key] = {
        "nome_originale": giocatore_trovato["giocatore"],
        "offerta_attuale": offerta_pubblica,
        "ultimo_offrente": squadra_utente,
        "squadra_offerente": squadra_utente,
        "creatore": squadra_utente,
        "scadenza": scadenza,
        "job": job,
        "offerte_massime": {squadra_utente: quota_massima}
    }

    await update.message.reply_text(
        f"📣 **ASTA APERTA!**\n\n"
        f"⚽ **Giocatore:** {giocatore_trovato['giocatore']} [{ruolo}]\n"
        f"💰 **Offerta attuale:** {offerta_pubblica} crediti\n"
        f"🏰 **Squadra:** {squadra_utente}\n"
        f"⏳ **Scadenza:** 24 ore."
    )

async def rilancio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        try: await update.message.delete()
        except: pass

    if mercato_bloccato:
        await update.message.reply_text("🛑 **Il mercato è bloccato!**")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text("⚠️ Usa: `/rilancio NomeGiocatore NuovaOfferta`", parse_mode="Markdown")
        return

    try:
        nuova_offerta = int(context.args[-1])
        nome_giocatore = " ".join(context.args[:-1]).replace('"', '').strip()
    except ValueError:
        await update.message.reply_text("⚠️ L'ultimo parametro deve essere un numero intero!")
        return

    giocatore_key = nome_giocatore.lower()

    if giocatore_key not in aste:
        await update.message.reply_text(f"❌ Nessuna asta attiva per **{nome_giocatore}**.")
        return

    utente = get_nome_squadra(update.effective_user.first_name)
    if update.effective_user.username:
        utente = f"@{update.effective_user.username}"

    squadra_utente = None
    for s in crediti_squadre.keys():
        if s.lower() in utente.lower() or utente.lower() in s.lower():
            squadra_utente = s
            break
    if not squadra_utente:
        squadra_utente = utente

    if giocatore_key in giocatori_tagliati:
        prezzo_minimo = giocatori_tagliati[giocatore_key]["prezzo"]
        if nuova_offerta < prezzo_minimo:
            await update.message.reply_text(f"❌ Questo giocatore ha un prezzo minimo di taglio pari a **{prezzo_minimo}** crediti.")
            return

    info_asta = aste[giocatore_key]
    info_asta["offerte_massime"][squadra_utente] = max(info_asta["offerte_massime"].get(squadra_utente, 0), nuova_offerta)

    miglior_offerente = max(info_asta["offerte_massime"], key=info_asta["offerte_massime"].get)
    massimo_disponibile = info_asta["offerte_massime"][miglior_offerente]

    offerte_ordinate = sorted(info_asta["offerte_massime"].values(), reverse=True)
    
    if len(offerte_ordinate) > 1:
        offerta_calcolata = min(max(info_asta["offerta_attuale"] + 1, offerte_ordinate[1] + 1), massimo_disponibile)
    else:
        offerta_calcolata = min(nuova_offerta, massimo_disponibile)

    if offerta_calcolata <= info_asta["offerta_attuale"] and squadra_utente != miglior_offerente:
        await update.message.reply_text("⚠️ Offerta superata dal rilancio automatico!")
        return

    info_asta["job"].schedule_removal()
    nuovo_job = context.job_queue.run_once(
        timer_asta,
        when=timedelta(hours=TIMER_ASTA_ORE),
        data={"giocatore_key": giocatore_key, "chat_id": update.effective_chat.id},
        name=giocatore_key
    )

    info_asta["offerta_attuale"] = offerta_calcolata
    info_asta["ultimo_offrente"] = miglior_offerente
    info_asta["squadra_offerente"] = miglior_offerente
    info_asta["scadenza"] = datetime.now() + timedelta(hours=TIMER_ASTA_ORE)
    info_asta["job"] = nuovo_job

    await update.message.reply_text(
        f"🔥 **RILANCIO AUTOMATICO!**\n\n"
        f"⚽ **Giocatore:** {info_asta['nome_originale']}\n"
        f"💰 **Offerta:** {offerta_calcolata} crediti\n"
        f"🏰 **In vantaggio:** {miglior_offerente}"
    )

async def stato_aste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stato_testo = "🛑 Bloccato" if mercato_bloccato else "🟢 Aperto"
    messaggio = f"📅 **Stato Mercato:** {stato_testo}\n\n"
    
    if not aste:
        messaggio += "ℹ️ Al momento non ci sono aste in corso.\n\n"
    else:
        messaggio += "📋 **ASTE ATTIVE IN CORSO:**\n"
        ora_attuale = datetime.now()

        for key, info in aste.items():
            tempo_rimanente = info["scadenza"] - ora_attuale
            ore, remainder = divmod(tempo_rimanente.seconds, 3600)
            minuti, _ = divmod(remainder, 60)

            messaggio += (
                f"⚽ **{info['nome_originale']}** (Aperta da: {info['creatore']})\n"
                f"  • Offerta: {info['offerta_attuale']} crediti (In vantaggio: {info['squadra_offerente']})\n"
                f"  • Tempo rimasto: ~{ore}h {minuti}m\n\n"
            )

    await update.message.reply_text(messaggio, parse_mode="Markdown")

# --- 5. MAIN ---
def main():
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        print("ERRORE: Variabile TELEGRAM_TOKEN non trovata.")
        return

    carica_dati_da_excel()

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler(["start"], start))
    app.add_handler(CommandHandler(["guida", "help"], guida))
    app.add_handler(CommandHandler(["setmercato"], set_mercato))
    app.add_handler(CommandHandler(["bloccamercato", "stopmercato"], blocca_mercato))
    
    app.add_handler(CommandHandler(["portafoglio", "budget"], mostra_crediti))
    
    app.add_handler(CommandHandler(["rosa"], seleziona_rosa))
    app.add_handler(CallbackQueryHandler(bottone_rosa_cliccato, pattern="^rosa_"))
    
    app.add_handler(CommandHandler(["liberi"], seleziona_liberi))
    app.add_handler(CallbackQueryHandler(bottone_liberi_cliccato, pattern="^liberi_"))
    
    app.add_handler(CommandHandler(["asta", "a"], nuova_asta))
    app.add_handler(CommandHandler(["rilancio", "ril"], rilancio))
    
    app.add_handler(CommandHandler(["stato"], stato_aste))
    app.add_handler(CommandHandler(["assegnati", "storico"], storico_aste))

    print("Bot Fantacalcio Asta in esecuzione...")
    app.run_polling()

if __name__ == "__main__":
    main()