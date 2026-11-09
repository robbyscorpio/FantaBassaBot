import os
import logging
import base64
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler

# Configurazione dei log per monitorare il bot in tempo reale su Render
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Recupero delle credenziali e configurazioni dalle variabili d'ambiente
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GH_PAT = os.getenv("GH_PAT")
GH_REPO = os.getenv("GH_REPO")  # Formato atteso: "robbyscorpio/FantaBassaBot"
ADMIN_ID = 592528299

def carica_su_github(file_path="Listone_Fantacalcio.xlsx", percorso_nel_repo="Listone_Fantacalcio.xlsx"):
    """
    Carica o aggiorna il file Excel direttamente nel repository GitHub usando il PAT.
    Gestisce automaticamente il recupero dello SHA se il file esiste già.
    """
    if not GH_PAT or not GH_REPO:
        logger.error("Credenziali GitHub mancanti nelle variabili d'ambiente (GH_PAT o GH_REPO).")
        return False

    url = f"https://api.github.com/repos/{GH_REPO}/contents/{percorso_nel_repo}"
    headers = {
        "Authorization": f"Bearer {GH_PAT}",
        "Accept": "application/vnd.github+json"
    }
    
    if not os.path.exists(file_path):
        logger.error(f"Il file locale {file_path} non esiste.")
        return False

    # Lettura del file locale e codifica in base64
    with open(file_path, "rb") as f:
        content_bytes = f.read()
    content_base64 = base64.b64encode(content_bytes).decode("utf-8")
    
    # Verifica se il file esiste già su GitHub per recuperare lo SHA (obbligatorio per gli update)
    response = requests.get(url, headers=headers)
    sha = None
    if response.status_code == 200:
        sha = response.json().get("sha")
        
    payload = {
        "message": "Aggiornamento automatico file Excel post-asta",
        "content": content_base64
    }
    if sha:
        payload["sha"] = sha
        
    # Invio della richiesta PUT a GitHub
    put_response = requests.put(url, headers=headers, json=payload)
    
    if put_response.status_code in [200, 201]:
        logger.info("File Excel sincronizzato con successo su GitHub!")
        return True
    else:
        logger.error(f"Errore GitHub API ({put_response.status_code}): {put_response.text}")
        return False

async def aggiorna_excel_dopo_asta(bot, file_path="Listone_Fantacalcio.xlsx"):
    """
    Funzione da richiamare alla chiusura dell'asta:
    1. Sincronizza il file su GitHub.
    2. Invia il file Excel aggiornato in privato all'amministratore (ID: 592528299).
    """
    # 1. Sincronizzazione GitHub
    caricato_su_github = carica_su_github(file_path)
    
    status_msg = "Sincronizzato su GitHub con successo! 🚀" if caricato_su_github else "⚠️ Errore durante la sincronizzazione su GitHub."

    # 2. Invio del documento all'admin via Telegram
    if os.path.exists(file_path):
        try:
            with open(file_path, "rb") as f:
                await bot.send_document(
                    chat_id=ADMIN_ID,
                    document=f,
                    caption=f"📊 **Asta Chiusa!** File Excel aggiornato.\n\n{status_msg}"
                )
            logger.info("File Excel inviato correttamente all'amministratore.")
        except Exception as e:
            logger.error(f"Errore durante l'invio del documento su Telegram: {e}")
    else:
        logger.warning(f"Impossibile inviare il file: {file_path} non trovato sul disco.")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando di benvenuto per testare che il bot sia online."""
    user = update.effective_user
    await update.message.reply_text(f"Ciao {user.first_name}! Il bot FantaBassaBot è attivo e operativo.")

async def test_asta_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Comando utile per testare manualmente il flusso di chiusura asta 
    (caricamento su GitHub e invio file all'admin).
    """
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Non sei autorizzato ad eseguire questo comando di test.")
        return

    await update.message.reply_text("🔄 Esecuzione test chiusura asta in corso...")
    await aggiorna_excel_dopo_asta(context.bot)
    await update.message.reply_text("Test completato! Controlla i log e la chat.")

def main():
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN non impostato nelle variabili d'ambiente!")
        return

    # Inizializzazione dell'applicazione Telegram Bot
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Registrazione dei comandi
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("testasta", test_asta_command))

    logger.info("Bot avviato correttamente. In ascolto di eventi...")
    
    # Avvio del bot in modalità polling
    app.run_polling()

if __name__ == "__main__":
    main()