.PHONY: test eval run docker
test:
	pytest -q tests
eval:
	-python -m eval.run_eval --prompt-version v1 --out eval_report_v1.json
	python -m eval.run_eval --prompt-version v2 --out eval_report.json
run:
	uvicorn app.main:app --port 8080 --reload
docker:
	docker build --build-arg GIT_SHA=$$(git rev-parse --short HEAD 2>/dev/null || echo local) -t medtriage .

# ---- Local minikube (same Rollout/analysis/HPA as EKS; mock LLM) ----
.PHONY: mk-up mk-release mk-bad-release mk-watch mk-rollback mk-ui mk-eval-mlflow mk-down
mk-up:
	scripts/minikube_up.sh
mk-release:            # make mk-release TAG=v3
	scripts/minikube_release.sh $(TAG)
mk-bad-release:        # canary fails analysis -> Argo auto-rolls back
	scripts/minikube_release.sh bad-$$(date +%H%M%S) --bad
mk-watch:
	kubectl argo rollouts get rollout medtriage -n medtriage --watch
mk-rollback:
	scripts/rollback.sh
mk-ui:                 # API :8080, Prometheus :9090, MLflow :5000, Argo dashboard :3100
	kubectl -n medtriage port-forward svc/medtriage 8080:80 & \
	kubectl -n monitoring port-forward svc/prometheus-server 9090:80 & \
	kubectl -n mlops port-forward svc/mlflow 5000:5000 & \
	kubectl argo rollouts dashboard -p 3100 & wait
mk-eval-mlflow:        # needs `make mk-ui` running (MLflow on :5000)
	-MLFLOW_TRACKING_URI=http://localhost:5000 python -m eval.run_eval --prompt-version v1 --mlflow --out eval_report_v1.json
	MLFLOW_TRACKING_URI=http://localhost:5000 python -m eval.run_eval --prompt-version v2 --mlflow
mk-down:
	minikube delete
