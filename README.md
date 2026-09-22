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
│   ├── tests/                    # Suite de tests (124 tests)
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

**Résultats :** 124 tests — 121 ✅ / 3 ⚠️ (compatibilité Python 3.14)

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
| Backend | Python / Django 5.1 + DRF |
| Frontend | React 19 + TypeScript + Tailwind CSS v4 |
| BDD | SQLite |
| Parsing Word | python-docx |
| Génération Excel | openpyxl |
| Conteneurisation | Docker + docker-compose |
| Serveur prod backend | gunicorn + whitenoise |
| Serveur prod frontend | Nginx |
| Email (dev) | Mailpit (SMTP :1025, UI :8025) |

---

## Auteur

**Alseny Traoré** — Mai 2026
QA Recipe Converter v2.0
