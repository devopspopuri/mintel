from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.pursuit_intelligence import MarketingRole


DEFAULT_MARKETING_ROLES = [
    {
        "code": "devops-engineer",
        "name": "DevOps Engineer",
        "description": "Automates software delivery, CI/CD, deployments, release operations, and production support.",
        "covers": "CI/CD, build and release, deployment automation, Docker, Kubernetes basics, Linux, scripting, cloud deployments, application operations, monitoring, logging, release promotion, rollback, artifacts, production support.",
        "common_tools": "Jenkins, GitHub Actions, GitLab CI, Azure DevOps, Docker, Kubernetes, Helm, Terraform basics, Ansible, Bash, Python, Nexus, Artifactory, SonarQube, Prometheus, Grafana, CloudWatch.",
        "aliases": "CI/CD Engineer, Build and Release Engineer, Cloud DevOps Engineer, Release Automation Engineer",
        "keywords": "devops ci/cd cicd jenkins github actions gitlab ci azure devops docker kubernetes helm release build deployment sonar nexus artifactory prometheus grafana cloudwatch linux bash",
    },
    {
        "code": "cloud-automation-engineer",
        "name": "Cloud Automation Engineer",
        "description": "Automates cloud provisioning, configuration, governance, and repeatable environment creation.",
        "covers": "Terraform, Ansible, CloudFormation, ARM/Bicep, Pulumi, provisioning, configuration management, modules, patching, policy automation, self-service infrastructure.",
        "common_tools": "Terraform, Ansible, AWS CloudFormation, Azure ARM/Bicep, Pulumi, Python, Bash, PowerShell, Git, Jenkins, GitHub Actions, Azure DevOps.",
        "aliases": "Infrastructure Automation Engineer, IaC Engineer, Automation Engineer, Configuration Management Engineer",
        "keywords": "terraform ansible cloudformation bicep arm pulumi infrastructure as code iac provisioning configuration management modules policy automation powershell",
        "active": False,
    },
    {
        "code": "cloud-infrastructure-engineer",
        "name": "Cloud Platform Engineer",
        "description": "Builds and operates cloud platforms, landing zones, Kubernetes foundations, self-service infrastructure, and developer platform capabilities.",
        "covers": "AWS/Azure/GCP infrastructure, VPC/VNet, IAM, load balancers, DNS, Kubernetes platform, Terraform modules, platform guardrails, self-service provisioning, golden paths, shared Helm charts, observability baseline.",
        "common_tools": "AWS, Azure, GCP, Terraform, Kubernetes, Helm, Argo CD, Backstage, Crossplane, Vault, External Secrets Operator, Prometheus, Grafana, CloudWatch, Azure Monitor.",
        "aliases": "Cloud Infrastructure Engineer, Cloud Automation Engineer, Platform Engineer, Kubernetes Platform Engineer, Developer Platform Engineer, Infrastructure Automation Engineer",
        "keywords": "cloud platform engineer aws azure gcp infrastructure terraform kubernetes helm backstage crossplane vpc vnet iam load balancer dns landing zone self service developer platform golden paths automation",
    },
    {
        "code": "platform-engineer",
        "name": "Platform Engineer",
        "description": "Builds internal platforms and reusable engineering capabilities for developers.",
        "covers": "Internal developer platform, Kubernetes platform, golden paths, self-service templates, reusable modules, developer portals, standardized CI/CD, shared services, onboarding, secrets.",
        "common_tools": "Kubernetes, Backstage, Terraform, Helm, Argo CD, Crossplane, GitHub Actions, GitLab CI, Azure DevOps, Vault, External Secrets Operator, Prometheus, Grafana.",
        "aliases": "Cloud Platform Engineer, Kubernetes Platform Engineer, Developer Platform Engineer, Developer Productivity Engineer, Internal Developer Platform Engineer",
        "keywords": "platform engineer internal developer platform idp backstage golden paths self service kubernetes platform crossplane developer productivity vault external secrets shared services",
        "active": False,
    },
    {
        "code": "gitops-engineer",
        "name": "GitOps Engineer",
        "description": "Uses Git as the source of truth for infrastructure, Kubernetes apps, and environment changes.",
        "covers": "Git-based deployments, Argo CD, Flux, Helm, Kustomize, Kubernetes manifests, environment promotion, PR-based changes, drift detection, declarative deployments, rollback.",
        "common_tools": "Argo CD, Flux, Helm, Kustomize, Kubernetes, GitHub, GitLab, Bitbucket, Terraform, Sealed Secrets, External Secrets Operator, Vault.",
        "aliases": "Kubernetes Deployment Engineer, GitOps Platform Engineer, Argo CD Engineer, Flux Engineer",
        "keywords": "gitops argo cd argocd flux helm kustomize kubernetes manifests declarative deployments drift detection sealed secrets environment promotion pull request",
        "active": False,
    },
    {
        "code": "site-reliability-engineer",
        "name": "Site Reliability / AIOps Engineer",
        "description": "Improves reliability, observability, incident response, alert intelligence, anomaly detection, RCA, and operational automation.",
        "covers": "SLO, SLI, error budgets, incident response, observability, alerting, monitoring, logging, tracing, alert correlation, anomaly detection, RCA, noise reduction, runbooks, MTTR reduction.",
        "common_tools": "Prometheus, Grafana, Datadog, New Relic, Splunk ITSI, Dynatrace, Moogsoft, BigPanda, ServiceNow ITOM, OpenTelemetry, PagerDuty, Opsgenie, Kubernetes, Python.",
        "aliases": "Site Reliability Engineer, SRE, AIOps Engineer, Observability Engineer, Production Engineer, Reliability Engineer, Intelligent Operations Engineer",
        "keywords": "sre site reliability aiops observability incident response slo sli error budget alert correlation anomaly detection log analytics rca mttr runbook pagerduty opsgenie datadog dynatrace splunk itsi moogsoft bigpanda servicenow itom",
    },
    {
        "code": "dataops-engineer",
        "name": "Data Platform Engineer",
        "description": "Builds and operates reliable enterprise data platforms, pipelines, orchestration, data quality, monitoring, and governance.",
        "covers": "Data pipeline automation, data CI/CD, workflow orchestration, data quality, validation, monitoring, batch and streaming, schema changes, metadata, lineage, governance.",
        "common_tools": "Apache Airflow, dbt, Spark, Kafka, Databricks, Snowflake, AWS Glue, Redshift, BigQuery, Azure Data Factory, Synapse, S3, ADLS, Great Expectations, Terraform, GitHub Actions.",
        "aliases": "Data Engineer - DataOps, DataOps Engineer, Cloud Data Platform Engineer, Data Pipeline Automation Engineer, Data Infrastructure Engineer",
        "keywords": "dataops data platform data pipeline airflow dbt spark kafka databricks snowflake glue redshift bigquery adf azure data factory synapse great expectations lineage metadata",
    },
    {
        "code": "mlops-engineer",
        "name": "MLOps / AI Platform Engineer",
        "description": "Builds and operates AI/ML platforms, model pipelines, registry workflows, inference services, monitoring, drift detection, and production model governance.",
        "covers": "ML pipelines, model training, model deployment, registry, experiment tracking, feature store, versioning, inference, model monitoring, drift, CI/CD for ML, AI platform.",
        "common_tools": "MLflow, Kubeflow, SageMaker, Azure ML, Vertex AI, Docker, Kubernetes, Airflow, DVC, Feast, TensorFlow Serving, KServe, BentoML, GitHub Actions, Jenkins, Prometheus, Grafana.",
        "aliases": "MLOps Engineer, ML Platform Engineer, AI Platform Engineer, Machine Learning Infrastructure Engineer, Model Deployment Engineer, AI Infrastructure Engineer",
        "keywords": "mlops ai platform ml platform machine learning model deployment mlflow kubeflow sagemaker azure ml vertex ai dvc feast kserve bentoml feature store model registry drift inference",
    },
    {
        "code": "aiops-engineer",
        "name": "AIOps Engineer",
        "description": "Uses AI, machine learning, automation, and observability data to improve IT operations.",
        "covers": "AI-assisted operations, anomaly detection, alert/event correlation, log analytics, incident prediction, RCA, automated remediation, runbook automation, observability intelligence, ticket enrichment.",
        "common_tools": "Dynatrace, Datadog, Splunk ITSI, New Relic, Moogsoft, BigPanda, ServiceNow ITOM, Prometheus, Grafana, OpenTelemetry, ELK/OpenSearch, Python, cloud monitoring tools.",
        "aliases": "AI Platform Operations Engineer, Observability Automation Engineer, Intelligent Operations Engineer, IT Operations Automation Engineer",
        "keywords": "aiops anomaly detection alert correlation event correlation log analytics incident prediction root cause automated remediation runbook automation dynatrace splunk itsi moogsoft bigpanda servicenow itom",
        "active": False,
    },
    {
        "code": "cloud-database-engineer",
        "name": "Cloud Database Engineer",
        "description": "Builds, operates, automates, secures, and optimizes cloud database platforms.",
        "covers": "Cloud databases, provisioning, backup/restore, replication, HA, tuning, patching, monitoring, access control, encryption, migration, automation, DR, cost optimization.",
        "common_tools": "AWS RDS, Aurora, DynamoDB, Redshift, ElastiCache, Azure SQL Database, Cosmos DB, Azure Database for PostgreSQL/MySQL, Google Cloud SQL, BigQuery, Spanner, Snowflake, Terraform, Ansible.",
        "aliases": "Cloud DBA, Database Reliability Engineer, Database Automation Engineer, Cloud Data Infrastructure Engineer",
        "keywords": "cloud database dba rds aurora dynamodb redshift elasticache azure sql cosmos db cloud sql bigquery spanner snowflake database reliability backup replication performance tuning",
        "active": False,
    },
]

RETIRED_ROLE_REMAPS = {
    "cloud-automation-engineer": "cloud-infrastructure-engineer",
    "platform-engineer": "cloud-infrastructure-engineer",
    "gitops-engineer": "devops-engineer",
    "aiops-engineer": "site-reliability-engineer",
    "cloud-database-engineer": "cloud-infrastructure-engineer",
}


def ensure_default_marketing_roles(db: Session) -> None:
    existing = {role.code: role for role in db.scalars(select(MarketingRole)).all()}
    for item in DEFAULT_MARKETING_ROLES:
        if item["code"] not in existing:
            db.add(MarketingRole(**item))
        else:
            role = existing[item["code"]]
            for field in ("name", "description", "covers", "common_tools", "aliases", "keywords", "active"):
                if field in item and getattr(role, field) != item[field]:
                    setattr(role, field, item[field])
    db.commit()
    _remap_retired_marketing_role_references(db)


def _remap_retired_marketing_role_references(db: Session) -> None:
    roles = {role.code: role for role in db.scalars(select(MarketingRole)).all()}
    for old_code, new_code in RETIRED_ROLE_REMAPS.items():
        old_role = roles.get(old_code)
        new_role = roles.get(new_code)
        if not old_role or not new_role or old_role.id == new_role.id:
            continue
        params = {"old_id": old_role.id, "new_id": new_role.id}
        for table, column in (
            ("consultant_profiles", "marketing_role_id"),
            ("resume_versions", "target_role_id"),
            ("mock_interviews", "marketing_role_id"),
            ("pursuit_requirements", "marketing_role_id"),
        ):
            db.execute(text(f"update {table} set {column} = :new_id where {column} = :old_id"), params)
        db.execute(
            text(
                """
                update staff_marketing_role_assignments
                set active = false
                where marketing_role_id = :old_id
                and exists (
                    select 1
                    from staff_marketing_role_assignments target
                    where target.user_id = staff_marketing_role_assignments.user_id
                    and target.marketing_role_id = :new_id
                )
                """
            ),
            params,
        )
        db.execute(text("update staff_marketing_role_assignments set marketing_role_id = :new_id where marketing_role_id = :old_id and active = true"), params)
    db.commit()


def classify_marketing_role(db: Session, text: str) -> MarketingRole | None:
    haystack = f" {text.lower()} "
    best: tuple[int, MarketingRole] | None = None
    for role in db.scalars(select(MarketingRole).where(MarketingRole.active.is_(True))).all():
        tokens = set((role.keywords or "").lower().replace("/", " ").replace(",", " ").split())
        score = sum(1 for token in tokens if token and token in haystack)
        if role.name.lower() in haystack:
            score += 10
        for alias in [item.strip().lower() for item in (role.aliases or "").split(",") if item.strip()]:
            if alias in haystack:
                score += 8
        if score and (best is None or score > best[0]):
            best = (score, role)
    return best[1] if best else None
