import os
import asyncio
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import pandas as pd
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
crediti_squadre = {}     # { "@utente": crediti_rimanenti }
storico_acquisti = []    # Lista di dizionari con i giocatori assegnati
rose_squadre = {}        # { "@utente": [ {giocatore, ruolo, prezzo}, ... ] }

TIMER_ASTA_ORE = 24
MAX_ASTE_CONTEMPORANEE = 2

data_inizio_mercato = None
data_fine_mercato = None
mercato_bloccato = False

def get_nome_utente(user) -> str:
    if user.username:
        return f"@{user.username}"
    return user.first_name

# --- CARICAMENTO AUTOMATICO DA EXCEL ---
def carica_dati_da_excel():
    global rose_squadre
    file_excel = "listone_fantacalcio.xlsx"
    
    if not os.path.exists(file_excel):
        print(f"ℹ️ File {file_excel} non trovato. Nessun dato precaricato.")
        return

    try:
        df = pd.read_excel(file_excel)
        print(f"📊 Lettura file {file_excel} in corso...")
        
        for _, row in df.iterrows():
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

            # Se la squadra è specificata nel file, la carichiamo nella rosa iniziale
            if squadra and squadra.lower() != "nan" and squadra != "":
                if not squadra.startswith("@"):
                    squadra = "@" + squadra
                
                if squadra not in rose_squadre:
                    rose_squadre[squadra] = []
                rose_squadre[squadra].append({
                    "giocatore": nome,
                    "ruolo": ruolo,
                    "prezzo": prezzo
                })
                
        print("✅ Rose iniziali caricate con successo da Excel!")
    except Exception as e:
        print(f"❌ Errore durante la lettura del file Excel: {e}")

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

        # Registra nello storico e nella rosa della squadra vincitrice
        storico_acquisti.append({
            "giocatore": nome,
            "vincitore": vincitore,
            "prezzo": offerta
        })

        if vincitore not in rose_squadre:
            rose_squadre[vincitore] = []
        rose_squadre[vincitore].append({
            "giocatore": nome,
            "ruolo": "N/D",
            "prezzo": offerta
        })

        # Scala crediti residui
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
        await update.message.reply_text(
            "⚠️ **Formato Errato!** Usa:\n"
            "`/setmercato DataInizio OraInizio DataFine OraFine`",
            parse_mode="Markdown"
        )
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
    
    await update.message.reply_text(
        f"🔒 **STATO MERCATO AGGIORNATO:** {stato_testo}",
        parse_mode="Markdown"
    )

async def imposta_crediti(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await verifica_admin(update, context):
        return

    if update.effective_chat.type != "private":
        try:
            await update.message.delete()
        except Exception:
            pass

    if not context.args or len(context.args) < 2:
        await update.message.reply_text("⚠️ Usa: `/crediti @Utente Crediti`", parse_mode="Markdown")
        return

    try:
        utente = context.args[0]
        crediti = int(context.args[1])
    except ValueError:
        await update.message.reply_text("⚠️ I crediti devono essere un numero intero.", parse_mode="Markdown")
        return

    crediti_squadre[utente] = crediti
    await update.message.reply_text(f"💰 **CREDITI AGGIORNATI!**\n👤 {utente}: **{crediti}** crediti", parse_mode="Markdown")

async def mostra_crediti(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not crediti_squadre:
        await update.message.reply_text("ℹ️ Non ci sono crediti registrati al momento.", parse_mode="Markdown")
        return

    messaggio = "💰 **BILANCIO CREDITI SQUADRE:**\n\n"
    for utente, crediti in crediti_squadre.items():
        messaggio += f"• {utente}: **{crediti}** crediti\n"

    await update.message.reply_text(messaggio, parse_mode="Markdown")

async def mostra_rosa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Specifica la squadra. Esempio: `/rosa @mario`", parse_mode="Markdown")
        return

    squadra = context.args[0]
    if not squadra.startswith("@"):
        squadra = "@" + squadra

    if squadra not in rose_squadre or not rose_squadre[squadra]:
        await update.message.reply_text(f"ℹ️ La rosa di {squadra} è attualmente vuota.", parse_mode="Markdown")
        return

    messaggio = f"📋 **ROSA SQUADRA: {squadra}**\n\n"
    tot_speso = 0
    for p in rose_squadre[squadra]:
        messaggio += f"• [{p.get('ruolo', 'N/D')}] **{p['giocatore']}** ({p['prezzo']} crediti)\n"
        tot_speso += p['prezzo']

    messaggio += f"\n💎 **Totale giocatori in rosa:** {len(rose_squadre[squadra])}\n"
    messaggio += f"💸 **Spesa totale:** {tot_speso} crediti"

    await update.message.reply_text(messaggio, parse_mode="Markdown")

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
        "⚙️ **Comandi Admin:**\n"
        "`/bloccamercato` - Blocca/Sblocca aste\n"
        "`/crediti @Utente Valore` - Imposta budget\n\n"
        "🔍 **Consultazione:**\n"
        "`/portafoglio` - Crediti residui\n"
        "`/rosa @Utente` - Mostra la rosa di una squadra\n"
        "`/assegnati` - Storico acquisti\n"
        "`/stato` - Aste attive in corso\n\n"
        f"🟢 **Aste e Rilanci:**\n"
        "`/asta NomeGiocatore Quota` (Max {MAX_ASTE_CONTEMPORANEE} simultanee per squadra)\n"
        "`/rilancio NomeGiocatore NuovaOfferta`"
    )
    await update.message.reply_text(testo_guida, parse_mode="Markdown")

async def nuova_asta(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        try:
            await update.message.delete()
        except Exception:
            pass

    if mercato_bloccato:
        await update.message.reply_text("🛑 **Il mercato è attualmente bloccato dagli amministratori!**")
        return

    utente = get_nome_utente(update.effective_user)

    aste_attive_utente = sum(1 for info in aste.values() if info["creatore"] == utente)
    if aste_attive_utente >= MAX_ASTE_CONTEMPORANEE:
        await update.message.reply_text(f"⚠️ Hai già {MAX_ASTE_CONTEMPORANEE} aste attive in simultanea.")
        return

    if not context.args or len(context.args) < 2:
        await update.message.reply_text("⚠️ Usa: `/asta NomeGiocatore Quota`", parse_mode="Markdown")
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

    await update.message.reply_text(
        f"📣 **ASTA APERTA!**\n\n"
        f"⚽ **Giocatore:** {nome_giocatore}\n"
        f"💰 **Offerta attuale:** {offerta_pubblica} credito\n"
        f"👤 **In vantaggio:** {utente}\n"
        f"⏳ **Scadenza:** 24 ore."
    )

async def rilancio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        try:
            await update.message.delete()
        except Exception:
            pass

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

    info_asta = aste[giocatore_key]
    utente = get_nome_utente(update.effective_user)

    info_asta["offerte_massime"][utente] = max(info_asta["offerte_massime"].get(utente, 0), nuova_offerta)

    miglior_offerente = max(info_asta["offerte_massime"], key=info_asta["offerte_massime"].get)
    massimo_disponibile = info_asta["offerte_massime"][miglior_offerente]

    offerte_ordinate = sorted(info_asta["offerte_massime"].values(), reverse=True)
    
    if len(offerte_ordinate) > 1:
        offerta_calcolata = min(max(info_asta["offerta_attuale"] + 1, offerte_ordinate[1] + 1), massimo_disponibile)
    else:
        offerta_calcolata = min(nuova_offerta, massimo_disponibile)

    if offerta_calcolata <= info_asta["offerta_attuale"] and utente != miglior_offerente:
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
    info_asta["scadenza"] = datetime.now() + timedelta(hours=TIMER_ASTA_ORE)
    info_asta["job"] = nuovo_job

    await update.message.reply_text(
        f"🔥 **RILANCIO AUTOMATICO!**\n\n"
        f"⚽ **Giocatore:** {info_asta['nome_originale']}\n"
        f"💰 **Offerta:** {offerta_calcolata} crediti\n"
        f"👤 **In vantaggio:** {miglior_offerente}"
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
                f"  • Offerta: {info['offerta_attuale']} crediti (In vantaggio: {info['ultimo_offrente']})\n"
                f"  • Tempo rimasto: ~{ore}h {minuti}m\n\n"
            )

    await update.message.reply_text(messaggio, parse_mode="Markdown")

# --- 5. MAIN ---
def main():
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        print("ERRORE: Variabile TELEGRAM_TOKEN non trovata.")
        return

    # Carica le rose e i dati iniziali dal file Excel all'avvio del bot
    carica_dati_da_excel()

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler(["start"], start))
    app.add_handler(CommandHandler(["guida", "help"], guida))
    app.add_handler(CommandHandler(["setmercato"], set_mercato))
    app.add_handler(CommandHandler(["bloccamercato", "stopmercato"], blocca_mercato))
    
    app.add_handler(CommandHandler(["crediti"], imposta_crediti))
    app.add_handler(CommandHandler(["portafoglio", "budget"], mostra_crediti))
    app.add_handler(CommandHandler(["rosa"], mostra_rosa))
    
    app.add_handler(CommandHandler(["asta", "a"], nuova_asta))
    app.add_handler(CommandHandler(["rilancio", "ril"], rilancio))
    
    app.add_handler(CommandHandler(["stato"], stato_aste))
    app.add_handler(CommandHandler(["assegnati", "storico"], storico_aste))

    print("Bot Fantacalcio Asta in esecuzione...")
    app.run_polling()

if __name__ == "__main__":
    main()