# Production operations
## Monitoring
Use GET /health for uptime checks. Review application logs and hosting metrics for 5xx errors, latency and restarts. Never log passwords or form bodies.

## Backups
PostgreSQL: enable the host's automated daily backups and test a restore monthly.
SQLite local: run `python scripts/backup.py`. Keep backups outside the repository and test restoration.

## Incident checklist
Pause risky writes, preserve logs, identify affected businesses, restore only from a verified backup, document the cause and fix, then notify affected users when appropriate.
