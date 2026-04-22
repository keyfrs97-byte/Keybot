import logging
import os
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, ContextTypes
)

# ============================================================
#  CONFIGURATION
# ============================================================
TELEGRAM_TOKEN   = os.environ.get"8665063398:AAFalhEc0o0543L74us3hX3wEpo10aO6WMk"
RAPIDAPI_KEY     = os.environ.get"571591e6fbmsh2b109dd94235c7bp1f5fdbjsnb6d667f5b5df"
RAPIDAPI_HOST    = "api-football-v1.p.rapidapi.com"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ============================================================
#  HELPERS API
# ============================================================
HEADERS = {
    "X-RapidAPI-Key":  RAPIDAPI_KEY,
    "X-RapidAPI-Host": RAPIDAPI_HOST,
}

def get_live_matches():
    """Récupère tous les matchs de foot en direct."""
    url = "https://api-football-v1.p.rapidapi.com/v3/fixtures"
    params = {"live": "all"}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        data = r.json()
        return data.get("response", [])
    except Exception as e:
        logger.error(f"Erreur live matches: {e}")
        return []

def get_fixture_stats(fixture_id: int):
    """Récupère les stats détaillées d'un match."""
    url = "https://api-football-v1.p.rapidapi.com/v3/fixtures/statistics"
    params = {"fixture": fixture_id}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        data = r.json()
        return data.get("response", [])
    except Exception as e:
        logger.error(f"Erreur stats: {e}")
        return []

def get_fixture_events(fixture_id: int):
    """Récupère les événements (buts, cartons) d'un match."""
    url = "https://api-football-v1.p.rapidapi.com/v3/fixtures/events"
    params = {"fixture": fixture_id}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        data = r.json()
        return data.get("response", [])
    except Exception as e:
        logger.error(f"Erreur events: {e}")
        return []

def get_top_scorers(league_id: int, season: int):
    """Récupère les meilleurs buteurs d'une compétition."""
    url = "https://api-football-v1.p.rapidapi.com/v3/players/topscorers"
    params = {"league": league_id, "season": season}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=10)
        data = r.json()
        return data.get("response", [])
    except Exception as e:
        logger.error(f"Erreur top scorers: {e}")
        return []

# ============================================================
#  LOGIQUE PRONO
# ============================================================
def compute_goal_proba(match: dict, stats: list) -> dict:
    """
    Calcule la probabilité de buts à partir des stats live.
    Retourne un dict avec les probas pour +0.5 / +1.5 / +2.5.
    """
    elapsed   = match["fixture"]["status"].get("elapsed") or 0
    score_h   = match["goals"]["home"] or 0
    score_a   = match["goals"]["away"] or 0
    total_goals = score_h + score_a

    # Extraire shots on goal des deux équipes
    shots_on_h = shots_on_a = 0
    for team_stat in stats:
        for s in team_stat.get("statistics", []):
            if s["type"] == "Shots on Goal":
                val = s["value"] or 0
                if team_stat["team"]["id"] == match["teams"]["home"]["id"]:
                    shots_on_h = int(val)
                else:
                    shots_on_a = int(val)

    shots_total = shots_on_h + shots_on_a
    minutes_left = max(90 - elapsed, 1)

    # Taux de buts par minute sur la partie déjà jouée
    rate = (total_goals + shots_total * 0.12) / max(elapsed, 1)
    expected_extra = rate * minutes_left

    total_expected = total_goals + expected_extra

    # Probas cumulatives (loi de Poisson approchée)
    import math
    def poisson_cdf(lam, k):
        return sum(
            (lam**i * math.exp(-lam)) / math.factorial(i)
            for i in range(k + 1)
        )

    p05  = round((1 - poisson_cdf(total_expected, 0))  * 100, 1)
    p15  = round((1 - poisson_cdf(total_expected, 1))  * 100, 1)
    p25  = round((1 - poisson_cdf(total_expected, 2))  * 100, 1)
    p35  = round((1 - poisson_cdf(total_expected, 3))  * 100, 1)

    return {
        "total_expected": round(total_expected, 2),
        "+0.5": min(p05, 99.9),
        "+1.5": min(p15, 99.9),
        "+2.5": min(p25, 99.9),
        "+3.5": min(p35, 99.9),
        "score": f"{score_h}-{score_a}",
        "elapsed": elapsed,
    }

def get_probable_scorers(match: dict, top_scorers: list, events: list) -> list:
    """
    Retourne les 3 buteurs les plus probables parmi les joueurs des deux équipes.
    On croise : buts marqués cette saison + buts déjà marqués dans ce match.
    """
    home_id = match["teams"]["home"]["id"]
    away_id = match["teams"]["away"]["id"]

    # Joueurs ayant déjà scoré dans CE match
    match_scorers = {}
    for ev in events:
        if ev["type"] == "Goal" and ev.get("detail") != "Missed Penalty":
            name = ev["player"]["name"]
            match_scorers[name] = match_scorers.get(name, 0) + 1

    # Top scoreurs de la saison dans cette ligue
    candidates = []
    for entry in top_scorers[:20]:
        player   = entry["player"]
        stats_pl = entry.get("statistics", [{}])[0]
        team_id  = stats_pl.get("team", {}).get("id")
        goals    = stats_pl.get("goals", {}).get("total") or 0
        games    = stats_pl.get("games", {}).get("appearences") or 1

        if team_id not in (home_id, away_id):
            continue

        gpm   = goals / max(games, 1)
        bonus = match_scorers.get(player["name"], 0) * 0.15
        score = gpm + bonus

        candidates.append({
            "name":  player["name"],
            "team":  stats_pl.get("team", {}).get("name", ""),
            "goals": goals,
            "gpm":   round(gpm, 2),
            "proba": round(min((gpm * 1.8 + bonus) * 100, 85), 1),
        })

    candidates.sort(key=lambda x: x["proba"], reverse=True)
    return candidates[:5]

# ============================================================
#  FORMATAGE MESSAGES
# ============================================================
def fmt_live_list(matches: list) -> str:
    if not matches:
        return "⚽ Aucun match de foot en direct pour le moment."

    lines = ["🔴 *MATCHS EN DIRECT*\n"]
    for i, m in enumerate(matches[:15], 1):
        home    = m["teams"]["home"]["name"]
        away    = m["teams"]["away"]["name"]
        score_h = m["goals"]["home"] if m["goals"]["home"] is not None else "-"
        score_a = m["goals"]["away"] if m["goals"]["away"] is not None else "-"
        elapsed = m["fixture"]["status"].get("elapsed") or "?"
        league  = m["league"]["name"]
        lines.append(
            f"{i}. *{home}* {score_h}–{score_a} *{away}*\n"
            f"   ⏱ {elapsed}' · {league}"
        )
    return "\n".join(lines)

def fmt_prono(match: dict, proba: dict, scorers: list) -> str:
    home   = match["teams"]["home"]["name"]
    away   = match["teams"]["away"]["name"]
    league = match["league"]["name"]

    lines = [
        f"📊 *ANALYSE — {home} vs {away}*",
        f"🏆 {league}  |  ⏱ {proba['elapsed']}'  |  Score : {proba['score']}\n",
        f"🎯 *Buts attendus (total) : ~{proba['total_expected']}*\n",
        "📈 *Probabilités de buts :*",
        f"  +0.5 buts : *{proba['+0.5']}%*",
        f"  +1.5 buts : *{proba['+1.5']}%*",
        f"  +2.5 buts : *{proba['+2.5']}%*",
        f"  +3.5 buts : *{proba['+3.5']}%*",
    ]

    if scorers:
        lines.append("\n⚽ *Buteurs probables :*")
        for s in scorers:
            lines.append(
                f"  • {s['name']} ({s['team']}) — {s['proba']}%  [{s['gpm']} g/match]"
            )
    else:
        lines.append("\n⚽ _Données buteurs insuffisantes pour ce match._")

    lines.append("\n_Analyse basée sur stats live + historique saison_")
    return "\n".join(lines)

# ============================================================
#  COMMANDES TELEGRAM
# ============================================================
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("🔴 Matchs Live",      callback_data="live")],
        [InlineKeyboardButton("📊 Probas de buts",   callback_data="buts")],
        [InlineKeyboardButton("⚽ Buteurs probables", callback_data="buteurs")],
    ]
    await update.message.reply_text(
        "👋 *Bienvenue sur ClainoProno Bot !*\n\n"
        "Je t'analyse les matchs en direct :\n"
        "• Probabilités de buts (+0.5, +1.5, +2.5…)\n"
        "• Buteurs les plus probables\n"
        "• Stats live\n\n"
        "Choisis une option ⬇️",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb),
    )

async def cmd_live(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message or update.callback_query.message
    await msg.reply_text("⏳ Récupération des matchs en direct…")
    matches = get_live_matches()
    text    = fmt_live_list(matches)

    # Boutons pour analyser chaque match
    kb = []
    for i, m in enumerate(matches[:10], 1):
        home = m["teams"]["home"]["name"][:12]
        away = m["teams"]["away"]["name"][:12]
        fid  = m["fixture"]["id"]
        kb.append([InlineKeyboardButton(
            f"{i}. {home} vs {away}",
            callback_data=f"analyze_{fid}"
        )])
    kb.append([InlineKeyboardButton("🔄 Rafraîchir", callback_data="live")])

    await msg.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb) if kb else None,
    )

async def cmd_buts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message or update.callback_query.message
    await msg.reply_text("⏳ Calcul des probabilités de buts…")
    matches = get_live_matches()

    if not matches:
        await msg.reply_text("⚽ Aucun match en direct.")
        return

    lines = ["📈 *PROBAS DE BUTS — MATCHS LIVE*\n"]
    for m in matches[:8]:
        fid    = m["fixture"]["id"]
        home   = m["teams"]["home"]["name"]
        away   = m["teams"]["away"]["name"]
        stats  = get_fixture_stats(fid)
        proba  = compute_goal_proba(m, stats)

        lines.append(
            f"⚽ *{home} vs {away}*  ({proba['score']} · {proba['elapsed']}')\n"
            f"  +0.5: *{proba['+0.5']}%*  |  +1.5: *{proba['+1.5']}%*  "
            f"|  +2.5: *{proba['+2.5']}%*\n"
        )

    await msg.reply_text("\n".join(lines), parse_mode="Markdown")

async def cmd_buteurs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = update.message or update.callback_query.message
    await msg.reply_text("⏳ Analyse des buteurs probables…")
    matches = get_live_matches()

    if not matches:
        await msg.reply_text("⚽ Aucun match en direct.")
        return

    # Afficher boutons pour choisir le match
    kb = []
    for i, m in enumerate(matches[:10], 1):
        home = m["teams"]["home"]["name"][:13]
        away = m["teams"]["away"]["name"][:13]
        fid  = m["fixture"]["id"]
        kb.append([InlineKeyboardButton(
            f"{i}. {home} vs {away}",
            callback_data=f"analyze_{fid}"
        )])

    await msg.reply_text(
        "⚽ *Choisis un match pour voir les buteurs probables :*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb),
    )

async def cmd_prono(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Alias de /live avec analyse directe."""
    await cmd_live(update, ctx)

# ============================================================
#  CALLBACK (boutons inline)
# ============================================================
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "live":
        await cmd_live(update, ctx)

    elif data == "buts":
        await cmd_buts(update, ctx)

    elif data == "buteurs":
        await cmd_buteurs(update, ctx)

    elif data.startswith("analyze_"):
        fixture_id = int(data.split("_")[1])
        await query.message.reply_text("⏳ Analyse en cours…")

        # Retrouver le match dans la liste live
        matches  = get_live_matches()
        match    = next((m for m in matches if m["fixture"]["id"] == fixture_id), None)

        if not match:
            await query.message.reply_text("❌ Match introuvable (peut-être terminé).")
            return

        stats    = get_fixture_stats(fixture_id)
        events   = get_fixture_events(fixture_id)
        proba    = compute_goal_proba(match, stats)

        # Top scorers de cette ligue
        league_id = match["league"]["id"]
        season    = match["league"]["season"]
        top_sc    = get_top_scorers(league_id, season)
        scorers   = get_probable_scorers(match, top_sc, events)

        text = fmt_prono(match, proba, scorers)
        kb   = [[InlineKeyboardButton("🔄 Actualiser", callback_data=f"analyze_{fixture_id}")],
                [InlineKeyboardButton("⬅️ Retour aux matchs", callback_data="live")]]

        await query.message.reply_text(
            text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(kb),
        )

# ============================================================
#  MAIN
# ============================================================
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("live",     cmd_live))
    app.add_handler(CommandHandler("buts",     cmd_buts))
    app.add_handler(CommandHandler("buteurs",  cmd_buteurs))
    app.add_handler(CommandHandler("prono",    cmd_prono))
    app.add_handler(CallbackQueryHandler(on_callback))

    print("🤖 ClainoProno Bot démarré ! Ctrl+C pour arrêter.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
