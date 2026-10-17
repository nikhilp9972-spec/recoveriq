.PHONY: data evaluate app dashboard inspect clean sources

sources:         ## scrape live NPCI statistics → data/real_distributions.json (needs monid CLI)
	python3 -m data.scrape_sources

app:             ## run the live operator console → http://localhost:8420
	python3 -m app.server

dashboard: evaluate  ## refresh data and open the batch-results console
	python3 -m dashboard.export
	open dashboard/index.html

data:            ## regenerate the synthetic batch (seeded — same output every time)
	python3 -m data.generator

evaluate:        ## run agent vs baselines on the frozen batch; writes eval/results.json + ledger.sqlite
	python3 -m eval.run_eval

inspect:         ## show the full audit trail of one recovered case
	sqlite3 ledger.sqlite "SELECT actor, event, detail, at_hour FROM audit_log WHERE case_id=(SELECT case_id FROM cases WHERE outcome='RECOVERED' ORDER BY amount DESC LIMIT 1) ORDER BY at_hour;"

clean:
	rm -f ledger.sqlite eval/results.json data/failed_payments.jsonl
