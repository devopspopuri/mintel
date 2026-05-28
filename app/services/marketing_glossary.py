from __future__ import annotations


ROLE_TERMS: dict[str, list[str]] = {
    "DevOps Engineer": [
        "CI/CD", "Jenkins", "GitHub Actions", "GitLab CI", "Azure DevOps", "Build Pipeline", "Release Pipeline", "Deployment Automation", "Blue-Green Deployment", "Canary Deployment",
        "Rollback", "Artifact", "Artifact Repository", "Nexus", "Artifactory", "Docker", "Container Image", "Kubernetes", "Helm", "Linux",
        "Shell Scripting", "Python Scripting", "Configuration Management", "Ansible", "Terraform", "Environment Promotion", "Source Control", "Branching Strategy", "Pull Request", "Code Review",
        "SonarQube", "Static Code Analysis", "Unit Test Automation", "Integration Testing", "Smoke Testing", "Monitoring", "Logging", "Prometheus", "Grafana", "CloudWatch",
        "Incident Support", "Production Support", "Release Notes", "Change Management", "Secrets Management", "Service Account", "Load Balancer", "Autoscaling", "Dockerfile", "YAML",
    ],
    "Cloud Automation Engineer": [
        "Infrastructure as Code", "Terraform", "Terraform Module", "Terraform State", "Remote Backend", "Ansible", "CloudFormation", "ARM Template", "Bicep", "Pulumi",
        "Provisioning", "Configuration Management", "Policy as Code", "Compliance Automation", "Self-Service Provisioning", "Environment Automation", "Reusable Module", "Parameterization", "Variables", "Secrets Management",
        "AWS CLI", "Azure CLI", "PowerShell", "Bash", "Python", "Git", "Pull Request Workflow", "Pipeline Automation", "Plan and Apply", "Drift Detection",
        "Resource Tagging", "Cost Governance", "Landing Zone", "Guardrails", "Network Automation", "IAM Automation", "Patch Automation", "Golden Image", "AMI", "VM Image",
        "Runbook Automation", "Change Approval", "Rollback Plan", "Dependency Graph", "State Locking", "Workspace", "Cloud SDK", "Template Validation", "Linting", "Idempotency",
    ],
    "Cloud Infrastructure Engineer": [
        "AWS", "Azure", "GCP", "VPC", "VNet", "Subnet", "Route Table", "Security Group", "NSG", "Firewall",
        "Load Balancer", "DNS", "Route 53", "Private Endpoint", "VPN", "Direct Connect", "ExpressRoute", "Cloud NAT", "IAM", "RBAC",
        "EC2", "Virtual Machine", "Autoscaling", "Object Storage", "S3", "Blob Storage", "Cloud Storage", "Backup", "Disaster Recovery", "High Availability",
        "Multi-AZ", "Region", "Availability Zone", "Hybrid Cloud", "Cloud Migration", "Landing Zone", "Network Peering", "Transit Gateway", "Bastion Host", "Key Management",
        "Encryption", "Certificate", "Monitoring", "CloudWatch", "Azure Monitor", "Cloud Monitoring", "Capacity Planning", "Cost Optimization", "Infrastructure Diagram", "Runbook",
    ],
    "Platform Engineer": [
        "Internal Developer Platform", "Developer Portal", "Backstage", "Golden Path", "Self-Service", "Service Catalog", "Kubernetes Platform", "Namespace", "Cluster", "Helm",
        "Argo CD", "Terraform Module", "Crossplane", "Platform API", "Shared Services", "Service Onboarding", "Template", "Scaffold", "Environment Provisioning", "Secrets Management",
        "Vault", "External Secrets", "Container Registry", "Policy as Code", "OPA", "Gatekeeper", "RBAC", "Multi-Tenant", "Developer Productivity", "Paved Road",
        "CI/CD Template", "GitHub Actions", "GitLab CI", "Azure DevOps", "Observability", "Prometheus", "Grafana", "Logging", "Service Mesh", "Ingress",
        "DNS Automation", "Certificate Management", "Cost Allocation", "Quota", "SRE", "Platform Reliability", "Release Promotion", "Documentation", "API Gateway", "Feature Flag",
    ],
    "GitOps Engineer": [
        "GitOps", "Argo CD", "Flux", "Kubernetes Manifest", "Helm", "Kustomize", "Declarative Configuration", "Desired State", "Actual State", "Drift Detection",
        "Sync Policy", "Auto Sync", "Manual Sync", "Prune", "Self Heal", "ApplicationSet", "Environment Promotion", "Pull Request", "Branch Strategy", "Merge Request",
        "Rollback", "Git Revert", "Tag", "Release Branch", "Overlay", "Base Manifest", "Sealed Secrets", "External Secrets", "Vault", "RBAC",
        "Namespace", "Cluster", "Multi-Cluster", "Progressive Delivery", "Canary Deployment", "Blue-Green Deployment", "Policy as Code", "Admission Controller", "OPA", "Gatekeeper",
        "Image Updater", "Container Registry", "Chart Repository", "YAML", "Diff", "Audit Trail", "Approval Workflow", "Deployment History", "Health Check", "Reconciliation",
    ],
    "Site Reliability Engineer": [
        "SRE", "Reliability", "Availability", "Latency", "Throughput", "SLO", "SLI", "SLA", "Error Budget", "Incident Response",
        "On-call", "PagerDuty", "Opsgenie", "Alerting", "Alert Fatigue", "Monitoring", "Logging", "Tracing", "Observability", "OpenTelemetry",
        "Prometheus", "Grafana", "Datadog", "New Relic", "Splunk", "ELK", "CloudWatch", "Azure Monitor", "Postmortem", "Root Cause Analysis",
        "Runbook", "Playbook", "Capacity Planning", "Performance Tuning", "Load Testing", "Chaos Engineering", "Disaster Recovery", "Backup Restore", "High Availability", "Failover",
        "Autoscaling", "Kubernetes", "Service Mesh", "Rate Limiting", "Circuit Breaker", "Production Support", "Toil Reduction", "Automation", "Burn Rate", "MTTR",
    ],
    "Data Platform Engineer": [
        "DataOps", "Data Pipeline", "ETL", "ELT", "Airflow", "DAG", "dbt", "Spark", "Kafka", "Databricks",
        "Snowflake", "Redshift", "BigQuery", "Azure Data Factory", "Synapse", "AWS Glue", "S3", "ADLS", "Data Lake", "Lakehouse",
        "Data Warehouse", "Batch Processing", "Streaming", "Schema Evolution", "Data Quality", "Data Validation", "Great Expectations", "Lineage", "Metadata", "Catalog",
        "Orchestration", "Workflow", "Partitioning", "Checkpointing", "Backfill", "CDC", "Bronze Silver Gold", "Transformation", "SQL", "Python",
        "Scala", "Monitoring", "Pipeline Failure", "Retry", "SLA", "Data Governance", "Access Control", "PII", "Cost Optimization", "CI/CD for Data",
    ],
    "MLOps Engineer": [
        "MLOps", "ML Pipeline", "Training Pipeline", "Inference Pipeline", "Batch Inference", "Real-Time Inference", "Model Registry", "MLflow", "Kubeflow", "SageMaker",
        "Azure ML", "Vertex AI", "Experiment Tracking", "Feature Store", "Feast", "DVC", "Model Versioning", "Model Deployment", "Model Serving", "KServe",
        "BentoML", "TensorFlow Serving", "Data Drift", "Model Drift", "Model Monitoring", "A/B Testing", "Champion Challenger", "Hyperparameter Tuning", "Training Dataset", "Validation Dataset",
        "Feature Engineering", "Containerization", "Docker", "Kubernetes", "GPU", "Model Artifact", "CI/CD for ML", "Pipeline Orchestration", "Airflow", "Data Validation",
        "Bias Detection", "Explainability", "Rollback", "Endpoint", "Latency", "Throughput", "Scalability", "Batch Scoring", "Prediction Service", "Monitoring",
    ],
    "AIOps Engineer": [
        "AIOps", "Anomaly Detection", "Alert Correlation", "Event Correlation", "Noise Reduction", "Incident Prediction", "Root Cause Analysis", "Automated Remediation", "Runbook Automation", "Observability Intelligence",
        "Log Analytics", "Metric Analytics", "Trace Analytics", "Capacity Forecasting", "ServiceNow ITOM", "Dynatrace", "Datadog", "Splunk ITSI", "Moogsoft", "BigPanda",
        "New Relic", "Prometheus", "Grafana", "OpenTelemetry", "ELK", "OpenSearch", "Ticket Enrichment", "Incident Enrichment", "Topology Mapping", "Dependency Mapping",
        "Change Correlation", "Event Deduplication", "Threshold Tuning", "Predictive Analytics", "Failure Prediction", "Remediation Workflow", "ChatOps", "PagerDuty", "Opsgenie", "MTTR Reduction",
        "Root Cause Signal", "Alert Suppression", "SLO Monitoring", "Synthetic Monitoring", "User Experience Monitoring", "Cloud Monitoring", "Kubernetes Events", "Log Pattern", "Incident Triage", "Operational Analytics",
    ],
    "Cloud Database Engineer": [
        "Cloud Database", "RDS", "Aurora", "DynamoDB", "Redshift", "ElastiCache", "Azure SQL", "Cosmos DB", "Cloud SQL", "BigQuery",
        "Spanner", "Snowflake", "PostgreSQL", "MySQL", "SQL Server", "NoSQL", "Database Provisioning", "Backup", "Restore", "Point-in-Time Recovery",
        "Replication", "Read Replica", "High Availability", "Failover", "Disaster Recovery", "Performance Tuning", "Query Optimization", "Indexing", "Partitioning", "Connection Pooling",
        "Encryption", "KMS", "Access Control", "IAM", "Secrets Management", "Patch Management", "Version Upgrade", "Migration", "Schema Change", "Data Masking",
        "Monitoring", "Slow Query", "Database Metrics", "Storage Scaling", "IOPS", "Caching", "Cost Optimization", "Terraform", "Ansible", "Database Reliability",
    ],
}

SHARED_ENTERPRISE_TERMS = [
    "Jira Story", "Sprint Planning", "Backlog Refinement", "Acceptance Criteria", "Definition of Done",
    "Change Request", "Release Evidence", "Rollback Criteria", "Ownership Matrix", "Support Handoff",
    "RCA Notes", "ServiceNow Ticket", "Runbook", "Operational Dashboard", "Production Validation",
]


ROLE_TERM_EXTENSIONS: dict[str, list[str]] = {
    "DevOps Engineer": [
        "Pipeline Template", "Reusable Workflow", "Deployment Freeze", "Environment Variable", "Feature Toggle",
        "Image Vulnerability Scan", "Dependency Scan", "Release Approval Gate", "Deployment Window", "Post-Deployment Validation",
    ],
    "Cloud Automation Engineer": [
        "Approval Workflow", "Resource Naming Standard", "Tagging Policy", "Certificate Rotation", "Backup Verification",
        "Cloud Account Baseline", "Provisioning Request", "Module Versioning", "Automation Exception", "Environment Decommissioning",
    ],
    "Cloud Infrastructure Engineer": [
        "Network Segmentation", "Ingress Path", "Egress Control", "Shared Services VPC", "Private DNS",
        "Certificate Chain", "Capacity Baseline", "DR Runbook", "Failover Test", "Infrastructure Handoff",
        "Firewall Change Review",
    ],
    "Platform Engineer": [
        "Platform Onboarding", "Developer Experience", "Service Template", "Platform Guardrail", "Namespace Quota",
        "Cluster Add-on", "Service Ownership", "Platform Runbook", "Golden Workflow", "Developer Self-Service Request",
    ],
    "GitOps Engineer": [
        "GitOps Promotion", "Environment Overlay", "Sync Wave", "GitOps Health Status", "Deployment Drift Report",
        "Manifest Review", "Secret Reference", "Reconciliation Error", "Rollback Commit", "Production Sync Window",
    ],
    "Site Reliability Engineer": [
        "Service Health Review", "Incident Timeline", "Alert Routing", "Error Rate", "Saturation",
        "Golden Signals", "Reliability Review", "Operational Readiness", "On-Call Handoff", "Incident Commander",
        "Incident Review Board",
    ],
    "Data Platform Engineer": [
        "Data Freshness", "Data Contract", "Source-to-Target Validation", "Pipeline Ownership", "Data Reconciliation",
        "Late Arriving Data", "SLA Miss", "Data Incident", "Backfill Plan", "Data Release Checklist",
    ],
    "MLOps Engineer": [
        "Model Approval", "Model Rollback", "Feature Drift", "Inference SLA", "Prediction Log",
        "Model Lineage", "Training Job Failure", "Model Release Checklist", "Shadow Deployment", "Endpoint Health Check",
    ],
    "AIOps Engineer": [
        "Incident Signal", "Alert Storm", "Service Topology", "Known Error", "Correlation Rule",
        "Anomaly Baseline", "Incident Noise Ratio", "Resolver Group", "Operational Knowledge Article", "Remediation Approval",
    ],
    "Cloud Database Engineer": [
        "Database Cutover", "Restore Drill", "Replica Lag", "Connection Saturation", "Query Plan",
        "Maintenance Window", "Schema Deployment", "Database Incident", "Credential Rotation", "Storage Growth Forecast",
    ],
}


for role_name, terms in ROLE_TERM_EXTENSIONS.items():
    ROLE_TERMS[role_name] = list(dict.fromkeys([*ROLE_TERMS[role_name], *SHARED_ENTERPRISE_TERMS, *terms]))

ROLE_TERMS["Cloud Platform Engineer"] = list(
    dict.fromkeys(
        [
            *ROLE_TERMS["Cloud Infrastructure Engineer"],
            *ROLE_TERMS["Cloud Automation Engineer"],
            *ROLE_TERMS["Platform Engineer"],
            "Landing Zone Standard",
            "Cloud Platform Blueprint",
            "Developer Golden Path",
            "Self-Service Infrastructure",
            "Platform Governance",
        ]
    )
)
ROLE_TERMS["Site Reliability / AIOps Engineer"] = list(
    dict.fromkeys(
        [
            *ROLE_TERMS["Site Reliability Engineer"],
            *ROLE_TERMS["AIOps Engineer"],
            "Alert Correlation Evidence",
            "Anomaly Review Window",
            "Incident Enrichment",
            "Noise Reduction Review",
            "Service Health Narrative",
        ]
    )
)
ROLE_TERMS["MLOps / AI Platform Engineer"] = list(
    dict.fromkeys(
        [
            *ROLE_TERMS["MLOps Engineer"],
            "AI Platform",
            "Model Governance",
            "Prompt Evaluation",
            "Vector Store",
            "AI Service Monitoring",
        ]
    )
)

CATEGORY_HINTS: list[tuple[str, str]] = [
    ("Pipeline", "Delivery"), ("Deployment", "Delivery"), ("Release", "Delivery"), ("Build", "Delivery"), ("Artifact", "Delivery"),
    ("Terraform", "Automation"), ("Ansible", "Automation"), ("Provisioning", "Automation"), ("Automation", "Automation"), ("Policy", "Automation"),
    ("VPC", "Cloud Infrastructure"), ("VNet", "Cloud Infrastructure"), ("Subnet", "Cloud Infrastructure"), ("IAM", "Security"), ("RBAC", "Security"), ("Encryption", "Security"),
    ("Kubernetes", "Platform"), ("Helm", "Platform"), ("Cluster", "Platform"), ("Namespace", "Platform"), ("Backstage", "Platform"),
    ("GitOps", "GitOps"), ("Argo", "GitOps"), ("Flux", "GitOps"), ("Drift", "GitOps"),
    ("SLO", "Reliability"), ("SLI", "Reliability"), ("Incident", "Reliability"), ("Monitoring", "Reliability"), ("Logging", "Reliability"), ("Tracing", "Reliability"),
    ("Airflow", "DataOps"), ("Spark", "DataOps"), ("Kafka", "DataOps"), ("Data", "DataOps"), ("dbt", "DataOps"),
    ("ML", "MLOps"), ("Model", "MLOps"), ("Inference", "MLOps"), ("Feature", "MLOps"),
    ("AIOps", "AIOps"), ("Anomaly", "AIOps"), ("Correlation", "AIOps"), ("Remediation", "AIOps"),
    ("Database", "Cloud Database"), ("SQL", "Cloud Database"), ("RDS", "Cloud Database"), ("Aurora", "Cloud Database"), ("DynamoDB", "Cloud Database"),
]


ROLE_CONTEXT: dict[str, str] = {
    "DevOps Engineer": "software delivery, release automation, CI/CD pipelines, deployments, and production support",
    "Cloud Automation Engineer": "cloud provisioning, Infrastructure as Code, reusable automation, policy controls, and repeatable environments",
    "Cloud Infrastructure Engineer": "cloud compute, networking, security, storage, availability, migration, and day-to-day infrastructure operations",
    "Cloud Platform Engineer": "cloud platforms, landing zones, Infrastructure as Code, Kubernetes foundations, self-service developer workflows, and platform governance",
    "Platform Engineer": "internal developer platforms, Kubernetes platforms, self-service engineering workflows, and reusable platform capabilities",
    "GitOps Engineer": "Git-based deployment, Kubernetes configuration, declarative environment management, sync, drift detection, and rollback",
    "Site Reliability Engineer": "production reliability, observability, incident response, availability, performance, automation, and operational excellence",
    "Site Reliability / AIOps Engineer": "production reliability, observability, incident response, alert intelligence, anomaly detection, RCA, and operational automation",
    "Data Platform Engineer": "data pipeline automation, orchestration, quality checks, monitoring, governance, and reliable data delivery",
    "MLOps Engineer": "machine learning pipelines, model deployment, model monitoring, registry, inference, and production ML operations",
    "MLOps / AI Platform Engineer": "AI/ML platforms, model pipelines, model deployment, inference, drift monitoring, registry, and production ML governance",
    "AIOps Engineer": "AI-assisted IT operations, alert correlation, anomaly detection, incident prediction, event analytics, and automated remediation",
    "Cloud Database Engineer": "cloud databases, backup, replication, high availability, performance, security, migration, and database automation",
}


CATEGORY_OVERVIEWS: dict[str, dict[str, list[str] | str]] = {
    "Delivery": {
        "definition": "This term is part of the software delivery flow. It usually connects code changes, build validation, release control, deployment safety, and handoff into production.",
        "key_points": [
            "Where it appears: CI/CD pipelines, release notes, deployment plans, change tickets, artifact flow, rollback plans, and environment promotion.",
            "What teams expect: predictable releases, fewer manual steps, clear approval flow, test evidence, and fast recovery if a release fails.",
            "Interview angle: explain how the work moved from source control to build, test, approval, deployment, validation, and production support.",
        ],
        "methods": ["Automated build", "Pipeline validation", "Release promotion", "Rollback or recovery", "Post-deployment smoke testing"],
    },
    "Automation": {
        "definition": "This term belongs to automation work where repeatable tasks are converted into scripts, modules, policies, policies, or workflows so teams do not depend on manual steps.",
        "key_points": [
            "Where it appears: Terraform, Ansible, scripts, pipeline jobs, reusable modules, configuration patterns, and approval workflows.",
            "What teams expect: repeatability, consistency across environments, fewer mistakes, better auditability, and faster environment creation.",
            "Interview angle: describe the manual process, the automation approach, validation, rollback planning, and how the team reused it.",
        ],
        "methods": ["Infrastructure as Code", "Reusable modules", "Scripted workflow", "Policy validation", "Idempotent execution"],
    },
    "Cloud Infrastructure": {
        "definition": "This term is part of core cloud infrastructure. It usually relates to how applications run securely and reliably across compute, network, storage, identity, and connectivity services.",
        "key_points": [
            "Where it appears: AWS, Azure, GCP, VPC/VNet design, subnets, routing, load balancers, DNS, IAM, backups, and migration work.",
            "What teams expect: secure connectivity, stable environments, right-sized capacity, clear ownership, monitoring, and cost awareness.",
            "Interview angle: explain the infrastructure component, why it was needed, how it connected to the application, and how it was validated.",
        ],
        "methods": ["Network design", "Access control", "High availability", "Backup and recovery", "Cost and capacity review"],
    },
    "Security": {
        "definition": "This term is connected to protecting systems, data, identities, secrets, networks, and operational access while still allowing teams to deliver work.",
        "key_points": [
            "Where it appears: IAM, RBAC, service accounts, secrets, encryption, certificates, firewall rules, compliance checks, and audit logs.",
            "What teams expect: least privilege, secure configuration, traceable access, protected credentials, and reduced operational risk.",
            "Interview angle: talk about what needed access, what risk existed, how access was controlled, and how the team verified it safely.",
        ],
        "methods": ["Least privilege", "Secret rotation", "Policy review", "Audit trail", "Encrypted communication"],
    },
    "Platform": {
        "definition": "This term belongs to platform engineering work where shared tooling and standards help application teams deploy, operate, and support services faster.",
        "key_points": [
            "Where it appears: Kubernetes, developer portals, service catalogs, service patterns, shared CI/CD, namespaces, platform APIs, and observability standards.",
            "What teams expect: self-service, reusable patterns, fewer one-off solutions, better developer productivity, and controlled operations.",
            "Interview angle: explain what capability the platform provided, who used it, how onboarding worked, and how reliability was maintained.",
        ],
        "methods": ["Self-service workflow", "Golden path", "Service onboarding", "Shared platform pattern", "Operational guardrail"],
    },
    "GitOps": {
        "definition": "This term belongs to GitOps, where Git is treated as the source of truth for deployment and environment changes.",
        "key_points": [
            "Where it appears: Argo CD, Flux, Kubernetes manifests, Helm charts, Kustomize overlays, pull requests, drift detection, and sync policies.",
            "What teams expect: auditable changes, environment consistency, clear approvals, automatic reconciliation, and easier rollback through Git.",
            "Interview angle: describe the Git change, review flow, sync process, validation, drift handling, and rollback approach.",
        ],
        "methods": ["Pull-request change", "Declarative configuration", "Sync and reconciliation", "Drift detection", "Git rollback"],
    },
    "Reliability": {
        "definition": "This term is part of reliability engineering. It helps teams keep systems available, observable, fast, recoverable, and supportable in production.",
        "key_points": [
            "Where it appears: SLOs, SLIs, alerting, dashboards, logs, traces, incident response, postmortems, capacity planning, and runbooks.",
            "What teams expect: fewer incidents, faster detection, faster recovery, clear ownership, measurable reliability, and reduced operational toil.",
            "Interview angle: explain the production problem, the signal you used, the action taken, and the operational improvement after the change.",
        ],
        "methods": ["Monitoring and alerting", "Incident triage", "Runbook execution", "Root cause analysis", "Reliability improvement"],
    },
    "DataOps": {
        "definition": "This term is part of operating data workflows reliably. It usually connects ingestion, transformation, orchestration, validation, monitoring, and governance.",
        "key_points": [
            "Where it appears: Airflow, dbt, Spark, Kafka, Databricks, Snowflake, Glue, BigQuery, data quality checks, lineage, and pipeline SLAs.",
            "What teams expect: accurate data, recoverable pipelines, visible failures, repeatable deployments, and clear ownership of data issues.",
            "Interview angle: describe the data flow, failure point, validation approach, orchestration, monitoring, and business impact.",
        ],
        "methods": ["Pipeline orchestration", "Data validation", "Backfill or replay", "Schema handling", "Data quality monitoring"],
    },
    "MLOps": {
        "definition": "This term belongs to the machine learning delivery lifecycle, where models move from experimentation to reliable production use.",
        "key_points": [
            "Where it appears: ML pipelines, model registry, experiment tracking, feature stores, model serving, drift monitoring, and CI/CD for ML.",
            "What teams expect: repeatable training, controlled model versions, stable inference, monitored predictions, and rollback options.",
            "Interview angle: explain the ML workflow, how model artifacts moved, how deployment was validated, and how production behavior was monitored.",
        ],
        "methods": ["Experiment tracking", "Model versioning", "Pipeline automation", "Model serving", "Drift monitoring"],
    },
    "AIOps": {
        "definition": "This term is part of AI-assisted operations. It uses operational data such as alerts, logs, metrics, traces, tickets, and topology to detect issues faster and reduce noise.",
        "key_points": [
            "Where it appears: anomaly detection, event correlation, incident prediction, ticket enrichment, root-cause signals, and automated remediation.",
            "What teams expect: fewer duplicate alerts, faster triage, better incident context, lower MTTR, and smarter operational decisions.",
            "Interview angle: explain the operational signal, how noise or failure was identified, how teams responded, and what improved after automation or correlation.",
        ],
        "methods": ["Anomaly detection", "Alert correlation", "Event deduplication", "Incident enrichment", "Automated remediation"],
    },
    "Cloud Database": {
        "definition": "This term belongs to cloud database operations. It usually affects availability, performance, backup, security, migration, monitoring, and cost.",
        "key_points": [
            "Where it appears: RDS, Aurora, Cloud SQL, Azure SQL, Cosmos DB, DynamoDB, backups, read replicas, indexes, encryption, and monitoring.",
            "What teams expect: reliable database access, fast queries, recoverable data, secure credentials, scaling plans, and controlled migrations.",
            "Interview angle: describe the database problem, the operational step you handled, how it was validated, and what improved for users or teams.",
        ],
        "methods": ["Backup and restore", "Replication", "Performance tuning", "Access control", "Migration validation"],
    },
    "Core Term": {
        "definition": "This is a common job-description term. It needs a plain definition, project context, ownership boundary, evidence, and outcome.",
        "key_points": [
            "Where it appears: requirements, resumes, recruiter screens, project discussions, design notes, tickets, and interview questions.",
            "What teams expect: practical understanding, clear communication, and the ability to connect the term to delivery or support work.",
            "Interview angle: define it simply, connect it to a project problem, explain your action, and describe the result.",
        ],
        "methods": ["Plain definition", "Project context", "Tool association", "Responsibility", "Outcome"],
    },
}


TERM_OVERRIDES: dict[str, dict[str, list[str] | str]] = {
    "Autoscaling": {
        "definition": "Autoscaling automatically adds or removes compute capacity based on demand so an application can maintain steady performance without keeping unnecessary servers running all the time.",
        "key_points": [
            "Common services: AWS Auto Scaling, EC2 Auto Scaling Groups, Kubernetes Horizontal Pod Autoscaler, Azure VM Scale Sets, and GCP managed instance groups.",
            "What it watches: CPU, memory, request count, queue depth, custom metrics, schedules, or predictive traffic patterns.",
            "Why teams use it: handle traffic spikes, reduce manual capacity planning, improve availability, and control cloud cost.",
        ],
        "methods": ["Dynamic scaling", "Target tracking", "Scheduled scaling", "Predictive scaling", "Scale out and scale in"],
        "consultant_sentence": "Autoscaling helps an application add capacity during high traffic and reduce capacity when demand drops, so the team can balance performance, availability, and cost.",
    },
    "CI/CD": {
        "definition": "CI/CD is the automated process that builds, tests, packages, and deploys application changes so teams can release faster with fewer manual errors.",
        "key_points": [
            "CI focuses on build and validation: compile, unit tests, static analysis, package creation, and artifact publishing.",
            "CD focuses on release flow: approvals, deployment automation, environment promotion, smoke tests, and rollback readiness.",
            "Interview answer covers pipeline stages, quality gates, deployment strategy, failure handling, and release evidence.",
        ],
        "methods": ["Build", "Test", "Package", "Deploy", "Validate and rollback"],
        "consultant_sentence": "CI/CD gives teams a repeatable path from code commit to deployment, with automated checks that improve release speed and reduce production risk.",
    },
    "Kubernetes": {
        "definition": "Kubernetes is a container orchestration platform that runs, scales, heals, and manages containerized applications across a cluster of machines.",
        "key_points": [
            "Core objects include pods, deployments, services, config maps, secrets, namespaces, ingress, and persistent volumes.",
            "Teams use it for application portability, self-healing, rolling updates, service discovery, scaling, and platform standardization.",
            "Interview answer covers deployment, exposure, configuration, monitoring, recovery, and ownership inside the cluster.",
        ],
        "methods": ["Deployment", "Service discovery", "Rolling update", "Horizontal scaling", "Self-healing"],
        "consultant_sentence": "Kubernetes helps teams run containerized applications reliably by managing deployment, scaling, networking, configuration, and recovery across a cluster.",
    },
    "Terraform": {
        "definition": "Terraform is an Infrastructure as Code tool used to define, provision, and change cloud infrastructure through version-controlled configuration files.",
        "key_points": [
            "Teams use Terraform for repeatable infrastructure, reviewable changes, reusable modules, state tracking, and controlled provisioning.",
            "Common workflow includes writing configuration, running plan, reviewing changes, applying safely, and storing state in a remote backend.",
            "Interview answer covers modules, variables, state, backend, plan/apply, drift, and validation evidence.",
        ],
        "methods": ["Configuration", "Plan", "Apply", "Remote state", "Reusable module"],
        "consultant_sentence": "Terraform lets teams manage cloud resources as code, so infrastructure changes can be reviewed, repeated, tracked, and automated across environments.",
    },
    "Docker": {
        "definition": "Docker packages an application and its dependencies into a container image so it can run consistently across developer machines, test environments, and production platforms.",
        "key_points": [
            "Core parts include Dockerfile, image, container, registry, tags, volumes, networks, and runtime configuration.",
            "Teams use Docker to reduce environment mismatch, simplify deployment, standardize application packaging, and support Kubernetes or container platforms.",
            "Interview answer covers image build, registry storage, configuration injection, runtime behavior, logs, and debugging.",
        ],
        "methods": ["Dockerfile", "Image build", "Registry push", "Container run", "Log and health check"],
        "consultant_sentence": "Docker makes application packaging repeatable by creating a container image that carries the application, dependencies, and runtime setup together.",
    },
}


def _category_for(term: str) -> str:
    lower_term = term.lower()
    for hint, category in CATEGORY_HINTS:
        if hint.lower() in lower_term:
            return category
    return "Core Term"


def _meaning_for(term: str, role: str) -> str:
    overview = _technical_overview(term, role, _category_for(term))
    return str(overview["definition"])


def _technical_overview(term: str, role: str, category: str) -> dict[str, str | list[str]]:
    base = CATEGORY_OVERVIEWS.get(category, CATEGORY_OVERVIEWS["Core Term"])
    override = TERM_OVERRIDES.get(term, {})
    definition = str(override.get("definition") or base["definition"])
    key_points = list(override.get("key_points") or base["key_points"])
    methods = list(override.get("methods") or base["methods"])
    consultant_sentence = str(
        override.get("consultant_sentence")
        or f"{term} is important in {role} work because it connects to {ROLE_CONTEXT.get(role, 'real project delivery')}. Project evidence includes where it appears, what problem it solves, and what was implemented or supported."
    )
    return {
        "definition": definition,
        "key_points": key_points,
        "methods": methods,
        "consultant_sentence": consultant_sentence,
        "jd_clues": [
            "build", "automate", "configure", "deploy", "monitor", "troubleshoot", "optimize", "migrate", "support", "document",
        ],
        "interview_flow": [
            "Plain definition.",
            "Project workflow location.",
            "Problem solved.",
            "Personal implementation or support ownership.",
            "Result: reliability, speed, cost, security, visibility, or quality.",
        ],
    }


def _beginner_guide(term: str, role: str, category: str) -> dict[str, str]:
    return {
        "simple_meaning": (
            f"{term} is a {category.lower()} term commonly seen in {role} job descriptions. "
            "The practical meaning is what it is, why a team uses it, and what problem it solves."
        ),
        "why_it_matters": (
            f"{term} confirms real project understanding, not only definition-only answers. "
            "A useful answer connects the term to delivery, support, monitoring, automation, access, performance, or reliability."
        ),
        "jd_signal": (
            f"When {term} appears in a job description, nearby verbs matter: build, automate, monitor, support, troubleshoot, configure, deploy, optimize, migrate, document, or collaborate. "
            "Those verbs identify the expected project story."
        ),
        "simple_sentence": (
            f"{term} was used in a project context to make the process more reliable, easier to support, and clearer for the team."
        ),
        "mini_example": (
            f"Example: a manual or confusing process was reviewed end to end, {term} was placed in the right workflow step, the improvement was validated, and the result was documented for the team."
        ),
        "mistake_to_avoid": (
            f"Weak answer: '{term} is a tool' or '{term} is used in {role}'. Stronger answer: problem solved, ownership handled, and team benefit."
        ),
    }


def _star_description(term: str, role: str, category: str) -> dict[str, str]:
    situation = (
        f"In a {role} job description, {term} usually appears when the client is describing real delivery work, not just a keyword checklist. "
        f"It signals practical experience in the {category.lower()} area, including daily engineering decisions, production constraints, team handoffs, and measurable business outcomes. "
        f"When this word appears with automation, reliability, deployment, monitoring, cloud, security, or governance, the expected answer covers where it was used, why it mattered, what problem it solved, and how it improved the environment. "
        f"The practical situation: a delivery team had manual work, slow releases, unstable operations, unclear ownership, missing visibility, inconsistent environments, data delays, model risk, or database pressure, and {term} became part of the solution."
    )
    task = (
        f"The task is to connect {term} to a project story with plain definition, associated tools or services, handled responsibility, and before/after state. "
        f"For marketing, this term maps into resume bullets, recruiter summaries, submission notes, and project-story evidence so the profile aligns with the {role} requirement. "
        f"Responsibility boundary: design, configure, automate, monitor, troubleshoot, optimize, document, validate, support, or coordinate the work. "
        f"Scope boundary: environment, pipeline, service, cluster, cloud account, data workflow, model endpoint, database, or operational process."
    )
    action = (
        f"Action shows ownership. Requirement analysis, collaboration with developers, QA, operations, security, data, or cloud teams, platform configuration or support, process documentation, repeatable automation, validation, monitoring, and risk communication all belong in the answer when relevant. "
        f"For tool-specific terms, the surrounding workflow matters more than the tool name alone. "
        f"Concrete evidence includes inputs, approvals, configuration, testing, deployment, troubleshooting, rollback, access control, observability, performance checks, tickets, Git commits, pipeline runs, dashboards, logs, alerts, design documents, runbooks, change records, and validation evidence."
    )
    result = (
        f"The result is measurable whenever possible. "
        f"Good outcomes include faster deployments, fewer manual steps, reduced incidents, better auditability, improved reliability, lower cloud cost, stronger security posture, cleaner data pipelines, faster model delivery, improved database performance, or better operational visibility. "
        f"Clear impact language includes reduced deployment time, improved recovery time, increased pipeline success rate, lowered alert noise, improved query performance, or delivery with more confidence. "
        f"When exact metrics are not available, observable improvement still matters: fewer escalations, smoother releases, easier onboarding, better repeatability, faster troubleshooting, improved compliance readiness, or a more stable production environment. "
        f"The close ties {term} back to the target {role} role and client environment."
    )
    return {"situation": situation, "task": task, "action": action, "result": result}


def _interview_script_sections(term: str, role: str, category: str, title: str, focus: str, result: str) -> list[dict[str, list[str] | str]]:
    return [
        {
            "label": "Business problem",
            "items": [
                focus[0].upper() + focus[1:] + ".",
                f"The goal was to turn {term} from a loose requirement into a stable, supportable engineering workflow.",
            ],
        },
        {
            "label": "Ownership",
            "items": [
                f"Reviewed the current process, identified gaps, improved the {category.lower()} workflow, validated the change, documented the approach, and handed it over to the team.",
                f"As a {role}, the focus stayed on engineering delivery, support readiness, and evidence. Product priority and feature behavior stayed with product and application owners.",
            ],
        },
        {
            "label": "Implementation",
            "items": [
                "Mapped the end-to-end flow: inputs, owners, manual steps, automation points, failure locations, and success signals.",
                "Worked with developers, QA, operations, cloud, security, or data teams depending on the affected layer.",
                "Used lower-environment validation, documented approval, pipeline checks, rollback plan, smoke validation, dashboards, logs, alerts, and incident history.",
            ],
        },
        {
            "label": "Troubleshooting",
            "items": [
                "Separated symptoms from causes such as missing variables, wrong permissions, environment mismatch, dependency failure, data delay, capacity limit, or unclear ownership.",
                "Documented decision points in plain language: selected option, avoided option, reason, risk, expected outcome, and validation evidence.",
            ],
        },
        {
            "label": "Result",
            "items": [
                result + ".",
                "The process became more reliable, repeatable, and easier for the team to support.",
            ],
        },
    ]


def _interview_script(term: str, role: str, category: str, title: str, focus: str, result: str) -> str:
    sections = _interview_script_sections(term, role, category, title, focus, result)
    parts: list[str] = []
    for section in sections:
        label = str(section["label"])
        items = " ".join(str(item) for item in section["items"])
        parts.append(f"{label}: {items}")
    return " ".join(parts)


def _use_cases_for(term: str, role: str, category: str) -> list[dict[str, str]]:
    scenarios = [
        (
            "Project implementation",
            f"the team needed to implement {term} as part of a new or improved engineering workflow",
            "This helped reduce confusion during delivery and gave the team a clearer, more consistent way to move work forward",
        ),
        (
            "Production troubleshooting",
            f"the team was seeing issues connected to {term}, and we needed to identify what was failing and why",
            "This improved troubleshooting speed and helped the team avoid repeating the same issue in later releases or support cycles",
        ),
        (
            "Automation and standardization",
            f"manual steps around {term} were slowing the team down and creating inconsistent results across environments",
            "This reduced manual work, improved repeatability, and made the process easier to audit and support",
        ),
        (
            "Cross-team collaboration",
            f"{term} required coordination across multiple teams because the change touched delivery, operations, access, validation, or support",
            "This improved handoff quality and helped different teams work from the same understanding instead of solving pieces separately",
        ),
        (
            "Optimization and measurable impact",
            f"the existing approach for {term} worked, but it needed to become faster, more reliable, easier to monitor, or less expensive",
            "This created a stronger operational result, such as fewer failures, faster execution, better visibility, improved reliability, or cleaner ownership",
        ),
    ]
    use_cases = []
    for title, focus, result in scenarios:
        script = _interview_script(term, role, category, title, focus, result)
        use_cases.append(
            {
                "title": title,
                "summary": f"{term} interview answer for {title.lower()}.",
                "script": script,
                "script_sections": _interview_script_sections(term, role, category, title, focus, result),
                "word_count": str(len(script.split())),
            }
        )
    return use_cases


def _build_glossary() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    row_id = 1
    for role, terms in ROLE_TERMS.items():
        for term in terms:
            category = _category_for(term)
            rows.append(
                {
                    "id": str(row_id),
                    "term": term,
                    "category": category,
                    "roles": role,
                    "meaning": _meaning_for(term, role),
                    "overview": _technical_overview(term, role, category),
                    "beginner": _beginner_guide(term, role, category),
                    "star": _star_description(term, role, category),
                    "use_cases": _use_cases_for(term, role, category),
                }
            )
            row_id += 1
    return rows


MARKETING_ROLE_GLOSSARY: list[dict[str, str]] = _build_glossary()


def glossary_categories() -> list[str]:
    return sorted({item["category"] for item in MARKETING_ROLE_GLOSSARY})


def glossary_roles() -> list[str]:
    return list(ROLE_TERMS.keys())


def glossary_item(item_id: int) -> dict[str, str] | None:
    for item in MARKETING_ROLE_GLOSSARY:
        if item["id"] == str(item_id):
            return item
    return None


def star_word_count(item: dict[str, str]) -> int:
    star = item["star"]
    return sum(len(star[key].split()) for key in ["situation", "task", "action", "result"])
