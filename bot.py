import os
import logging
import base64
import threading
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
import requests
import openpyxl
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler

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

# STRUTTURA ASTA AVANZATA (Gestione offerte massime e rilanci automatici)
# Tracciamo tutte le offerte massime inserite per l'asta attiva: {squadra: offerta_massima}
asta_attiva = {
    "giocatore": None,
    "offerta_corrente": 1,        # Prezzo visibile a tutti (parte sempre da 1)
    "squadra_leader": None,       # Chi è in testa al momento
    "offerte_massime": {},        # { "Nome Squadra": max_offerto }
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
    payload = {"message": "Aggiornamento automatico file Excel post-asta", "content": content_base64}
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
    # Conta quante offerte massime ha attive questa squadra in questo momento (massimo 2)
    count = 0
    if asta_attiva["giocatore"] and asta_attiva["squadra_leader"] == nome_squadra:
        count += 1
    return count

def verifica_taglio(nome_giocatore):
    # Controlla se il giocatore è nel foglio Tagli e restituisce il valore minimo di taglio (altrimenti 1)
    if not os.path.exists(FILE_NAME):
        return 1
    wb = openpyxl.load_workbook(FILE_NAME, data_only=True)
    if "Tagli" not in wb.sheetnames:
        return 1
    ws = wb["Tagli"]
    for row in range(2, ws.max_row + 1):
        # Ipotizziamo colonna A = Giocatore, Colonna B = Valore Taglio (o simile)
        val = ws.cell(row=row, column=1).value
        if val and str(val).strip().lower() == str(nome_giocatore).strip().lower():
            try:
                return float(ws.cell(row=row, column=2).value or 1)
            except ValueError:
                return 1
    return 1

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
    await update.message.reply_text("🤖 FantaBassaBot operativo con aste segrete e rilanci automatici!")

async def asta_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global asta_attiva
    # Cancella subito il messaggio dell'utente per non mostrare l'offerta massima
    try:
        await update.message.delete()
    except Exception:
        pass

    if update.effective_user.id != ADMIN_ID:
        return

    if asta_attiva["giocatore"] is not None:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"⚠️ C'è già un'asta attiva per **{asta_attiva['giocatore']}**!")
        return

    args = context.args
    if len(args) < 3:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="⚠️ Uso corretto: `/asta [Nome Squadra] [Nome Giocatore] [Offerta Massima]`", parse_mode="Markdown")
        return

    try:
        offerta_massima = float(args[-1])
        giocatore = args[-2]
        squadra = " ".join(args[:-2])
    except ValueError:
        return

    # Controlli vincoli crediti e slot
    crediti_rimasti = ottieni_crediti_rimasti(squadra)
    if offerta_massima > (crediti_rimasti - 1):
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ Offerta non valida per {squadra}: supera il limite consentito (Crediti rimasti: {crediti_rimasti}, max offerta: {crediti_rimasti - 1}).")
        return

    if conta_aste_attive_squadra(squadra) >= 2:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ {squadra} ha già il limite massimo di 2 aste in contemporanea!")
        return

    valore_minimo = verifica_taglio(giocatore)
    offerta_iniziale = max(1, valore_minimo)

    if offerta_massima < offerta_iniziale:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ L'offerta massima deve essere almeno pari al minimo richiesto ({offerta_iniziale}).")
        return

    # Avvia l'asta partendo da 1 (o dal valore di taglio) e registrando l'offerta massima in segreto
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
             f"*(L'offerta massima iniziale è protetta e nascosta)*",
        parse_mode="Markdown"
    )

async def offerta_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global asta_attiva
    # Cancella il messaggio dell'offerta per mantenere la riservatezza delle cifre massime
    try:
        await update.message.delete()
    except Exception:
        pass

    if asta_attiva["giocatore"] is None:
        return

    args = context.args
    if len(args) < 2:
        return

    try:
        nuova_offerta_max = float(args[-1])
        squadra = " ".join(args[:-1])
    except ValueError:
        return

    giocatore = asta_attiva["giocatore"]

    # Controlli crediti e slot
    crediti_rimasti = ottieni_crediti_rimasti(squadra)
    if nuova_offerta_max > (crediti_rimasti - 1):
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ {squadra}: Offerta massima superiore ai crediti disponibili (-1).")
        return

    if asta_attiva["squadra_leader"] != squadra and conta_aste_attive_squadra(squadra) >= 2:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"❌ {squadra} ha già raggiunto il limite di 2 aste in contemporanea!")
        return

    # Registra o aggiorna l'offerta massima segreta
    asta_attiva["offerte_massime"][squadra] = nuova_offerta_max
    leader_attuale = asta_attiva["squadra_leader"]
    max_leader = asta_attiva["offerte_massime"].get(leader_attuale, 1)

    # Logica di rilancio automatico stile eBay
    if squadra == leader_attuale:
        # Se chi è già in testa alza la sua offerta massima
        asta_attiva["scadenza"] = datetime.now() + timedelta(hours=24)
        await context.bot.send_message(chat_id=update.effective_chat.id, text=f"📈 {squadra} ha aggiornato la propria offerta massima per {giocatore}. Timer 24h rinnovato!")
        return

    # Se un'altra squadra fa un'offerta
    if nuova_offerta_max > max_leader:
        # Il nuovo sfidante supera il vecchio leader
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
        # La vecchia squadra in testa aveva un'offerta massima superiore, rilancia automaticamente per lei alzata di 1
        nuova_offerta_corrente = nuova_offerta_max + 1
        if nuova_offerta_corrente > max_leader:
            nuova_offerta_corrente = max_leader

        asta_attiva["offerta_corrente"] = nuova_offerta_corrente
        asta_attiva["scadenza"] = datetime.now() + timedelta(hours=24)

        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"🤖 **RILANCIO AUTOMATICO DEL SISTEMA!**\n"
                 f"Per {giocatore}, la squadra **{leader_attuale}** copre il rilancio.\n"
                 f"💰 Nuova offerta corrente visibile: **{nuova_offerta_corrente}** crediti\n"
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

def main():
    if not TELEGRAM_TOKEN:
        return
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("asta", asta_command))
    app.add_handler(CommandHandler("offerta", offerta_command))
    app.add_handler(CommandHandler("stato", stato_asta_command))

    app.job_queue.run_repeating(controlla_scadenza_asta, interval=30, first=10)
    app.run_polling()

if __name__ == "__main__":
    server_thread = threading.Thread(target=run_health_check_server, daemon=True)
    server_thread.start()
    main()