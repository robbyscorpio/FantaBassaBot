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

# --- 2. STATO MEMORIA E ASTE ---
aste = {}  # { "nomegiocatore": { ... } }
TIMER_ASTA_ORE = 24

data_inizio_mercato = None
data_fine_mercato = None

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
    job = context.job
    giocatore_key = job.data["giocatore_key"]
    chat_id = job.data["chat_id"]

    if giocatore_key in aste:
        info = aste[giocatore_key]
        nome = info["nome_originale"]
        vincitore = info["ultimo_offrente"]
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
        await update.message.reply_text("⚠️ Data o ora non valide. Usa il formato `GG/MM/AAAA HH:MM`.", parse_mode="Markdown")

async def guida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    info_mercato = "📅 **PERIODO MERCATO:** Attivo\n\n"
    if data_inizio_mercato and data_fine_mercato:
        info_mercato = (
            f"📅 **PERIODO MERCATO:**\n"
            f"• **Inizio:** {data_inizio_mercato.strftime('%d/%m/%Y %H:%M')}\n"
            f"• **Fine:** {data_fine_mercato.strftime('%d/%m/%Y %H:%M')}\n\n"
        )

    testo_guida = (
        "📖 **GUIDA E REGOLAMENTO ASTE (Stile eBay)**\n\n"
        f"{info_mercato}"
        "🟢 **Avviare un'asta (il tuo messaggio verrà eliminato automaticamente):**\n"
        "`.new NomeGiocatore QuotaMassima`\n"
        "_(Es: `.new totti 10` - L'asta parte pubblicamente a 1 credito e il bot difenderà la tua quota fino a 10)_\n\n"
        "⚡ **Rilanciare (il tuo messaggio verrà eliminato automaticamente):**\n"
        "`.ril NomeGiocatore NuovaOfferta`\n"
        "_(Es: `.ril totti 12`)_\n\n"
        "📊 **Stato Aste:**\n"
        "`/stato` - Mostra le aste attive ed il tempo rimanente."
    )
    await update.message.reply_text(testo_guida, parse_mode="Markdown")

async def nuova_asta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Cancella il messaggio dell'utente per mantenere la quota segreta
    if update.effective_chat.type != "private":
        try:
            await update.message.delete()
        except Exception:
            pass

    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "⚠️ **Formato errato!**\n"
            "Devi inserire il nome del giocatore seguito dalla quota massima.\n"
            "Esempio: `.new totti 10`",
            parse_mode="Markdown"
        )
        return

    try:
        quota_massima = int(context.args[-1])
        nome_giocatore = " ".join(context.args[:-1]).replace('"', '').strip()
    except ValueError:
        await update.message.reply_text(
            "⚠️ **La quota deve essere un numero!**\n"
            "Esempio corretto: `.new totti 10`",
            parse_mode="Markdown"
        )
        return

    if not nome_giocatore:
        await update.message.reply_text("⚠️ Devi inserire il nome del giocatore!", parse_mode="Markdown")
        return

    giocatore_key = nome_giocatore.lower()

    if giocatore_key in aste:
        await update.message.reply_text(
            f"⚠️ C'è già un'asta in corso per **{nome_giocatore}**! Usa `.ril {nome_giocatore} [offerta]` per rilanciare.",
            parse_mode="Markdown"
        )
        return

    utente = update.effective_user.username or update.effective_user.first_name
    ora_attuale = datetime.now()
    scadenza = ora_attuale + timedelta(hours=TIMER_ASTA_ORE)

    job = context.job_queue.run_once(
        timer_asta,
        when=timedelta(hours=TIMER_ASTA_ORE),
        data={"giocatore_key": giocatore_key, "chat_id": update.effective_chat.id},
        name=giocatore_key
    )

    offerta_pubblica = 1
    if quota_massima < offerta_pubblica:
        offerta_pubblica = quota_massima

    aste[giocatore_key] = {
        "nome_originale": nome_giocatore,
        "offerta_attuale": offerta_pubblica,
        "ultimo_offrente": utente,
        "scadenza": scadenza,
        "job": job,
        "offerte_massime": {utente: quota_massima}
    }

    await update.message.reply_text(
        f"📣 **ASTA APERTA!**\n\n"
        f"⚽ **Giocatore:** {nome_giocatore}\n"
        f"💰 **Offerta attuale:** {offerta_pubblica} credito\n"
        f"👤 **In vantaggio:** @{utente}\n"
        f"⏳ **Scadenza:** 24 ore (senza rilanci)."
    )

async def rilancio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Cancella il messaggio di rilancio dell'utente
    if update.effective_chat.type != "private":
        try:
            await update.message.delete()
        except Exception:
            pass

    if not context.args or len(context.args) < 2:
        await update.message.reply_text("⚠️ Formato errato. Usa: `.ril NomeGiocatore NuovaOfferta` (es: `.ril totti 12`)", parse_mode="Markdown")
        return

    try:
        nuova_offerta = int(context.args[-1])
        nome_giocatore = " ".join(context.args[:-1]).replace('"', '').strip()
    except ValueError:
        await update.message.reply_text("⚠️ L'ultimo parametro deve essere un numero intero!", parse_mode="Markdown")
        return

    giocatore_key = nome_giocatore.lower()

    if giocatore_key not in aste:
        await update.message.reply_text(f"❌ Non c'è nessuna asta attiva per **{nome_giocatore}**.", parse_mode="Markdown")
        return

    info_asta = aste[giocatore_key]
    utente = update.effective_user.username or update.effective_user.first_name

    info_asta["offerte_massime"][utente] = max(info_asta["offerte_massime"].get(utente, 0), nuova_offerta)

    miglior_offerente = max(info_asta["offerte_massime"], key=info_asta["offerte_massime"].get)
    massimo_disponibile = info_asta["offerte_massime"][miglior_offerente]

    offerte_ordinate = sorted(info_asta["offerte_massime"].values(), reverse=True)
    
    if len(offerte_ordinate) > 1:
        offerta_calcolata = min(max(info_asta["offerta_attuale"] + 1, offerte_ordinate[1] + 1), massimo_disponibile)
    else:
        offerta_calcolata = min(nuova_offerta, massimo_disponibile)

    if offerta_calcolata <= info_asta["offerta_attuale"] and utente != miglior_offerente:
        await update.message.reply_text(f"⚠️ La tua offerta massima ({nuova_offerta}) è stata superata dal rilancio automatico!")
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
    info_asta["scadenza"] = datetime.now() + timedelta(hours=TIMER_ASTA_ORE)
    info_asta["job"] = nuovo_job

    await update.message.reply_text(
        f"🔥 **RILANCIO AUTOMATICO!**\n\n"
        f"⚽ **Giocatore:** {info_asta['nome_originale']}\n"
        f"💰 **Offerta attuale:** {offerta_calcolata} crediti\n"
        f"👤 **In vantaggio:** @{miglior_offerente}\n"
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

    messaggio += "📋 **ASTE ATTIVE IN CORSO:**\n\n"
    ora_attuale = datetime.now()

    for key, info in aste.items():
        tempo_rimanente = info["scadenza"] - ora_attuale
        ore, remainder = divmod(tempo_rimanente.seconds, 3600)
        minuti, _ = divmod(remainder, 60)

        messaggio += (
            f"⚽ **{info['nome_originale']}**\n"
            f"  • Offerta: {info['offerta_attuale']} crediti (In vantaggio: @{info['ultimo_offrente']})\n"
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
    app.add_handler(CommandHandler(["new"], nuova_asta))
    app.add_handler(CommandHandler(["ril"], rilancio))
    app.add_handler(CommandHandler(["stato"], stato_aste))

    print("Bot Fantacalcio Asta in esecuzione...")
    app.run_polling()

if __name__ == "__main__":
    main()