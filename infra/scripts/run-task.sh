#!/usr/bin/env bash
# Run a one-off command in the app image on ECS Fargate and wait for it, e.g.:
#   infra/scripts/run-task.sh migrate
#   infra/scripts/run-task.sh python -m batchlight.cli seed-india
#   DB_HOST_OVERRIDE=restored.xxxx.rds.amazonaws.com infra/scripts/run-task.sh python -m batchlight.cli db-check
# Needs CLUSTER, TASK_DEFINITION, SUBNETS (comma-separated), SECURITY_GROUP and AWS credentials in the environment.
set -euo pipefail
: "${CLUSTER:?}" "${TASK_DEFINITION:?}" "${SUBNETS:?}" "${SECURITY_GROUP:?}"

command_json=$(python3 -c 'import json, sys; print(json.dumps(sys.argv[1:]))' "$@")
env_json="[]"
if [ -n "${DB_HOST_OVERRIDE:-}" ]; then
  env_json=$(python3 -c 'import json, sys; print(json.dumps([{"name": "BATCHLIGHT_DATABASE_HOST_OVERRIDE", "value": sys.argv[1]}]))' "$DB_HOST_OVERRIDE")
fi
overrides=$(python3 -c 'import json, sys; print(json.dumps({"containerOverrides": [{"name": "app", "command": json.loads(sys.argv[1]), "environment": json.loads(sys.argv[2])}]}))' "$command_json" "$env_json")

task_arn=$(aws ecs run-task \
  --cluster "$CLUSTER" \
  --task-definition "$TASK_DEFINITION" \
  --launch-type FARGATE \
  --count 1 \
  --network-configuration "awsvpcConfiguration={subnets=[${SUBNETS}],securityGroups=[${SECURITY_GROUP}],assignPublicIp=ENABLED}" \
  --overrides "$overrides" \
  --query 'tasks[0].taskArn' --output text)
if [ -z "$task_arn" ] || [ "$task_arn" = "None" ]; then
  echo "run-task: ECS did not start the task" >&2
  exit 1
fi
echo "run-task: started $task_arn ($*)"
# One waiter call gives up after 10 minutes; allow up to an hour for long migrations or data loads.
for attempt in 1 2 3 4 5 6; do
  if aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$task_arn"; then break; fi
  if [ "$attempt" = 6 ]; then echo "run-task: still running after an hour; see the ECS console" >&2; exit 1; fi
  echo "run-task: still running"
done

read -r exit_code reason < <(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$task_arn" \
  --query 'tasks[0].[containers[0].exitCode, stoppedReason]' --output text)
task_id="${task_arn##*/}"
if [ -n "${LOG_GROUP:-}" ]; then
  echo "run-task: log output"
  aws logs get-log-events --log-group-name "$LOG_GROUP" --log-stream-name "cli/app/${task_id}" \
    --query 'events[].message' --output text 2>/dev/null | tr '\t' '\n' | tail -n 100 || true
fi
echo "run-task: exit code ${exit_code} (${reason})"
[ "$exit_code" = "0" ]
