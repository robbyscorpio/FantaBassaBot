import os
import asyncio
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

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

# --- 2. MEMORIA ASTE E MERCATO ---
mercato_aperto = True  # Di default il mercato parte aperto
aste = {}  # Dizionario per gestire le aste simultanee: { "nomegiocatore": { ... } }
TIMER_ASTA_ORE = 24

# --- 3. GESTORE TIMER ASTA ---
async def timer_asta(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    giocatore_key = job.data["giocatore_key"]
    chat_id = job.data["chat_id"]

    if giocatore_key in aste:
        info = aste[giocatore_key]
        nome = info["nome_originale"]
        vincitore = info["ultimo_offrente"]
        offerta = info["offerta_attuale"]

        # Se nessuno ha offerto oltre la base
        if vincitore is None:
            vincitore = info["creatore"]

        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🔴 **ASTA CHIUSA!** 🔴\n\n"
                 f"⚽ **Giocatore:** {nome}\n"
                 f"🏆 **Aggiudicato a:** @{vincitore}\n"
                 f"💰 **Prezzo finale:** {offerta} crediti"
        )
        del aste[giocatore_key]

# --- 4. COMANDI DEL BOT ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Benvenuto nel Bot Aste Fantacalcio!\n"
        "Usa /guida o /help per vedere le istruzioni e la lista dei comandi."
    )

async def guida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    testo_guida = (
        "📖 **GUIDA E COMANDI MERCATO**\n\n"
        "🟢 **Avviare un'asta:**\n"
        "`/a NomeGiocatore OffertaIniziale`\n"
        "_(Es: `/a Dybala 10` - Fai partire un'asta di 24 ore)_\n\n"
        "⚡ **Rilanciare:**\n"
        "`/r NomeGiocatore NuovaOfferta`\n"
        "_(Es: `/r Dybala 12` - Resetta il timer a 24 ore per quel giocatore)_\n\n"
        "📊 **Stato Aste:**\n"
        "`/stato` - Mostra tutte le aste attive e il tempo rimanente.\n\n"
        "⚙️ **Comandi Admin/Mercato:**\n"
        "`/aprimercato` - Apre la sessione d'asta.\n"
        "`/chiudomercato` - Blocca l'avvio di nuove aste."
    )
    await update.message.reply_text(testo_guida, parse_mode="Markdown")

async def apri_mercato(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global mercato_aperto
    mercato_aperto = True
    await update.message.reply_text("🟢 **Il mercato delle aste è ufficialmente APERTO!**")

async def chiudi_mercato(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global mercato_aperto
    mercato_aperto = False
    await update.message.reply_text("🔴 **Il mercato è CHIUSO.** Non è possibile aprire nuove aste.")

async def nuova_asta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not mercato_aperto:
        await update.message.reply_text("❌ Il mercato è attualmente chiuso! Non puoi aprire nuove aste.")
        return

    if len(context.args) < 2:
        await update.message.reply_text("⚠️ Formato errato. Usa: `/a NomeGiocatore OffertaBase` (es: `/a Dybala 10`)", parse_mode="Markdown")
        return

    nome_giocatore = context.args[0]
    giocatore_key = nome_giocatore.lower()

    try:
        offerta_base = int(context.args[1])
    except ValueError:
        await update.message.reply_text("⚠️ L'offerta deve essere un numero intero!")
        return

    if giocatore_key in aste:
        await update.message.reply_text(f"⚠️ C'è già un'asta in corso per **{nome_giocatore}**! Usa `/r {nome_giocatore} {offerta_base}` per rilanciare.", parse_mode="Markdown")
        return

    utente = update.effective_user.username or update.effective_user.first_name
    scadenza = datetime.now() + timedelta(hours=TIMER_ASTA_ORE)

    # Programma la chiusura tra 24 ore
    job = context.job_queue.run_once(
        timer_asta,
        when=timedelta(hours=TIMER_ASTA_ORE),
        data={"giocatore_key": giocatore_key, "chat_id": update.effective_chat.id},
        name=giocatore_key
    )

    aste[giocatore_key] = {
        "nome_originale": nome_giocatore,
        "offerta_attuale": offerta_base,
        "ultimo_offrente": utente,
        "creatore": utente,
        "scadenza": scadenza,
        "job": job
    }

    await update.message.reply_text(
        f"📣 **NUOVA ASTA APERTA!**\n\n"
        f"⚽ **Giocatore:** {nome_giocatore}\n"
        f"💰 **Base d'asta:** {offerta_base} crediti\n"
        f"👤 **Offerta di:** @{utente}\n"
        f"⏳ **Scadenza:** 24 ore da adesso (senza ulteriori rilanci)."
    )

async def rilancio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("⚠️ Formato errato. Usa: `/r NomeGiocatore NuovaOfferta` (es: `/r Dybala 12`)", parse_mode="Markdown")
        return

    nome_giocatore = context.args[0]
    giocatore_key = nome_giocatore.lower()

    try:
        nuova_offerta = int(context.args[1])
    except ValueError:
        await update.message.reply_text("⚠️ L'offerta deve essere un numero intero!")
        return

    if giocatore_key not in aste:
        await update.message.reply_text(f"❌ Non c'è nessuna asta attiva per **{nome_giocatore}**. Per aprirla usa `/a {nome_giocatore} {nuova_offerta}`.", parse_mode="Markdown")
        return

    info_asta = aste[giocatore_key]

    if nuova_offerta <= info_asta["offerta_attuale"]:
        await update.message.reply_text(f"⚠️ L'offerta deve essere superiore all'offerta attuale ({info_asta['offerta_attuale']} crediti)!")
        return

    utente = update.effective_user.username or update.effective_user.first_name

    # Cancella il vecchio timer e ne programma uno nuovo da 24 ore
    info_asta["job"].schedule_removal()
    nuovo_job = context.job_queue.run_once(
        timer_asta,
        when=timedelta(hours=TIMER_ASTA_ORE),
        data={"giocatore_key": giocatore_key, "chat_id": update.effective_chat.id},
        name=giocatore_key
    )

    info_asta["offerta_attuale"] = nuova_offerta
    info_asta["ultimo_offrente"] = utente
    info_asta["scadenza"] = datetime.now() + timedelta(hours=TIMER_ASTA_ORE)
    info_asta["job"] = nuovo_job

    await update.message.reply_text(
        f"🔥 **RILANCIO!**\n\n"
        f"⚽ **Giocatore:** {info_asta['nome_originale']}\n"
        f"💰 **Nuova Offerta:** {nuova_offerta} crediti\n"
        f"👤 **Offerto da:** @{utente}\n"
        f"⏱️ **Timer resettato:** 24 ore rimanenti!"
    )

async def stato_aste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not aste:
        await update.message.reply_text("ℹ️ Al momento non ci sono aste in corso.")
        return

    messaggio = "📋 **ASTE ATTIVAMENTE IN CORSO:**\n\n"
    ora_attuale = datetime.now()

    for key, info in aste.items():
        tempo_rimanente = info["scadenza"] - ora_attuale
        ore, remainder = divmod(tempo_rimanente.seconds, 3600)
        minuti, _ = divmod(remainder, 60)

        messaggio += (
            f"⚽ **{info['nome_originale']}**\n"
            f"  • Offerta: {info['offerta_attuale']} crediti (@{info['ultimo_offrente']})\n"
            f"  • Tempo rimasto: ~{ore}h {minuti}m\n\n"
        )

    await update.message.reply_text(messaggio, parse_mode="Markdown")

# --- 5. MAIN ---
def main():
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        print("ERRORE: Variabile TELEGRAM_TOKEN non trovata.")
        return

    app = Application.builder().token(token).build()

    # Registrazione Comandi
    app.add_handler(CommandHandler(["start"], start))
    app.add_handler(CommandHandler(["guida", "help"], guida))
    app.add_handler(CommandHandler(["aprimercato"], apri_mercato))
    app.add_handler(CommandHandler(["chiudomercato"], chiudi_mercato))
    app.add_handler(CommandHandler(["a", "asta"], nuova_asta))
    app.add_handler(CommandHandler(["r", "rilancio"], rilancio))
    app.add_handler(CommandHandler(["stato"], stato_aste))

    print("Bot Fantacalcio Asta in esecuzione...")
    app.run_polling()

if __name__ == "__main__":
    main()