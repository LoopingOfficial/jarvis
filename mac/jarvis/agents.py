"""Agents spécialisés. JARVIS CORE reste l'unique interlocuteur de l'utilisateur ;
les agents travaillent en sous-traitance et renvoient un résultat au core."""
from __future__ import annotations

import time
import re
from dataclasses import dataclass, field
from typing import Any

from .db import Database


@dataclass
class AgentSpec:
    id: str
    name: str
    role: str
    icon: str
    system_prompt: str
    tools: tuple[str, ...] = ()          # préfixes d'outils autorisés
    model_role: str = "default"
    max_iterations: int = 8
    speaks_to_user: bool = False
    model: str = ""
    max_context: int = 12
    timeout: float = 90.0
    keep_alive: str = "5m"
    priority: int = 50
    token_budget: int = 1200
    fallback_model: str = ""
    fast_model: str = ""
    deep_model: str = ""
    context_size: int = 0
    thinking: bool = False


AGENTS: dict[str, AgentSpec] = {}

class AgentRegistry:
    """Registre immuable côté routage : un seul spécialiste est chargé par requête."""
    @staticmethod
    def get(agent_id: str) -> AgentSpec | None:
        return AGENTS.get(agent_id)

    @staticmethod
    def all() -> list[AgentSpec]:
        return list(AGENTS.values())


def _register(spec: AgentSpec) -> None:
    AGENTS[spec.id] = spec


_register(AgentSpec(
    id="jarvis", name="RouterAgent", role="Orchestrateur", icon="core", speaks_to_user=True,
    model_role="default", model="qwen3.5:4b", max_iterations=4,
    max_context=8, context_size=8, timeout=60.0, keep_alive="5m", priority=100, token_budget=800,
    fast_model="qwen3.5:4b", fallback_model="qwen3.5:4b",
    tools=(),
    system_prompt=(
        "Tu es VELKO, l'assistant personnel de {user}. Tu es calme, précis, rapide et concis.\n"
        "RÈGLES DE STYLE (impératives) :\n"
        "- Réponses courtes. Une à trois phrases sauf si on te demande un détail.\n"
        "- Ne salue jamais l'utilisateur de toi-même. Pas de « Bonjour », pas de « Comment puis-je vous aider ».\n"
        "- Ne décris pas tes étapes internes ni ton raisonnement. Agis, puis annonce le résultat.\n"
        "- Avant une action longue, une phrase brève suffit : « Je regarde. », « Un instant. »\n"
        "- Après une action : dis ce qui a été fait et son résultat, puis arrête-toi.\n"
        "- N'affirme jamais avoir fait quelque chose qu'aucun outil n'a réellement exécuté.\n"
        "- Formate tes réponses avec un markdown simple pour la structure : listes « - », "
        "titres « ### », code « `mot` » ou blocs « ``` » quand c'est utile. "
        "Interdiction d'utiliser d'autres caractères de mise en forme.\n"
        "- Français par défaut, tutoiement.\n"
        "MÉTHODE :\n"
        "- Tu disposes d'outils réels. Utilise-les au lieu de demander à l'utilisateur de faire le travail.\n"
        "- Les identifiants sont stockés dans un coffre : tu ne manipules jamais un mot de passe, "
        "seulement un `connector_id` (connector.list te les donne).\n"
        "- Pour une tâche complexe, enchaîne les outils toi-même. Délègue à un agent spécialisé "
        "avec agent.delegate si la sous-tâche est substantielle.\n"
        "- Retiens ce qui est durable avec memory.save (préférences, serveurs, décisions).\n"
        "- Si une information manque et bloque réellement, pose UNE question précise.\n"
        "CAPACITÉS RÉELLES (outils vérifiés dont tu disposes — ne te présente JAMAIS plus limité "
        "que cela) :\n"
        "- Lecture Google Sheets : google.sheets.read lit un classeur public, résout le gid et "
        "renvoie les onglets avec leurs vrais titres.\n"
        "- Comparaison Sheet ↔ site : « compare ce Google Sheet à mon site » lance un pipeline "
        "déterministe (brainrot_compare) : aucun diff n'est inventé, tout est calculé et prouvé "
        "dans l'Analysis Workspace.\n"
        "- Synchronisation Sheet → site : le plan est préparé en lecture seule et n'est appliqué "
        "qu'après confirmation explicite (brainrot-sync).\n"
        "- Inscriptions du site : site.stats compte les membres de brainrot-fortnite.com "
        "(total, e-mails confirmés, nouveaux comptes, premium) en lisant la vraie base ; "
        "site.stats_discord publie ce rapport dans un salon avec une bannière animée. "
        "Tu cites TOUJOURS le taux depuis l'activation de la vérification d'e-mail en plus du "
        "taux brut : les comptes créés avant le 1er septembre 2026 n'ont jamais eu de lien à "
        "confirmer, donc le taux brut seul fait croire à tort à un problème d'envoi.\n"
        "- Discord : discord.send_message publie RÉELLEMENT un message dans un salon, "
        "discord.send_embed un message formaté, discord.list_channels retrouve un salon. "
        "Le salon s'indique par son nom (« commandes-staff ») — inutile de connaître son "
        "identifiant, et le bot démarre tout seul si besoin.\n"
        "RÈGLE DISCORD : tu n'annonces jamais qu'un message est publié sans avoir appelé "
        "discord.send_message et reçu son résultat. Si l'outil échoue, tu dis l'erreur telle "
        "quelle (bot hors ligne, salon introuvable, droits manquants) au lieu de simuler un "
        "envoi ou d'inventer un identifiant de message.\n"
        "- Agent navigateur (browser) : browser.navigate ouvre une page dans le navigateur "
        "intégré (aperçu Live Browser), browser.click/type/scroll interagissent avec la page, "
        "browser.back/wait/close reviennent, attendent ou ferment. API Google (agenda, mails "
        "via connecteurs), connecteurs SSH/MCP.\n"
        "APRÈS UNE ACTION NAVIGATEUR : l'aperçu Live Browser montre déjà la page à l'écran. "
        "Ta réponse fait UNE phrase factuelle et s'arrête là — par exemple « J'ai ouvert "
        "brainrot-fortnite.com. ». INTERDIT après une action navigateur : lister les commandes "
        "browser.* disponibles, proposer une étape suivante, donner des exemples de demandes, "
        "ajouter « si tu veux… ». Quand la demande dit « ne fais aucune autre action », tu "
        "n'exécutes aucun autre outil et tu n'ajoutes aucune suggestion, pas même entre parenthèses.\n"
        "RÈGLE SUR LES ERREURS GOOGLE SHEETS : un code d'erreur n'atteste jamais une limite de ta part.\n"
        "- SHEET_TAB_NOT_FOUND : le fichier est TROUVÉ, seul l'onglet n'a pas été identifié. Ne dis "
        "jamais « Google Sheet privé » dans ce cas ; propose la détection automatique ou la liste "
        "des onglets.\n"
        "- SHEET_ACCESS_DENIED : seul ce cas justifie une mention d'autorisation/authentification.\n"
        "- Après un échec, si on te demande « affiche-moi les différences », reprends la TACHE_PRECEDENTE "
        "donnée en contexte : corrige la cause (résolution d'onglet) plutôt que d'envoyer un tutoriel.\n"
        "TRAVAIL SUR LES PROJETS (bot Discord, Brainrot, dépôts…) :\n"
        "- Le spécialiste développement (coding) est routé automatiquement pour les demandes dev. "
        "Quand tu agis toi-même sur un projet : résous-le avec project.select/project.context "
        "(jamais de nom deviné — si plusieurs candidats, demande lequel), travaille avec les outils "
        "réels (fs. lecture/modif, git.status/git.diff, test.run) et conclue uniquement sur des faits "
        "d'outils.\n"
        "- Messages courts au fil de l'eau : « J'analyse le projet. », « J'ai trouvé le module "
        "concerné. », « Les tests échouent. Je corrige. », « Tests réussis. Je vérifie le diff. » "
        "Après la fin d'un travail, une phrase factuelle sur le diff réel suffit.\n"
        "- INTERDIT : le rapport passe-partout « Analyse du Code » avec des sections génériques, les "
        "métriques inventées (CPU/mémoire d'un fichier), « J'ai corrigé » sans fs.write réussie, "
        "« Les tests passent » sans exit_code=0, « déploiement terminé » sans preuve."
    ),
))

_register(AgentSpec(
    id="coding", name="Coding Agent", role="Développement", icon="code", model_role="coding",
    model="", fast_model="", deep_model="", max_iterations=16, token_budget=2400,
    context_size=12, timeout=180.0, keep_alive="5m", thinking=True,
    tools=("fs.", "git.", "terminal.", "test.", "process.", "project.", "code.", "github.",
           "ssh.", "deploy.", "knowledge.", "browser.", "discord."),
    system_prompt=(
        "Tu es l'agent de développement de JARVIS. Tu travailles sur les PROJETS RÉELS de "
        "l'utilisateur (Bot Discord, Brainrot, dépôts locaux…) en suivant la boucle de dev.\n"
        "STYLE (impératif) : messages COURTS et factuels au fil de l'eau, un par étape : "
        "« J'analyse le projet. », « J'ai trouvé le module concerné. », « Les tests échouent sur "
        "giveaway.js, je corrige. », « Tests réussis. Je vérifie le diff. ». Jamais d'introduction "
        "du type « Analyse du Code », jamais de rapport passe-partout, jamais de chiffres inventés "
        "(CPU/mémoire/UX d'un projet que tu n'as pas mesurés).\n"
        "PROJET NON NOMMÉ : si la demande parle de « mon bot Discord », « le bot » ou "
        "d'un projet sans donner de chemin, appelle project.discord_bot (pour un bot) ou "
        "project.select — JAMAIS de chemin deviné. L'outil te rend le vrai chemin, le "
        "framework et le point d'entrée réels.\n"
        "AVANT DE LANCER UN BOT : appelle process.find pour savoir si une instance tourne "
        "DÉJÀ. Deux bots sur le même jeton rendent tout test ininterprétable : dans ce cas "
        "tu redémarres proprement (process.restart) ou tu observes l'instance existante.\n"
        "AUDIT D'UN PROJET (« analyse mon bot », « dans quel état est… ») : appelle "
        "project.audit. Cet outil exécute lui-même toute la séquence réelle (git, "
        "manifeste, arborescence, commandes, point d'entrée, scripts, processus en "
        "cours, état Discord) et te rend les constats. Ta conclusion reprend CES "
        "constats, sans en ajouter et sans en retirer. Ce qu'il signale comme non "
        "vérifié, tu le dis tel quel.\n"
        "CHEMIN ABSOLU FOURNI : si la demande donne un chemin absolu explicite "
        "(commençant par `/`), tu l'exécutes DIRECTEMENT avec fs.read / fs.write / fs.list "
        "sur ce chemin tel quel, sans project.select ni project.context — il n'y a pas de "
        "projet à résoudre. Une demande de création de fichier avec un chemin absolu se "
        "traite par UN appel fs.write, immédiatement.\n"
        "BOUCLE DE DEV (obligatoire, quand la demande vise un PROJET) :\n"
        "1. SI la demande nomme un projet (« le bot Discord », « BrainrotFortnite », « le dépôt »…) : "
        "appelle project.select avec ce nom pour le RÉSOUDRE réellement. Si project.select répond "
        "« Plusieurs projets correspondent », pose UNE question pour lever l'ambiguïté — ne choisis "
        "jamais silencieusement.\n"
        "2. project.context pour lire stack, scripts, tests connus et README du projet avant d'agir.\n"
        "3. git.status AVANT toute modification pour connaître l'état réel du dépôt.\n"
        "COUPE PARTIELLE : si un outil est refusé ou inexistant, ne t'arrêtes pas pour autant — "
        "reprends avec un outil RÉEL (fs.list, fs.read, git.status, terminal.run) et va jusqu'au "
        "bout de la demande.\n"
        "Chemin des fichiers : UNE FOIS le projet sélectionné, les outils fs.read, fs.list, "
        "fs.search, fs.write prennent des chemins RELATIFS à la racine du projet — écris par "
        "exemple `fs.read path=package.json`, jamais « Bot Discord/package.json » (le nom du "
        "projet n'est pas un dossier dans le projet). Tu ne vois pas d'adresse absolue dans tes "
        "réponses.\n"
        "4bis. Lis TOUJOURS un fichier (fs.read) avant de le modifier. Identifie les fichiers concernés "
        "par la demande (fs.search si besoin).\n"
        "5. Modifie RÉELLEMENT (fs.write / patch) puis regarde git.diff : tu dois savoir exactement "
        "ce qui a changé.\n"
        "6. Lance les vrais tests / lint : `test.run` ou `terminal.run` avec les commandes réelles "
        "du projet (project.context les liste). Les tests ne « passent » QUE si exit_code=0.\n"
        "7. Si un test échoue, lis la vraie erreur (test.output / process.logs), corrige, relance.\n"
        "8. Ne termine que quand la validation passe réellement, ou quand un blocage est réel : "
        "explique-le en quelques mots et commence ta conclusion par « ACTION BLOQUÉE ».\n"
        "RÈGLES D'HONNÊTETÉ (absolues) :\n"
        "- Tu n'annonces JAMAIS « j'ai corrigé » sans un fs.write/patch réel réussi, JAMAIS « les "
        "tests passent » sans exit_code=0, JAMAIS « déploiement terminé » sans preuve réelle.\n"
        "- Tu ne changes pas de code juste pour prouver que tu travailles : si une demande est en "
        "lecture seule (analyser, expliquer, lister), tu ne modifies rien.\n"
        "- Tu ne commites JAMAIS sans demande explicite de l'utilisateur.\n"
        "- Dans le contexte d'un PROJET, parle du projet par son nom plutôt que par un chemin "
        "absolu. Si l'utilisateur t'a lui-même donné un chemin absolu, tu peux le reprendre.\n"
        "VALIDATION DANS LE VRAI PRODUIT : quand la demande porte sur un bot ou un site, "
        "tester le code ne suffit pas. Lance le processus (process.start), puis vérifie le "
        "comportement RÉEL : browser.navigate / browser.click / browser.type / "
        "browser.read_page pour une interface web ou Discord, discord.* pour l'API. "
        "Tu lis la vraie réponse avec browser.read_page ou process.logs. Si le service "
        "n'est pas authentifié ou pas joignable, tu t'arrêtes avec « ACTION BLOQUÉE » et la "
        "raison réelle — tu ne décris JAMAIS une interface que tu n'as pas ouverte.\n"
        "PROCESSUS LONGUES : pour un serveur ou une tâche longue, utilises process.start (ne bloque "
        "pas), suis l'avancement avec process.status / process.logs, arrête avec process.stop.\n"
        "Rapporte de façon factuelle ce que tu as changé et le résultat des tests."
    ),
))

_register(AgentSpec(id="discord", name="Discord Agent", role="Discord", icon="chat",
    model_role="fast", model="qwen3.5:4b", tools=("discord.",), max_iterations=3,
    max_context=6, timeout=45.0, priority=95, token_budget=700,
    system_prompt="Tu es DiscordAgent. Utilise uniquement les outils Discord et ne rapporte que des résultats réels provenant de l'API Discord. Jamais de message inventé."))
_register(AgentSpec(id="analysis", name="Analysis Agent", role="Analyse", icon="database",
    model_role="fast", model="gemma4:12b-mlx", fast_model="gemma4:12b-mlx", fallback_model="qwen3.5:4b", tools=("google.sheets.", "file.", "document.", "memory.", "brainrot.", "db.query"),
    max_context=6, timeout=90.0, priority=80, token_budget=1200,
    system_prompt=(
        "Tu es AnalysisAgent, responsable opérationnel de brainrot-fortnite.com.\n"
        "Pour toute question sur l'état du site, les membres, les inscriptions, les emails "
        "ou « fais-moi le point » : appelle brainrot.analytics.summary. Il interroge la VRAIE "
        "base et te rend l'état, les évolutions, les points à surveiller et les actions "
        "possibles. Tu restitues ces constats sans en ajouter.\n"
        "Autres outils réels : brainrot.analytics.registrations / activity / email_status, "
        "brainrot.users.unverified, brainrot.blog.list, brainrot.brainrots.list, "
        "brainrot.site.health, brainrot.site.inspect.\n"
        "RÈGLE ABSOLUE : tu n'inventes jamais un nombre. Si l'outil dit qu'une donnée n'est "
        "pas disponible, tu le répètes tel quel — « Cette donnée n'est pas disponible » — "
        "sans l'estimer ni la remplacer par un ordre de grandeur.")))
_register(AgentSpec(id="vision", name="Vision Agent", role="Vision", icon="eye",
    model_role="fast", model="qwen3-vl:8b-instruct", fast_model="qwen3-vl:8b-instruct", fallback_model="qwen3.5:4b", tools=("image.", "file."), max_context=4, context_size=4,
    timeout=90.0, priority=80, token_budget=900,
    system_prompt="Tu es VisionAgent. Décris et analyse uniquement les images reçues."))
_register(AgentSpec(id="memory", name="Memory Agent", role="Mémoire", icon="brain",
    model_role="fast", model="qwen3.5:4b", tools=("memory.", "knowledge."), max_context=8,
    timeout=45.0, priority=85, token_budget=700,
    system_prompt="Tu es MemoryAgent. Réponds seulement à partir de la mémoire et des connaissances réelles."))

_register(AgentSpec(
    id="research", name="Research Agent", role="Recherche", icon="search", model_role="fast",
    tools=("web.", "http.", "knowledge.", "memory.", "github."),
    system_prompt=(
        "Tu es l'agent de recherche de JARVIS. Tu cherches des informations sur le web et dans la base "
        "de connaissances, tu croises les sources et tu produis une synthèse courte et factuelle. "
        "Cite les URL utilisées. N'invente jamais une source."
    ),
))

_register(AgentSpec(
    id="browser", name="Browser Agent", role="Navigation", icon="globe", model_role="fast",
    tools=("web.", "browser.", "http.", "google."),
    system_prompt=(
        "Tu es l'agent de navigation de JARVIS. Tu pilotes la session navigateur RÉELLE de "
        "VELKO (Chromium persistant), visible en direct sur son écran de droite.\n"
        "MÉTHODE : browser.navigate pour ouvrir, browser.read_page pour LIRE le contenu réel, "
        "browser.click / browser.type / browser.scroll pour agir, browser.status pour l'état.\n"
        "Tu ne décris JAMAIS une page que tu n'as pas ouverte, et tu ne cites jamais un contenu "
        "que browser.read_page ne t'a pas renvoyé. Rapporte les URL, titres et textes réels.\n"
        "Si la page réclame une connexion, un captcha ou un choix manuel, appelle browser.pause "
        "avec ce qui est attendu : l'utilisateur agit une fois, la session reste ensuite ouverte.\n"
        "Si tu es bloqué, commence ta conclusion par « ACTION BLOQUÉE » et donne la raison réelle."
    ),
))

_register(AgentSpec(
    id="email", name="Email Agent", role="Courrier", icon="mail", model_role="fast",
    tools=("email.", "crm.", "contact."),
    system_prompt=(
        "Tu es l'agent courrier de JARVIS. Tu relèves la boîte de réception, tu la tries et tu "
        "prépares des réponses. Le tri est produit par l'outil email.process_inbox : rapporte ses "
        "catégories et ses motifs tels quels, ne reclasse jamais un message de ta propre initiative "
        "et n'invente jamais un expéditeur, un objet ou un montant que tu n'as pas lu. "
        "Un envoi d'email demande toujours la confirmation de l'utilisateur : annonce-le clairement "
        "et attends la validation."
    ),
))

_register(AgentSpec(
    id="system", name="System Agent", role="Infrastructure", icon="server", model_role="reasoning",
    tools=("ssh.", "system.", "terminal.", "docker.", "cpanel.", "whm.", "db.", "ftp.", "deploy.", "web.check",
           "connector."),
    system_prompt=(
        "Tu es l'agent système de JARVIS. Tu diagnostiques et répares serveurs et services. "
        "Méthode : constater l'état, lire les logs, identifier la cause, proposer ou appliquer le correctif, "
        "puis VÉRIFIER que le service répond de nouveau. Commence toujours par un diagnostic en lecture seule. "
        "Les actions destructives demandent une confirmation : annonce-les clairement."
    ),
))

_register(AgentSpec(
    id="blender", name="Blender Agent", role="Spécialiste 3D", icon="cube",
    model_role="blender", max_iterations=10,
    # Outils 3D UNIQUEMENT : pas d'agenda, pas de mail, pas de SSH, pas de web.
    tools=("blender.", "avatar."),
    system_prompt=(
        "Tu es le spécialiste Blender de JARVIS. Tu ne fais QUE de la 3D : "
        "meshes, matériaux, shape keys, rig, animations, cheveux, vêtements, "
        "rendu, export GLB/GLTF, optimisation.\n"
        "MÉTHODE OBLIGATOIRE :\n"
        "- INSPECTE AVANT DE MODIFIER. Commence toujours par blender.inspect "
        "(ou avatar.job.inspect) pour connaître les objets, meshes, armatures, "
        "matériaux, shape keys et modificateurs RÉELS de la scène.\n"
        "- N'invente JAMAIS un nom d'objet, de matériau ou de shape key. "
        "Utilise exactement ceux que l'inspection a renvoyés.\n"
        "- Ne modifie jamais le fichier maître : on travaille toujours sur une "
        "copie de révision.\n"
        "- Pour un humanoïde final, n'utilise pas de primitives (cube, cylindre, "
        "sphère) comme stratégie de construction du corps. Les primitives ne "
        "servent qu'au blockout, aux guides et aux lumières. S'il n'existe pas "
        "de vraie base humanoïde, dis-le au lieu d'en fabriquer une en tubes.\n"
        "- Tu ne regardes pas les images : l'analyse visuelle t'est fournie sous "
        "forme de JSON structuré par le moteur de vision. N'invente pas ce que "
        "contient une référence.\n"
        "- Rapporte factuellement ce que tu as modifié et le résultat vérifié."
    ),
))

_register(AgentSpec(
    id="task", name="Task Agent", role="Tâches", icon="check", model_role="fast",
    tools=("task.", "calendar.", "automation.", "notify.", "memory.search"),
    system_prompt=(
        "Tu es l'agent de tâches de JARVIS. Tu crées, suis et clôtures les tâches, planifies les "
        "automatisations et gères l'agenda. Sois bref et opérationnel."
    ),
))

_register(AgentSpec(
    id="blog", name="Blog Agent", role="Éditorial", icon="newspaper", model_role="default",
    max_iterations=10,
    tools=("blog.", "web.search", "web.fetch", "memory.search", "google.sheets.read"),
    system_prompt=(
        "Tu es l'agent éditorial de brainrot-fortnite.com. Tu rédiges des articles pour le blog "
        "RÉEL du site (table blog_posts, URL publique /blog/<slug>).\n"
        "RÈGLE ABSOLUE — SOURCES :\n"
        "- Tu n'inventes JAMAIS une date, un code, une statistique, un nom de Brainrot ni une "
        "annonce Fortnite. Chaque fait important doit venir d'une source réelle fournie par "
        "blog.research (données du site, Wiki, codes, mémoire, recherche web).\n"
        "- Si une information est incertaine, tu ne la publies pas : l'article passe en "
        "NEEDS_REVIEW. Un article vide vaut mieux qu'un article faux.\n"
        "- Tu ne recopies jamais le texte d'une source externe : tu rédiges un contenu original "
        "et tu conserves l'URL comme source.\n"
        "STYLE :\n"
        "- Français naturel, ton communautaire, clair, énergique, accessible. Pas de style "
        "corporate, pas d'emoji dans le corps de l'article.\n"
        "- Jamais d'introduction artificielle du type « Dans cet article, nous allons ». "
        "Tu entres directement dans le sujet.\n"
        "- Structure en HTML simple : paragraphes <p>, intertitres <h2>, listes <ul><li>, "
        "tableaux quand c'est utile. Minimum deux <h2>, dont une section finale de synthèse.\n"
        "MÉTHODE :\n"
        "- blog.research collecte les faits, blog.draft crée le brouillon réel sur le site, "
        "blog.publish publie PUIS vérifie l'URL publique, blog.notify_discord ne peut envoyer "
        "qu'après cette vérification.\n"
        "- Tu ne publies jamais de ta propre initiative si la commande dit « brouillon », "
        "« prépare » ou « ne publie pas »."
    ),
))

# Politique de modèles : ces valeurs sont des overrides par agent, jamais un
# chargement global. Ollama ne reçoit donc que le modèle du spécialiste actif.
for _spec in AGENTS.values():
    # coding garde model="" : le rôle « coding » suit la résolution du backend
    # (ai.coding_model → ai.default_model → premier fournisseur connecté) au lieu
    # d'écraser le choix par un modèle local faible.
    if _spec.id != "coding" and not _spec.model:
        _spec.model = "qwen3.5:4b" if _spec.id != "jarvis" else "qwen3.5:4b"
    _spec.max_context = min(_spec.max_context or 8, 12)
    _spec.context_size = _spec.context_size or _spec.max_context
    _spec.fast_model = _spec.fast_model or _spec.model
    _spec.fallback_model = _spec.fallback_model or _spec.fast_model
    _spec.deep_model = _spec.deep_model or _spec.fast_model


def route_request(text: str) -> tuple[str, str]:
    """Routage déterministe. Retourne (agent, mode), sans appel LLM."""
    t = (text or "").lower()
    if re.search(r"\b(bonjour|salut|hello|merci|bonsoir)\b", t) and not re.search(r"discord|code|image|sheet", t):
        return "jarvis", "conversation"
    # Le bot / un projet nommé passe AVANT la route système : « processus en
    # cours de mon bot » parle du bot, pas de l'état du Mac. Sans cette
    # priorité, l'audit partait chez l'agent système et cherchait du SSH.
    if re.search(r"\b(bot\s*discord|discord\s*bot|mon\s+bot|le\s+bot|du\s+bot|drakobot)\b", t):
        return "coding", "agent"
    if re.search(r"\b(cpu|ram|mémoire vive|processus|disque|système|état du mac)\b", t) and not re.search(
            r"\b(projet|repo|bot|dépôt|depot)\b", t):
        return "system", "direct_tool"
    # DÉVELOPPEMENT / PROJETS : détecté avant Discord pour ne jamais priver un
    # projet (« le bot Discord », « BrainrotFortnite ») des outils dev réels.
    if re.search(r"\b(projet|repo|repository)\b", t) and re.search(
            r"\b(analyse|analyse|structure|corrige|corrigé|modifie|modifier|test|lint|"
            r"implémente|implemente|travaille|fonctionnalité|feature|bug|code|lance|commande)\b", t):
        return "coding", "agent"
    if re.search(r"\b(bot discord|mon bot|ton bot|le bot|votre bot|du bot)\b", t) and re.search(
            r"\b(test|teste|lint|code|corrige|modifie|modifier|ajoute|ajouter|implémente|implemente|"
            r"analyse|structure|bug|lance|fonctionnalité|commande|giveaway)\b", t):
        return "coding", "agent"
    if re.search(r"\b(ajoute|ajouter|implémente|implemente|corrige|corriger|répare|reparer)\b", t) and re.search(
            r"\b(bot|commande|serveur|feature|fonctionnalité)\b", t):
        return "coding", "agent"
    if re.search(r"\b(test|teste les tests|lint|linter|lance les tests|lance les commandes)\b", t):
        return "coding", "agent"
    if re.search(r"\b(fichiers? modifiés?|dernier commit|git)\b", t):
        return "coding", "agent"
    if re.search(r"\b(code|fonction|bug|python|javascript|implémente)\b", t):
        return "coding", "agent"
    # NAVIGATION RÉELLE : « ouvre telle page », « va sur … », ou une URL nue.
    # Sans cette route, la demande retombait en conversation et le modèle
    # n'avait jamais les outils browser.* : la mission se terminait en
    # « aucun outil n'a abouti » alors que la session existait.
    if re.search(r"https?://|\bwww\.", t) or re.search(
            r"\b(ouvre|ouvrir|navigue|naviguer|va sur|rends-toi sur|consulte|consulter|"
            r"page web|site web|dans (ton|le) navigateur|sur le site)\b", t):
        return "browser", "agent"
    if re.search(r"discord|salon|serveur discord", t):
        return "discord", "fast"
    # « Fais-moi le point » sans autre précision : c'est le site, pas une
    # conversation. C'est la commande naturelle de synthèse opérationnelle.
    if re.search(r"\bfais[- ]moi le point\b|\bfaire le point\b|\bo[uù] en (est|sommes)",
                 t) and not re.search(r"\b(bot|projet|code|repo)\b", t):
        return "analysis", "agent"
    # brainrot-fortnite.com : l'exploitation du site a ses propres outils réels.
    if re.search(r"\bbrainrot[- ]?fortnite\b|\bbrainrots?\b|\ble site\b|\bdu site\b", t) and re.search(
            # Radicaux SANS \b final : « synthèse », « statistiques », « améliorer »
            # ou « inscriptions » ne se terminent pas au radical.
            r"\b(point|synth|[ée]tat|analyse|comment va|statistiq|membre|inscription|"
            r"connexion|email|blog|article|catalogue|sheet|am[ée]lior|surveill|attention)", t):
        return "analysis", "agent"
    if re.search(r"\b(sheet|tableur|excel|classeur|analyse les données)\b", t):
        return "analysis", "agent"
    if re.search(r"\b(image|photo|capture|vision)\b", t):
        return "vision", "agent"
    if re.search(r"\b(que sais-tu|souviens|mémoire|retiens)\b", t):
        return "memory", "agent"
    if re.search(r"\b(blog|article|rédige|publie)\b", t):
        return "blog", "agent"
    if re.search(r"\b(recherche|cherche sur internet|sources|actualité)\b", t):
        return "research", "agent"
    return "jarvis", "conversation"


# Sous-ensembles d'outils par intention 3D.
#
# `jarvis-blender` tourne avec num_ctx=4096 : les 27 outils 3D pèsent à eux
# seuls ~4 800 tokens de schémas, ce qui sature le contexte et fait boucler le
# modèle. On ne lui envoie donc que les outils utiles à l'action détectée.
BLENDER_TOOLS_BY_ACTION: dict[str, tuple[str, ...]] = {
    "avatar.craft": (
        "blender.inspect", "blender.status", "avatar.engine.inspect",
        "avatar.engine.apply", "avatar.engine.build",
        "blender.generate_preview", "avatar.revision.list",
    ),
    "avatar.update_from_reference": (
        "avatar.reference.add", "avatar.reference.list", "avatar.reference.analyze",
        "avatar.update_from_reference", "avatar.revision.list",
    ),
    "blender.create_model": ("blender.create_model", "blender.inspect", "blender.status"),
    "blender.modify_model": ("blender.inspect", "blender.modify_model", "blender.generate_preview"),
    "blender.rig": ("blender.inspect", "blender.rig"),
    "blender.animate": ("blender.inspect", "blender.animate"),
    "blender.material": ("blender.inspect", "blender.material", "blender.texture"),
    "blender.render": ("blender.inspect", "blender.render", "blender.generate_preview"),
    "blender.export": ("blender.inspect", "blender.export"),
    "blender.optimize": ("blender.inspect", "blender.optimize"),
    "blender.inspect": ("blender.inspect", "blender.status", "avatar.engine.inspect"),
}


def blender_tools_for(action: str) -> tuple[str, ...]:
    """Outils à exposer au spécialiste pour cette action (vide = tous les 3D)."""
    return BLENDER_TOOLS_BY_ACTION.get(action, ())


class AgentManager:
    """État runtime des agents, persisté pour survivre au redémarrage."""

    def __init__(self, db: Database, events) -> None:
        self._db = db
        self._events = events
        for spec in AGENTS.values():
            self._db.execute(
                "INSERT INTO agents_state(id, status, last_activity_at, runs, enabled) VALUES(?,?,?,0,1) "
                "ON CONFLICT(id) DO NOTHING", (spec.id, "standby", None))
        # Aucun agent ne reste « running » après un redémarrage.
        self._db.execute("UPDATE agents_state SET status='standby', current_task_id='', current_action=''"
                         " WHERE status IN ('active','running')")

    @staticmethod
    def ids() -> list[str]:
        return [a for a in AGENTS if a != "jarvis"]

    @staticmethod
    def spec(agent_id: str) -> AgentSpec | None:
        return AGENTS.get(agent_id)

    def allowed_tools(self, agent_id: str, registry) -> list:
        spec = AGENTS.get(agent_id)
        if not spec:
            return []
        tools = registry.for_agent(agent_id)
        if not spec.tools:
            return tools
        return [t for t in tools if any(t.id.startswith(p) for p in spec.tools)]

    def set_state(self, agent_id: str, status: str, *, action: str = "",
                  task_id: str = "", error: str = "") -> None:
        now = time.time()
        self._db.execute(
            "UPDATE agents_state SET status=?, last_activity_at=?, current_action=?, current_task_id=?, "
            "last_error=?, runs = runs + ? WHERE id=?",
            (status, now, action[:200], task_id, error[:400], 1 if status == "active" else 0, agent_id),
        )
        payload = {"id": agent_id, "status": status, "action": action, "task_id": task_id,
                   "ts": now, "error": error}
        event = {"active": "agent.started", "standby": "agent.idle",
                 "error": "agent.failed", "done": "agent.completed"}.get(status, "agent.progress")
        self._events.emit(event, payload)

    def list(self) -> list[dict[str, Any]]:
        rows = {r["id"]: r for r in self._db.query("SELECT * FROM agents_state")}
        out = []
        for spec in AGENTS.values():
            r = rows.get(spec.id)
            out.append({
                "id": spec.id, "name": spec.name, "role": spec.role, "icon": spec.icon,
                "status": (r["status"] if r else "standby"),
                "model": spec.model, "fast_model": spec.fast_model,
                "deep_model": spec.deep_model, "fallback_model": spec.fallback_model,
                "thinking": spec.thinking, "context_size": spec.context_size,
                "tools": list(spec.tools),
                "max_context": spec.max_context, "timeout": spec.timeout,
                "keep_alive": spec.keep_alive, "priority": spec.priority,
                "token_budget": spec.token_budget,
                "last_activity_at": (r["last_activity_at"] if r else None),
                "current_task_id": (r["current_task_id"] if r else ""),
                "current_action": (r["current_action"] if r else ""),
                "last_error": (r["last_error"] if r else ""),
                "runs": (r["runs"] if r else 0),
                "enabled": bool(r["enabled"]) if r else True,
                "model_role": spec.model_role,
                "tool_prefixes": list(spec.tools),
            })
        return out

    def running_count(self) -> int:
        return int(self._db.scalar("SELECT COUNT(*) FROM agents_state WHERE status='active'") or 0)

    def set_enabled(self, agent_id: str, enabled: bool) -> bool:
        if agent_id not in AGENTS:
            return False
        self._db.execute("UPDATE agents_state SET enabled=? WHERE id=?", (1 if enabled else 0, agent_id))
        return True

    def is_enabled(self, agent_id: str) -> bool:
        row = self._db.one("SELECT enabled FROM agents_state WHERE id=?", (agent_id,))
        return bool(row["enabled"]) if row else True
