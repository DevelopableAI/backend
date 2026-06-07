terraform {
  required_providers {
    heroku = {
      source  = "heroku/heroku"
      version = "~> 5.0"
    }
  }
  required_version = ">= 1.5.0"
}

provider "heroku" {
  api_key = var.heroku_api_key
  email   = var.heroku_email
}

# ── Heroku app ────────────────────────────────────────────────────────────────

resource "heroku_app" "main" {
  name   = var.project_name
  region = var.heroku_region
  stack  = "container"
}

# ── Postgres addon ────────────────────────────────────────────────────────────

resource "heroku_addon" "postgres" {
  app_id = heroku_app.main.id
  plan   = "heroku-postgresql:essential-0"
}

# ── App config vars ───────────────────────────────────────────────────────────

resource "heroku_app_config_association" "main" {
  app_id     = heroku_app.main.id
  depends_on = [heroku_addon.postgres]

  vars = {
    NODE_ENV                 = "production"
    PORT                     = "3000"
    DEVELOPABLE_PROJECT_NAME = var.project_name
  }

  sensitive_vars = {
    JWT_SECRET = var.jwt_secret
  }
}
