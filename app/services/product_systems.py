from __future__ import annotations

import re
from typing import Any, Optional


BANKING_PRODUCT_SYSTEM_NAMES = [
    "Online banking platform",
    "Mobile banking app",
    "Payments platform",
    "Credit card processing",
    "Loan origination system",
]

DOMAIN_PRODUCT_SYSTEM_NAMES: dict[str, list[str]] = {
    "Healthcare / Health Insurance": [
        "Member enrollment platform",
        "Claims processing system",
        "Policy administration system",
        "Provider network management",
        "Prior authorization platform",
        "Patient portal",
        "Provider portal",
        "Appointment scheduling",
        "Telehealth platform",
        "Electronic Health Record integration",
        "Lab integration",
        "Pharmacy integration",
        "Care management platform",
        "Customer support platform",
        "Healthcare analytics platform",
    ],
    "Banking / Financial Services": [
        "Online banking platform",
        "Mobile banking app",
        "Payments platform",
        "Credit card processing",
        "Loan origination system",
        "KYC onboarding",
        "Fraud detection platform",
        "Risk management system",
        "Transaction monitoring",
        "Customer profile service",
        "Account management system",
        "Regulatory reporting platform",
        "Data warehouse",
        "AI fraud analytics platform",
    ],
    "Retail / E-Commerce": [
        "E-commerce web platform",
        "Mobile shopping app",
        "Product catalog service",
        "Cart service",
        "Order management system",
        "Inventory management",
        "Payment service",
        "Loyalty platform",
        "Promotions engine",
        "Recommendation engine",
        "Warehouse integration",
        "Shipping integration",
        "Customer support portal",
        "Customer analytics platform",
    ],
    "Insurance": [
        "Policy administration system",
        "Claims intake platform",
        "Claims adjudication system",
        "Underwriting platform",
        "Premium billing system",
        "Agent portal",
        "Customer portal",
        "Document management system",
        "Risk scoring platform",
        "Fraud detection",
        "Regulatory reporting",
        "Customer support workflow",
        "Data warehouse",
        "Analytics dashboard",
    ],
    "Logistics / Transportation": [
        "Shipment tracking platform",
        "Fleet management system",
        "Route optimization engine",
        "Warehouse management system",
        "Delivery scheduling platform",
        "Driver mobile app",
        "Customer tracking portal",
        "Order fulfillment platform",
        "Inventory movement system",
        "Partner API integrations",
        "IoT telemetry platform",
        "Real-time analytics dashboard",
        "Customer support platform",
    ],
    "Manufacturing / Automotive / Industrial": [
        "Manufacturing execution system",
        "Plant operations portal",
        "Machine telemetry platform",
        "Quality management system",
        "Production scheduling system",
        "ERP integration platform",
        "Supplier portal",
        "Inventory planning system",
        "Warehouse integration",
        "Procurement workflow",
        "Connected product platform",
        "Predictive maintenance platform",
        "Industrial data lake",
        "Asset performance dashboard",
        "Robotics integration",
    ],
    "Technology / SaaS / Enterprise Software": [
        "SaaS application platform",
        "Tenant management service",
        "API platform",
        "Feature flag service",
        "Usage telemetry platform",
        "Customer onboarding platform",
        "Identity and access service",
        "Subscription billing system",
        "Entitlement service",
        "Customer support platform",
        "Product analytics platform",
        "Experimentation platform",
        "AI feature platform",
        "Model serving platform",
        "Observability platform",
    ],
    "Energy / Utilities / Data Centers": [
        "Grid monitoring platform",
        "Outage management system",
        "Field crew dispatch platform",
        "Smart meter data platform",
        "Customer outage portal",
        "Utility billing system",
        "Customer account portal",
        "Energy trading platform",
        "Settlement reporting platform",
        "Regulatory compliance platform",
        "Data center infrastructure monitoring",
        "Power capacity platform",
        "Cooling telemetry platform",
        "Asset maintenance system",
        "Reliability analytics platform",
    ],
    "Telecom / Media / Communications": [
        "Mobile account app",
        "Web customer portal",
        "Identity and login service",
        "Plan management platform",
        "Device activation platform",
        "Telecom billing platform",
        "Payments platform",
        "Device financing system",
        "Promotions engine",
        "Revenue assurance platform",
        "Network provisioning platform",
        "Service assurance platform",
        "Outage management system",
        "Network monitoring platform",
        "Field service platform",
        "5G analytics platform",
    ],
}

PRODUCT_SYSTEM_NAMES = sorted({name for names in DOMAIN_PRODUCT_SYSTEM_NAMES.values() for name in names})

SYSTEM_SLUGS = {name: re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") for name in PRODUCT_SYSTEM_NAMES}

DOMAIN_PROFILES: dict[str, dict[str, Any]] = {
    "Healthcare / Health Insurance": {
        "label": "healthcare and health insurance",
        "users": ["Members", "Patients", "Providers", "Care operations", "Claims operations", "Compliance users"],
        "capabilities": ["Eligibility checks", "Enrollment updates", "Claims handling", "Provider workflows", "Prior authorization", "Secure portal access", "Care coordination", "Analytics"],
        "controls": ["PHI protection", "HIPAA audit evidence", "member consent", "secure clinical integrations", "claims SLA tracking"],
        "signals": ["portal uptime", "eligibility latency", "claims queue age", "integration errors", "audit log completeness", "batch SLA status"],
    },
    "Banking / Financial Services": {
        "label": "banking and financial services",
        "users": ["Customers", "Banking operations", "Fraud analysts", "Risk teams", "Compliance users", "Customer support"],
        "capabilities": ["Secure account access", "money movement", "onboarding", "risk checks", "transaction monitoring", "regulatory reporting", "servicing", "analytics"],
        "controls": ["PCI controls", "SOX audit evidence", "segregation of duties", "encryption", "payment and account workflow controls"],
        "signals": ["payment availability", "fraud signal integrity", "KYC completion", "transaction alert volume", "API latency", "reconciliation status"],
    },
    "Retail / E-Commerce": {
        "label": "retail and e-commerce",
        "users": ["Shoppers", "Merchandising teams", "Store operations", "Warehouse teams", "Customer support", "Analytics users"],
        "capabilities": ["Catalog browsing", "cart and checkout", "order management", "inventory visibility", "payment handling", "promotions", "fulfillment", "customer analytics"],
        "controls": ["PCI-safe checkout", "promotion governance", "inventory accuracy", "release windows", "customer data protection"],
        "signals": ["checkout conversion", "cart errors", "payment authorization rate", "inventory sync lag", "promotion defects", "fulfillment SLA status"],
    },
    "Insurance": {
        "label": "insurance",
        "users": ["Policyholders", "Agents", "Underwriters", "Claims adjusters", "Billing operations", "Compliance users"],
        "capabilities": ["Policy lifecycle", "claims intake", "claims adjudication", "underwriting", "premium billing", "document workflows", "fraud review", "regulatory reporting"],
        "controls": ["policy audit trail", "claims SLA tracking", "document retention", "fraud and risk controls", "regulated reporting evidence"],
        "signals": ["claims queue age", "policy transaction failures", "billing batch status", "document upload errors", "agent portal uptime", "fraud review backlog"],
    },
    "Logistics / Transportation": {
        "label": "logistics and transportation",
        "users": ["Dispatchers", "Drivers", "Warehouse teams", "Customers", "Partner carriers", "Operations leaders"],
        "capabilities": ["Shipment tracking", "fleet visibility", "route optimization", "warehouse operations", "delivery scheduling", "partner integrations", "IoT telemetry", "real-time analytics"],
        "controls": ["partner API governance", "location data protection", "route SLA tracking", "telemetry quality checks", "operational recovery controls"],
        "signals": ["tracking lag", "driver app errors", "route update latency", "partner API timeouts", "telemetry ingestion rate", "warehouse event backlog"],
    },
    "Manufacturing / Automotive / Industrial": {
        "label": "manufacturing, automotive, and industrial",
        "users": ["Plant operations", "Quality teams", "Supply chain users", "Maintenance teams", "Engineering users", "Operations leaders"],
        "capabilities": ["MES execution", "production scheduling", "quality checks", "ERP integration", "supplier collaboration", "industrial telemetry", "predictive maintenance", "asset analytics"],
        "controls": ["plant change windows", "quality traceability", "ERP transaction audit", "equipment safety controls", "production evidence"],
        "signals": ["MES transaction failures", "machine telemetry lag", "quality exception volume", "ERP sync status", "line downtime", "supplier API errors"],
    },
    "Technology / SaaS / Enterprise Software": {
        "label": "technology, SaaS, and enterprise software",
        "users": ["Tenant admins", "End users", "Customer success", "Support engineers", "Product teams", "Compliance users"],
        "capabilities": ["multi-tenant access", "API workflows", "subscription entitlements", "feature flags", "product telemetry", "experimentation", "AI features", "observability"],
        "controls": ["tenant isolation", "entitlement checks", "audit logs", "release gates", "data privacy controls"],
        "signals": ["API latency", "tenant error rate", "feature flag exposure", "usage event lag", "subscription sync status", "model endpoint health"],
    },
    "Energy / Utilities / Data Centers": {
        "label": "energy, utilities, and data centers",
        "users": ["Grid operators", "Field crews", "Customer operations", "Trading users", "Data center operations", "Compliance users"],
        "capabilities": ["grid monitoring", "outage response", "field dispatch", "smart meter ingestion", "utility billing", "energy settlement", "capacity monitoring", "asset reliability"],
        "controls": ["operational safety", "regulatory evidence", "change approval", "telemetry quality", "mission-critical uptime"],
        "signals": ["grid event lag", "outage queue age", "smart meter ingestion rate", "billing batch status", "power capacity alerts", "cooling telemetry errors"],
    },
    "Telecom / Media / Communications": {
        "label": "telecom, media, and communications",
        "users": ["Subscribers", "Customer support", "Network operations", "Billing operations", "Field service", "Revenue assurance teams"],
        "capabilities": ["customer account access", "device activation", "billing", "payments", "plan management", "network provisioning", "service assurance", "5G analytics"],
        "controls": ["customer data protection", "billing audit", "service assurance evidence", "network change controls", "revenue reconciliation"],
        "signals": ["login success rate", "activation failures", "billing exceptions", "network alert volume", "service assurance MTTR", "payment confirmation lag"],
    },
}


SYSTEM_PROFILES: dict[str, dict[str, Any]] = {
    "online-banking-platform": {
        "name": "Online banking platform",
        "summary": "Customer-facing web channel for account access, money movement, statements, profile servicing, alerts, and self-service support.",
        "users": ["Retail banking customers", "Small business customers", "Customer support users", "Fraud operations", "Digital banking product owners"],
        "capabilities": ["Secure login and MFA", "Account summary", "Transaction history", "Transfers", "Bill pay", "Statements", "Profile updates", "Secure messages", "Service requests"],
        "systems": ["Customer profile service", "Account management system", "Payments platform", "Fraud detection platform", "KYC onboarding", "Regulatory reporting platform"],
        "data": ["Customer identity", "Account balances", "Posted and pending transactions", "Beneficiary details", "Device and session signals", "Audit events"],
        "risks": ["Account takeover", "Session hijacking", "Incorrect balance display", "Payment initiation failure", "PII exposure", "High latency during peak traffic"],
        "operational_signals": ["Login failure spike", "MFA challenge drop-off", "API 5xx rate", "Account summary latency", "Fraud rule blocks", "WAF denials", "Synthetic journey failure"],
        "business_flow": "A customer signs in through the browser, completes MFA, lands on account summary, reviews balances and transactions, initiates a transfer or bill payment, receives confirmation, and expects the activity to appear consistently across statements, alerts, fraud review, and support tools.",
        "architecture": "The web application usually sits behind DNS, CDN, WAF, identity provider, API gateway, load balancers, service mesh or ingress, microservices, managed databases, cache, event queues, and downstream banking integrations. Static assets can be served from CDN, while authenticated traffic goes through API gateway policies and token validation. Backend services call account, customer, payment, notification, fraud, document, and support services. Every important action emits audit events, metrics, logs, and traces because banking support teams must prove who did what, when it happened, which device was used, and whether the request reached the system of record.",
        "operations": "Production support starts by separating customer-impacting symptoms from isolated browser, identity, API, dependency, or data problems. A balance issue is handled differently from a login outage, a WAF false positive, or a delayed bill payment confirmation. Useful evidence includes browser synthetic checks, API traces, gateway status, session logs, account service response time, fraud decision logs, and recent deployment history. Recovery might involve rollback, traffic shift, cache invalidation, feature flag disablement, downstream escalation, runbook execution, or incident communication.",
        "interview": "The strongest explanation starts with customer journey, then moves through security, API flow, data dependencies, controls, observability, and support ownership. The platform is not just a website. It is the digital front door of the bank, connected to core account systems, payment rails, identity, fraud, documents, alerts, customer support, and regulatory evidence. The delivery work should be explained through concrete outcomes: safer releases, measurable API reliability, faster incident triage, better deployment evidence, stronger MFA and access controls, clearer resolver ownership, and lower customer-impacting downtime.",
    },
    "mobile-banking-app": {
        "name": "Mobile banking app",
        "summary": "iOS and Android banking channel for account access, biometric login, card controls, mobile deposits, alerts, transfers, and customer self-service.",
        "users": ["Retail customers", "Mobile-first customers", "Small business users", "Digital support teams", "Fraud operations"],
        "capabilities": ["Biometric login", "Device registration", "Account summary", "Mobile check deposit", "Card controls", "Push alerts", "Transfers", "Bill pay", "Secure messaging"],
        "systems": ["Identity provider", "Device risk service", "Online banking APIs", "Payments platform", "Credit card processing", "Fraud detection platform", "Notification service"],
        "data": ["Device fingerprint", "Push token", "Customer profile", "Session token", "Transaction history", "Deposit image metadata", "Location and risk signals"],
        "risks": ["Device compromise", "Push notification delay", "Biometric enrollment issue", "Mobile deposit image failure", "API version mismatch", "App release regression"],
        "operational_signals": ["Crash-free session rate", "App startup latency", "API error rate by app version", "Push delivery failures", "Device registration failures", "Mobile deposit exceptions"],
        "business_flow": "A customer opens the app, unlocks with biometrics or MFA, views accounts, performs a mobile-first action such as transfer, card lock, bill pay, or deposit, receives confirmation, and expects the same result to be visible in web banking, support tools, fraud systems, and statements.",
        "architecture": "The mobile app depends on mobile release pipelines, app store deployment, API versioning, identity and device trust, API gateway, backend-for-frontend services, account APIs, payment APIs, card services, image capture services, notification services, and observability. Mobile traffic often carries richer context than web traffic: device id, app version, OS version, location hint, jailbreak or root signal, push token, biometric state, and session risk score. The backend must handle older app versions during staged rollout, enforce contract compatibility, protect sensitive data on device, and produce support evidence that links a mobile session to API behavior and downstream decisions.",
        "operations": "A mobile incident is often split between client behavior and backend behavior. A crash after an app update is different from API 500 errors, stale balances, failed push notifications, or biometric enrollment failures. Support evidence includes crash analytics, release version, app store rollout percentage, API gateway logs, device risk decision, downstream service health, push provider status, and customer segment impact. Rollback is not always immediate because app store releases are different from backend deploys, so feature flags, remote configuration, staged rollout pause, API compatibility, and server-side mitigation are important operational controls.",
        "interview": "I explain the mobile banking app as ownership of a regulated customer channel, not only mobile UI support. The app connects identity, fraud, payments, cards, deposits, notifications, and account systems. My delivery story covers release control, API contract stability, device-risk observability, production issue triage, backend reliability, customer-impact measurement, and evidence for support teams. I name the mobile-specific constraints: app versions in the field, app store rollout delay, biometric and device registration, push provider dependency, crash analytics, and secure handling of customer data.",
    },
    "payments-platform": {
        "name": "Payments platform",
        "summary": "Enterprise money-movement layer for internal transfers, external transfers, bill pay, ACH, wires, payment scheduling, posting, exceptions, and reconciliation.",
        "users": ["Banking customers", "Treasury operations", "Payment operations", "Fraud teams", "Customer support", "Compliance users"],
        "capabilities": ["Payment initiation", "Beneficiary validation", "Limits and approvals", "ACH and wire processing", "Bill pay", "Payment status", "Exception handling", "Reconciliation", "Notifications"],
        "systems": ["Online banking platform", "Mobile banking app", "Fraud detection platform", "Core banking system", "ACH processor", "Wire network", "Notification service", "Regulatory reporting platform"],
        "data": ["Payment instruction", "Debit and credit account", "Beneficiary profile", "Payment status", "Settlement date", "Fraud score", "Exception code", "Reconciliation record"],
        "risks": ["Duplicate payment", "Delayed settlement", "Fraudulent transfer", "Incorrect status display", "Posting mismatch", "Processor outage", "Limit bypass"],
        "operational_signals": ["Queue lag", "Processor rejection rate", "Duplicate detection alerts", "Payment status mismatch", "Fraud hold volume", "Settlement file failure", "Reconciliation breaks"],
        "business_flow": "A customer or operations user initiates a payment, the platform validates account, beneficiary, limits, risk, and compliance rules, submits the instruction to the correct rail or processor, tracks status, posts confirmation back to customer channels, and reconciles settlement against core banking and reporting systems.",
        "architecture": "The payments platform is usually event-driven because payment initiation, risk scoring, approval, submission, settlement, notification, exception handling, and reconciliation happen across multiple systems and time windows. APIs receive instructions from web, mobile, branch, support, or internal channels. Validation services check limits, account status, beneficiary rules, OFAC or compliance constraints, and fraud risk. Queues and workflow engines move payment states from created to validated, held, submitted, settled, rejected, returned, reversed, or reconciled. Databases store idempotency keys, payment state, audit trail, and exception metadata. Integration adapters connect to ACH, wire, bill pay, card, core banking, and notification systems.",
        "operations": "Payments support requires strict evidence because wrong recovery can create financial loss. A failed transfer cannot simply be retried without checking idempotency, processor acknowledgement, ledger state, fraud hold, and reconciliation status. Useful evidence includes payment id, idempotency key, status transition history, queue offset, processor response, fraud decision, core posting result, notification status, and reconciliation outcome. Recovery might involve replay from a safe checkpoint, manual operations review, processor escalation, status correction, hold release, duplicate suppression, or customer communication. Controls must prevent duplicate debit, silent failure, and inconsistent channel status.",
        "interview": "I highlight payment-state ownership and operational discipline. The platform is not a simple API; it is the bank’s money movement control plane. I describe the work through safety, idempotency, auditability, queue reliability, processor integration, fraud and compliance handoff, exception handling, and reconciliation. My delivery story names failure modes such as duplicate payment, stuck payment, rejected payment, delayed processor file, mismatch between customer status and settlement, queue backlog, and fraud hold escalation. The outcome is faster triage, clearer ownership, safer retries, and better evidence for payment operations.",
    },
    "credit-card-processing": {
        "name": "Credit card processing",
        "summary": "Card servicing and transaction-processing ecosystem for authorization, posting, card controls, disputes, rewards, fraud checks, statements, and payment integration.",
        "users": ["Cardholders", "Card operations", "Fraud analysts", "Disputes team", "Customer support", "Statement operations"],
        "capabilities": ["Card authorization", "Transaction posting", "Card lock/unlock", "Limit management", "Disputes", "Rewards", "Statement generation", "Payment posting", "Fraud review"],
        "systems": ["Mobile banking app", "Online banking platform", "Card processor", "Fraud detection platform", "Payments platform", "Customer profile service", "Regulatory reporting platform"],
        "data": ["Card account", "Authorization request", "Merchant data", "Transaction status", "Fraud decision", "Dispute case", "Statement balance", "Payment posting record"],
        "risks": ["Authorization outage", "Incorrect available credit", "Card control delay", "Fraud false negative", "Statement mismatch", "Dispute SLA miss", "Payment posting delay"],
        "operational_signals": ["Authorization decline spike", "Processor latency", "Card control API failures", "Fraud queue volume", "Posting file failure", "Statement batch errors", "Payment posting exceptions"],
        "business_flow": "A cardholder uses a card, the authorization request flows through network and processor controls, the bank receives decision and transaction events, customer channels display pending and posted activity, payments reduce balance, disputes and fraud reviews handle exceptions, and statements summarize the account cycle.",
        "architecture": "Credit card processing is a connected ecosystem rather than one application. Real-time authorization depends on card network and processor integrations, fraud scoring, account status, available credit, merchant rules, and card controls. Near-real-time and batch flows handle clearing, posting, statements, rewards, disputes, chargebacks, payment posting, and regulatory reporting. Customer channels call servicing APIs for card lock, travel notice, transaction search, statement view, and payment. Event streams carry authorization, clearing, fraud, dispute, and payment events into operational stores and analytics platforms. The architecture must tolerate processor latency, batch windows, replay needs, and strict audit requirements.",
        "operations": "Card incidents are sensitive because customer impact is immediate and visible at the point of sale. An authorization decline spike can come from processor outage, fraud rule change, card network issue, account status defect, card control delay, or available-credit calculation problem. Evidence includes processor status, authorization response codes, fraud rule decisions, account status, card control events, deployment history, batch file status, and customer segment impact. Recovery can involve traffic reroute if available, rule rollback, card-control cache refresh, processor escalation, batch replay, statement correction, or customer support alerting.",
        "interview": "I explain card servicing through both real-time and batch operations. Authorization is time-sensitive; posting and statements are accuracy-sensitive; disputes and fraud are control-sensitive. My delivery story focuses on observability, integration reliability, deployment safety, processor handoff, reconciliation evidence, and incident response. I use concrete events such as decline spike, fraud rule false positive, card lock delay, payment posting mismatch, statement batch failure, dispute workflow backlog, or processor file delay. My role-specific contribution is making those flows measurable, recoverable, and explainable without claiming ownership of card product policy.",
    },
    "loan-origination-system": {
        "name": "Loan origination system",
        "summary": "Application intake and decisioning workflow for loan applications, applicant data, credit checks, underwriting, documents, approvals, disclosures, and booking.",
        "users": ["Loan applicants", "Loan officers", "Underwriters", "Credit analysts", "Document operations", "Compliance reviewers", "Customer support"],
        "capabilities": ["Application intake", "Identity verification", "Credit bureau integration", "Document upload", "Underwriting workflow", "Decisioning", "Disclosures", "Approval conditions", "Loan booking"],
        "systems": ["Online banking platform", "Mobile banking app", "KYC onboarding", "Credit bureau integrations", "Risk management system", "Document management system", "Account management system", "Regulatory reporting platform"],
        "data": ["Applicant profile", "Employment and income data", "Credit pull result", "Risk score", "Submitted documents", "Underwriting decision", "Approval conditions", "Disclosure package"],
        "risks": ["Application stuck in workflow", "Credit bureau timeout", "Incorrect decision status", "Document upload failure", "Disclosure generation error", "Compliance evidence gap", "Booking failure"],
        "operational_signals": ["Application drop-off", "Bureau API latency", "Workflow queue age", "Document processing errors", "Decision engine failures", "Disclosure batch errors", "Booking exceptions"],
        "business_flow": "An applicant starts a loan request through web, mobile, branch, or assisted channel, submits identity and financial information, uploads documents, passes KYC and credit checks, enters underwriting or automated decisioning, receives conditions or approval, signs disclosures, and moves to booking or funding.",
        "architecture": "Loan origination combines customer experience, workflow orchestration, third-party integrations, document handling, decisioning, and compliance controls. Intake APIs collect application data and validate required fields. Workflow services route the application through KYC, credit bureau pull, fraud checks, income verification, document review, underwriting, approval conditions, disclosure generation, and booking. Decision engines and risk systems score the application. Document systems store paystubs, statements, identity documents, disclosures, and signed packages. Integration adapters connect to bureaus, core banking, e-signature, notification, regulatory, and reporting platforms. Every state change requires an audit trail because lending decisions must be explainable and compliant.",
        "operations": "Loan origination support focuses on stuck applications, integration failures, document processing issues, decisioning exceptions, compliance evidence, and downstream booking errors. A credit bureau timeout is handled differently from an underwriting queue backlog or disclosure generation failure. Useful evidence includes application id, workflow state, bureau response, document status, decision engine result, underwriting assignment, disclosure package id, e-signature status, and booking response. Recovery may involve retrying an integration call, re-queuing a workflow step, regenerating a disclosure package, routing to manual underwriting, correcting a document metadata issue, or escalating to a product owner when business policy is involved.",
        "interview": "I connect the technology directly to lending operations. The system is not just an application form; it is a controlled workflow from intake to decision to disclosure to booking. My delivery story covers workflow visibility, integration reliability, audit trail, document handling, risk and compliance handoff, operational dashboards, and safe recovery from stuck states. I can name real failures such as bureau API timeout, stale application state, missing document metadata, decision engine error, disclosure generation failure, underwriting queue backlog, or booking mismatch. My role contribution is stronger reliability and evidence around the workflow while lending criteria and product policy stay with business owners.",
    },
}


ROLE_OWNERSHIP: dict[str, dict[str, str]] = {
    "DevOps Engineer": {
        "focus": "CI/CD, containerization, release automation, rollback, environment consistency, deployment evidence, and release governance",
        "delivery": "pipeline standards, image promotion, Helm values, smoke tests, deployment approvals, rollback validation, and production release notes",
    },
    "Cloud Automation Engineer": {
        "focus": "Terraform modules, self-service provisioning, IAM automation, certificate automation, backup checks, cloud governance, and operational scripts",
        "delivery": "reusable infrastructure modules, automated environment creation, policy checks, tagging controls, access automation, and validation jobs",
    },
    "Cloud Infrastructure Engineer": {
        "focus": "landing zone, VPC or VNet, private networking, routing, firewalls, load balancers, IAM, backup, DR, and private endpoints",
        "delivery": "network patterns, secure connectivity, load balancing, firewall rules, private service access, backup posture, and DR runbooks",
    },
    "Cloud Platform Engineer": {
        "focus": "cloud landing zones, Kubernetes platforms, Terraform modules, self-service provisioning, developer platform standards, IAM, networking, and observability baselines",
        "delivery": "cloud platform blueprints, reusable Terraform modules, Kubernetes standards, service scaffolds, golden paths, namespace guardrails, and platform onboarding evidence",
    },
    "Platform Engineer": {
        "focus": "internal developer platform, golden paths, shared workflows, reusable standards, Kubernetes standards, onboarding, and self-service",
        "delivery": "developer portals, service scaffolds, shared Helm charts, reusable CI workflows, namespace standards, and observability baselines",
    },
    "GitOps Engineer": {
        "focus": "Argo CD, Git as source of truth, promotion workflow, environment-specific configuration, drift detection, sync policies, and Git rollback",
        "delivery": "GitOps repos, app-of-apps structure, sync waves, policy gates, rollback procedures, drift alerts, and environment promotion controls",
    },
    "Site Reliability Engineer": {
        "focus": "SLOs, SLIs, error budgets, observability, incident response, RCA, runbooks, MTTR reduction, and toil reduction",
        "delivery": "SLO dashboards, paging rules, incident runbooks, post-incident actions, reliability reviews, error-budget reports, and recovery automation",
    },
    "Site Reliability / AIOps Engineer": {
        "focus": "SLOs, observability, incident response, alert correlation, anomaly detection, RCA assistance, noise reduction, runbooks, and MTTR reduction",
        "delivery": "SLO dashboards, correlation rules, anomaly baselines, incident enrichment, alert suppression logic, RCA dashboards, ServiceNow routing, and runbook updates",
    },
    "Data Platform Engineer": {
        "focus": "data pipelines, orchestration, schema validation, data quality checks, SLA monitoring, lineage, batch recovery, and warehouse reliability",
        "delivery": "pipeline DAGs, data quality gates, backfill controls, schema checks, freshness dashboards, failed-pipeline recovery, and consumer alerts",
    },
    "MLOps Engineer": {
        "focus": "ML pipelines, model registry, feature pipelines, batch and real-time inference, model monitoring, drift detection, and secure model deployment",
        "delivery": "model deployment pipelines, registry workflows, feature validation, inference monitoring, drift dashboards, retraining triggers, and rollback controls",
    },
    "MLOps / AI Platform Engineer": {
        "focus": "AI/ML platforms, ML pipelines, model registry, feature pipelines, batch and real-time inference, model monitoring, drift detection, and model deployment",
        "delivery": "AI platform standards, model deployment pipelines, registry workflows, feature validation, inference monitoring, drift dashboards, retraining triggers, and rollback controls",
    },
    "AIOps Engineer": {
        "focus": "alert correlation, anomaly detection, log analytics, incident intelligence, noise reduction, event correlation, and RCA assistance",
        "delivery": "correlation rules, anomaly baselines, incident enrichment, alert suppression logic, RCA dashboards, ServiceNow routing, and operational knowledge updates",
    },
    "Cloud Database Engineer": {
        "focus": "managed databases, backups, read replicas, encryption, performance tuning, migration, high availability, and replication monitoring",
        "delivery": "database provisioning, backup validation, slow-query analysis, replica monitoring, encryption controls, failover tests, and migration runbooks",
    },
}


def product_system_slug(name: str) -> Optional[str]:
    normalized = " ".join((name or "").split()).lower()
    for label, slug in SYSTEM_SLUGS.items():
        if " ".join(label.split()).lower() == normalized:
            return slug
    return None


def product_system_link_map(application_names: list[str]) -> dict[str, str]:
    return {name: slug for name in application_names if (slug := product_system_slug(name))}


def product_system_slug_lookup(application_names: list[str]) -> dict[str, str]:
    return {
        " ".join(name.split()).lower(): slug
        for name in application_names
        if (slug := product_system_slug(name))
    }


def product_system_cards(application_names: list[str]) -> list[dict[str, str]]:
    return [{"label": name, "slug": slug} for name in application_names if (slug := product_system_slug(name))]


def _jd_alignment(profile: dict[str, Any], role_name: str, role: dict[str, str]) -> dict[str, Any]:
    return {
        "source": "MAAS active JD alignment",
        "must_have_signals": [
            f"Explain {profile['name']} through {role['focus']}.",
            f"Connect JD terms to product evidence: {', '.join(profile['operational_signals'][:4]).lower()}.",
            f"Use product examples around {', '.join(profile['capabilities'][:3]).lower()} instead of only listing tools.",
        ],
        "interview_use_cases": [
            f"{profile['name']} request or data flow support",
            f"{profile['name']} release, rollback, or controlled change",
            f"{profile['name']} incident triage with evidence and handoff",
            f"{profile['name']} monitoring, data quality, or reliability improvement",
        ],
        "evidence_to_prepare": [
            "architecture diagram",
            "workflow diagram",
            "dashboard/log/query screenshot",
            "runbook or RCA note",
            "safe validation output",
        ],
    }


def _jd_diagrams(profile: dict[str, Any], role_name: str, role: dict[str, str]) -> list[dict[str, str]]:
    name = profile["name"]
    systems = profile["systems"][:3]
    signals = profile["operational_signals"][:3]
    return [
        {
            "title": f"{name} JD request flow",
            "diagram": f"User/Partner -> {name} -> {systems[0]} -> {systems[1]} -> Evidence Store",
            "interview_use": "In system-design and product-flow questions from active JDs, I explain this as the end-to-end business and technical flow.",
        },
        {
            "title": f"{name} support evidence flow",
            "diagram": f"Alert/Symptom -> {signals[0]} -> {signals[1]} -> Runbook -> RCA",
            "interview_use": "In production support, SRE, platform, and data reliability questions, I explain this as the operating model for detection, triage, recovery, and evidence.",
        },
        {
            "title": f"{role_name} ownership boundary",
            "diagram": f"Product Owner -> Application Team -> {role_name} -> Security/Operations -> Support Handoff",
            "interview_use": "I explain ownership clearly without overclaiming feature or business-policy decisions.",
        },
    ]


def product_system_detail(slug: str, role_name: str, domain_name: str = "") -> Optional[dict[str, Any]]:
    profile = SYSTEM_PROFILES.get(slug) or _generated_product_system_profile(slug, domain_name)
    if not profile:
        return None
    role = ROLE_OWNERSHIP.get(role_name, {"focus": "platform delivery, reliability, observability, support evidence, and controlled change", "delivery": "standards, dashboards, automation, runbooks, validation evidence, and support handoffs"})
    sections = _detail_sections(profile, role_name, role)
    return {
        "slug": slug,
        "name": profile["name"],
        "summary": profile["summary"],
        "users": profile["users"],
        "capabilities": profile["capabilities"],
        "systems": profile["systems"],
        "data": profile["data"],
        "risks": profile["risks"],
        "operational_signals": profile["operational_signals"],
        "interview_answer": _interview_answer(profile, role_name, role),
        "workflow_steps": _workflow_steps(profile),
        "visual_workflows": _visual_workflows(profile, role_name),
        "jd_alignment": _jd_alignment(profile, role_name, role),
        "jd_diagrams": _jd_diagrams(profile, role_name, role),
        "ownership_cards": _ownership_cards(profile, role_name, role),
        "issue_cards": _issue_cards(profile),
        "capability_details": _capability_details(profile, role_name),
        "integration_details": _integration_details(profile),
        "data_details": _data_details(profile),
        "risk_details": _risk_details(profile),
        "signal_details": _signal_details(profile),
        "sections": [_with_paragraphs(section) for section in sections],
        "word_count": sum(len(section["body"].split()) for section in sections),
    }


def banking_product_system_detail(slug: str, role_name: str) -> Optional[dict[str, Any]]:
    return product_system_detail(slug, role_name, "Banking / Financial Services")


def _generated_product_system_profile(slug: str, domain_name: str) -> Optional[dict[str, Any]]:
    name = _product_system_name_for_slug(slug)
    if not name:
        return None
    domain = _domain_for_product_system(name, domain_name)
    domain_profile = DOMAIN_PROFILES.get(domain or "", _generic_domain_profile())
    capabilities = _unique([_capability_from_name(name), *domain_profile["capabilities"]])[:9]
    connected_systems = _connected_systems_for(name, domain)
    operational_signals = _unique([_signal_from_name(name), *domain_profile["signals"]])[:7]
    risks = _risks_for(name, domain_profile)
    users = _unique(domain_profile["users"])[:6]
    data = _data_objects_for(name, domain_profile)
    system_label = domain_profile["label"]
    lower_name = name.lower()
    summary = f"{name} supports {system_label} workflows by coordinating user actions, operational controls, data changes, integration handoffs, and support evidence."
    return {
        "name": name,
        "summary": summary,
        "users": users,
        "capabilities": capabilities,
        "systems": connected_systems,
        "data": data,
        "risks": risks,
        "operational_signals": operational_signals,
        "business_flow": (
            f"A user or operations team starts a {lower_name} workflow, the platform validates identity, permissions, required data, and domain rules, then calls connected services to complete the request. "
            f"The result is stored, surfaced to the right channel, monitored through operational signals, and available as evidence for support, compliance, and recovery."
        ),
        "architecture": (
            f"{name} usually sits inside a layered {system_label} architecture with channel entry points, API gateway policies, runtime services, workflow jobs, domain data stores, event queues, observability, and integrations. "
            f"It exchanges data with systems such as {', '.join(connected_systems[:5])}. The architecture must preserve correlation ids, access decisions, state transitions, and audit events so teams can prove what happened during normal processing and during incidents."
        ),
        "operations": (
            f"Production support for {lower_name} starts by separating channel symptoms from API, workflow, data, integration, vendor, and reporting issues. "
            f"Useful evidence includes {', '.join(operational_signals[:5]).lower()}, recent deployments, configuration changes, tickets, runbooks, and downstream status. Recovery may involve rollback, traffic shift, replay from a safe checkpoint, queue drain, cache refresh, vendor escalation, data correction through approved controls, or customer-support communication."
        ),
        "interview": (
            f"I frame {lower_name} as a product workflow with engineering controls, not just a tool name. I connect business purpose, users, data, integrations, reliability signals, risks, and role-specific ownership. "
            f"Concrete examples include improving release safety, adding dashboards, reducing incident handoff time, strengthening audit evidence, or making recovery steps repeatable."
        ),
    }


def _product_system_name_for_slug(slug: str) -> Optional[str]:
    for name, candidate in SYSTEM_SLUGS.items():
        if candidate == slug:
            return name
    return None


def _domain_for_product_system(name: str, domain_name: str) -> str:
    if domain_name in DOMAIN_PRODUCT_SYSTEM_NAMES and name in DOMAIN_PRODUCT_SYSTEM_NAMES[domain_name]:
        return domain_name
    for domain, names in DOMAIN_PRODUCT_SYSTEM_NAMES.items():
        if name in names:
            return domain
    return domain_name


def _generic_domain_profile() -> dict[str, Any]:
    return {
        "label": "enterprise",
        "users": ["Customers", "Operations teams", "Support users", "Compliance users"],
        "capabilities": ["workflow processing", "secure access", "data updates", "integration handoffs", "reporting", "support visibility"],
        "controls": ["audit evidence", "access control", "encryption", "change approval"],
        "signals": ["API latency", "error rate", "queue depth", "workflow exceptions", "support ticket volume"],
    }


def _capability_from_name(name: str) -> str:
    base = re.sub(r"\b(platform|system|service|app|portal|engine|integration|workflow|dashboard)\b", "", name, flags=re.IGNORECASE)
    return f"{' '.join(base.split()).title()} workflow processing"


def _signal_from_name(name: str) -> str:
    base = " ".join(name.lower().split())
    return f"{base} availability and error rate"


def _connected_systems_for(name: str, domain: str) -> list[str]:
    names = [item for item in DOMAIN_PRODUCT_SYSTEM_NAMES.get(domain, []) if item != name]
    common = ["Identity provider", "API gateway", "Managed database", "Event queue", "Observability platform", "Customer support workflow", "Regulatory reporting platform"]
    return _unique([*names[:5], *common])[:9]


def _risks_for(name: str, domain_profile: dict[str, Any]) -> list[str]:
    lower_name = name.lower()
    return [
        f"{lower_name} outage or degraded availability",
        f"Incorrect or stale {lower_name} status",
        "Unauthorized access or exposed sensitive data",
        "Downstream integration timeout",
        "Queue backlog or delayed processing",
        "Reporting or audit evidence gap",
        f"Configuration or release regression affecting {lower_name}",
        *domain_profile["controls"][:2],
    ][:7]


def _data_objects_for(name: str, domain_profile: dict[str, Any]) -> list[str]:
    lower_name = name.lower()
    return [
        f"{name} record",
        "User identity and access context",
        "Workflow state and status history",
        "Request and response metadata",
        "Audit events and correlation ids",
        "Operational metrics and support tickets",
        *[f"{item.title()} evidence" for item in domain_profile["controls"][:2]],
    ][:8]


def _with_paragraphs(section: dict[str, str]) -> dict[str, Any]:
    paragraphs = [paragraph.strip() for paragraph in section["body"].split("\n\n") if paragraph.strip()]
    if len(paragraphs) == 1:
        paragraphs = _split_readable_paragraphs(paragraphs[0])
    return {**section, "paragraphs": paragraphs}


def _split_readable_paragraphs(value: str) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", value.strip())
    paragraphs: list[str] = []
    current: list[str] = []
    current_words = 0
    for sentence in sentences:
        if not sentence:
            continue
        sentence_words = len(sentence.split())
        if current and current_words + sentence_words > 58:
            paragraphs.append(" ".join(current))
            current = []
            current_words = 0
        current.append(sentence)
        current_words += sentence_words
    if current:
        paragraphs.append(" ".join(current))
    return paragraphs


def _interview_answer(profile: dict[str, Any], role_name: str, role: dict[str, str]) -> list[str]:
    name = profile["name"]
    return [
        f"{name} is a product system used to complete customer or operations workflows such as {', '.join(profile['capabilities'][:4]).lower()}.",
        f"The request path touches channel, identity, API gateway, runtime services, data stores, integrations, observability, and support workflows.",
        f"Critical dependencies include {', '.join(profile['systems'][:5])}.",
        f"As a {role_name}, ownership stayed around {role['focus']}; product rules and feature behavior stayed with product owners and application teams.",
        f"Evidence came from {', '.join(profile['operational_signals'][:5]).lower()}, deployment/configuration history, tickets, runbooks, and recovery validation.",
        f"The delivery outcome was safer operation of the system: clearer ownership, faster triage, stronger audit evidence, and fewer repeat support gaps.",
    ]


def _workflow_steps(profile: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {"title": "User action", "body": profile["business_flow"]},
        {"title": "Access and control", "body": f"Identity, MFA, device/session risk, authorization, WAF, API policy, and audit controls protect the request before it reaches business services."},
        {"title": "Business processing", "body": f"Services execute capabilities such as {', '.join(profile['capabilities'][:5]).lower()} while calling connected systems and recording state changes."},
        {"title": "Data and events", "body": f"The platform reads or writes {', '.join(profile['data'][:5]).lower()} and emits events for downstream processing, reporting, alerts, and support."},
        {"title": "Support evidence", "body": f"Teams validate success or failure using {', '.join(profile['operational_signals'][:5]).lower()}, incident records, runbooks, and recovery checks."},
    ]


def _visual_workflows(profile: dict[str, Any], role_name: str) -> list[dict[str, Any]]:
    name = profile["name"]
    return [
        {
            "title": "Business Flow",
            "type": "flow",
            "nodes": ["Customer / user", "Channel action", "Validation", "Processing", "Confirmation", "Support evidence"],
            "note": f"How {name.lower()} turns a user request into a confirmed banking outcome.",
        },
        {
            "title": "System Sequence",
            "type": "sequence",
            "nodes": _unique(["Channel", "API gateway", profile["systems"][0], profile["systems"][1], profile["systems"][2], "Audit / observability"]),
            "note": "How the request moves across APIs, product services, dependencies, and evidence systems.",
        },
        {
            "title": "Ownership Swimlane",
            "type": "swimlane",
            "lanes": [
                {"owner": "Product", "items": ["Business rules", "Priority", "Customer experience"]},
                {"owner": "App team", "items": ["Feature code", "API behavior", "Functional fixes"]},
                {"owner": role_name, "items": ["Signals", "Automation", "Runbooks", "Evidence"]},
                {"owner": "Ops / Support", "items": ["Ticket intake", "Customer updates", "Resolver handoff"]},
            ],
            "note": "Clear boundary for interview explanation: role ownership without overclaiming product or feature ownership.",
        },
        {
            "title": "Status / Recovery Flow",
            "type": "state",
            "nodes": ["Created", "Validated", "Submitted", "Completed", "Exception", "Recovered / closed"],
            "note": "How status changes are tracked, triaged, recovered, and documented.",
        },
    ]


def _unique(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _ownership_cards(profile: dict[str, Any], role_name: str, role: dict[str, str]) -> list[dict[str, list[str] | str]]:
    return [
        {
            "title": f"{role_name} owned",
            "items": [
                role["focus"],
                role["delivery"],
                "Dashboards, logs, alerts, deployment/configuration evidence, runbooks, and handoff notes.",
            ],
        },
        {
            "title": "Not owned",
            "items": [
                "Product roadmap, customer-facing business rules, domain policy, and priority decisions.",
                "Feature code fixes owned by application teams.",
                "Specialist remediation owned by security, database, vendor, or downstream platform teams.",
            ],
        },
        {
            "title": "Evidence to mention",
            "items": [
                f"System touched: {profile['name']}.",
                f"Connected systems: {', '.join(profile['systems'][:4])}.",
                f"Signals reviewed: {', '.join(profile['operational_signals'][:4])}.",
            ],
        },
    ]


def _issue_cards(profile: dict[str, Any]) -> list[dict[str, str]]:
    cards = []
    signals = profile["operational_signals"]
    for index, risk in enumerate(profile["risks"][:6]):
        signal = signals[index % len(signals)]
        cards.append(
            {
                "issue": risk,
                "signal": signal,
                "response": "Confirm impact, check recent change, isolate the failing layer, attach evidence, route to owner, validate recovery.",
            }
        )
    return cards


def _capability_details(profile: dict[str, Any], role_name: str) -> list[dict[str, str]]:
    name = profile["name"]
    details = []
    for capability in profile["capabilities"][:8]:
        details.append(
            {
                "title": capability,
                "meaning": f"Business function inside {name} that must complete consistently for customers, operations, and support teams.",
                "flow": f"Typical flow: request enters the channel, identity and policy checks run, the service updates workflow state, connected systems respond, and confirmation or exception status is recorded.",
                "ownership": f"{role_name} focuses on release safety, runtime health, automation, monitoring, runbooks, evidence, and handoff clarity around this capability.",
                "interview": f"Explain {capability.lower()} by covering user action, systems touched, status tracking, failure points, operational signals, and evidence used to validate recovery.",
            }
        )
    return details


def _integration_details(profile: dict[str, Any]) -> list[dict[str, str]]:
    name = profile["name"]
    signals = profile["operational_signals"]
    details = []
    for index, system in enumerate(profile["systems"][:8]):
        signal = signals[index % len(signals)]
        details.append(
            {
                "title": system,
                "purpose": f"Connected dependency used by {name} to complete the product workflow or provide evidence for operations and support.",
                "handoff": f"The handoff should preserve request id, correlation id, status, error reason, retry decision, and owner group so the issue can be routed without guessing.",
                "watch": f"Watch {signal.lower()}, timeout rate, rejected requests, stale status, queue lag, and recent deployment or configuration changes.",
                "interview": f"Say what {system} contributes to the flow, what happens when it is slow or unavailable, and how the team proves whether the failure is inside {name} or downstream.",
            }
        )
    return details


def _data_details(profile: dict[str, Any]) -> list[dict[str, str]]:
    name = profile["name"]
    details = []
    for item in profile["data"]:
        details.append(
            {
                "title": item,
                "meaning": f"Data element needed to understand the current state, history, or support evidence for {name}.",
                "used_by": "Used by application services, operations dashboards, support users, audit reviews, reporting jobs, and incident responders.",
                "quality": "Needs consistent ownership, timestamps, source system, correlation id, masking rules, retention rules, and clear status values.",
                "interview": f"Bring up {item.lower()} when explaining how the team traced requests, proved what happened, and avoided unsupported assumptions during incidents.",
            }
        )
    return details


def _risk_details(profile: dict[str, Any]) -> list[dict[str, str]]:
    signals = profile["operational_signals"]
    details = []
    for index, risk in enumerate(profile["risks"]):
        signal = signals[index % len(signals)]
        details.append(
            {
                "title": risk,
                "impact": "Can affect customer trust, operations workload, audit confidence, SLA performance, or downstream reconciliation.",
                "detect": f"Detect using {signal.lower()}, dependency health, error logs, queue depth, status mismatch, ticket volume, and recent change history.",
                "response": "Confirm impact, isolate the failing layer, attach evidence, route to the resolver group, validate recovery, and update the runbook or RCA.",
                "interview": f"My troubleshooting story follows this sequence: symptom, signal, failing layer, evidence collected, owner routed, recovery validated, and prevention added.",
            }
        )
    return details


def _signal_details(profile: dict[str, Any]) -> list[dict[str, str]]:
    details = []
    for signal in profile["operational_signals"]:
        details.append(
            {
                "title": signal,
                "meaning": "Operational indicator that shows whether the product journey is healthy, slow, failing, delayed, or producing exceptions.",
                "source": "Usually comes from application metrics, gateway logs, traces, synthetic checks, queue metrics, database metrics, tickets, or downstream integration status.",
                "action": "A useful signal should map to an owner, threshold, dashboard panel, alert rule, runbook step, and recovery validation check.",
                "interview": f"Explain {signal.lower()} as evidence, not decoration: what it measures, what threshold mattered, which team used it, and what decision it supported.",
            }
        )
    return details


def _detail_sections(profile: dict[str, Any], role_name: str, role: dict[str, str]) -> list[dict[str, str]]:
    name = profile["name"]
    capabilities = ", ".join(profile["capabilities"][:6])
    systems = ", ".join(profile["systems"][:6])
    signals = ", ".join(profile["operational_signals"][:6])
    risks = ", ".join(profile["risks"][:6])
    data = ", ".join(profile["data"][:6])
    return [
        {
            "title": "Business Purpose",
            "body": (
                f"{name} is one of the major product systems in the enterprise because it turns customer intent, operational controls, and regulated business activity into a controlled digital workflow. "
                f"The business value comes from speed, trust, accuracy, and availability. Customers and internal users expect the system to work during normal traffic, payroll days, statement cycles, product campaigns, fraud events, and incident windows. "
                f"The system supports {capabilities}. These are not isolated screens or API calls; they are customer journeys that depend on identity, risk, data correctness, downstream processing, audit evidence, and support readiness. "
                f"{profile['business_flow']} The product owner defines customer behavior, compliance expectations, prioritization, and business rules. Engineering teams make the workflow reliable, observable, secure, and recoverable."
            ),
        },
        {
            "title": "Users And Product Boundaries",
            "body": (
                f"The main user groups are {', '.join(item.lower() for item in profile['users'][:-1])}, and {profile['users'][-1].lower()}. Each group interacts with a different side of the same {name.lower()}.\n\n"
                f"Customers mainly care about whether the workflow is completed, confirmed, fast, and trustworthy. Operations teams care about exceptions, status, audit trails, and reconciliation. Risk, fraud, and compliance users focus on policy enforcement, evidence, and traceability. Customer support users need enough visibility to answer customer questions clearly without guessing.\n\n"
                f"This boundary is important to explain in interviews. The {name.lower()} owns the product workflow and the technical services that support it, but it does not own every downstream system. It integrates with systems such as {systems}.\n\n"
                f"When an issue occurs, the first responsibility is to identify where the failure is happening. The problem could be in the channel, identity layer, API gateway, runtime service, database, queue, third-party processor, rules engine, reporting layer, or customer support workflow. Once the failure area is identified, the team can route the issue to the right owner and focus on resolution instead of guessing."
            ),
        },
        {
            "title": "Core Capabilities",
            "body": (
                f"The core capabilities can be explained as a chain of controlled actions. A request enters through a channel, identity is verified, authorization is checked, business rules are applied, data is read or written, events are emitted, downstream systems are called, and the final status is shown back to the user. "
                f"For {name}, the important capabilities are {capabilities}. Each capability has a happy path and multiple exception paths. A successful transaction is only one part of the story; a production-grade system also needs duplicate prevention, timeout handling, status tracking, audit logging, access control, rollback or compensation logic, and customer-support visibility. "
                f"I describe what happens before the action, during processing, after confirmation, and during failure recovery."
            ),
        },
        {
            "title": "High-Level Architecture",
            "body": (
                f"{profile['architecture']} From a platform point of view, the architecture can be split into channel, access, API, runtime, data, integration, observability, and support layers. "
                f"The channel layer handles web or mobile entry points. The access layer covers identity, MFA, device trust, WAF, tokens, and authorization. The API layer handles routing, throttling, versioning, request validation, and policy checks. "
                f"The runtime layer runs services, workers, jobs, and integration adapters. The data layer stores operational records, reference data, state transitions, audit logs, and reporting extracts. The support layer connects telemetry, incidents, runbooks, and resolver groups."
            ),
        },
        {
            "title": "Data And Integrations",
            "body": (
                f"Important data objects include {data}. In enterprise product systems, data quality is not a back-office detail; it directly affects customer trust, compliance, financial correctness, and operational risk. "
                f"Integrations are usually the most important part of the system because the visible product action often depends on services outside the immediate application team. {name} commonly integrates with {systems}. "
                f"Good design keeps correlation ids, state history, request and response metadata, idempotency keys where needed, replay controls, and clear failure codes. This allows operations teams to explain whether a request failed before submission, after downstream acceptance, during processing, during notification, or during reconciliation."
            ),
        },
        {
            "title": "Security, Compliance, And Controls",
            "body": (
                f"Security controls must cover customer identity, privileged access, secrets, encryption, data masking, audit trail, and abuse detection. The most common risk areas are {risks}. "
                f"Controls should be visible in both runtime behavior and evidence. Examples include MFA enforcement, least-privilege service access, token expiration, WAF rules, API throttling, encryption at rest and in transit, masked logs, immutable audit events, change approval, and segregation between production support and product decision-making. "
                f"For regulated enterprise work, the technical answer must show that the system can prove what changed, who approved it, which version ran, what data was touched, which control fired, and how recovery was validated."
            ),
        },
        {
            "title": "Operational Model",
            "body": (
                f"{profile['operations']} My monitoring explanation includes customer journey checks, API health, dependency health, database and queue signals, error budgets, incident tickets, and business-level indicators. "
                f"Useful operational signals include {signals}. The operating model should make support repeatable: detect the issue, confirm impact, check recent changes, identify the failing layer, gather evidence, route to the correct resolver group, validate recovery, communicate status, and update the runbook or RCA. "
                f"That workflow matters because incidents can cross product, application, platform, database, vendor, risk, compliance, and customer-support teams within minutes."
            ),
        },
        {
            "title": f"{role_name} Ownership",
            "body": (
                f"As a {role_name}, the ownership should be explained through {role['focus']}. The delivered work is not the full product roadmap and not the business policy. "
                f"The contribution is the engineering layer that makes {name} easier to release, operate, monitor, troubleshoot, secure, or recover. Typical deliverables include {role['delivery']}. "
                f"The boundary is also clear: developers own feature code, product owners own business priority, QA owns functional validation, security owns control review, database teams own deep database remediation, and operations owns service desk intake. "
                f"The {role_name} connects those groups with technical evidence, repeatable workflows, standards, dashboards, automation, and production support artifacts."
            ),
        },
        {
            "title": "Interview Explanation",
            "body": (
                f"{profile['interview']} A crisp answer can start with the customer or operations journey, then explain the request path, major dependencies, risk controls, observability signals, and role-specific delivery. "
                f"For {name}, I explain what the system does, why it matters to banking, which systems it depends on, what can fail, how impact is detected, what evidence is collected, how ownership is routed, and what improvements were delivered. "
                f"My answer becomes stronger when I include measurable outcomes such as fewer noisy alerts, safer deployments, reduced MTTR, better support evidence, faster dependency isolation, cleaner rollback, stronger audit trail, or fewer repeat incidents."
            ),
        },
        {
            "title": "What To Remember",
            "body": (
                f"Remember {name} as product journey plus engineering controls. The direct narrative is: user action, systems touched, critical data, failure modes, signals, ownership boundary, and delivered improvements."
            ),
        },
    ]
