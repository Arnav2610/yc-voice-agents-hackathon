# Chronos 911 — demo commands. Run from the server/ directory.
# Requires `uv` on PATH (installer puts it in ~/.local/bin).

UV ?= uv

.PHONY: help install seed bot bot-twilio twilio-twiml pipecat-twiml deploy-secrets deploy-cloud deploy-dashboard dash dashboard \
        democall regression improve improve-llm reset fake-cekura text-ws test demo

help:
	@echo "Chronos 911 — make targets:"
	@echo "  make install      uv sync (install deps)"
	@echo "  make seed         seed institutional memory into Supermemory + local"
	@echo "  make bot          run the voice bot (WebRTC :7860) + dashboard (:7861)"
	@echo "  make bot-twilio   run for Twilio phone calls (needs PROXY=your.ngrok.io)"
	@echo "  make twilio-twiml print TwiML XML for your Twilio number (PROXY=...)"
	@echo "  make pipecat-twiml print TwiML for Pipecat Cloud (ORG=your-org-name)"
	@echo "  make deploy-secrets upload .env secrets to Pipecat Cloud"
	@echo "  make deploy-cloud build + deploy chronos-911 to Pipecat Cloud"
	@echo "  make deploy-dashboard-env print env vars needed for remote dashboard sync"
	@echo "  make dash         run the dashboard standalone (:7861) — drive the whole demo from the browser"
	@echo "  make democall     play a scripted call onto the dashboard (no mic needed)"
	@echo "  make text-ws      run the Cekura text-WS bridge (:8970) for live Cekura runs"
	@echo "  make regression   run the scenario suite against current policies"
	@echo "  make improve      run the self-improvement loop (baseline -> patch -> after)"
	@echo "  make improve-llm  same, but author the patch rationale with Nemotron"
	@echo "  make reset        revert policies to baseline + clear runtime reports"
	@echo "  make fake-cekura  write the seeded Cekura fallback reports"
	@echo "  make test         unit tests"
	@echo "  make demo         prep the demo (reset, seed, baseline metrics) then run the bot"

install:
	$(UV) sync

seed:
	$(UV) run python scripts/seed_memory.py

bot:
	$(UV) run bot-chronos.py

# Twilio: expose port 7860 publicly (ngrok http 7860), set PROXY to the ngrok host.
# Attach POST https://PROXY/ as the voice webhook on your Twilio number (or TwiML Bin).
bot-twilio:
	@PROXY_HOST="$(PROXY)"; \
	if [ -z "$$PROXY_HOST" ]; then PROXY_HOST="$$TWILIO_PUBLIC_URL"; fi; \
	if [ -z "$$PROXY_HOST" ]; then \
		echo "Set PROXY=your-subdomain.ngrok-free.app or TWILIO_PUBLIC_URL in .env"; exit 1; \
	fi; \
	case "$$PROXY_HOST" in your-subdomain*|example.com|localhost*) \
		echo "PROXY is still a placeholder ($$PROXY_HOST). Run: ngrok http 7860"; \
		echo "Then use the real host, e.g. make bot-twilio PROXY=abc123.ngrok-free.app"; exit 1;; \
	esac; \
	echo "Twilio voice webhook: https://$$PROXY_HOST/"; \
	echo "Dashboard (local):    http://localhost:7861"; \
	$(UV) run bot-chronos.py -t twilio -x $$PROXY_HOST --host 0.0.0.0

twilio-twiml:
	$(UV) run python scripts/print_twilio_twiml.py $(PROXY)

pipecat-twiml:
	@ORG_NAME="$(ORG)"; \
	if [ -z "$$ORG_NAME" ]; then \
		echo "Set ORG=your-pipecat-org (from: uv run pipecatcloud organizations list)"; exit 1; \
	fi; \
	$(UV) run python scripts/print_pipecat_twiml.py $$ORG_NAME

deploy-secrets:
	@test -f .env || (echo "Missing server/.env — copy .env.example and fill in keys"; exit 1)
	@grep -E '^[A-Za-z_][A-Za-z0-9_]*=.+' .env > .env.deploy
	$(UV) run pipecatcloud secrets set chronos-911-secrets --file .env.deploy --skip
	@rm -f .env.deploy

deploy-cloud:
	$(UV) run pipecatcloud deploy --yes

deploy-dashboard-env:
	@KEY="$${DASHBOARD_INGEST_KEY:-$$(openssl rand -hex 24)}"; \
	echo "Add these to server/.env and Pipecat Cloud secrets (make deploy-secrets && make deploy-cloud):"; \
	echo "DASHBOARD_INGEST_KEY=$$KEY"; \
	echo "CHRONOS_DASHBOARD_URL=https://YOUR-DASHBOARD-HOST  # after Render deploy"

dash dashboard:
	@set -a && [ -f .env ] && . ./.env && set +a; \
	$(UV) run python -m chronos.dashboard_server

democall:
	$(UV) run python scripts/demo_call.py $(SCENARIO)

text-ws:
	$(UV) run python scripts/run_text_ws.py

regression:
	$(UV) run python scripts/run_regression.py

improve:
	$(UV) run python scripts/run_improvement.py

improve-llm:
	$(UV) run python scripts/run_improvement.py --llm

reset:
	$(UV) run python scripts/reset_demo.py

fake-cekura:
	$(UV) run python scripts/run_fake_cekura_report.py

test:
	$(UV) run python -m pytest -q test_nemotron_llm.py || true
	$(UV) run python -m chronos.improvement_loop

demo: reset seed regression
	@echo ""
	@echo "Baseline loaded. Now starting the bot + dashboard."
	@echo "  Voice call:  http://localhost:7860"
	@echo "  Dashboard:   http://localhost:7861"
	@echo "After the live call, run 'make improve' in another terminal to show before/after."
	$(UV) run bot-chronos.py
