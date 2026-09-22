"""
Gherkin / Cucumber script generator from ExtractedUseCase objects.

Generates:
  - One .feature file per use case (automatable)
  - A Cypress project scaffold (cypress.config.js, support files, step definitions)
"""
import re
import os
import io
import zipfile
from typing import List


def _slugify(text: str) -> str:
    """Convert a string to a safe filename slug."""
    text = text.lower().strip()
    text = re.sub(r'[àáâãäå]', 'a', text)
    text = re.sub(r'[èéêë]', 'e', text)
    text = re.sub(r'[ìíîï]', 'i', text)
    text = re.sub(r'[òóôõö]', 'o', text)
    text = re.sub(r'[ùúûü]', 'u', text)
    text = re.sub(r'[ç]', 'c', text)
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    text = re.sub(r'[\s_]+', '_', text)
    return text[:60] or 'use_case'


def _split_lines(text: str) -> List[str]:
    """Split multi-line text into a clean list of non-empty strings."""
    lines = []
    for line in re.split(r'\n|;|•|–|-{2,}', text or ''):
        line = line.strip()
        if line:
            lines.append(line)
    return lines or ['(non défini)']


def _format_step(raw: str, keyword: str = "And") -> str:
    """Turn a raw line into a Gherkin step."""
    raw = raw.strip().rstrip('.')
    if not raw:
        return ''
    # Capitalize first letter
    raw = raw[0].upper() + raw[1:]
    return f"    {keyword} {raw}"


def generate_feature_file(uc) -> str:
    """
    Generate the content of a .feature file for a single use case.
    uc: ExtractedUseCase model instance
    """
    lines = []
    uc_id = f"UC-{uc.order:03d}"
    description = uc.use_case_text or uc.description or "Cas de test"

    lines.append(f"# Fichier généré automatiquement par QA Recipe Converter")
    lines.append(f"# Use Case : {uc_id}")
    lines.append("")
    lines.append(f"Feature: {uc_id} — {description}")
    lines.append("")

    # Background (preconditions)
    precond_lines = _split_lines(uc.preconditions)
    if precond_lines and precond_lines != ['(non défini)']:
        lines.append("  Background:")
        for i, p in enumerate(precond_lines):
            kw = "Given" if i == 0 else "And"
            lines.append(f"    {kw} {p[0].upper() + p[1:]}")
        lines.append("")

    # Scenario
    lines.append(f"  Scenario: {description}")

    # Steps (Given/When/Then)
    step_lines = _split_lines(uc.steps)
    n = len(step_lines)
    for i, step in enumerate(step_lines):
        if i == 0:
            kw = "When"
        elif i == n - 1 and n > 1:
            kw = "Then"
        else:
            kw = "And"
        lines.append(f"    {kw} {step[0].upper() + step[1:]}")

    # Expected results as Then/And
    expected_lines = _split_lines(uc.expected_results)
    if expected_lines and expected_lines != ['(non défini)']:
        for i, exp in enumerate(expected_lines):
            kw = "Then" if (not step_lines or i == 0) else "And"
            lines.append(f"    {kw} {exp[0].upper() + exp[1:]}")

    lines.append("")
    return "\n".join(lines)


def generate_cypress_step_definitions(uc) -> str:
    """
    Generate a Cypress step definitions JS file for a use case.
    """
    uc_id = f"UC-{uc.order:03d}"
    slug = _slugify(uc_id + '_' + (uc.use_case_text or uc.description or ''))

    step_lines = _split_lines(uc.steps)
    expected_lines = _split_lines(uc.expected_results)
    precond_lines = _split_lines(uc.preconditions)

    lines = []
    lines.append(f"// Step definitions — {uc_id}")
    lines.append(f"// Généré par QA Recipe Converter")
    lines.append("")
    lines.append("import {{ Given, When, Then, And }} from '@badeball/cypress-cucumber-preprocessor';")
    lines.append("")

    # Preconditions
    for p in precond_lines:
        if p != '(non défini)':
            safe = p.replace("'", "\\'")
            lines.append(f"Given('{safe}', () => {{")
            lines.append(f"  // TODO: Implémenter la précondition")
            lines.append(f"}});")
            lines.append("")

    # Steps
    for i, step in enumerate(step_lines):
        safe = step.replace("'", "\\'")
        kw = "When" if i == 0 else "And"
        lines.append(f"{kw}('{safe}', () => {{")
        lines.append(f"  // TODO: Implémenter l'étape")
        lines.append(f"}});")
        lines.append("")

    # Expected results
    for i, exp in enumerate(expected_lines):
        if exp != '(non défini)':
            safe = exp.replace("'", "\\'")
            kw = "Then" if i == 0 else "And"
            lines.append(f"{kw}('{safe}', () => {{")
            lines.append(f"  // TODO: Vérifier le résultat attendu")
            lines.append(f"}});")
            lines.append("")

    return "\n".join(lines)


def generate_cypress_project_zip(use_cases, company_name: str = "") -> io.BytesIO:
    """
    Generate a full Cypress project as a ZIP archive.
    Returns a BytesIO buffer.
    """
    automated_ucs = [uc for uc in use_cases if uc.is_automated]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:

        # ── cypress.config.js ──────────────────────────────────────────────
        zf.writestr("cypress.config.js", _cypress_config())

        # ── package.json ───────────────────────────────────────────────────
        zf.writestr("package.json", _package_json())

        # ── .gitignore ─────────────────────────────────────────────────────
        zf.writestr(".gitignore", "node_modules/\ncypress/screenshots/\ncypress/videos/\n")

        # ── README ─────────────────────────────────────────────────────────
        zf.writestr("README.md", _cypress_readme(automated_ucs, company_name=company_name))

        # ── cypress/support/commands.js ────────────────────────────────────
        zf.writestr("cypress/support/commands.js", _commands_js())

        # ── cypress/support/e2e.js ─────────────────────────────────────────
        zf.writestr("cypress/support/e2e.js", (
            "// Import commands.js\n"
            "import './commands';\n"
            "import '@badeball/cypress-cucumber-preprocessor/support';\n"
        ))

        # ── cypress/fixtures/example.json ─────────────────────────────────
        zf.writestr("cypress/fixtures/example.json", '{\n  "username": "testuser",\n  "password": "secret"\n}\n')

        # ── Feature files + step definitions ──────────────────────────────
        for uc in automated_ucs:
            uc_id = f"UC-{uc.order:03d}"
            slug = _slugify(uc_id + '_' + (uc.use_case_text or uc.description or ''))

            feature_content = generate_feature_file(uc)
            zf.writestr(f"cypress/e2e/{slug}.feature", feature_content)

            steps_content = generate_cypress_step_definitions(uc)
            zf.writestr(f"cypress/e2e/step_definitions/{slug}.js", steps_content)

        # If no automated UCs, add a placeholder
        if not automated_ucs:
            zf.writestr("cypress/e2e/placeholder.feature", (
                "# Aucun cas automatisé détecté.\n"
                "# Marquez des Use Cases comme 'automatisés' dans l'interface\n"
                "# pour générer les fichiers .feature correspondants.\n"
            ))

    buf.seek(0)
    return buf


def generate_gherkin_only_zip(use_cases) -> io.BytesIO:
    """
    Generate only .feature files (no Cypress project) as a ZIP.
    """
    automated_ucs = [uc for uc in use_cases if uc.is_automated]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for uc in automated_ucs:
            uc_id = f"UC-{uc.order:03d}"
            slug = _slugify(uc_id + '_' + (uc.use_case_text or uc.description or ''))
            zf.writestr(f"features/{slug}.feature", generate_feature_file(uc))

        if not automated_ucs:
            zf.writestr("features/README.txt",
                "Aucun cas automatisé.\nMarquez des Use Cases comme automatisés pour générer les .feature.\n")

    buf.seek(0)
    return buf


# ── Template helpers ───────────────────────────────────────────────────────────

def _cypress_config() -> str:
    return """\
const { defineConfig } = require('cypress');
const createBundler = require('@bahmutov/cypress-esbuild-preprocessor');
const {
  addCucumberPreprocessorPlugin,
} = require('@badeball/cypress-cucumber-preprocessor');
const {
  createEsbuildPlugin,
} = require('@badeball/cypress-cucumber-preprocessor/esbuild');

module.exports = defineConfig({
  e2e: {
    baseUrl: 'http://localhost:3000',
    specPattern: 'cypress/e2e/**/*.feature',
    async setupNodeEvents(on, config) {
      await addCucumberPreprocessorPlugin(on, config);
      on(
        'file:preprocessor',
        createBundler({ plugins: [createEsbuildPlugin(config)] })
      );
      return config;
    },
  },
  video: false,
  screenshotOnRunFailure: true,
});
"""


def _package_json() -> str:
    return """\
{
  "name": "qa-recipe-cypress",
  "version": "1.0.0",
  "description": "Projet Cypress généré par QA Recipe Converter",
  "scripts": {
    "cy:open": "cypress open",
    "cy:run": "cypress run",
    "cy:run:headed": "cypress run --headed"
  },
  "devDependencies": {
    "cypress": "^13.6.0",
    "@badeball/cypress-cucumber-preprocessor": "^20.0.0",
    "@bahmutov/cypress-esbuild-preprocessor": "^2.2.0",
    "esbuild": "^0.20.0"
  },
  "cypress-cucumber-preprocessor": {
    "nonGlobalStepDefinitions": false,
    "stepDefinitions": "cypress/e2e/step_definitions/**/*.js"
  }
}
"""


def _commands_js() -> str:
    return """\
// Custom Cypress commands
// Ajoutez vos commandes réutilisables ici

// Exemple : login
// Cypress.Commands.add('login', (username, password) => {
//   cy.visit('/login');
//   cy.get('[data-cy=username]').type(username);
//   cy.get('[data-cy=password]').type(password);
//   cy.get('[data-cy=submit]').click();
// });
"""


def _cypress_readme(automated_ucs, company_name: str = "") -> str:
    count = len(automated_ucs)
    uc_list = "\n".join(
        f"- UC-{uc.order:03d} — {uc.use_case_text or uc.description or ''}"
        for uc in automated_ucs
    ) or "Aucun"

    return f"""\
# 🌲 Projet Cypress — QA Recipe Converter

Projet généré automatiquement depuis **QA Recipe Converter**.

## 📋 Use Cases automatisés ({count})

{uc_list}

## 🚀 Démarrage rapide

```bash
# 1. Installer les dépendances
npm install

# 2. Ouvrir Cypress (mode interactif)
npm run cy:open

# 3. Lancer tous les tests (mode headless)
npm run cy:run
```

## 📁 Structure

```
cypress/
├── e2e/
│   ├── *.feature              # Scénarios Gherkin
│   └── step_definitions/      # Implémentations des steps
├── fixtures/
│   └── example.json           # Données de test
└── support/
    ├── commands.js             # Commandes personnalisées
    └── e2e.js                  # Configuration support
cypress.config.js
package.json
```

## ✍️ Prochaines étapes

1. Mettre à jour `baseUrl` dans `cypress.config.js`
2. Implémenter les steps `// TODO` dans `step_definitions/`
3. Ajouter vos données de test dans `fixtures/`
4. Lancer `npm run cy:open` pour déboguer interactivement

---
*Généré par QA Recipe Converter — {company_name or "AT-TechGN"}*
"""
