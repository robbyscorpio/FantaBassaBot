import os
import logging
import base64
import threading
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import openpyxl
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, CallbackQueryHandler

# Configurazione dei log
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GH_PAT = os.getenv("GH_PAT")
GH_REPO = os.getenv("GH_REPO")
ADMIN_ID = 592528299
FILE_NAME = "Listone_Fantacalcio.xlsx"

# STATO GLOBALE DELL'ASTA ATTIVA
asta_attiva = {
    "giocatore": None,
    "offerta_corrente": 1,
    "squadra_leader": None,
    "offerte_massime": {},
    "scadenza": None
}

# --- SERVER HTTP PER RENDER (Health Check) ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"FantaBassaBot is active and live!")
        
    def log_message(self, format, *args):
        return

def run_health_check_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthCheckHandler)
    server.serve_forever()
# ---------------------------------------------

def carica_su_github(file_path=FILE_NAME, percorso_nel_repo=FILE_NAME):
    if not GH_PAT or not GH_REPO:
        logger.error("Credenziali GitHub mancanti.")
        return False
    url = f"https://api.github.com/repos/{GH_REPO}/contents/{percorso_nel_repo}"
    headers = {"Authorization": f"Bearer {GH_PAT}", "Accept": "application/vnd.github+json"}
    if not os.path.exists(file_path):
        return False
    with open(file_path, "rb") as f:
        content_bytes = f.read()
    content_base64 = base64.b64encode(content_bytes).decode("utf-8")
    response = requests.get(url, headers=headers)
    sha = response.json().get("sha") if response.status_code == 200 else None
    payload = {"message": "Aggiornamento automatico file Excel", "content": content_base64}
    if sha:
        payload["sha"] = sha
    put_response = requests.put(url, headers=headers, json=payload)
    return put_response.status_code in [200, 201]

COLORI_SQUADRE = ["FFC7CE", "C6EFCE", "FFEB9C", "BDD7EE", "F8CBAD", "D9E1F2", "E1D5E7", "FCE4D6"]

def assegna_giocatore_excel(nome_squadra, nome_giocatore, prezzo):
    if not os.path.exists(FILE_NAME):
        return False, "File Excel non trovato."
    wb = openpyxl.load_workbook(FILE_NAME)
    if "Rosa" not in wb.sheetnames or "Crediti" not in wb.sheetnames:
        return False, "Fogli Excel mancanti."

    ws_rosa = wb["Rosa"]
    ws_crediti = wb["Crediti"]

    colore_idx = abs(hash(nome_squadra)) % len(COLORI_SQUADRE)
    fill_color = openpyxl.styles.PatternFill(start_color=COLORI_SQUADRE[colore_idx], end_color=COLORI_SQUADRE[colore_idx], fill_type="solid")

    # 1. Foglio Rosa
    trovato = False
    for row in range(2, ws_rosa.max_row + 1):
        if str(ws_rosa.cell(row=row, column=1).value).strip().lower() == str(nome_giocatore).strip().lower():
            ws_rosa.cell(row=row, column=3, value=nome_squadra)
            ws_rosa.cell(row=row, column=4, value=prezzo)
            for col in range(1, 5):
                ws_rosa.cell(row=row, column=col).fill = fill_color
            trovato = True
            break

    if not trovato:
        wb.save(FILE_NAME)
        return False, f"Giocatore '{nome_giocatore}' non trovato nel foglio Rosa."

    # 2. Foglio Crediti (Colonna A = Squadra, Colonna B = Crediti rimasti)
    for row in range(2, ws_crediti.max_row + 1):
        if str(ws_crediti.cell(row=row, column=1).value).strip().lower() == str(nome_squadra).strip().lower():
            crediti_attuali = float(ws_crediti.cell(row=row, column=2).value or 0)
            ws_crediti.cell(row=row, column=2, value=crediti_attuali - float(prezzo))
            break

    wb.save(FILE_NAME)
    return True, f"Assegnazione: ⚽ {nome_giocatore} -> {nome_squadra} ({prezzo} crediti)."

def trova_squadra_da_telegram(user):
    """
    Cerca nel foglio 'Crediti' la squadra associata all'utente Telegram.
    Colonna A: Squadra
    Colonna B: Crediti rimasti
    Colonna C: UtenteTelegram (Nome visualizzato)
    Colonna D: Username (con o senza '@')
    """
    if not os.path.exists(FILE_NAME):
        return None
    wb = openpyxl.load_workbook(FILE_NAME, data_only=True)
    if "Crediti" not in wb.sheetnames:
        return None
    ws = wb["Crediti"]
    
    nome_utente_tg = str(user.first_name).strip().lower() if user.first_name else ""
    full_name_tg = f"{user.first_name} {user.last_name}".strip().lower() if user.first_name and user.last_name else nome_utente_tg
    username_tg = str(user.username).strip().lower().lstrip('@') if user.username else ""

    for row in range(2, ws.max_row + 1):
        squadra = ws.cell(row=row, column=1).value
        tg_nome_cell = str(ws.cell(row=row, column=3).value or "").strip().lower()
        username_cell = str(ws.cell(row=row, column=4).value or "").strip().lower().lstrip('@')

        match_nome = tg_nome_cell and (tg_nome_cell == nome_utente_tg or tg_nome_cell == full_name_tg)
        match_username = username_cell and username_tg and username_cell == username_tg

        if squadra and (match_nome or match_username):
            return str(squadra).strip()
            
    return None

def ottieni_crediti_rimasti(nome_squadra):
    if not os.path.exists(FILE_NAME):
        return 0
    wb = openpyxl.load_workbook(FILE_NAME, data_only=True)
    if "Crediti" not in wb.sheetnames:
        return 0
    ws = wb["Crediti"]
    for row in range(2, ws.max_row + 1):
        if str(ws.cell(row=row, column=1).value).strip().lower() == str(nome_squadra).strip().lower():
            try:
                return float(ws.cell(row=row, column=2).value or 0)
            except ValueError:
                return 0
    return 0

def conta_aste_attive_squadra(nome_squadra):
    count = 0
    if asta_attiva["giocatore"] and asta_attiva["squadra_leader"] == nome_squadra:
        count += 1
    return count

def verifica_taglio(nome_giocatore):
    if not os.path.exists(FILE_NAME):
        return 1
    wb = openpyxl.load_workbook(FILE_NAME, data_only=True)
    if "Tagli" not in wb.sheetnames:
        return 1
    ws = wb["Tagli"]
    for row in range(2, ws.max_row + 1):
        val = ws.cell(row=row, column=1).value
        if val and str(val).strip().lower() == str(nome_giocatore).strip().lower():
            try:
                return float(ws.cell(row=row, column=2).value or 1)
            except ValueError:
                return 1
    return 1

def leggi_impostazioni_mercato():
    """Legge dal foglio IMPOSTAZIONI data inizio, data fine e stato blocco."""
    if not os.path.exists(FILE_NAME):
        return "N/D", "N/D", False
    wb = openpyxl.load_workbook(FILE_NAME, data_only=True)
    if "IMPOSTAZIONI" not in wb.sheetnames and "Impostazioni" not in wb.sheetnames:
        return "N/D", "N/D", False
    
    sheet_name = "IMPOSTAZIONI" if "IMPOSTAZIONI" in wb.sheetnames else "Impostazioni"
    ws = wb[sheet_name]
    
    data_inizio = ws.cell(row=1, column=2).value
    data_fine = ws.cell(row=2, column=2).value
    bloccato_val = ws.cell(row=3, column=2).value
    
    bloccato = False
    if isinstance(bloccato_val, bool):
        bloccato = bloccato_val
    elif isinstance(bloccato_val, str):
        bloccato = bloccato_val.strip().lower() in ["true", "vero", "1", "si", "yes"]

    return str(data_inizio), str(data_fine), bloccato

def imposta_stato_mercato(stato_bool):
    """Aggiorna lo stato di mercato_bloccato nel foglio IMPOSTAZIONI."""
    if not os.path.exists(FILE_NAME):
        return False
    wb = openpyxl.load_workbook(FILE_NAME)
    sheet_name = "IMPOSTAZIONI" if "IMPOSTAZIONI" in wb.sheetnames else "Impostazioni"
    if sheet_name not in wb.sheetnames:
        return False
    ws = wb[sheet_name]
    ws.cell(row=3, column=2, value=stato_bool)
    wb.save(FILE_NAME)
    carica_su_github(FILE_NAME)
    return True

def get_lista_squadre():
    """Estrae l'elenco delle squadre dal foglio Crediti (colonna A)."""
    if not os.path.exists(FILE_NAME):
        return []
    wb = openpyxl.load_workbook(FILE_NAME, data_only=True)
    if "Crediti" not in wb.sheetnames:
        return []
    ws = wb["Crediti"]
    squadre = []
    for row in range(2, ws.max_row + 1):
        sq = ws.cell(row=row, column=1).value
        if sq and str(sq).strip():
            squadre.append(str(sq).strip())
    return squadre

# --- TIMER 24 ORE BACKGROUND ---
async def controlla_scadenza_asta(context: ContextTypes.DEFAULT_TYPE):
    global asta_attiva
    if asta_attiva["giocatore"] is None or asta_attiva["scadenza"] is None:
        return

    if datetime.now() >= asta_attiva["scadenza"]:
        giocatore = asta_attiva["giocatore"]
        squadra = asta_attiva["squadra_leader"]
        prezzo = asta_attiva["offerta_corrente"]

        if squadra:
            ok, messaggio = assegna_giocatore_excel(squadra, giocatore, prezzo)
            if ok:
                carica_su_github(FILE_NAME)
                if os.path.exists(FILE_NAME):
                    with open(FILE_NAME, "rb") as f:
                        await context.bot.send_document(
                            chat_id=ADMIN_ID, document=f,
                            caption=f"⏰ **Asta Chiusa in Automatico!**\n{messaggio}\n\nSincronizzato su GitHub 🚀"
                        )
        else:
            await context.bot.send_message(chat_id=ADMIN_ID, text=f"⏰ Asta scaduta per **{giocatore}** senza offerte.")

        asta_attiva = {"giocatore": None, "offerta_corrente": 1, "squadra_leader": None, "offerte_massime": {}, "scadenza": None}

# --- COMANDI TELEGRAM ---
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🤖 FantaBassaBot operativo! Usa /guida per vedere tutti i comandi.")

async def guida_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    testo = (
        "📖 **GUIDA AI COMANDI**\n\n"
        "🔹 `/asta [Nome Giocatore] [OffertaMax]` - Avvia un'asta (riconosce la squadra in automatico)[cite: 1].\n"
        "🔹 `/rilancia [Nome Giocatore] [OffertaMax]` - Invia un rilancio segreto su un'asta specifica[cite: 1].\n"
        "🔹 `/stato` - Mostra lo stato dell'asta corrente.\n"
        "🔹 `/rosa` - Mostra i pulsanti con le squadre per visualizzare le rose.\n"
        "🔹 `/liberi` - Mostra i 4 ruoli (P, D, C, A) per filtrare i giocatori liberi.\n"
        "🔹 `/mercato` - Mostra date e stato attuale del mercato (foglio IMPOSTAZIONI).\n"
        "🔹 `/annulla_asta` - Annulla l'asta attiva in corso (Admin).\n"
        "🔹 `/blocca_merc` - Blocca il mercato (Admin).\n"
        "🔹 `/sblocca_merc` - Sblocca il mercato (Admin).\n"
        "🔹 `/testasta` - Sincronizza Excel su GitHub (Admin)."
    )
    await update.message.reply_text(testo, parse_mode="Markdown")

async def test_asta_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Non autorizzato.")
        return
    await update.message.reply_text("🔄 Sincronizzazione file Excel in corso...")
    carica_su_github(FILE_NAME)
    if os.path.exists(FILE_NAME):
        with open(FILE_NAME, "rb") as f:
            await context.bot.send_document(chat_id=ADMIN_ID, document=f, caption="📊 File Excel attuale.")
    await update.message.reply_text("Test completato!")

async def asta_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global asta_attiva
    user = update.effective_user

    try:
        await update.message.delete()
    except Exception:
        pass

    _, _, bloccato = leggi_impostazioni_mercato()
    if bloccato:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Il mercato è attualmente BLOCCATO.")
        return

    squadra = trova_squadra_da_telegram(user)
    if not squadra:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Non risulti associato a nessuna squadra nel foglio 'Crediti' (controlla il tuo Nome o Username).")
        return

    if asta_attiva["giocatore"] is not None:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"⚠️ C'è già un'asta attiva per **{asta_attiva['giocatore']}**!")
        return

    args = context.args
    if len(args) < 2:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ Uso corretto: `/asta [Nome Giocatore] [Offerta Massima]`", parse_mode="Markdown")
        return

    try:
        offerta_massima = float(args[-1])
        giocatore = " ".join(args[:-1])
    except ValueError:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ Formato non valido. Usa: `/asta [Nome Giocatore] [Offerta Massima]`", parse_mode="Markdown")
        return

    crediti_rimasti = ottieni_crediti_rimasti(squadra)
    if offerta_massima > (crediti_rimasti - 1):
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Offerta non valida per {squadra}: supera il limite consentito (-1). Crediti attuali: {crediti_rimasti}.")
        return

    if conta_aste_attive_squadra(squadra) >= 2:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ {squadra} ha raggiunto il limite di 2 aste in contemporanea!")
        return

    valore_minimo = verifica_taglio(giocatore)
    offerta_iniziale = max(1, valore_minimo)

    if offerta_massima < offerta_iniziale:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ L'offerta massima deve essere almeno pari a {offerta_iniziale}.")
        return

    asta_attiva["giocatore"] = giocatore
    asta_attiva["offerta_corrente"] = offerta_iniziale
    asta_attiva["squadra_leader"] = squadra
    asta_attiva["offerte_massime"] = {squadra: offerta_massima}
    asta_attiva["scadenza"] = datetime.now() + timedelta(hours=24)

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"🚀 **NUOVA ASTA APERTA!**\n"
             f"⚽ Giocatore: **{giocatore}**\n"
             f"💰 Offerta corrente: **{offerta_iniziale}** crediti\n"
             f"🛡️ In testa: **{squadra}**\n"
             f"⏳ Scadenza timer: 24h\n\n"
             f"*(L'offerta massima inserita da {squadra} è protetta e nascosta)*",
        parse_mode="Markdown"
    )

async def rilancia_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global asta_attiva
    user = update.effective_user

    try:
        await update.message.delete()
    except Exception:
        pass

    _, _, bloccato = leggi_impostazioni_mercato()
    if bloccato:
        return

    if asta_attiva["giocatore"] is None:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Al momento non c'è nessuna asta attiva.")
        return

    squadra = trova_squadra_da_telegram(user)
    if not squadra:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ Non risulti associato a nessuna squadra nel foglio 'Crediti'.")
        return

    args = context.args
    if len(args) < 2:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ Uso corretto: `/rilancia [Nome Giocatore] [Offerta Massima]`", parse_mode="Markdown")
        return

    try:
        nuova_offerta_max = float(args[-1])
        giocatore_richiesto = " ".join(args[:-1]).strip().lower()
    except ValueError:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ Inserisci un valore numerico valido per il rilancio.")
        return

    giocatore_attuale = str(asta_attiva["giocatore"]).strip().lower()
    if giocatore_richiesto != giocatore_attuale:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ L'asta attiva al momento è per **{asta_attiva['giocatore']}**, non per '{' '.join(args[:-1])}'.")
        return

    giocatore = asta_attiva["giocatore"]

    crediti_rimasti = ottieni_crediti_rimasti(squadra)
    if nuova_offerta_max > (crediti_rimasti - 1):
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ {squadra}: Offerta superiore ai crediti disponibili (-1). Crediti attuali: {crediti_rimasti}.")
        return

    if asta_attiva["squadra_leader"] != squadra and conta_aste_attive_squadra(squadra) >= 2:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ {squadra} ha raggiunto il limite di 2 aste in contemporanea!")
        return

    asta_attiva["offerte_massime"][squadra] = nuova_offerta_max
    leader_attuale = asta_attiva["squadra_leader"]
    max_leader = asta_attiva["offerte_massime"].get(leader_attuale, 1)

    if squadra == leader_attuale:
        asta_attiva["scadenza"] = datetime.now() + timedelta(hours=24)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"📈 {squadra} ha aggiornato la propria offerta massima per {giocatore}. Timer 24h rinnovato!")
        return

    if nuova_offerta_max > max_leader:
        nuova_offerta_corrente = max_leader + 1
        if nuova_offerta_corrente > nuova_offerta_max:
            nuova_offerta_corrente = nuova_offerta_max

        asta_attiva["squadra_leader"] = squadra
        asta_attiva["offerta_corrente"] = nuova_offerta_corrente
        asta_attiva["scadenza"] = datetime.now() + timedelta(hours=24)

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"🔥 **RILANCIO AUTOMATICO!**\n"
                 f"⚽ Giocatore: {giocatore}\n"
                 f"🛡️ Nuova squadra in testa: **{squadra}**\n"
                 f"💰 Offerta corrente visibile: **{nuova_offerta_corrente}** crediti\n"
                 f"⏱️ Timer 24h azzerato e fatto ripartire!"
        )
    else:
        nuova_offerta_corrente = nuova_offerta_max + 1
        if nuova_offerta_corrente > max_leader:
            nuova_offerta_corrente = max_leader

        asta_attiva["offerta_corrente"] = nuova_offerta_corrente
        asta_attiva["scadenza"] = datetime.now() + timedelta(hours=24)

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"🤖 **RILANCIO AUTOMATICO DEL SISTEMA!**\n"
                 f"Per {giocatore}, la squadra **{leader_attuale}** copre il rilancio.\n"
                 f"💰 Offerta corrente visibile: **{nuova_offerta_corrente}** crediti\n"
                 f"⏱️ Timer 24h rinnovato!"
        )

async def stato_asta_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if asta_attiva["giocatore"] is None:
        await update.message.reply_text("Nessuna asta in corso.")
        return
    rimanente = asta_attiva["scadenza"] - datetime.now()
    ore = int(rimanente.total_seconds() // 3600)
    minuti = int((rimanente.total_seconds() % 3600) // 60)
    await update.message.reply_text(
        f"📊 **STATO ASTA**\n"
        f"⚽ Giocatore: {asta_attiva['giocatore']}\n"
        f"💰 Prezzo visibile: {asta_attiva['offerta_corrente']} crediti\n"
        f"🛡️ In testa: {asta_attiva['squadra_leader']}\n"
        f"⏳ Tempo residuo: ~{ore}h {minuti}m"
    )

# --- GESTIONE INTERATTIVA /ROSA (Pulsanti Squadre) ---
async def rosa_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    squadre = get_lista_squadre()
    if not squadre:
        await update.message.reply_text("Nessuna squadra trovata nel file Excel.")
        return
    
    keyboard = []
    row = []
    for sq in squadre:
        row.append(InlineKeyboardButton(sq, callback_data=f"rosa_{sq}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("🛡️ **Seleziona la squadra** di cui vuoi visualizzare la rosa:", reply_markup=reply_markup, parse_mode="Markdown")

# --- GESTIONE INTERATTIVA /LIBERI (Pulsanti 4 Ruoli) ---
async def liberi_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🟢 Portieri (P)", callback_data="liberi_P"), InlineKeyboardButton("🔵 Difensori (D)", callback_data="liberi_D")],
        [InlineKeyboardButton("🟡 Centrocampisti (C)", callback_data="liberi_C"), InlineKeyboardButton("🔴 Attaccanti (A)", callback_data="liberi_A")],
        [InlineKeyboardButton("📋 Mostra Tutti", callback_data="liberi_TUTTI")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("📋 **Seleziona il ruolo** per visualizzare i giocatori liberi:", reply_markup=reply_markup, parse_mode="Markdown")

# --- CALLBACK QUERY HANDLER (Gestione dei click sui pulsanti) ---
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("rosa_"):
        squadra_cercata = data.replace("rosa_", "").strip()
        if not os.path.exists(FILE_NAME):
            await query.edit_message_text("File Excel non trovato.")
            return

        wb = openpyxl.load_workbook(FILE_NAME, data_only=True)
        if "Rosa" not in wb.sheetnames:
            await query.edit_message_text("Foglio Rosa non trovato.")
            return

        ws = wb["Rosa"]
        giocatori = []
        for row in range(2, ws.max_row + 1):
            sq_cell = ws.cell(row=row, column=3).value
            if sq_cell and str(sq_cell).strip().lower() == squadra_cercata.lower():
                nome = ws.cell(row=row, column=1).value
                ruolo = ws.cell(row=row, column=2).value
                prezzo = ws.cell(row=row, column=4).value
                giocatori.append(f"• {nome} ({ruolo}) - {prezzo} cr.")

        if not giocatori:
            await query.edit_message_text(f"📭 Nessun giocatore trovato per la squadra '{squadra_cercata}'.")
        else:
            testo = f"🛡️ **ROSA: {squadra_cercata.upper()}**\n" + "\n".join(giocatori)
            await query.edit_message_text(testo, parse_mode="Markdown")

    elif data.startswith("liberi_"):
        ruolo_cercato = data.replace("liberi_", "").strip()
        if ruolo_cercato == "TUTTI":
            ruolo_cercato = None

        if not os.path.exists(FILE_NAME):
            await query.edit_message_text("File Excel non trovato.")
            return

        wb = openpyxl.load_workbook(FILE_NAME, data_only=True)
        if "Rosa" not in wb.sheetnames:
            await query.edit_message_text("Foglio Rosa non trovato.")
            return

        ws = wb["Rosa"]
        liberi = []
        for row in range(2, ws.max_row + 1):
            sq_cell = ws.cell(row=row, column=3).value
            if not sq_cell or str(sq_cell).strip() == "":
                nome = ws.cell(row=row, column=1).value
                ruolo = str(ws.cell(row=row, column=2).value or "").strip().upper()
                quotazione = ws.cell(row=row, column=4).value

                if ruolo_cercato and ruolo != ruolo_cercato:
                    continue

                liberi.append(f"• {nome} ({ruolo}) - Quot: {quotazione}")
                if len(liberi) >= 40:
                    liberi.append("\n*(Lista troncata ai primi 40 risultati)*")
                    break

        if not liberi:
            filter_text = f" per il ruolo {ruolo_cercato}" if ruolo_cercato else ""
            await query.edit_message_text(f"📭 Nessun giocatore libero trovato{filter_text}.")
        else:
            title = f"📋 **GIOCATORI LIBERI**" + (f" ({ruolo_cercato})" if ruolo_cercato else "")
            await query.edit_message_text(title + "\n" + "\n".join(liberi), parse_mode="Markdown")

# --- COMANDI MERCATO E ANNULLAMENTO ASTA ---
async def mercato_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data_inizio, data_fine, bloccato = leggi_impostazioni_mercato()
    stato = "🔴 BLOCCATO (Chiuso)" if bloccato else "🟢 APERTO"
    testo = (
        f"📊 **STATO DEL MERCATO**\n\n"
        f"📅 **Inizio:** {data_inizio}\n"
        f"📅 **Fine:** {data_fine}\n"
        f"🔒 **Stato:** {stato}"
    )
    await update.message.reply_text(testo, parse_mode="Markdown")

async def annulla_asta_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global asta_attiva
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Non autorizzato.")
        return
    
    if asta_attiva["giocatore"] is None:
        await update.message.reply_text("⚠️ Nessuna asta attiva da annullare.")
        return

    giocatore_annullato = asta_attiva["giocatore"]
    asta_attiva = {"giocatore": None, "offerta_corrente": 1, "squadra_leader": None, "offerte_massime": {}, "scadenza": None}
    
    await update.message.reply_text(f"❌ L'asta in corso per **{giocatore_annullato}** è stata annullata dall'amministratore.", parse_mode="Markdown")

async def blocca_merc_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Non autorizzato.")
        return
    imposta_stato_mercato(True)
    await update.message.reply_text("🔒 Mercato bloccato con successo! (Aggiornato su Excel e GitHub)")

async def sblocca_merc_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Non autorizzato.")
        return
    imposta_stato_mercato(False)
    await update.message.reply_text("🔓 Mercato sbloccato con successo! (Aggiornato su Excel e GitHub)")

def main():
    if not TELEGRAM_TOKEN:
        return
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("guida", guida_command))
    app.add_handler(CommandHandler("testasta", test_asta_command))
    app.add_handler(CommandHandler("asta", asta_command))
    app.add_handler(CommandHandler("rilancia", rilancia_command))
    app.add_handler(CommandHandler("stato", stato_asta_command))
    app.add_handler(CommandHandler("rosa", rosa_command))
    app.add_handler(CommandHandler("liberi", liberi_command))
    app.add_handler(CommandHandler("mercato", mercato_command))
    app.add_handler(CommandHandler("annulla_asta", annulla_asta_command))
    app.add_handler(CommandHandler("blocca_merc", blocca_merc_command))
    app.add_handler(CommandHandler("sblocca_merc", sblocca_merc_command))
    app.add_handler(CallbackQueryHandler(button_callback))

    app.job_queue.run_repeating(controlla_scadenza_asta, interval=30, first=10)
    app.run_polling()

if __name__ == "__main__":
    server_thread = threading.Thread(target=run_health_check_server, daemon=True)
    server_thread.start()
    main()