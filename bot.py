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
aste = {}                # { "giocatore_key": { ... } }
giocatori_tagliati = {}  # { "giocatore_key": { "utente": "NomeUtente", "prezzo": 15 } }

TIMER_ASTA_ORE = 24
MAX_ASTE_CONTEMPORANEE = 2

data_inizio_mercato = None
data_fine_mercato = None

def get_nome_utente(user) -> str:
    if user.username:
        return f"@{user.username}"
    return user.first_name

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

async def taglia_giocatore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await verifica_admin(update, context):
        return

    if update.effective_chat.type != "private":
        try:
            await update.message.delete()
        except Exception:
            pass

    if not context.args or len(context.args) < 3:
        await update.message.reply_text(
            "⚠️ **Formato errato!** Usa:\n"
            "`/taglia NomeGiocatore Prezzo Utente`\n"
            "Esempio: `/taglia immobile 15 @mario`",
            parse_mode="Markdown"
        )
        return

    try:
        utente_taglio = context.args[-1]
        prezzo_taglio = int(context.args[-2])
        nome_giocatore = " ".join(context.args[:-2]).replace('"', '').strip()
    except ValueError:
        await update.message.reply_text(
            "⚠️ **Formato errato!** Il prezzo del taglio deve essere un numero intero.\n"
            "Usa: `/taglia NomeGiocatore Prezzo Utente`",
            parse_mode="Markdown"
        )
        return

    giocatore_key = nome_giocatore.lower()

    giocatori_tagliati[giocatore_key] = {
        "utente": utente_taglio,
        "prezzo": prezzo_taglio
    }

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"✂️ **TAGLIO REGISTRATO!**\n\n"
             f"⚽ **Giocatore:** {nome_giocatore}\n"
             f"👤 **Tagliato da:** {utente_taglio}\n"
             f"💰 **Costo del taglio:** {prezzo_taglio} crediti\n"
             f"*(Se vorrà riprenderlo, dovrà offrire almeno questa cifra).*`",
            parse_mode="Markdown"
    )

async def guida(update: Update, context: ContextTypes.DEFAULT_TYPE):
    info_mercato = "📅 **PERIODO MERCATO:** Attivo\n\n"
    if data_inizio_mercato and data_fine_mercato:
        info_mercato = (
            f"📅 **PERIODO MERCATO:**\n"
            f"• **Inizio:** {data_inizio_mercato.strftime('%d/%m/%Y %H:%M')}\n"
            f"• **Fine:** {data_fine_mercato.strftime('%d/%m/%Y %H:%M')}\n\n"
        )

    testo_guida = (
        "📖 **GUIDA E REGOLAMENTO ASTE**\n\n"
        f"{info_mercato}"
        "✂️ **Registrare un taglio (Solo Admin):**\n"
        "`/taglia NomeGiocatore Prezzo Utente`\n\n"
        f"🟢 **Avviare un'asta (Max {MAX_ASTE_CONTEMPORANEE} in simultanea per squadra):**\n"
        "`/asta NomeGiocatore QuotaMassima` (o `/a`)\n\n"
        "⚡ **Rilanciare:**\n"
        "`/rilancio NomeGiocatore NuovaOfferta` (o `/ril`)\n\n"
        "📊 **Stato Aste:**\n"
        "`/stato` - Mostra le aste attive ed i tagli registrati."
    )
    await update.message.reply_text(testo_guida, parse_mode="Markdown")

async def nuova_asta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        try:
            await update.message.delete()
        except Exception:
            pass

    utente = get_nome_utente(update.effective_user)

    # Controlla limite aste in simultanea per utente
    aste_attive_utente = sum(1 for info in aste.values() if info["creatore"] == utente)
    if aste_attive_utente >= MAX_ASTE_CONTEMPORANEE:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"⚠️ **Limite raggiunto!** Hai già {MAX_ASTE_CONTEMPORANEE} aste attive in simultanea. Aspetta che una di esse si concluda prima di aprirne un'altra.",
            parse_mode="Markdown"
        )
        return

    if not context.args or len(context.args) < 2:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="⚠️ **Formato errato!** Usa: `/asta NomeGiocatore Quota` (es: `/asta totti 10`)",
            parse_mode="Markdown"
        )
        return

    try:
        quota_massima = int(context.args[-1])
        nome_giocatore = " ".join(context.args[:-1]).replace('"', '').strip()
    except ValueError:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="⚠️ **La quota deve essere un numero intero!** (es: `/asta totti 10`)",
            parse_mode="Markdown"
        )
        return

    if not nome_giocatore:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="⚠️ Devi inserire il nome del giocatore!"
        )
        return

    giocatore_key = nome_giocatore.lower()

    if giocatore_key in aste:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"⚠️ C'è già un'asta in corso per **{nome_giocatore}**! Usa `/rilancio {nome_giocatore} [offerta]` per rilanciare.",
            parse_mode="Markdown"
        )
        return

    # Controllo regola tagli: se l'utente è quello registrato nel taglio
    if giocatore_key in giocatori_tagliati:
        info_taglio = giocatori_tagliati[giocatore_key]
        # Confrontiamo sia il nomeesatto sia l'eventuale username (rimuovendo la @ per sicurezza)
        proprietario_taglio = info_taglio["utente"].lower().replace("@", "")
        utente_corrente_pulito = utente.lower().replace("@", "")
        
        if proprietario_taglio in utente_corrente_pulito or utente_corrente_pulito in proprietario_taglio:
            prezzo_minimo = info_taglio["prezzo"]
            if quota_massima < prezzo_minimo:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"❌ **Regola Tagli:** Avendo tagliato tu questo giocatore, la tua offerta minima iniziale deve essere almeno di **{prezzo_minimo}** crediti.",
                    parse_mode="Markdown"
                )
                return

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
        "creatore": utente,
        "scadenza": scadenza,
        "job": job,
        "offerte_massime": {utente: quota_massima}
    }

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"📣 **ASTA APERTA!**\n\n"
             f"⚽ **Giocatore:** {nome_giocatore}\n"
             f"💰 **Offerta attuale:** {offerta_pubblica} credito\n"
             f"👤 **In vantaggio:** {utente}\n"
             f"⏳ **Scadenza:** 24 ore (senza rilanci)."
    )

async def rilancio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        try:
            await update.message.delete()
        except Exception:
            pass

    if not context.args or len(context.args) < 2:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="⚠️ Formato errato. Usa: `/rilancio NomeGiocatore NuovaOfferta` (es: `/rilancio totti 12`)",
            parse_mode="Markdown"
        )
        return

    try:
        nuova_offerta = int(context.args[-1])
        nome_giocatore = " ".join(context.args[:-1]).replace('"', '').strip()
    except ValueError:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="⚠️ L'ultimo parametro deve essere un numero intero!"
        )
        return

    giocatore_key = nome_giocatore.lower()

    if giocatore_key not in aste:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"❌ Non c'è nessuna asta attiva per **{nome_giocatore}**.",
            parse_mode="Markdown"
        )
        return

    info_asta = aste[giocatore_key]
    utente = get_nome_utente(update.effective_user)

    # Controllo regola tagli in fase di rilancio
    if giocatore_key in giocatori_tagliati:
        info_taglio = giocatori_tagliati[giocatore_key]
        proprietario_taglio = info_taglio["utente"].lower().replace("@", "")
        utente_corrente_pulito = utente.lower().replace("@", "")
        
        if proprietario_taglio in utente_corrente_pulito or utente_corrente_pulito in proprietario_taglio:
            prezzo_minimo = info_taglio["prezzo"]
            if nuova_offerta < prezzo_minimo:
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"❌ **Regola Tagli:** Essendo il giocatore che hai tagliato, la tua offerta deve essere di almeno **{prezzo_minimo}** crediti.",
                    parse_mode="Markdown"
                )
                return

    info_asta["offerte_massime"][utente] = max(info_asta["offerte_massime"].get(utente, 0), nuova_offerta)

    miglior_offerente = max(info_asta["offerte_massime"], key=info_asta["offerte_massime"].get)
    massimo_disponibile = info_asta["offerte_massime"][miglior_offerente]

    offerte_ordinate = sorted(info_asta["offerte_massime"].values(), reverse=True)
    
    if len(offerte_ordinate) > 1:
        offerta_calcolata = min(max(info_asta["offerta_attuale"] + 1, offerte_ordinate[1] + 1), massimo_disponibile)
    else:
        offerta_calcolata = min(nuova_offerta, massimo_disponibile)

    if offerta_calcolata <= info_asta["offerta_attuale"] and utente != miglior_offerente:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"⚠️ La tua offerta massima ({nuova_offerta}) è stata superata dal rilancio automatico!"
        )
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

    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"🔥 **RILANCIO AUTOMATICO!**\n\n"
             f"⚽ **Giocatore:** {info_asta['nome_originale']}\n"
             f"💰 **Offerta attuale:** {offerta_calcolata} crediti\n"
             f"👤 **In vantaggio:** {miglior_offerente}\n"
             f"⏱️ **Timer resettato:** 24 ore rimanenti!"
    )

async def stato_aste(update: Update, context: ContextTypes.DEFAULT_TYPE):
    messaggio = ""
    if data_inizio_mercato and data_fine_mercato:
        messaggio += f"📅 **Mercato:** {data_inizio_mercato.strftime('%d/%m %H:%M')} ➔ {data_fine_mercato.strftime('%d/%m %H:%M')}\n\n"

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
                f"  • Offerta: {info['offerta_attuale']} crediti (In vantaggio: {info['ultimo_offrente']})\n"
                f"  • Tempo rimasto: ~{ore}h {minuti}m\n\n"
            )

    if giocatori_tagliati:
        messaggio += "✂️ **GIOCATORI TAGLIATI REGISTRATI:**\n"
        for key, info in giocatori_tagliati.items():
            messaggio += f"• {key.capitalize()} ➔ {info['utente']} ({info['prezzo']} crediti)\n"

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
    
    app.add_handler(CommandHandler(["taglia"], taglia_giocatore))
    app.add_handler(CommandHandler(["asta", "a"], nuova_asta))
    app.add_handler(CommandHandler(["rilancio", "ril"], rilancio))
    
    app.add_handler(CommandHandler(["stato"], stato_aste))

    print("Bot Fantacalcio Asta in esecuzione...")
    app.run_polling()

if __name__ == "__main__":
    main()