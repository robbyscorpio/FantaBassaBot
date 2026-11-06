import logging
import asyncio
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# Un server HTTP minimale per soddisfare il controllo delle porte di Render
class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), SimpleHandler)
    server.serve_forever()

# Avvia il server in un thread separato così non blocca il bot Telegram
server_thread = threading.Thread(target=run_server, daemon=True)
server_thread.start()

# --- Qui sotto inserisci il codice principale del tuo bot Telegram (polling) ---
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

# Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

aste_attive = {}

DURATA_ASTA_SECONDI = 120  
ESTENSIONE_ANTI_SNIPING = 60  
SOGLIA_ANTI_SNIPING = 30  

async def avvia_asta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id in aste_attive:
        await update.message.reply_text(
            f"❌ C'è già un'asta in corso per **{aste_attive[chat_id]['giocatore']}**!"
        )
        return

    if len(context.args) < 1:
        await update.message.reply_text("Sintassi errata. Uso: `/asta <NomeGiocatore> [BaseAsta]`")
        return

    giocatore = context.args[0]
    base_asta = int(context.args[1]) if len(context.args) > 1 and context.args[1].isdigit() else 1
    scadenza = datetime.now() + timedelta(seconds=DURATA_ASTA_SECONDI)

    timer_task = asyncio.create_task(gestisci_timer_asta(context, chat_id))

    aste_attive[chat_id] = {
        "giocatore": giocatore,
        "prezzo_attuale": base_asta,
        "vincente_id": None,
        "vincente_nome": "Nessuno (Base)",
        "scadenza": scadenza,
        "timer_task": timer_task
    }

    await update.message.reply_text(
        f"⚡ **NUOVA ASTA APERTA!** ⚡\n\n"
        f"🏃‍♂️ **Giocatore:** {giocatore}\n"
        f"💰 **Base d'asta:** {base_asta} crediti\n"
        f"⏳ **Scadenza:** {DURATA_ASTA_SECONDI} secondi\n\n"
        f"Usa `/rilancio <importo>` per fare la tua offerta!"
    )

async def rilancio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user

    if chat_id not in aste_attive:
        await update.message.reply_text("❌ Non c'è alcuna asta attiva in questo momento.")
        return

    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Sintassi errata. Uso: `/rilancio <importo>`")
        return

    offerta = int(context.args[0])
    asta = aste_attive[chat_id]

    offerta_minima = asta["prezzo_attuale"] + 1 if asta["vincente_id"] is not None else asta["prezzo_attuale"]
    if offerta < offerta_minima:
        await update.message.reply_text(
            f"❌ Offerta insufficiente! Devi offrire almeno **{offerta_minima} crediti**."
        )
        return

    ora_attuale = datetime.now()
    asta["prezzo_attuale"] = offerta
    asta["vincente_id"] = user.id
    asta["vincente_nome"] = user.first_name

    tempo_rimasto = (asta["scadenza"] - ora_attuale).total_seconds()
    esteso = False
    if tempo_rimasto <= SOGLIA_ANTI_SNIPING:
        asta["scadenza"] = ora_attuale + timedelta(seconds=ESTENSIONE_ANTI_SNIPING)
        esteso = True
        asta["timer_task"].cancel()
        asta["timer_task"] = asyncio.create_task(gestisci_timer_asta(context, chat_id))

    msg = (
        f"🚀 **NUOVO MIGLIOR OFFERENTE!**\n"
        f"👤 {user.first_name} ha offerto **{offerta} crediti** per {asta['giocatore']}!"
    )
    if esteso:
        msg += f"\n⏳ *Anti-sniping attivato! Timer esteso di {ESTENSIONE_ANTI_SNIPING}s.*"

    await update.message.reply_text(msg)

async def gestisci_timer_asta(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    try:
        while True:
            asta = aste_attive.get(chat_id)
            if not asta:
                return

            tempo_rimasto = (asta["scadenza"] - datetime.now()).total_seconds()
            if tempo_rimasto <= 0:
                break
            await asyncio.sleep(min(tempo_rimasto, 5))

        asta = aste_attive.pop(chat_id, None)
        if asta:
            if asta["vincente_id"]:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🔴 **ASTA CHIUSA!** 🔴\n\n"
                         f"🎉 **{asta['giocatore']}** è stato aggiudicato a **{asta['vincente_nome']}** "
                         f"per **{asta['prezzo_attuale']} crediti**!"
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🔴 **ASTA CHIUSA!** 🔴\n\nNessuna offerta ricevuta per **{asta['giocatore']}**."
                )
    except asyncio.CancelledError:
        pass

async def stato_asta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in aste_attive:
        await update.message.reply_text("Nessuna asta in corso.")
        return

    asta = aste_attive[chat_id]
    secondi_rimasti = max(0, int((asta["scadenza"] - datetime.now()).total_seconds()))

    await update.message.reply_text(
        f"📊 **STATO ASTA CORRENTE**\n\n"
        f"🏃‍♂️ Giocatore: **{asta['giocatore']}**\n"
        f"💰 Offerta migliore: **{asta['prezzo_attuale']} crediti** ({asta['vincente_nome']})\n"
        f"⏱️ Tempo rimasto: **{secondi_rimasti} secondi**"
    )

def main():
    # Prende il token in sicurezza dalle variabili d'ambiente di Render
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    if not TOKEN:
        print("Errore: TELEGRAM_TOKEN non trovato nelle variabili d'ambiente!")
        return

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("asta", avvia_asta))
    app.add_handler(CommandHandler("rilancio", rilancio))
    app.add_handler(CommandHandler("stato", stato_asta))

    print("Bot Fantacalcio Asta in esecuzione...")
    app.run_polling()

if __name__ == "__main__":
    main()

