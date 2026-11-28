# QA Recipe Converter v2.0

Convertissez automatiquement vos documents Word de recette en fichiers Excel structurés, scripts Gherkin et projets Cypress.

Application web avec backend Django (API REST) et frontend React (TypeScript + Tailwind CSS).

---

## Architecture

```
qa_recipe/
├── qa_recipe_converter/          # Backend Django (API REST)
│   ├── config/                   # Configuration Django
│   │   └── settings/
│   │       ├── base.py           # Paramètres communs
│   │       ├── development.py    # Dev (DEBUG=True, SQLite)
│   │       └── production.py     # Prod (sécurité renforcée)
│   ├── apps/
│   │   ├── core/                 # App principale (modèles, vues)
│   │   ├── parser/               # Moteur de parsing & génération
│   │   ├── api/                  # API REST (DRF)
│   │   └── teams/                # Gestion équipes & projets
│   ├── tests/                    # Suite de tests (629 tests)
│   ├── Dockerfile
│   └── docker-compose.yml
│
├── qa_recipe_converter_frontend/ # Frontend React
│   ├── src/
│   │   ├── components/           # Composants UI + Layout
│   │   ├── pages/                # Pages (Login, Dashboard, Convert...)
│   │   ├── api/                  # Client API Axios
│   │   ├── context/              # Contextes (Auth, Theme)
│   │   └── types/                # Types TypeScript
│   ├── Dockerfile
│   └── nginx.conf
│
└── stitch/                       # Maquettes UI (Stitch)
```

---

## Fonctionnalités

| Fonctionnalité | Description |
|---|---|
| Upload Word | Glisser-déposer ou sélection `.docx` / `.doc` |
| Détection intelligente | Reconnaissance automatique des colonnes (FR/EN) |
| Fusion de cellules | Gestion automatique des cellules fusionnées |
| Prévisualisation éditable | Tableau interactif éditable avant export |
| Export Excel | 2 feuilles : *Use Cases* + *Cas automatisé* |
| Scripts Gherkin | Génération `.feature` BDD/Cucumber |
| Projet Cypress | Projet Cypress complet avec step definitions |
| Ouvrir VS Code | Ouvre le projet Cypress dans VS Code |
| Teams & Projets | Multi-utilisateurs, rôles, invitations |
| Dashboard | KPIs, statistiques, activité récente |

### Comptes d'équipe et tickets Kanban

- Un propriétaire peut créer un compte avec nom d'utilisateur, mot de passe (8 caractères minimum) et rôle, puis l'ajouter à une ou plusieurs équipes dont il est propriétaire depuis le tableau de l'équipe.
- Depuis un sprint, le tableau Kanban permet de créer, modifier et supprimer des tickets autonomes, de les assigner à un membre du projet et de les déplacer entre les colonnes. Chaque ticket reçoit un identifiant `TKT-XXXXXXXX` et peut être recherché par numéro. Les tickets restent indépendants des cas extraits d'une recette et des anomalies du projet.
- L'affectation en lot des cas de test propose une case « Sélectionner tous les cas » parmi les cas non assignés.

Les migrations `qamanagement.0003_defect_sprint_defect_ticket_number` et `qamanagement.0004_sprintticket` doivent être appliquées avant d'utiliser les tickets dans un environnement existant (`python manage.py migrate`). L'API de création des comptes est `POST /api/teams/members/create/` et accepte `username`, `password`, `role` et `team_slugs`; elle vérifie que l'utilisateur connecté est propriétaire de toutes les équipes demandées. Les tickets de sprint utilisent des routes dédiées imbriquées sous `/api/teams/{team}/projects/{project}/sprints/{sprint}/tickets/`.

### Rapports, performance, anomalies et régressions

Routes sous `/api/teams/{team}/projects/{project}/` ; `?sprints=id1,id2` restreint le périmètre (absent = projet entier).

| Route | Rôle |
|---|---|
| `report/` · `report/pdf/` | Rapport JSON / PDF du projet, d'un ou plusieurs sprints |
| `sprints/{id}/report/pdf/` | Rapport PDF d'un sprint |
| `performance/` | Score /100, niveau, composantes, comparaison des sprints |
| `evolution/` | Courbe d'évolution (pas journalier, hebdomadaire au-delà de 62 jours) |
| `regressions/` | Cas passés de « Passé » à « Échoué » et anomalies liées |
| `defects/{id}/comments/` · `defects/{id}/history/` | Suivi d'une anomalie |
| `assignable-members/` | Membres de l'équipe assignables depuis le Kanban |
| `sprints/{id}/` (PATCH) | Nom, objectif, dates ou `duration_days`, statut |

`/api/teams/{team}/performance/` compare tous les projets visibles.

- **Score** : 40 % réussite + 25 % avancement + 20 % résolution des anomalies + 15 % stabilité. Excellent ≥ 85, Bon ≥ 70, Moyen ≥ 50, Critique sinon ; « Non évalué » sans exécution. Pondérations dans `apps/qamanagement/analytics.py`.
- **Historique** : tout changement de statut crée une `UseCaseExecution` (signal), source des courbes et des régressions.
- **Sprint terminé/clôturé** : ses résultats sont figés ; le rouvrir pour les modifier.
- **Cas par sprint** : chaque sprint garde ses cas. L'assignation (`unassigned-ucs/?sprint=`, `assignments/`) exclut et refuse les cas déjà planifiés dans un autre sprint ; un cas ne se partage qu'explicitement (« Reporter les cas », campagne de non-régression). Données antérieures : `python manage.py audit_sprint_cases` (rapport) puis `--fix` (retire les doublons sans résultat ni capture).
- **Statut par sprint** : chaque affectation porte le résultat du cas dans SON sprint (`UseCaseAssignment.status`) ; `ExtractedUseCase.status` reste le dernier résultat connu. Un cas reporté peut donc être « Échoué » dans un sprint et « Passé » dans un autre. Les cartes du Kanban indiquent les autres sprints du cas (`other_sprints`, badge « Aussi dans »).
- **Assignation** : recherche (n° CAS, ticket Jira, titre), filtre par statut, et répartition équilibrée entre plusieurs testeurs (`assignments/` avec `user_ids`, réponse `distribution`).
- **Import de cas dans un sprint (Kanban)** : bouton « 📥 Importer des cas » (fichier → aperçu → responsables). `POST /api/teams/{team}/projects/{project}/sprints/{id}/import-cases/` (`dry_run=true` pour l'aperçu, `user_ids` pour assigner ou répartir). Les cas importés n'appartiennent qu'à ce sprint (recette liée via `ConversionJob.sprint`) ; seul « Reporter les cas » les partage. Un cas déjà présent (même intitulé et mêmes tickets Jira) ou répété dans le document est ignoré ; tout est créé en une transaction.
- **Aperçu d'import** : `POST /api/upload/analyze/` (rien n'est enregistré) détaille les colonnes reconnues, les lignes ignorées et les tickets Jira ; affiché dès le choix du fichier.
- **Validation Jira** : format `PROJET-123`, puis existence si Jira est configuré — `POST /api/jobs/{id}/jira/validate/` (recette) et `POST /api/teams/{team}/projects/{project}/jira/validate-tickets/` (Kanban).
- **Import Word** : la colonne « Tickets Jira » (variantes : « Ticket Jira », « N° Ticket », « Numéro Jira »…) est importée ; plusieurs tickets dans une cellule deviennent « QA-1, QA-2 ».
- **Charte GLOBAL ITEC** (logo, pied de page) : `apps/teams/reports.py`.

Mise à jour d'un environnement existant : `pip install -r requirements.txt` puis `python manage.py migrate` (les migrations reprennent l'historique existant).

### Automatisation et intégrations

| Fonction | API (sous `/api/teams/{team}/projects/{project}/`) | Interface |
|---|---|---|
| Réglages (alertes, Jira) | `qa-settings/` | Projet → Réglages |
| Export Excel | `report/xlsx/`, `defects/export/xlsx/` | Rapports, Anomalies |
| Envoi du rapport | `sprints/{id}/report/send/` (automatique à la clôture) | Kanban |
| Report de cas | `sprints/{id}/carry-over/` | Kanban |
| Campagne de non-régression | `regression-campaign/` | Anomalies → Régressions |
| Import de résultats | `test-results/import/` (`dry_run` = aperçu) | Kanban |
| Jira | `jira/sync/`, `defects/{id}/jira/` | Réglages, fiche d'anomalie |

**E-mails** — définir `EMAIL_HOST`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` et `FRONTEND_URL`.

**Jira Cloud** — définir `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, puis lier le projet dans Réglages (super-admin). Pour le temps réel, créer un webhook vers `https://<backend>/api/integrations/jira/webhook/` avec le secret `JIRA_WEBHOOK_SECRET` (événements : ticket créé / modifié / supprimé, commentaire créé). En complément, planifier `python manage.py sync_jira`.

| Jira (catégorie) | Application |
|---|---|
| À faire | Ouvert / Réouvert |
| En cours | En cours |
| Terminé | Résolu / Fermé |

**Cypress** — `npm run cy:report` dans le projet généré produit `results/*.xml`. Les tests doivent porter le numéro du cas (`CAS-001`) dans leur titre, ce que fait le projet généré.

**Stockage S3** — définir `S3_BUCKET` et les variables `S3_*` (voir `.env.example`), puis :

```bash
python manage.py media_storage --check
python manage.py media_storage --migrate --dry-run
python manage.py media_storage --migrate
```

---

## Pipeline de conversion

```
Word (.docx)
    │
    ▼
┌─────────────────────┐
│   DocxParser        │  → Extraction des tableaux UC
└─────────────────────┘
    │
    ▼
┌─────────────────────┐
│   Preview éditable  │  → Interface web éditable
│   (Django views)    │  → Marquage des cas automatisables
└─────────────────────┘
    │
    ├──────────────────────────────────────────┐
    ▼                                          ▼
┌─────────────────────┐          ┌─────────────────────────────┐
│   ExcelGenerator    │          │    GherkinGenerator         │
│   → .xlsx           │          │    → .feature + Cypress    │
└─────────────────────┘          └─────────────────────────────┘
```

---

## Démarrage rapide

### Option 1 — Docker (production)

```bash
cd qa_recipe_converter
cp .env.example .env
docker compose up --build
```

- **Frontend** : http://localhost:80
- **Backend API** : http://localhost:8000/api/

### Option 2 — Docker (développement avec Mailpit)

```bash
cd qa_recipe_converter
cp .env.example .env
docker compose -f ../docker-compose.yml -f ../docker-compose.dev.yml up --build
```

- **Frontend** : http://localhost:80
- **Backend API** : http://localhost:8000/api/
- **Mailpit UI** : http://localhost:8025 (boîte de réception email)

### Option 3 — Développement local (sans Docker)

**Backend :**
```bash
cd qa_recipe_converter
python -m venv .venv
.venv\Scripts\activate     # Windows
# source .venv/bin/activate  # Linux/macOS
pip install -r requirements.txt
cp .env.example .env
mkdir -p db
python manage.py migrate
python manage.py runserver --settings=config.settings.development
```

**Frontend :**
```bash
cd qa_recipe_converter_frontend
npm install
npm run dev
```

**Mailpit** (optionnel — pour recevoir les emails en local) :
```bash
docker run --rm -p 1025:1025 -p 8025:8025 axllent/mailpit
```

Le frontend tourne sur **http://localhost:3000** avec proxy automatique vers le backend (port 8000).
Les emails sont visibles dans **Mailpit UI** : http://localhost:8025.

---

## Tests

```bash
cd qa_recipe/qa_recipe_converter
python -m pytest tests/ -v

# Avec couverture
python -m pytest tests/ --cov=apps --cov-report=term-missing
```

**Résultats :** 629 tests, 100 % de réussite (Python 3.12 et 3.14). Les tests n'écrivent ni dans `media/`, ni dans la base, et n'ouvrent pas VS Code.

---

## API REST

| Endpoint | Méthode | Auth | Description |
|---|---|---|---|
| `/api/auth/register/` | POST | - | Inscription |
| `/api/auth/login/` | POST | - | Connexion |
| `/api/auth/me/` | GET | Oui | Profil utilisateur |
| `/api/teams/` | GET/POST | Oui | Liste/création équipes |
| `/api/teams/<slug>/` | GET/PATCH/DELETE | Oui | Détail équipe |
| `/api/teams/<slug>/dashboard/` | GET | Oui | Dashboard + KPIs |
| `/api/teams/<slug>/members/` | GET/POST | Oui | Membres équipe |
| `/api/teams/<slug>/projects/` | GET/POST | Oui | Projets |
| `/api/upload/` | POST | Oui | Upload Word |
| `/api/jobs/` | GET | Oui | Liste conversions |
| `/api/jobs/<uuid>/` | GET | Oui | Détail conversion |

---

## Stack technique

| Composant | Technologie |
|---|---|---|
| Backend | Python / Django 5.2 LTS + DRF |
| Frontend | React 19 + TypeScript + Tailwind CSS v4 |
| BDD | SQLite |
| Parsing Word | python-docx |
| Génération Excel | openpyxl |
| Rapports PDF | ReportLab (aucune dépendance système) |
| Conteneurisation | Docker + docker-compose |
| Serveur prod backend | gunicorn + whitenoise |
| Serveur prod frontend | Nginx |
| Email (dev) | Mailpit (SMTP :1025, UI :8025) |

---

## Auteur

**Alseny Traoré** — Mai 2026
QA Recipe Converter v2.0
