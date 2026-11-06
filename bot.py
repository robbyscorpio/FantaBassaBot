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

# --- 2. STATO MEMORIA, ASTE E DATE MERCATO ---
aste = {}  # { "nomegiocatore": { ... } }
TIMER_ASTA_ORE = 24

# Impostazioni di default (Mercato sempre aperto se non configurato)
data_inizio_mercato = None
data_fine_mercato = None

# --- CONTROLLO AMMINISTRATORE ---
async def verifica_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if update.effective_chat.type == "private":
        return True
    
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    member = await context.bot.get_chat_member(chat_id, user_id)
    
    if member.status in ["creator", "administrator"]:
        return True
    
    await update.message.reply_text("⛔ **Comando riservato agli amministratori del gruppo.**")
    return False

# --- 3. GESTORE TIMER ASTA ---
async def timer_asta(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    giocatore_key = job.data["giocatore_key"]
    chat_id = job.data["chat_id"]

    if giocatore_key in aste:
        info = aste[giocatore_key]
        nome = info["nome_originale"]
        vincitore = info["ultimo_offrente"] or info["creatore"]
        offerta = info["offerta_attuale"]

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
        "Usa `/guida` per consultare le istruzioni e le date del mercato.",
        parse_mode="Markdown"
    )

async def set_mercato(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await verifica_admin(update, context):
        return

    # Formato atteso: /setmercato GG/MM/AAAA HH:MM GG/MM/AAAA HH:MM
    if len(context.args) < 4:
        await update.message.reply_text(
            "⚠️ **Formato Errato!** Usa:\n"
            "`/setmercato DataInizio OraInizio DataFine OraFine`\n\n"
            "Esempio:\n`/setmercato 01/11/2026 08:00 30/11/2026 23:59`",
            parse_mode="Markdown"
        )
        return

    str_inizio = f"{context.args[0]} {context.args[1]}"
    str_fine = f"{context.args[2]} {context.args[3]}"

    try:
        global data_inizio_mercato, data_fine_mercato
        data_inizio_mercato = datetime.strptime(str_inizio, "%d/%m/%Y %H:%M")
        data_fine_mercato = datetime.strptime(str_fine, "%d/%m/%Y %H:%M")

        await update.message.reply_text(
            f"📅 **PERIODO MERCATO IMPOSTATO!**\n\n"
            f"🟢 **Apertura:** {data_inizio_mercato.strftime('%d/%m/%Y alle %H:%M')}\n"
            f"🔴 **Chiusura:** {data_fine_mercato.strftime('%d/%m/%Y alle %H:%M')}",
            parse_mode="Markdown"
        )
    except ValueError:
        await update.message.reply_text("⚠️ Data o ora non valide. Usa il formato `GG/MM/AAAA HH:MM` (es. `25/11/2026 15:30`).", parse_mode="Markdown")

async def guida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if data_inizio_mercato and data_fine_mercato:
        info_mercato = (
            f"📅 **PERIODO MERCATO:**\n"
            f"• **Inizio:** {data_inizio_mercato.strftime('%d/%m/%Y %H:%M')}\n"
            f"• **Fine Tassativa:** {data_fine_mercato.strftime('%d/%m/%Y %H:%M')}\n\n"
        )
    else:
        info_mercato = "📅 **PERIODO MERCATO:** Non ancora definito dagli amministratori.\n\n"

    testo_guida = (
        "📖 **GUIDA E REGOLAMENTO ASTE**\n\n"
        f"{info_mercato}"
        "🟢 **Avviare un'asta:**\n"
        "`/a NomeGiocatore OffertaIniziale`\n"
        "_(Es: `/a Dybala 10` - Fai partire l'asta di 24 ore)_\n\n"
        "⚡ **Rilanciare:**\n"
        "`/r NomeGiocatore NuovaOfferta`\n"
        "_(Es: `/r Dybala 12` - Resetta il timer a 24 ore per quel giocatore)_\n\n"
        "📊 **Stato Aste:**\n"
        "`/stato` - Mostra le aste attive ed il tempo rimanente.\n\n"
        "⚙️ **Comandi Admin:**\n"
        "`/setmercato DataInizio OraInizio DataFine OraFine`\n"
        "_(Es: `/setmercato 01/11/2026 08:00 30/11/2026 23:59`)_"
    )
    await update.message.reply_text(testo_guida, parse_mode="Markdown")

async def nuova_asta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ora_attuale = datetime.now()

    # Controlli temporali di apertura/chiusura
    if data_inizio_mercato and ora_attuale < data_inizio_mercato:
        await update.message.reply_text(f"⏳ **Mercato non ancora aperto!** Apertura prevista per il {data_inizio_mercato.strftime('%d/%m/%Y alle %H:%M')}.")
        return

    if data_fine_mercato:
        if ora_attuale >= data_fine_mercato:
            await update.message.reply_text("🔴 **Il mercato è chiuso!** Non è più possibile aprire nuove aste.")
            return

        # Blocco aste se mancano meno di 24 ore alla fine del mercato
        if ora_attuale + timedelta(hours=TIMER_ASTA_ORE) > data_fine_mercato:
            await update.message.reply_text(
                f"⚠️ **Tempo insufficiente!** Il mercato chiude il {data_fine_mercato.strftime('%d/%m/%Y alle %H:%M')}.\n"
                f"Non ci sono le 24 ore garantite necessarie per aprire una nuova asta."
            )
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
    scadenza = ora_attuale + timedelta(hours=TIMER_ASTA_ORE)

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
    ora_attuale = datetime.now()
    if data_fine_mercato and ora_attuale >= data_fine_mercato:
        await update.message.reply_text("🔴 **Il mercato è chiuso!** Non è più possibile fare rilanci.")
        return

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

    info_asta["job"].schedule_removal()
    nuovo_job = context.job_queue.run_once(
        timer_asta,
        when=timedelta(hours=TIMER_ASTA_ORE),
        data={"giocatore_key": giocatore_key, "chat_id": update.effective_chat.id},
        name=giocatore_key
    )

    info_asta["offerta_attuale"] = nuova_offerta
    info_asta["ultimo_offrente"] = utente
    info_asta["scadenza"] = ora_attuale + timedelta(hours=TIMER_ASTA_ORE)
    info_asta["job"] = nuovo_job

    await update.message.reply_text(
        f"🔥 **RILANCIO!**\n\n"
        f"⚽ **Giocatore:** {info_asta['nome_originale']}\n"
        f"💰 **Nuova Offerta:** {nuova_offerta} crediti\n"
        f"👤 **Offerto da:** @{utente}\n"
        f"⏱️ **Timer resettato:** 24 ore rimanenti!"
    )

async def stato_aste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    messaggio = ""
    if data_inizio_mercato and data_fine_mercato:
        messaggio += f"📅 **Mercato:** {data_inizio_mercato.strftime('%d/%m %H:%M')} ➔ {data_fine_mercato.strftime('%d/%m %H:%M')}\n\n"

    if not aste:
        messaggio += "ℹ️ Al momento non ci sono aste in corso."
        await update.message.reply_text(messaggio, parse_mode="Markdown")
        return

    messaggio += "📋 **ASTE ATTIVAMENTE IN CORSO:**\n\n"
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

    app.add_handler(CommandHandler(["start"], start))
    app.add_handler(CommandHandler(["guida", "help"], guida))
    app.add_handler(CommandHandler(["setmercato"], set_mercato))
    app.add_handler(CommandHandler(["a", "asta"], nuova_asta))
    app.add_handler(CommandHandler(["r", "rilancio"], rilancio))
    app.add_handler(CommandHandler(["stato"], stato_aste))

    print("Bot Fantacalcio Asta in esecuzione...")
    app.run_polling()

if __name__ == "__main__":
    main()