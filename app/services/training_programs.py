from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.company import Company
from app.models.job import JobOpportunity
from app.models.pursuit_intelligence import MarketingRole
from app.models.training import TrainingJobDescription, TrainingProgram
from app.services.marketing_glossary import MARKETING_ROLE_GLOSSARY, ROLE_TERMS


MARKETING_ROLE_NAMES = [
    "DevOps Engineer",
    "Cloud Platform Engineer",
    "Site Reliability / AIOps Engineer",
    "Data Platform Engineer",
    "MLOps / AI Platform Engineer",
]

RETIRED_MARKETING_ROLE_NAMES = {
    "Cloud Automation Engineer",
    "Platform Engineer",
    "GitOps Engineer",
    "AIOps Engineer",
    "Cloud Database Engineer",
}

INDUSTRY_DOMAINS = [
    "Healthcare / Health Insurance",
    "Banking / Financial Services",
    "Retail / E-Commerce",
    "Insurance",
    "Logistics / Transportation",
    "Manufacturing / Automotive / Industrial",
    "Technology / SaaS / Enterprise Software",
    "Energy / Utilities / Data Centers",
    "Telecom / Media / Communications",
]

ALL_MARKETING_ROLES_LABEL = "All marketing roles"
ALL_DOMAINS_LABEL = "All domains"

JD_PATTERNS = [
    ("Entry-Level", "Junior {role}", "Foundation"),
    ("Mid-Level", "{role}", "Market-ready"),
    ("Cloud-Heavy", "Cloud-focused {role}", "Market-ready"),
    ("Automation-Heavy", "Automation-focused {role}", "Advanced"),
    ("Production Support", "Production Support {role}", "Foundation"),
    ("Consulting C2C", "Consultant {role}", "Market-ready"),
    ("Regulated Enterprise", "{role} - {domain} Systems", "Advanced"),
]

MAAS_INTERVIEW_PLAYBOOKS: dict[str, dict[str, Any]] = {
    "devops": {
        "name": "DevOps / CI-CD / GitOps",
        "opening": "Start with the deployment flow, infrastructure ownership, and the last production change delivered.",
        "flow": ["Resume walkthrough and ownership scope", "Delivery pipeline and release safety", "Failure mode or rollback discussion", "Automation and drift probes", "Next improvement focus"],
        "coreQuestions": [
            "How do you design a safe CI/CD pipeline for multi-environment releases?",
            "What guardrails keep GitOps or IaC changes from drifting in production?",
            "How do you reduce deployment risk without slowing the team down?",
            "Tell me about a release failure you owned and how you recovered it.",
        ],
        "followUpProbes": [
            "What if a pipeline stage is flaky only in one region?",
            "How do you separate infrastructure drift from an application defect?",
            "Which checks belong before merge versus before production promotion?",
        ],
        "pressureChecks": [
            "A production deployment is stuck halfway through. What do you do first?",
            "A rollback is failing because the previous artifact is gone. How do you recover?",
        ],
        "evaluationFocus": ["Release safety", "Rollback discipline", "Automation quality", "Repeatability", "Infrastructure as Code", "Drift control", "Tradeoff clarity"],
        "rejectionSignals": ["Tool names without release flow", "No rollback path", "No explanation for why a guardrail exists"],
    },
    "sre": {
        "name": "Site Reliability Engineer",
        "opening": "Start with a recent incident, the timeline, and the first 10 minutes of triage.",
        "flow": ["Incident timeline and triage", "SLO, alerting, and observability", "Root cause and mitigation", "Resilience and prevention", "Toil reduction"],
        "coreQuestions": [
            "How would you define SLOs for a service with bursty traffic?",
            "What alert would you page on, and what alert would you suppress?",
            "How do you run an incident when the root cause is still unknown?",
            "How do you turn a postmortem into an operational change?",
        ],
        "followUpProbes": [
            "What is your first signal that the incident is user-impacting?",
            "How do you know whether to rollback, scale, or isolate traffic?",
            "What is the smallest safe change under pressure?",
        ],
        "pressureChecks": [
            "Traffic is rising, error budget is nearly exhausted, and the on-call is offline. What do you do?",
            "An alert is firing but the SLI looks okay. How do you decide whether to page?",
        ],
        "evaluationFocus": ["Triage calmness", "Escalation judgement", "SLO thinking", "Error-budget awareness", "Postmortem quality", "Toil reduction"],
        "rejectionSignals": ["Blame before evidence", "Cannot explain SLI versus SLO", "No recovery or prevention plan"],
    },
    "dataops": {
        "name": "DataOps / Data Platform",
        "opening": "Start with a data pipeline or warehouse change and explain the data path from source to consumer.",
        "flow": ["Data flow and ownership", "Pipeline reliability and backfills", "Schema or contract change", "Data quality and lineage", "Operating model"],
        "coreQuestions": [
            "How do you design a reliable orchestration flow for daily and late-arriving data?",
            "How do you catch bad data before it reaches analytics users?",
            "What do you do when a schema change breaks downstream consumers?",
            "How do you backfill without double-counting or corrupting history?",
        ],
        "followUpProbes": [
            "How do you separate transient pipeline failure from a true data defect?",
            "What lineage or metadata do you keep for debugging?",
            "How do you communicate a data outage to stakeholders?",
        ],
        "pressureChecks": [
            "A dashboard looks wrong 10 minutes before a leadership review. What do you check first?",
            "A backfill is taking longer than the SLA and warehouse cost is rising. How do you respond?",
        ],
        "evaluationFocus": ["Data correctness", "Freshness", "Backfill safety", "Schema evolution", "Consumer communication", "Cost-aware platform thinking"],
        "rejectionSignals": ["Only ETL tool talk", "No data quality checks", "No consumer protection story"],
    },
    "mlops": {
        "name": "MLOps",
        "opening": "Start with the last model or inference system operated and explain how it moved from training to production.",
        "flow": ["Model lifecycle and ownership", "Deployment and inference", "Drift and retraining", "Monitoring and rollback", "Governance"],
        "coreQuestions": [
            "How do you deploy a model safely without disrupting production traffic?",
            "What signals tell you a model is drifting or degrading?",
            "How do you keep training and inference features consistent?",
            "How do you decide when to retrain versus when to rollback?",
        ],
        "followUpProbes": [
            "How do you compare an old model and a new model before rollout?",
            "What happens when data quality drops but the model still serves?",
            "How do you make inference reproducible under pressure?",
        ],
        "pressureChecks": [
            "The model is returning wrong results after a deploy. What do you do first?",
            "An inference endpoint is healthy but user complaints are rising. How do you diagnose it?",
        ],
        "evaluationFocus": ["Model lifecycle ownership", "Deployment safety", "Canary or shadow rollout", "Drift monitoring", "Feature consistency", "Governance"],
        "rejectionSignals": ["Only training discussion", "No rollback or drift story", "Cannot explain feature parity"],
    },
    "aiops": {
        "name": "AIOps",
        "opening": "Start with an operations problem and explain how telemetry becomes action, not just a dashboard.",
        "flow": ["Telemetry and signal quality", "Alert correlation and noise reduction", "Automation or remediation", "Human review", "Trust and adoption"],
        "coreQuestions": [
            "How do you reduce noisy alerts without hiding a real incident?",
            "How do you correlate events across logs, metrics, and traces?",
            "What remediation would you automate first, and why?",
            "How do you keep operators trusting the AI output?",
        ],
        "followUpProbes": [
            "What if the model is right statistically but wrong operationally?",
            "How do you review false positives and false negatives?",
            "How do you explain the recommendation to an on-call engineer?",
        ],
        "pressureChecks": [
            "The AI system recommends the wrong runbook during an outage. What do you do?",
            "Alert volume doubles overnight and the team is overwhelmed. How do you respond?",
        ],
        "evaluationFocus": ["Signal quality", "Correlation", "Useful automation", "Human override", "Incident support", "False-positive handling"],
        "rejectionSignals": ["Models without ops context", "No false-positive plan", "No human override flow"],
    },
    "gitops": {
        "name": "GitOps",
        "opening": "Start with a change flowing through Git and explain how the cluster reaches desired state.",
        "flow": ["Desired state and Git source of truth", "Reconciliation and drift", "Rollback and promotion", "Policy and multi-cluster control", "Operational safety"],
        "coreQuestions": [
            "How do you design a GitOps flow for multiple environments without drift?",
            "What happens when the cluster and Git disagree?",
            "How do you roll back a bad change in a declarative system?",
            "How do you enforce policy before sync?",
        ],
        "followUpProbes": [
            "How do you handle secrets or encrypted values in GitOps?",
            "How do you promote changes safely across clusters?",
            "What audit trail proves the system changed as intended?",
        ],
        "pressureChecks": [
            "A sync succeeds but the app is broken. What do you inspect first?",
            "Two clusters are out of sync and one is production. How do you prioritize recovery?",
        ],
        "evaluationFocus": ["Declarative delivery", "Reconciliation", "Drift detection", "Rollback", "Policy enforcement", "Source-of-truth reasoning"],
        "rejectionSignals": ["GitOps as only a deployment tool", "No reconciliation or drift explanation", "No recovery plan for sync failures"],
    },
}


@dataclass(frozen=True)
class DomainProfile:
    short_key: str
    lines_of_business: list[dict[str, Any]]
    applications: list[str]
    context: str
    platform_signals: list[str]
    incidents: list[str]


@dataclass(frozen=True)
class SeedMarketingRole:
    name: str
    code: str
    description: str
    covers: str
    common_tools: str
    aliases: str
    keywords: str


DOMAIN_PROFILES: dict[str, DomainProfile] = {
    "Healthcare / Health Insurance": DomainProfile(
        short_key="healthcare",
        lines_of_business=[
            {
                "name": "Claims & Eligibility",
                "description": "High-volume insurance operations around member eligibility, claims intake, adjudication, provider validation, prior authorization, and billing handoff.",
                "systems": ["Member enrollment platform", "Claims processing system", "Policy administration system", "Provider network management", "Prior authorization platform"],
                "jobSignals": ["claims modernization", "eligibility APIs", "EDI integrations", "provider data", "healthcare data pipelines", "HIPAA controls"],
            },
            {
                "name": "Member / Patient / Provider Experience",
                "description": "Digital channels used by members, patients, providers, and support teams for care access, appointments, portal workflows, and service requests.",
                "systems": ["Patient portal", "Provider portal", "Appointment scheduling", "Telehealth platform", "Customer support platform"],
                "jobSignals": ["patient portal", "provider portal", "telehealth", "API reliability", "customer experience", "portal modernization"],
            },
            {
                "name": "Care Management & Healthcare Analytics",
                "description": "Clinical and operational data flows used for care coordination, EHR/lab/pharmacy integration, reporting, population health, and analytics.",
                "systems": ["Electronic Health Record integration", "Lab integration", "Pharmacy integration", "Care management platform", "Healthcare analytics platform"],
                "jobSignals": ["healthcare analytics", "EHR integration", "FHIR/HL7", "care management", "data warehouse", "data quality"],
            },
        ],
        applications=[
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
        context=(
            "The healthcare enterprise supports insurance and care delivery business units. Insurance applications handle member enrollment, "
            "eligibility, claims, provider networks, prior authorization, billing, and regulatory reporting. Care delivery applications handle "
            "patient and provider portals, scheduling, telehealth, EHR, lab, pharmacy, and care management integrations. Production work must protect PHI, preserve audit evidence, and keep patient/member-facing workflows reliable."
        ),
        platform_signals=["PHI controls", "HIPAA audit evidence", "eligibility and claims SLAs", "patient and provider portal uptime", "secure EHR integrations"],
        incidents=[
            "Claims API deployment failed because an environment-specific secret was missing.",
            "Patient portal pods restarted after a release introduced memory pressure.",
            "Eligibility service latency increased because a downstream connection pool was exhausted.",
            "Provider portal deployment needed rollback after smoke tests failed.",
            "Certificate renewal impacted API gateway routing for internal healthcare services.",
        ],
    ),
    "Banking / Financial Services": DomainProfile(
        short_key="banking",
        lines_of_business=[
            {
                "name": "Digital Banking",
                "description": "Customer-facing account access, profile, authentication, balances, transfers, alerts, and support visibility across web and mobile channels.",
                "systems": ["Online banking platform", "Mobile banking app", "Customer profile service", "Account management system"],
                "jobSignals": ["online banking", "mobile banking", "customer profile", "account platform", "digital channels", "API gateway"],
            },
            {
                "name": "Payments & Cards",
                "description": "Revenue-critical money movement and card workflows involving payment initiation, authorization, settlement, reconciliation, exceptions, and audit trail.",
                "systems": ["Payments platform", "Credit card processing", "Transaction monitoring", "Regulatory reporting platform"],
                "jobSignals": ["payments", "ACH", "wire", "cards", "PCI", "settlement", "reconciliation"],
            },
            {
                "name": "Lending, Risk & Fraud",
                "description": "Loan intake, KYC onboarding, fraud scoring, risk decisions, transaction surveillance, model-assisted detection, and regulatory evidence.",
                "systems": ["Loan origination system", "KYC onboarding", "Fraud detection platform", "Risk management system", "AI fraud analytics platform", "Data warehouse"],
                "jobSignals": ["loan origination", "KYC", "fraud detection", "risk management", "transaction monitoring", "AI fraud analytics"],
            },
        ],
        applications=[
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
        context=(
            "The banking enterprise supports digital banking, card, payment, lending, customer onboarding, fraud, risk, and regulatory reporting platforms. "
            "Teams must deliver changes with strong segregation of duties, audit trails, encryption, uptime controls, and rapid recovery because payment and account workflows are revenue-critical and highly regulated."
        ),
        platform_signals=["PCI controls", "SOX audit evidence", "payment availability", "fraud signal integrity", "KYC and transaction monitoring"],
        incidents=[
            "Payments deployment was paused after fraud scoring latency increased.",
            "Mobile banking release needed rollback because login success rate dropped.",
            "KYC onboarding pipeline failed after a schema change in customer profile data.",
            "Transaction monitoring alerts spiked after a noisy rules deployment.",
            "Regulatory reporting batch missed SLA because upstream payment files arrived late.",
        ],
    ),
    "Retail / E-Commerce": DomainProfile(
        short_key="retail",
        lines_of_business=[
            {
                "name": "Digital Commerce",
                "description": "Customer-facing shopping journeys from product discovery to cart, checkout, payment authorization, order confirmation, and conversion tracking.",
                "systems": ["E-commerce web platform", "Mobile shopping app", "Product catalog service", "Cart service", "Payment service"],
                "jobSignals": ["e-commerce", "checkout", "cart", "catalog", "payment reliability", "conversion"],
            },
            {
                "name": "Order, Inventory & Fulfillment",
                "description": "Post-purchase operational flow covering order orchestration, inventory accuracy, warehouse integration, shipping handoff, and fulfillment status.",
                "systems": ["Order management system", "Inventory management", "Warehouse integration", "Shipping integration"],
                "jobSignals": ["order management", "inventory", "fulfillment", "warehouse integration", "shipping", "supply chain"],
            },
            {
                "name": "Customer, Loyalty & Recommendations",
                "description": "Growth and retention systems for promotions, loyalty, recommendations, customer support, personalization, and customer analytics.",
                "systems": ["Loyalty platform", "Promotions engine", "Recommendation engine", "Customer support portal", "Customer analytics platform"],
                "jobSignals": ["loyalty", "promotions", "recommendations", "personalization", "customer analytics", "marketing technology"],
            },
        ],
        applications=[
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
        context=(
            "The retail enterprise supports customer-facing commerce, mobile shopping, catalog, cart, checkout, payments, loyalty, promotion, recommendation, order, inventory, warehouse, shipping, support, and analytics capabilities. "
            "Training emphasizes high traffic releases, seasonal readiness, inventory accuracy, payment reliability, and customer experience metrics."
        ),
        platform_signals=["peak traffic readiness", "checkout conversion", "cart and payment reliability", "inventory accuracy", "promotion release windows"],
        incidents=[
            "Checkout latency rose during a promotion because payment service autoscaling lagged.",
            "Catalog cache invalidation caused stale pricing on the web platform.",
            "Order management deployment failed smoke tests against shipping integration.",
            "Recommendation service consumed unexpected CPU after a model update.",
            "Warehouse API throttling delayed inventory updates during peak traffic.",
        ],
    ),
    "Insurance": DomainProfile(
        short_key="insurance",
        lines_of_business=[
            {
                "name": "Policy Administration & Billing",
                "description": "Policy lifecycle operations covering quote-to-bind, endorsements, renewals, premium billing, document generation, and customer/agent visibility.",
                "systems": ["Policy administration system", "Premium billing system", "Agent portal", "Customer portal", "Document management system"],
                "jobSignals": ["policy administration", "billing", "Guidewire", "agent portal", "document management", "policy lifecycle"],
            },
            {
                "name": "Claims Management",
                "description": "Claims intake, document upload, adjudication, payment handoff, customer updates, support workflows, and claims SLA tracking.",
                "systems": ["Claims intake platform", "Claims adjudication system", "Customer support workflow", "Analytics dashboard"],
                "jobSignals": ["claims", "claims adjudication", "FNOL", "claims automation", "document workflow", "claims analytics"],
            },
            {
                "name": "Underwriting, Risk & Fraud",
                "description": "Risk evaluation, underwriting workflow, fraud detection, regulatory reporting, actuarial/analytics support, and decision evidence.",
                "systems": ["Underwriting platform", "Risk scoring platform", "Fraud detection", "Regulatory reporting", "Data warehouse"],
                "jobSignals": ["underwriting", "risk scoring", "fraud detection", "regulatory reporting", "insurance analytics", "data warehouse"],
            },
        ],
        applications=[
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
        context=(
            "The insurance enterprise supports policy lifecycle, quote, underwriting, premium billing, claims intake, adjudication, agent/customer portals, document management, fraud, risk, regulatory reporting, and analytics. "
            "Production delivery must support auditability, document-heavy workflows, batch integrations, and reliable claims processing."
        ),
        platform_signals=["policy lifecycle audit", "claims SLA", "agent portal availability", "document retention", "fraud and risk scoring"],
        incidents=[
            "Claims intake release failed because document upload permissions were misconfigured.",
            "Underwriting workflow slowed after risk scoring API latency increased.",
            "Premium billing batch needed rerun after downstream file validation failed.",
            "Agent portal deployment required rollback due to session errors.",
            "Regulatory reporting dashboard missed daily refresh because warehouse load failed.",
        ],
    ),
    "Logistics / Transportation": DomainProfile(
        short_key="logistics",
        lines_of_business=[
            {
                "name": "Shipment Visibility & Tracking",
                "description": "Customer and operations visibility into shipment status, partner events, tracking timelines, exception states, and real-time analytics.",
                "systems": ["Shipment tracking platform", "Customer tracking portal", "Partner API integrations", "Real-time analytics dashboard"],
                "jobSignals": ["shipment tracking", "visibility platform", "partner APIs", "real-time analytics", "event streaming", "customer tracking"],
            },
            {
                "name": "Fleet, Route & Delivery Operations",
                "description": "Operational systems for fleet health, route optimization, delivery scheduling, driver mobile workflows, telemetry, and field execution.",
                "systems": ["Fleet management system", "Route optimization engine", "Delivery scheduling platform", "Driver mobile app", "IoT telemetry platform"],
                "jobSignals": ["fleet management", "route optimization", "driver app", "delivery scheduling", "IoT telemetry", "last mile"],
            },
            {
                "name": "Warehouse, Inventory & Fulfillment",
                "description": "Warehouse and fulfillment workflows for inventory movement, order fulfillment, WMS integration, stock accuracy, and support operations.",
                "systems": ["Warehouse management system", "Order fulfillment platform", "Inventory movement system", "Customer support platform"],
                "jobSignals": ["warehouse management", "WMS", "fulfillment", "inventory movement", "supply chain", "operations support"],
            },
        ],
        applications=[
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
        context=(
            "The logistics enterprise supports shipment tracking, fleet, routing, warehouse, delivery scheduling, driver mobile, customer tracking, fulfillment, inventory movement, partner APIs, IoT telemetry, real-time analytics, and support operations. "
            "Training emphasizes real-time visibility, mobile reliability, partner integration stability, telemetry scale, and operational recovery."
        ),
        platform_signals=["real-time tracking", "fleet and route SLAs", "driver app uptime", "IoT telemetry quality", "partner API reliability"],
        incidents=[
            "Shipment tracking lagged after Kafka consumer throughput dropped.",
            "Driver mobile app release was rolled back because route updates stopped syncing.",
            "Partner API timeout caused fulfillment status mismatch.",
            "IoT telemetry ingestion spiked and exhausted stream processing capacity.",
            "Warehouse integration deployment failed validation for inventory movement events.",
        ],
    ),
    "Manufacturing / Automotive / Industrial": DomainProfile(
        short_key="manufacturing",
        lines_of_business=[
            {
                "name": "Plant Operations & MES",
                "description": "Factory-floor execution covering production orders, work centers, machine status, quality checkpoints, shift operations, and manufacturing execution visibility.",
                "systems": ["Manufacturing execution system", "Plant operations portal", "Machine telemetry platform", "Quality management system", "Production scheduling system"],
                "jobSignals": ["MES", "plant systems", "industrial IoT", "quality systems", "production scheduling", "smart factory"],
            },
            {
                "name": "Supply Chain, ERP & Inventory",
                "description": "Enterprise operations connecting procurement, ERP, inventory, supplier integrations, materials planning, warehouse flow, and shipment readiness.",
                "systems": ["ERP integration platform", "Supplier portal", "Inventory planning system", "Warehouse integration", "Procurement workflow"],
                "jobSignals": ["SAP", "ERP integration", "supply chain", "inventory planning", "supplier portal", "warehouse automation"],
            },
            {
                "name": "Connected Products & Industrial Analytics",
                "description": "Telemetry, predictive maintenance, connected vehicle or device data, asset health, anomaly detection, and analytics for product and plant reliability.",
                "systems": ["Connected product platform", "Predictive maintenance platform", "Industrial data lake", "Asset performance dashboard", "Robotics integration"],
                "jobSignals": ["IoT telemetry", "predictive maintenance", "robotics", "industrial analytics", "asset performance", "edge data"],
            },
        ],
        applications=[
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
        context=(
            "The manufacturing enterprise supports plant operations, MES, ERP integration, supplier collaboration, production scheduling, quality, warehouse, connected products, IoT telemetry, predictive maintenance, and industrial analytics. "
            "Training emphasizes factory uptime, production traceability, quality evidence, supplier reliability, edge-to-cloud telemetry, and controlled releases for plant-facing systems."
        ),
        platform_signals=["plant uptime", "MES transaction reliability", "quality traceability", "ERP integration stability", "industrial telemetry freshness"],
        incidents=[
            "MES deployment was paused because production order status stopped syncing with ERP.",
            "Machine telemetry ingestion lagged after an edge gateway certificate expired.",
            "Quality dashboard showed stale inspection data after a schema change.",
            "Supplier portal release failed smoke tests for purchase order acknowledgment.",
            "Predictive maintenance pipeline missed SLA during a high-volume plant shift.",
        ],
    ),
    "Technology / SaaS / Enterprise Software": DomainProfile(
        short_key="saas",
        lines_of_business=[
            {
                "name": "Core Product Platform",
                "description": "Multi-tenant customer product capabilities covering accounts, subscriptions, feature flags, APIs, workflow services, and product usage telemetry.",
                "systems": ["SaaS application platform", "Tenant management service", "API platform", "Feature flag service", "Usage telemetry platform"],
                "jobSignals": ["SaaS platform", "multi-tenant", "API platform", "feature flags", "product telemetry", "cloud-native"],
            },
            {
                "name": "Customer Lifecycle & Revenue Operations",
                "description": "Customer onboarding, identity, billing, subscription management, entitlement, support, and customer success workflows.",
                "systems": ["Customer onboarding platform", "Identity and access service", "Subscription billing system", "Entitlement service", "Customer support platform"],
                "jobSignals": ["customer onboarding", "subscription billing", "entitlements", "identity", "customer success", "support operations"],
            },
            {
                "name": "Data, AI & Platform Operations",
                "description": "Product analytics, data platform, experimentation, AI features, model serving, observability, release safety, and reliability engineering.",
                "systems": ["Product analytics platform", "Experimentation platform", "AI feature platform", "Model serving platform", "Observability platform"],
                "jobSignals": ["product analytics", "experimentation", "AI features", "model serving", "SRE", "platform engineering"],
            },
        ],
        applications=[
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
        context=(
            "The SaaS enterprise supports a multi-tenant product platform, APIs, subscriptions, customer onboarding, identity, entitlement, support, product analytics, experimentation, AI features, model serving, and observability. "
            "Training emphasizes tenant isolation, release safety, uptime, product telemetry, entitlement correctness, AI feature governance, and scalable cloud-native delivery."
        ),
        platform_signals=["tenant isolation", "API availability", "subscription entitlement correctness", "product telemetry freshness", "AI feature reliability"],
        incidents=[
            "API platform latency increased after a high-traffic customer rollout.",
            "Feature flag misconfiguration exposed an incomplete workflow to a tenant segment.",
            "Subscription entitlement sync failed after a billing integration change.",
            "Product analytics ingestion lagged after event volume doubled.",
            "Model serving endpoint needed rollback after prediction latency exceeded SLA.",
        ],
    ),
    "Energy / Utilities / Data Centers": DomainProfile(
        short_key="energy",
        lines_of_business=[
            {
                "name": "Grid, Outage & Field Operations",
                "description": "Utility operations around grid monitoring, outage detection, restoration workflow, field crew dispatch, smart meter events, and customer outage communication.",
                "systems": ["Grid monitoring platform", "Outage management system", "Field crew dispatch platform", "Smart meter data platform", "Customer outage portal"],
                "jobSignals": ["grid modernization", "outage management", "smart meters", "field dispatch", "utility operations", "SCADA integration"],
            },
            {
                "name": "Energy Trading, Billing & Customer Systems",
                "description": "Customer account, billing, usage, rate plans, energy trading, settlement, regulatory reporting, and customer service workflows.",
                "systems": ["Utility billing system", "Customer account portal", "Energy trading platform", "Settlement reporting platform", "Regulatory compliance platform"],
                "jobSignals": ["utility billing", "energy trading", "settlement", "regulatory reporting", "customer account", "usage analytics"],
            },
            {
                "name": "Data Centers & Asset Reliability",
                "description": "Mission-critical data center operations, capacity telemetry, power/cooling monitoring, asset health, maintenance planning, and reliability analytics.",
                "systems": ["Data center infrastructure monitoring", "Power capacity platform", "Cooling telemetry platform", "Asset maintenance system", "Reliability analytics platform"],
                "jobSignals": ["data center", "power infrastructure", "capacity monitoring", "asset reliability", "predictive maintenance", "renewables"],
            },
        ],
        applications=[
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
        context=(
            "The energy, utility, and data center enterprise supports grid monitoring, outage management, field dispatch, smart meters, billing, customer account, energy trading, settlement, regulatory reporting, data center telemetry, power/cooling capacity, maintenance, and reliability analytics. "
            "Training emphasizes uptime, safety, real-time telemetry, regulatory evidence, mission-critical infrastructure, incident response, and controlled platform changes."
        ),
        platform_signals=["grid event freshness", "outage restoration SLA", "smart meter data quality", "data center power capacity", "asset reliability signals"],
        incidents=[
            "Outage management dashboard lagged after smart meter event volume spiked.",
            "Field dispatch integration failed to update crew assignment status.",
            "Utility billing batch missed SLA because usage files arrived late.",
            "Data center cooling telemetry alert storm hid a real capacity issue.",
            "Energy settlement report needed rerun after an upstream schema change.",
        ],
    ),
    "Telecom / Media / Communications": DomainProfile(
        short_key="telecom",
        lines_of_business=[
            {
                "name": "Customer & Digital Experience",
                "description": "Customer-facing mobile, web, identity, plan management, device activation, order status, support visibility, and service-change journeys.",
                "systems": ["Mobile account app", "Web customer portal", "Identity and login service", "Plan management platform", "Device activation platform"],
                "jobSignals": ["mobile app", "customer portal", "device activation", "identity", "plan management", "digital experience"],
            },
            {
                "name": "Billing, Payments & Revenue Operations",
                "description": "Subscription billing, payments, device financing, promotions, collections, revenue assurance, invoice generation, and customer account corrections.",
                "systems": ["Telecom billing platform", "Payments platform", "Device financing system", "Promotions engine", "Revenue assurance platform"],
                "jobSignals": ["telecom billing", "payments", "subscriptions", "device financing", "revenue assurance", "collections"],
            },
            {
                "name": "Network, Service Assurance & Field Operations",
                "description": "5G/network provisioning, service assurance, outage management, network monitoring, field work orders, capacity telemetry, and AIOps event correlation.",
                "systems": ["Network provisioning platform", "Service assurance platform", "Outage management system", "Network monitoring platform", "Field service platform", "5G analytics platform"],
                "jobSignals": ["5G", "network automation", "service assurance", "network monitoring", "AIOps", "field service"],
            },
        ],
        applications=[
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
        context=(
            "The telecom enterprise supports customer digital channels, identity, plan management, device activation, billing, payments, device financing, revenue assurance, network provisioning, service assurance, outage management, network monitoring, field service, and 5G analytics. "
            "Training emphasizes always-on customer service, high-volume billing, network event visibility, field operations, observability, and controlled release support for customer-impacting systems."
        ),
        platform_signals=["customer login success", "device activation SLA", "billing accuracy", "network event correlation", "service assurance MTTR"],
        incidents=[
            "Device activation workflow stalled after downstream provisioning API latency increased.",
            "Billing statement batch needed rerun after promotion rules produced incorrect discounts.",
            "Mobile account app release caused login failures for a customer segment.",
            "Network monitoring alert storm masked a service assurance incident.",
            "Revenue assurance reconciliation found missing payment confirmation events.",
        ],
    ),
}


ROLE_OWNERSHIP: dict[str, dict[str, list[str]]] = {
    "DevOps Engineer": {
        "focus": ["CI/CD", "GitHub Actions", "Docker", "Kubernetes", "Helm", "Terraform support", "deployment automation", "release management", "rollback", "secrets integration", "environment standardization"],
        "responsibilities": [
            "Built reusable GitHub Actions workflows for Java, .NET, Python, and frontend services.",
            "Containerized domain applications with Docker and standardized runtime configuration.",
            "Created Helm charts and Kubernetes manifests for dev, QA, stage, and production.",
            "Supported Terraform-based provisioning for namespaces, secrets, storage, ingress, and monitoring resources.",
            "Implemented approval-based production deployment workflows with rollback and smoke testing.",
            "Integrated image scanning, dependency scanning, and code quality checks into CI/CD pipelines.",
            "Created deployment dashboards, runbooks, and release evidence for production support.",
        ],
        "tools": ["GitHub", "GitHub Actions", "Docker", "Kubernetes", "Helm", "Terraform", "Argo CD", "SonarQube", "Trivy", "Prometheus", "Grafana", "Secrets Manager"],
    },
    "Cloud Automation Engineer": {
        "focus": ["Terraform modules", "infrastructure automation", "self-service provisioning", "environment creation", "IAM automation", "backup validation automation", "cloud governance", "cost tagging", "certificate automation", "operational scripts"],
        "responsibilities": [
            "Created reusable Terraform modules for network, compute, Kubernetes, database, IAM, storage, and monitoring resources.",
            "Automated environment creation for dev, QA, stage, production, and disaster recovery.",
            "Built self-service provisioning workflows with approval, tagging, policy, and cost controls.",
            "Automated certificate renewal, backup validation, IAM role creation, and operational scripts.",
            "Standardized cloud governance guardrails and drift checks across application teams.",
        ],
        "tools": ["Terraform", "Ansible", "Python", "Bash", "PowerShell", "GitHub Actions", "AWS CloudFormation", "Azure Bicep", "Vault", "CloudWatch", "Azure Monitor"],
    },
    "Cloud Platform Engineer": {
        "focus": ["cloud landing zone", "networking", "VPC / VNet", "subnets", "routing", "load balancers", "Kubernetes platform", "Terraform modules", "developer platform", "self-service provisioning", "IAM", "observability baseline"],
        "responsibilities": [
            "Designed secure cloud network foundations with VPC/VNet, subnets, routing, firewalls, private endpoints, and load balancers.",
            "Built Kubernetes, compute, storage, DNS, IAM, backup, and disaster recovery foundations.",
            "Created reusable Terraform modules, platform guardrails, service scaffolds, and golden paths for application teams.",
            "Standardized Kubernetes namespace, ingress, secrets, observability, and deployment conventions.",
            "Implemented environment isolation and connectivity patterns for internal, partner, and customer-facing systems.",
            "Hardened infrastructure with encryption, access controls, logging, monitoring, and DR validation.",
        ],
        "tools": ["AWS", "Azure", "GCP", "VPC", "VNet", "IAM", "Load Balancer", "DNS", "Kubernetes", "Terraform", "Helm", "Argo CD", "Backstage", "Crossplane", "Vault", "CloudWatch", "Azure Monitor"],
    },
    "Platform Engineer": {
        "focus": ["internal developer platform", "golden paths", "reusable standards", "shared GitHub workflows", "shared Helm charts", "Kubernetes standards", "developer onboarding", "self-service platform", "observability baseline"],
        "responsibilities": [
            "Built internal developer platform patterns with golden paths, service scaffolds, shared workflows, and platform documentation.",
            "Standardized Kubernetes namespace, ingress, secrets, observability, and deployment conventions.",
            "Created self-service onboarding for application teams using reusable standards and shared Helm charts.",
            "Maintained developer productivity standards while enforcing security, reliability, and operational baselines.",
        ],
        "tools": ["Backstage", "Kubernetes", "Helm", "Terraform", "Argo CD", "GitHub Actions", "Crossplane", "Vault", "Prometheus", "Grafana", "OpenTelemetry"],
    },
    "GitOps Engineer": {
        "focus": ["Argo CD", "GitHub as source of truth", "environment-specific Helm values", "deployment drift detection", "promotion workflow", "Git-based rollback", "sync policies", "production deployment governance"],
        "responsibilities": [
            "Implemented GitOps deployment workflows using GitHub, Argo CD, Helm, and environment-specific values.",
            "Configured sync policies, drift detection, promotion flow, rollback, and production deployment governance.",
            "Separated application, infrastructure, and secret changes into auditable pull request workflows.",
            "Created dashboards and runbooks for sync health, failed deployments, and rollback procedures.",
        ],
        "tools": ["Argo CD", "Flux", "GitHub", "Helm", "Kustomize", "Kubernetes", "Sealed Secrets", "External Secrets Operator", "Vault", "Terraform"],
    },
    "Site Reliability / AIOps Engineer": {
        "focus": ["SLO", "SLI", "error budgets", "Datadog service health", "APM traces", "log analytics", "Kubernetes telemetry", "observability pipelines", "alert correlation", "anomaly detection", "incident response", "RCA", "noise reduction", "MTTR reduction", "production runbooks"],
        "responsibilities": [
            "Defined SLIs, SLOs, alert thresholds, and error budget reporting for production services.",
            "Built Datadog-style observability dashboards using metrics, logs, traces, service maps, Kubernetes health, and synthetic checks.",
            "Connected APM traces, logs, infrastructure metrics, deployment events, and user-impact signals into one triage path.",
            "Designed Kubernetes monitoring with agent, cluster agent, workload, node, pod, service, and container health views.",
            "Planned observability pipelines for filtering, routing, redaction, and cost-aware telemetry retention.",
            "Built alert correlation, anomaly detection, incident enrichment, and noise-reduction workflows.",
            "Led incident response, triage, RCA, runbook updates, and MTTR reduction initiatives.",
            "Improved alert quality, capacity planning, deployment correlation, and production readiness reviews.",
        ],
        "tools": ["Datadog APM", "Datadog Log Management", "Datadog Infrastructure Monitoring", "Datadog Kubernetes Monitoring", "Datadog SLOs", "Datadog Monitors", "Datadog Service Map", "Datadog Observability Pipelines", "Datadog Watchdog", "Datadog Incident Management", "Prometheus", "Grafana", "OpenTelemetry", "New Relic", "Splunk ITSI", "Dynatrace", "Moogsoft", "BigPanda", "ServiceNow ITOM", "PagerDuty", "Opsgenie", "Kubernetes", "Python"],
    },
    "Data Platform Engineer": {
        "focus": ["data pipelines", "Airflow / Cloud Composer", "data quality checks", "schema validation", "pipeline SLA", "data lake", "data warehouse", "dbt", "failed pipeline recovery", "batch and streaming reliability", "Python and PySpark services", "MDM and governance", "data observability", "legacy migration", "operational dashboards"],
        "responsibilities": [
            "Built and supported batch and streaming data pipelines for domain analytics and operational reporting.",
            "Implemented Airflow orchestration, dbt transformations, schema validation, data quality checks, and SLA monitoring.",
            "Created pipeline recovery runbooks, data freshness dashboards, and failed job alerting.",
            "Coordinated source system changes, warehouse loads, lineage, and governance controls with data teams.",
            "Built Python, SQL, and PySpark processing services for production datasets, customer-facing analytics, and ML platform inputs.",
            "Defined MDM standards, conceptual/logical data models, data dictionaries, lineage notes, and master-data validation rules.",
            "Supported legacy migration, reconciliation, dashboard-ready marts, and data observability signals such as freshness, volume, schema, lineage, and anomaly alerts.",
        ],
        "tools": ["Python", "SQL", "PySpark", "Apache Airflow", "Cloud Composer", "dbt", "Spark", "Kafka", "Databricks", "Snowflake", "Redshift", "BigQuery", "Azure Data Factory", "AWS Glue", "Great Expectations", "Monte Carlo-style data observability", "Terraform", "Git", "Power BI", "Tableau", "Looker"],
    },
    "MLOps / AI Platform Engineer": {
        "focus": ["ML pipelines", "MLflow", "model registry", "feature pipelines", "batch inference", "real-time inference", "model deployment", "model monitoring", "drift detection", "secure model access"],
        "responsibilities": [
            "Built ML training, validation, registry, deployment, and monitoring pipelines for domain prediction use cases.",
            "Managed feature pipelines, model versioning, batch inference, real-time inference, and secure endpoint access.",
            "Implemented model drift, data drift, performance monitoring, rollback, and audit-ready release records.",
            "Collaborated with data science, platform, security, and application teams to productionize models safely.",
        ],
        "tools": ["MLflow", "Kubeflow", "SageMaker", "Azure ML", "Vertex AI", "Docker", "Kubernetes", "Airflow", "Feast", "KServe", "Prometheus", "Grafana"],
    },
    "AIOps Engineer": {
        "focus": ["alert correlation", "anomaly detection", "log analytics", "incident intelligence", "noise reduction", "RCA assistance", "deployment-event correlation", "operational knowledge base"],
        "responsibilities": [
            "Built alert correlation, anomaly detection, log analytics, and incident intelligence workflows.",
            "Reduced alert noise by grouping symptoms, deployments, infrastructure events, and known incidents.",
            "Created RCA assistance, operational knowledge base enrichment, and automated ticket context.",
            "Integrated observability data with ServiceNow, incident response, and runbook automation processes.",
        ],
        "tools": ["Dynatrace", "Datadog", "Splunk ITSI", "New Relic", "Moogsoft", "BigPanda", "ServiceNow ITOM", "OpenTelemetry", "OpenSearch", "Python"],
    },
    "Cloud Database Engineer": {
        "focus": ["managed databases", "PostgreSQL", "SQL Server", "MySQL", "NoSQL", "Redis", "OpenSearch", "backups", "read replicas", "encryption", "performance tuning", "migration", "replication monitoring"],
        "responsibilities": [
            "Provisioned and operated managed relational, NoSQL, cache, search, and analytical database platforms.",
            "Implemented backups, restore tests, read replicas, encryption, access controls, monitoring, and patching routines.",
            "Tuned slow queries, connection pools, indexes, storage, replication, and migration cutover plans.",
            "Created database runbooks, DR tests, capacity dashboards, and production support procedures.",
        ],
        "tools": ["PostgreSQL", "SQL Server", "MySQL", "AWS RDS", "Aurora", "DynamoDB", "Redis", "OpenSearch", "Azure SQL", "Cloud SQL", "Terraform", "Datadog"],
    },
}

FULL_CONTENT_KEYS = {
    ("Healthcare / Health Insurance", "DevOps Engineer"),
    ("Healthcare / Health Insurance", "Site Reliability / AIOps Engineer"),
    ("Healthcare / Health Insurance", "Data Platform Engineer"),
    ("Banking / Financial Services", "DevOps Engineer"),
    ("Retail / E-Commerce", "Cloud Platform Engineer"),
}


def ensure_training_programs(db: Session) -> None:
    roles = db.scalars(select(MarketingRole).where(MarketingRole.active.is_(True)).order_by(MarketingRole.id)).all()
    supported_roles = [role for role in roles if role.name in MARKETING_ROLE_NAMES]
    existing = {(program.marketing_role_id, program.industry_domain or "Healthcare / Health Insurance"): program for program in db.scalars(select(TrainingProgram)).all()}
    active_market_jobs = _load_active_maas_jobs(db)
    changed = False
    retired_role_ids = set(
        db.scalars(select(MarketingRole.id).where(MarketingRole.name.in_(RETIRED_MARKETING_ROLE_NAMES))).all()
    )
    if retired_role_ids:
        for program in db.scalars(select(TrainingProgram).where(TrainingProgram.marketing_role_id.in_(retired_role_ids))).all():
            if program.active:
                program.active = False
                changed = True
    role_order = {name: index for index, name in enumerate(MARKETING_ROLE_NAMES, start=1)}
    for role in supported_roles:
        for domain_index, domain in enumerate(INDUSTRY_DOMAINS, start=1):
            program = existing.get((role.id, domain))
            if not program:
                program = TrainingProgram(marketing_role_id=role.id, industry_domain=domain)
                db.add(program)
                changed = True
            record = build_training_program_record(role, domain, display_order=(role_order[role.name] * 10) + domain_index, active_market_jobs=active_market_jobs)
            if _apply_program_record(program, record):
                changed = True
            jds = _job_descriptions(role, domain, active_market_jobs=active_market_jobs)
            if jds:
                program.job_descriptions[:] = []
                for jd in jds:
                    program.job_descriptions.append(TrainingJobDescription(**jd))
                changed = True
            elif program.job_descriptions:
                program.job_descriptions[:] = []
                changed = True
    if changed:
        db.commit()


def training_program_seed_records() -> list[dict[str, Any]]:
    records = []
    for role_index, role_name in enumerate(MARKETING_ROLE_NAMES, start=1):
        role = _seed_role(role_name)
        for domain_index, domain in enumerate(INDUSTRY_DOMAINS, start=1):
            records.append(build_training_program_record(role, domain, display_order=(role_index * 10) + domain_index))
    return records


def filter_training_seed_records(programs: list[dict[str, Any]], selected_role: str, selected_domain: str, search_text: str) -> list[dict[str, Any]]:
    search = search_text.strip().lower()
    filtered = []
    for program in programs:
        role_match = selected_role in {"", ALL_MARKETING_ROLES_LABEL} or program["marketingRole"] == selected_role
        domain_match = selected_domain in {"", ALL_DOMAINS_LABEL} or program["industryDomain"] == selected_domain
        haystack_items = [
            program["title"],
            program["shortDescription"],
            program["enterpriseContext"],
            *program["toolsAndTechnologies"],
            *program["keyDeliverables"],
        ]
        search_match = not search or any(search in item.lower() for item in haystack_items)
        if role_match and domain_match and search_match and program["isActive"]:
            filtered.append(program)
    return filtered


def build_training_program_record(role: Any, domain: str, display_order: int, active_market_jobs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    profile = DOMAIN_PROFILES[domain]
    ownership = ROLE_OWNERSHIP[role.name]
    is_full = (domain, role.name) in FULL_CONTENT_KEYS
    applications = profile.applications
    focus = ownership["focus"]
    tools = _unique([*ownership["tools"], "AWS", "Azure", "GCP"])
    title = f"{domain} - {role.name} Training Program"
    short = f"{role.name} enterprise delivery narrative for {profile.short_key} systems, with delivered use cases, product boundaries, workflows, and evidence."
    architecture_summary = (
        f"Traffic and integration flows for {domain.lower()} applications pass through DNS, CDN/WAF, API gateway, load balancers, Kubernetes or managed compute, private services, managed databases, queues, object storage, observability, and data platforms. "
        f"The {role.name} owns the {', '.join(focus[:5]).lower()} layer while aligning with security, audit, reliability, environment standards, and the product team boundaries described in the use cases."
    )
    responsibilities = _full_responsibilities(role.name, domain, [item.replace("domain", profile.short_key) for item in ownership["responsibilities"]])
    deliverables = _deliverables(role.name, domain, focus)
    timeline = _timeline(role.name, domain, applications, focus, is_full)
    story = _interview_story(role.name, domain, applications, focus, is_full)
    resume = _resume_summary(role.name, domain, applications, focus)
    questions = _interview_questions(role.name, domain, focus)
    market_jd_pack = _market_job_description_pack(role, domain, applications, focus, tools, active_market_jobs=active_market_jobs)
    return {
        "id": f"{_slug(domain)}-{_slug(role.name)}",
        "marketingRole": role.name,
        "industryDomain": domain,
        "title": title,
        "shortDescription": short,
        "enterpriseContext": profile.context,
        "linesOfBusiness": profile.lines_of_business,
        "applicationLandscape": applications,
        "cloudArchitecture": {
            "cloudProviderOptions": ["AWS", "Azure", "GCP"],
            "linesOfBusiness": profile.lines_of_business,
            "architectureSummary": architecture_summary,
            "coreComponents": _unique(["DNS", "CDN", "WAF", "API Gateway", "Load Balancer", "Kubernetes", "Managed Databases", "Object Storage", "Event Queue", "Secrets Manager", "Prometheus", "Grafana", "OpenTelemetry", *tools[:8]]),
            "architectureLayers": _architecture_layers(role.name, domain, applications, focus, tools),
            "architectureFlows": _architecture_flows(role.name, domain, applications, focus),
            "architectureMindmap": _architecture_mindmap(role.name, domain, applications, focus, tools),
            "roleArchitectureOwnership": _role_architecture_ownership(role.name, domain, applications, focus),
            "componentResponsibilities": _component_responsibilities(role.name, domain, applications, focus, tools),
            "architectureInterviewExplanation": _architecture_interview_explanation(role.name, domain, applications, focus, tools),
            "consultantProjectContext": _consultant_project_context(role.name, domain, applications, focus),
            "consultantProjectContextBrief": _consultant_project_context_brief(role.name, domain, applications, focus),
            "enterpriseOperatingModel": _enterprise_operating_model(role.name, domain, applications),
            "roleProductExplanation": _role_product_explanation(role.name, domain, applications, focus),
            "productGlossary": _product_glossary(role.name, domain, profile, focus),
            "useCaseBoundaries": _use_case_boundaries(role.name, domain, profile, applications, focus, is_full),
            "deliveredUseCases": _delivered_use_cases(role.name, domain, profile, applications, focus, is_full),
            "datadogInlineDiagrams": _datadog_inline_diagrams(role.name, domain, applications),
            "sprintDeliveryModel": _sprint_delivery_model(role.name, domain, applications, focus),
            "workflowDiagrams": _workflow_diagrams(role.name, domain, applications, focus),
            "projectDeliveryPlan": _project_delivery_plan(role.name, domain, applications, focus, is_full),
            "interviewTalkTracks": _interview_talk_tracks(role.name, domain, applications, focus),
            "maasInterviewBenchmark": _maas_interview_benchmark(role.name, domain, applications, focus),
            "marketJobDescriptionPack": market_jd_pack,
            "consultantUseCaseReadinessTarget": {
                "count": "10-12",
                "standard": "Each use case includes the business problem, reference architecture, workflow diagram, role ownership, evidence, failure mode, troubleshooting path, and interview story.",
            },
        },
        "projectResponsibilities": responsibilities,
        "threeYearDeliveryTimeline": timeline,
        "keyDeliverables": deliverables,
        "toolsAndTechnologies": tools,
        "interviewStory": story,
        "resumeProjectSummary": resume,
        "productionSupportScenarios": _scenarios(role.name, profile.incidents),
        "interviewQuestions": questions,
        "displayOrder": display_order,
        "isActive": True,
    }


def _apply_program_record(program: TrainingProgram, record: dict[str, Any]) -> bool:
    changed = False
    assignments = {
        "industry_domain": record["industryDomain"],
        "title": record["title"],
        "short_description": record["shortDescription"],
        "enterprise_context": record["enterpriseContext"],
        "application_landscape_json": json.dumps(record["applicationLandscape"]),
        "cloud_architecture_json": json.dumps(record["cloudArchitecture"]),
        "project_responsibilities_json": json.dumps(record["projectResponsibilities"]),
        "three_year_delivery_timeline_json": json.dumps(record["threeYearDeliveryTimeline"]),
        "key_deliverables_json": json.dumps(record["keyDeliverables"]),
        "tools_and_technologies_json": json.dumps(record["toolsAndTechnologies"]),
        "interview_story": record["interviewStory"],
        "resume_project_summary": record["resumeProjectSummary"],
        "production_support_scenarios_json": json.dumps(record["productionSupportScenarios"]),
        "interview_questions_json": json.dumps(record["interviewQuestions"]),
        "display_order": record["displayOrder"],
        "active": record["isActive"],
    }
    for field, value in assignments.items():
        if getattr(program, field) != value:
            setattr(program, field, value)
            changed = True
    if not program.duration_weeks:
        program.duration_weeks = 6
        changed = True
    if not program.target_audience:
        _fill_legacy_program_fields(program, record)
        changed = True
    return changed


def _fill_legacy_program_fields(program: TrainingProgram, record: dict[str, Any]) -> None:
    architecture = record["cloudArchitecture"]
    program.duration_weeks = 6
    program.target_audience = f"Consultants preparing for {record['marketingRole']} submissions in {record['industryDomain']} enterprise environments."
    program.outcome = f"{record['industryDomain']} application landscape, architecture, ownership, single-project use cases, support stories, and resume positioning for {record['marketingRole']} roles."
    program.vocabulary_plan = "\n".join(f"{item['term']}: {item['productMeaning']} Answer point: {item['consultantTalkTrack']}" for item in architecture.get("productGlossary", []))
    program.concepts_plan = record["cloudArchitecture"]["architectureSummary"]
    program.usecases_plan = "\n".join(f"{item['name']}: {item['businessGoal']} In scope: {item['inScope']} Out of scope: {item['outOfScope']}" for item in architecture.get("useCaseBoundaries", []))
    program.interview_plan = "\n".join(record["interviewQuestions"])
    program.resume_plan = record["resumeProjectSummary"]
    program.labs_plan = "\n".join(f"{item['phase']}: {item['focus']}" for item in architecture.get("projectDeliveryPlan", {}).get("phases", []))
    program.readiness_checklist = "\n".join(record["productionSupportScenarios"])
    program.missing_areas = "Add client-specific screenshots, diagrams, evidence links, and mock interview scores."


def _maas_role_family(role_name: str) -> str:
    mapping = {
        "DevOps Engineer": "devops",
        "Cloud Automation Engineer": "devops",
        "Cloud Infrastructure Engineer": "sre",
        "Cloud Platform Engineer": "devops",
        "Platform Engineer": "devops",
        "GitOps Engineer": "gitops",
        "Site Reliability Engineer": "sre",
        "Site Reliability / AIOps Engineer": "aiops",
        "Data Platform Engineer": "dataops",
        "MLOps Engineer": "mlops",
        "MLOps / AI Platform Engineer": "mlops",
        "AIOps Engineer": "aiops",
        "Cloud Database Engineer": "sre",
    }
    return mapping.get(role_name, "devops")


def _maas_interview_benchmark(role_name: str, domain: str, applications: list[str], focus: list[str]) -> dict[str, Any]:
    family_key = _maas_role_family(role_name)
    playbook = MAAS_INTERVIEW_PLAYBOOKS[family_key]
    primary_app = applications[0]
    return {
        "source": "MAAS past-interview patterns and curated question-bank playbook",
        "roleFamily": playbook["name"],
        "opening": playbook["opening"],
        "roundFlow": playbook["flow"],
        "coreQuestions": playbook["coreQuestions"],
        "followUpProbes": playbook["followUpProbes"],
        "pressureChecks": playbook["pressureChecks"],
        "evaluationFocus": playbook["evaluationFocus"],
        "rejectionSignals": playbook["rejectionSignals"],
        "marketSignals": _market_interview_signals(role_name),
        "domainApplicationPrompt": f"Answers must anchor to {primary_app}, {domain} product impact, {', '.join(focus[:4]).lower()}, evidence produced, failure handled, and measurable operational outcome.",
        "questionBank": _maas_question_bank(playbook, role_name, domain, applications, focus),
        "readinessMatrix": _interview_readiness_matrix(role_name, domain, applications, focus, playbook),
    }


def _maas_question_bank(playbook: dict[str, Any], role_name: str, domain: str, applications: list[str], focus: list[str]) -> list[dict[str, Any]]:
    groups = [
        ("Core", playbook["coreQuestions"]),
        ("Follow-up", playbook["followUpProbes"]),
        ("Pressure", playbook["pressureChecks"]),
    ]
    rows: list[dict[str, Any]] = []
    for category, questions in groups:
        for question in questions:
            rows.append(
                {
                    "category": category,
                    "question": question,
                    "answerResponse": _maas_answer_response(question, category, role_name, domain, applications, focus),
                    "answerBullets": _maas_answer_bullets(question, category, role_name, domain, applications, focus),
                    "evidenceToMention": _maas_evidence_to_mention(category, role_name, applications, focus),
                }
            )
    return rows


def _maas_answer_response(question: str, category: str, role_name: str, domain: str, applications: list[str], focus: list[str]) -> str:
    primary_app = applications[0]
    secondary_app = applications[1]
    focus_text = ", ".join(focus[:4]).lower()
    lower = question.lower()
    if "noisy alerts" in lower or "alert volume" in lower:
        return (
            f"I start by separating noise from real user impact. For a {domain} workflow like {primary_app}, I first check whether users, transactions, or SLOs are affected, then group repeated alerts by service, dependency, topology, recent deployment, and time window. "
            "I do not suppress alerts just because they are frequent; I validate false positives, resolver ownership, runbook path, and escalation rules first. The result I want to show is cleaner signal quality: fewer duplicate alerts, the important user-impacting alerts still visible, and a support path that tells the right team what to do next."
        )
    if "correlate events" in lower or "logs, metrics, and traces" in lower:
        return (
            f"I use each signal for a different purpose around {primary_app}: metrics tell me the symptom, logs give the error detail, traces show the request path, tickets show history, and deployment markers tell me what changed. "
            f"After correlation, I name the affected service, suspected dependency, time window, owner, and next action, especially when {secondary_app} or a connected system is involved. I close the loop with recovery proof: the alert clears, error rate drops, user flow passes, and the RCA captures the actual cause."
        )
    if "remediation" in lower or "automate" in lower:
        return (
            "I only automate recovery when the failure is low-risk, repeatable, and has clear guardrails, such as a known service restart, failed job retry, cache clear, or ticket enrichment. "
            f"For production-impacting change, data correction, security exception, rollback, or customer-visible risk, I keep human approval in the path. My {role_name} responsibility is the automation evidence and handoff; resolver teams still own code, data, database, security, or infrastructure fixes outside {focus_text}."
        )
    if "trusting" in lower or "human" in lower or "override" in lower:
        return (
            f"I build trust by making the recommendation explainable for {primary_app} support teams. The output needs to show the reason, signal, confidence, known limitation, runbook link, and suggested owner instead of asking operators to trust a black box. "
            "I also keep feedback buttons, false-positive review, manual override, and post-incident tuning in the process. The business value is fewer duplicate alerts, faster triage, and safer escalation."
        )
    if "pipeline" in lower or "multi-environment" in lower or "ci/cd" in lower:
        return (
            f"I explain the pipeline as a controlled path from source to production for {primary_app}: source trigger, build, tests, artifact or image creation, security scan, environment promotion, approval, deployment, smoke check, and rollback. "
            "In that setup, Dev, QA, stage, and production use consistent workflow standards with environment-specific values and auditable approvals. The evidence I mention is the pipeline run, artifact version, change ticket, validation output, release marker, and rollback note."
        )
    if "slo" in lower or "alert" in lower or "incident" in lower or "postmortem" in lower:
        return (
            f"I define the user-facing signal first for {primary_app}: availability, latency, error rate, freshness, or transaction success. During an incident, I separate impact, symptom, suspected layer, mitigation, owner, communication, and recovery validation. "
            "The postmortem is useful only when it names root cause, contributing factors, prevention item, owner, due date, and the runbook update that prevents the same issue from repeating."
        )
    if "model" in lower or "inference" in lower or "drift" in lower or "feature" in lower:
        return (
            f"I treat ML as a production system, not just a notebook. The flow includes feature pipeline, training run, model registry, deployment, endpoint health, drift signal, and rollback version. "
            f"For {domain}, I connect model behavior to product risk: wrong prediction, high latency, stale feature, biased output, or degraded customer/support decision. The evidence is model version, feature checks, inference metrics, drift dashboard, approval record, and recovery action."
        )
    if "data" in lower or "schema" in lower or "backfill" in lower or "dashboard" in lower:
        return (
            f"I explain a data issue by walking the flow from source to consumer for the {domain} platform. I start with the source system, then ingestion, transformation, validation, warehouse or lake storage, dashboard, and the downstream owner who depends on that data. "
            "To protect consumers, I mention schema checks, row-count checks, freshness checks, lineage, replay or backfill plan, and communication before users trust the report again. My recovery evidence is the failed task, a bad-record sample with sensitive data removed, the corrected run, reconciled counts, and the stakeholder update confirming the data is safe to use."
        )
    if "gitops" in lower or "cluster and git" in lower or "declarative" in lower or "sync" in lower:
        return (
            "I describe GitOps as Git being the approved source of truth, while the cluster shows whether the desired state actually reconciled. When Git and cluster behavior disagree, I compare sync status, health status, diff, events, and application smoke checks. "
            "Rollback should come through a Git revert or an approved previous value, followed by reconciliation and product-health validation. I also call out secrets, policy, promotion controls, and audit trail before production sync."
        )
    return (
        f"I frame the answer around {primary_app}: the business impact, affected system, owned {role_name} layer, and evidence reviewed. "
        f"I use {focus_text} to explain implementation or troubleshooting without claiming ownership outside my role boundary. I close with validation, support handoff, measurable outcome, and what changed in the operating model."
    )


def _maas_answer_bullets(question: str, category: str, role_name: str, domain: str, applications: list[str], focus: list[str]) -> list[str]:
    response = _maas_answer_response(question, category, role_name, domain, applications, focus)
    return [part.strip() for part in response.split(". ") if part.strip()]


def _maas_evidence_to_mention(category: str, role_name: str, applications: list[str], focus: list[str]) -> list[str]:
    base = [
        "Jira story or incident ticket",
        "Architecture or workflow diagram",
        "Validation screenshot or command output",
        "Runbook/RCA/update note",
    ]
    if category == "Pressure":
        return [*base, "Timeline of first 10 minutes", "Escalation and rollback decision"]
    if role_name in {"AIOps Engineer", "Site Reliability Engineer"}:
        return [*base, "Alert group", "Dashboard/log/trace evidence", "Recovery validation"]
    if role_name in {"Data Platform Engineer", "MLOps Engineer", "MLOps / AI Platform Engineer"}:
        return [*base, "Pipeline or model run", "Quality/drift/freshness signal", "Replay/rollback evidence"]
    return [*base, "Deployment or configuration record", "Approval and rollback evidence"]


def _interview_readiness_matrix(role_name: str, domain: str, applications: list[str], focus: list[str], playbook: dict[str, Any]) -> list[dict[str, Any]]:
    primary_app = applications[0]
    return [
        {
            "area": "Role ownership",
            "mustKnow": f"{role_name} boundary across {', '.join(focus[:5]).lower()} and how it differs from product, developer, QA, security, support, database, and platform ownership.",
            "programEvidence": ["Responsibilities tab", "Use case boundaries", "Glossary boundary column"],
            "mockCheck": "Explain exact ownership without saying the whole team did it.",
        },
        {
            "area": "Domain product flow",
            "mustKnow": f"{domain} systems, especially {primary_app}, upstream/downstream dependencies, customer/support impact, and compliance or operational signals.",
            "programEvidence": ["Overview", "Architecture mindmap", "Application landscape", "Product glossary"],
            "mockCheck": "Trace one user/business flow from channel to runtime to data/support evidence.",
        },
        {
            "area": "Hands-on delivery",
            "mustKnow": "Jira story grouping, implementation artifact, validation result, deployment/support note, and measurable outcome.",
            "programEvidence": ["Use cases", "Project plan", "Project workstreams", "Key deliverables"],
            "mockCheck": "Turn 5 related stories into one delivered use-case story with evidence.",
        },
        {
            "area": "Production troubleshooting",
            "mustKnow": "Impact check, recent change, logs, metrics, traces, runtime status, dependency health, owner routing, recovery validation, and RCA note.",
            "programEvidence": ["Production support scenarios", "Troubleshooting questions", "Workflow diagrams"],
            "mockCheck": "Handle one pressure check from the MAAS question bank in under three minutes.",
        },
        {
            "area": "Interview pressure",
            "mustKnow": f"{playbook['name']} round flow, follow-up probes, pressure checks, evaluation focus, and rejection signals.",
            "programEvidence": ["MAAS Interview Benchmark", "MAAS Question Bank", "Interview answers"],
            "mockCheck": "Answer core, follow-up, and pressure questions with product impact and evidence.",
        },
    ]


def _market_interview_signals(role_name: str) -> list[str]:
    if role_name in {"MLOps Engineer", "MLOps / AI Platform Engineer"}:
        return [
            "Expect model lifecycle questions: training pipeline, registry, deployment, inference, monitoring, drift, rollback, and retraining.",
            "Expect design questions around canary, blue/green, shadow deployment, feature consistency, reproducibility, and production model degradation.",
            "Answers need both ML vocabulary and platform vocabulary: MLflow or registry, feature store, Docker, Kubernetes, CI/CD, observability, and governance.",
        ]
    if role_name == "Site Reliability Engineer":
        return [
            "Expect incident and SLO questions: first signal, user impact, error budget, alert choice, escalation, rollback, mitigation, postmortem, and toil reduction.",
            "Expect troubleshooting simulations where the root cause is unknown and communication discipline matters as much as the technical fix.",
            "Answers need Linux, networking, Kubernetes, observability, distributed systems basics, and production judgement.",
        ]
    if role_name in {"AIOps Engineer", "Site Reliability / AIOps Engineer"}:
        return [
            "Expect alert correlation, anomaly detection, noise reduction, false positive, ticket enrichment, topology, and human override questions.",
            "Answers must show how telemetry becomes incident action: alert group, anomaly window, suspected owner, runbook link, and recovery validation.",
            "Tool names are not enough; the panel looks for operational trust, review loop, and measurable MTTR or alert-noise improvement.",
        ]
    if role_name == "Data Platform Engineer":
        return [
            "Expect orchestration, data quality, schema change, late-arriving data, lineage, backfill, freshness SLA, and consumer-impact questions.",
            "Answers need source-to-target flow, validation gates, failure recovery, replay/backfill safety, and stakeholder communication.",
            "I separate transient job failure from a real data defect and explain how consumers were protected.",
            "For manufacturing quality roles, expect MES, defect prediction, yield/defect dashboards, equipment telemetry, statistical trend analysis, and live visualization questions.",
            "For telecom or broadband roles, expect MDM, golden record, customer/service-address master data, legacy migration, data governance, conceptual/logical modeling, and BI stakeholder questions.",
            "For SaaS data-platform roles, expect Python, SQL, PySpark, AWS, data observability, backend pipeline services, ML-platform datasets, customer-facing reliability, and ownership from requirement to deployment.",
        ]
    if role_name in {"GitOps Engineer", "DevOps Engineer"}:
        return [
            "Expect desired-state, reconciliation, drift detection, environment promotion, secrets, sync policy, rollback, and audit-trail questions.",
            "Answers need Git source-of-truth reasoning plus cluster health, application health, policy gate, and production recovery details.",
            "I explain what happens when Git, cluster state, and application behavior disagree.",
        ]
    if role_name == "Cloud Database Engineer":
        return [
            "Expect backup/restore, HA, failover, read replica, slow query, connection pool, migration, encryption, access, and incident questions.",
            "Answers need operational safety: maintenance window, validation, rollback, monitoring, capacity, and application-team handoff.",
            "I connect database symptoms to product impact and separate query, connection, storage, replica, and network causes.",
        ]
    return [
        "Expect CI/CD, cloud, Kubernetes, Terraform, monitoring, incident response, rollback, security, and collaboration questions.",
        "Expect scenario-based questions around failed deployments, infrastructure drift, noisy alerts, access issues, and production support.",
        "I connect tool output to product impact, ownership boundary, validation evidence, and measurable delivery improvement.",
    ]


def _job_descriptions(role: MarketingRole, domain: str, active_market_jobs: list[dict[str, Any]] | None = None) -> list[dict[str, str | int]]:
    ownership = ROLE_OWNERSHIP[role.name]
    required = ", ".join(ownership["tools"][:6]) or role.name
    nice = ", ".join(ownership["tools"][6:10]) or role.aliases
    applications = DOMAIN_PROFILES[domain].applications
    market_pack = _market_job_description_pack(role, domain, applications, ownership["focus"], _unique([*ownership["tools"], "AWS", "Azure", "GCP"]), active_market_jobs=active_market_jobs)
    market_items = market_pack.get("items", []) if isinstance(market_pack, dict) else []
    if not market_items:
        return []
    rows = []
    for index, market_row in enumerate(market_items[:7], start=1):
        rows.append(
            {
                "sequence": index,
                "pattern_type": market_row["pattern"],
                "title": market_row["title"],
                "summary": market_row["summary"],
                "responsibilities": "\n".join(market_row["responsibilities"]),
                "required_skills": f"{required}. {'; '.join(market_row['mandatorySkills'])}.",
                "nice_to_have": f"{nice}. {'; '.join(market_row['preferredSkills'])}. Helpful aliases and related titles: {role.aliases}",
                "domain": domain,
                "difficulty": market_row["difficulty"],
                "work_auth_signal": market_row["workAuthSignal"],
            }
        )
    return rows


def _load_active_maas_jobs(db: Session) -> list[dict[str, Any]]:
    rows = _load_maas_job_link_export()
    seen_keys = {(str(row.get("maasJobId") or row.get("url") or row.get("title", ""))).strip() for row in rows}
    for row in _load_active_maas_postgres_jobs():
        key = (str(row.get("maasJobId") or row.get("url") or row.get("title", ""))).strip()
        if key and key in seen_keys:
            continue
        rows.append(row)
        if key:
            seen_keys.add(key)
    jobs = db.scalars(
        select(JobOpportunity)
        .join(Company)
        .where(JobOpportunity.active.is_(True))
        .order_by(JobOpportunity.updated_at.desc(), JobOpportunity.id.desc())
        .limit(500)
    ).all()
    for job in jobs:
        company = job.company
        rows.append(
            {
                "id": job.id,
                "title": job.title or "",
                "company": company.name if company else "",
                "companyIndustry": company.industry if company else "",
                "location": job.location or "",
                "description": _plain_job_text(job.description or ""),
                "marketingRoleIds": _csv_int_set(job.marketing_role_ids or ""),
                "url": job.url or "",
                "source": job.source_type or str(job.source or ""),
                "postedOn": job.posted_on.isoformat() if job.posted_on else "",
                "updatedAt": job.updated_at.isoformat() if job.updated_at else "",
                "jobType": job.job_type or "",
                "experienceLevel": job.experience_level or "",
                "workAuth": _plain_job_text(job.sponsorship_notes or ""),
            }
        )
    return rows


def _load_maas_job_link_export() -> list[dict[str, Any]]:
    if not settings.maas_job_links_csv_path:
        return []
    path = Path(settings.maas_job_links_csv_path)
    if not path.exists():
        return []
    description_cache = _load_maas_job_description_cache()
    rows: list[dict[str, Any]] = []
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            for item in csv.DictReader(handle):
                if str(item.get("is_active", "")).strip().lower() != "true":
                    continue
                if str(item.get("approval_status", "")).strip().lower() == "rejected":
                    continue
                cache_key = item.get("maas_job_id") or item.get("job_id") or item.get("url") or ""
                cached = description_cache.get(cache_key) or description_cache.get(item.get("url", "")) or {}
                rows.append(
                    {
                        "id": item.get("job_id", ""),
                        "maasJobId": item.get("maas_job_id", ""),
                        "title": item.get("title", ""),
                        "company": item.get("company", ""),
                        "companyIndustry": "",
                        "location": "",
                        "description": cached.get("description", ""),
                        "jdFetchedFromUrl": bool(cached.get("description")),
                        "jdFetchStatus": cached.get("status", "not_fetched"),
                        "marketingRoleIds": set(),
                        "marketingRoleName": item.get("marketing_role", ""),
                        "url": item.get("url", ""),
                        "source": f"MAAS job-link export: {path}",
                        "postedOn": item.get("posted_or_created_at", ""),
                        "updatedAt": item.get("updated_at", ""),
                        "jobType": "",
                        "experienceLevel": "",
                        "approvalStatus": item.get("approval_status", ""),
                        "aiStatus": item.get("ai_status", ""),
                        "dateSource": item.get("date_source", ""),
                        "workAuth": "",
                    }
                )
    except OSError:
        return []
    return rows


def _load_maas_job_description_cache() -> dict[str, dict[str, Any]]:
    if not settings.maas_job_description_cache_path:
        return {}
    path = Path(settings.maas_job_description_cache_path)
    if not path.exists():
        return {}
    cache: dict[str, dict[str, Any]] = {}
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for key in (item.get("maas_job_id"), item.get("job_id"), item.get("url")):
                    if key:
                        cache[str(key)] = item
    except OSError:
        return {}
    return cache


def _load_active_maas_postgres_jobs() -> list[dict[str, Any]]:
    if not settings.maas_database_url:
        return []
    rows: list[dict[str, Any]] = []
    try:
        with psycopg.connect(settings.maas_database_url, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    select
                        j.id,
                        j.title,
                        coalesce(j.company_name, ''),
                        coalesce(j.location, ''),
                        coalesce(j.description, ''),
                        coalesce(j.url, ''),
                        coalesce(j.source_type, j.source, ''),
                        coalesce(j.experience_level, ''),
                        coalesce(j.job_type, ''),
                        coalesce(j.approval_status, ''),
                        j.posted_on,
                        j.updated_at,
                        coalesce(r.name, ''),
                        coalesce(j.parsed_skills::text, '[]'),
                        coalesce(j.parsed_tools::text, '[]')
                    from jobs_job j
                    left join consultants_marketingrole r on r.id = j.marketing_role_id
                    where j.is_active = true
                    order by j.updated_at desc nulls last, j.id desc
                    limit 500
                    """
                )
                rows.extend(_maas_pg_rows(cur.fetchall(), source_table="jobs_job"))
                if not rows:
                    cur.execute(
                        """
                        select
                            j.id,
                            j.title,
                            coalesce(j.company_name, ''),
                            coalesce(j.location_work_type, ''),
                            coalesce(j.job_description, ''),
                            coalesce(j.external_job_url, ''),
                            coalesce(j.source_label, ''),
                            coalesce(j.seniority, ''),
                            '',
                            coalesce(j.status, ''),
                            null,
                            j.updated_at,
                            coalesce(j.matched_role_names::text, '[]'),
                            coalesce(j.intake_tags::text, '[]'),
                            '[]'
                        from jobs_atsingestionjob j
                        where lower(coalesce(j.status, '')) in ('active', 'approved', 'ready', 'new', 'pending')
                        order by j.updated_at desc nulls last, j.id desc
                        limit 500
                        """
                    )
                    rows.extend(_maas_pg_rows(cur.fetchall(), source_table="jobs_atsingestionjob"))
    except Exception:
        return []
    return rows


def _maas_pg_rows(pg_rows: list[tuple[Any, ...]], source_table: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in pg_rows:
        role_name = row[12] or ""
        rows.append(
            {
                "id": row[0],
                "title": row[1] or "",
                "company": row[2] or "",
                "companyIndustry": "",
                "location": row[3] or "",
                "description": _plain_job_text(row[4] or ""),
                "marketingRoleIds": set(),
                "marketingRoleName": role_name,
                "url": row[5] or "",
                "source": f"MAAS Postgres {source_table}" + (f" / {row[6]}" if row[6] else ""),
                "postedOn": row[10].isoformat() if row[10] else "",
                "updatedAt": row[11].isoformat() if row[11] else "",
                "jobType": row[8] or "",
                "experienceLevel": row[7] or "",
                "approvalStatus": row[9] or "",
                "parsedSkills": row[13] or "[]",
                "parsedTools": row[14] or "[]",
                "workAuth": "",
            }
        )
    return rows


def _market_job_description_pack(
    role: Any,
    domain: str,
    applications: list[str],
    focus: list[str],
    tools: list[str],
    active_market_jobs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    role_name = role.name
    source_jobs = _matching_maas_jobs(active_market_jobs or [], role, domain)[:7]
    if not source_jobs:
        return {
            "source": "MAAS prod active jobs",
            "sourceUrl": "https://midhtech.in/jobs/?active=active&marketing_role=%5B%27%27%5D&per_page=50",
            "status": "no_matching_active_maas_jobs_loaded",
            "requiredCount": 7,
            "items": [],
            "note": "No active prod MAAS jobs were available to this process for this role/domain. Training content must not label synthetic patterns as real MAAS JD evidence.",
        }

    use_case_titles = [item["theme"] for item in _role_sprint_themes(role_name, domain, applications, focus)[:12]]
    rows: list[dict[str, Any]] = []
    for index, job in enumerate(source_jobs, start=1):
        selected_use_cases = use_case_titles[index - 1 : index + 3] or use_case_titles[:4]
        mandatory = _jd_mandatory_skills(role_name, domain, focus, tools, index)
        matched_tools = _matched_job_terms(job.get("description", ""), tools)
        preferred = _unique([*matched_tools, *_jd_preferred_skills(role_name, domain, index)])[:8]
        product_systems = _job_product_systems(job.get("description", ""), applications, index)
        coverage = _training_coverage_score(job, role_name, focus, tools, selected_use_cases)
        rows.append(
            {
                "sequence": index,
                "pattern": "MAAS Active JD",
                "title": job.get("title") or f"{role_name} active JD",
                "company": job.get("company", ""),
                "location": job.get("location", ""),
                "sourceUrl": job.get("url", ""),
                "postedOn": job.get("postedOn", ""),
                "domainMatchStatus": job.get("domainMatchStatus", ""),
                "domainValidationScore": job.get("domainValidationScore", 0),
                "domainValidationEvidence": job.get("domainValidationEvidence", []),
                "jdFetchedFromUrl": job.get("jdFetchedFromUrl", False),
                "jdFetchStatus": job.get("jdFetchStatus", ""),
                "trainingCoverageScore": coverage["score"],
                "programUpdateRequired": coverage["score"] < 80,
                "programGapActions": coverage["gapActions"],
                "summary": _jd_summary_from_job(job, role_name, domain),
                "difficulty": _difficulty_from_job(job),
                "productSystems": product_systems,
                "mandatorySkills": mandatory,
                "preferredSkills": preferred,
                "responsibilities": _responsibilities_from_job(job, role_name, domain, focus, product_systems, selected_use_cases),
                "useCasesToPractice": selected_use_cases[:4],
                "diagramFocus": [
                    "product-system request or data flow",
                    "cloud/provider reference architecture",
                    "failure and recovery path",
                    "evidence package path",
                ],
                "workAuthSignal": job.get("workAuth") or "Review sponsorship, contract, hybrid, relocation, and client-specific authorization language before submission.",
                "readinessCheck": "Explain the JD pattern through a specific system, role boundary, failure scenario, evidence artifact, and 60-second interview story.",
            }
        )
    return {
        "source": "MAAS prod active jobs",
        "sourceUrl": "https://midhtech.in/jobs/?active=active&marketing_role=%5B%27%27%5D&per_page=50",
        "status": "loaded" if len(rows) >= 7 else "partial",
        "requiredCount": 7,
        "loadedCount": len(rows),
        "items": rows,
    }


def _training_coverage_score(job: dict[str, Any], role_name: str, focus: list[str], tools: list[str], selected_use_cases: list[str]) -> dict[str, Any]:
    text = " ".join(
        str(job.get(key, ""))
        for key in ("title", "company", "description", "marketingRoleName", "parsedSkills", "parsedTools")
    ).lower()
    role_terms = _role_match_terms(type("RoleLike", (), {"name": role_name, "aliases": ""})())
    role_matched = any(term in text for term in role_terms)
    focus_matched = [term for term in focus[:8] if term.lower() in text]
    tool_matched = [term for term in tools[:10] if term.lower() in text]
    use_case_matched = [term for term in selected_use_cases[:4] if term.lower() in text]
    score = 40 if role_matched else 0
    score += min(30, len(focus_matched) * 8)
    score += min(20, len(tool_matched) * 5)
    score += min(10, len(use_case_matched) * 5)
    score = min(100, score)
    required_terms = _unique([*focus[:6], *tools[:8], *selected_use_cases[:4]])
    matched = set([*focus_matched, *tool_matched, *use_case_matched])
    gaps = [term for term in required_terms if term and term not in matched][:6]
    return {
        "score": score,
        "gapActions": [
            f"Add or strengthen training coverage for JD signal: {gap}."
            for gap in gaps
        ]
        or ["Training coverage is sufficient for the visible JD signals."],
    }


def _matching_maas_jobs(active_market_jobs: list[dict[str, Any]], role: Any, domain: str) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    role_terms = _role_match_terms(role)
    for job in active_market_jobs:
        haystack = " ".join(
            str(job.get(key, ""))
            for key in ("title", "description", "company", "companyIndustry", "location", "marketingRoleName")
        ).lower()
        role_id_match = bool(getattr(role, "id", None) and role.id in set(job.get("marketingRoleIds", set())))
        maas_role_name = str(job.get("marketingRoleName", "")).lower()
        role_text_match = any(term in haystack for term in role_terms) or _maas_role_name_matches(role.name, maas_role_name)
        domain_score = _domain_validation_score(job, domain)
        if role_id_match or role_text_match:
            matches.append(
                {
                    **job,
                    "domainMatchStatus": "validated_80_plus" if domain_score["score"] >= 80 else "domain_gap_update_training",
                    "domainValidationScore": domain_score["score"],
                    "domainValidationEvidence": domain_score["evidence"],
                }
            )
    deduped: list[dict[str, Any]] = []
    seen = set()
    for job in matches:
        key = str(job.get("maasJobId") or job.get("url") or job.get("title", "")).strip()
        if key in seen:
            continue
        deduped.append(job)
        seen.add(key)
    return deduped


def _domain_validation_score(job: dict[str, Any], domain: str) -> dict[str, Any]:
    text = " ".join(
        str(job.get(key, ""))
        for key in ("title", "company", "companyIndustry", "location", "description", "url")
    ).lower()
    domain_terms = _domain_match_terms(domain)
    evidence = [term for term in domain_terms if term in text]
    score = 0
    if evidence:
        score += min(60, len(evidence) * 25)
    if str(job.get("description", "")).strip() and evidence:
        score += 30
    if any(term in str(job.get("companyIndustry", "")).lower() for term in domain_terms):
        score += 20
    if any(term in str(job.get("url", "")).lower() for term in domain_terms):
        score += 10
    return {"score": min(100, score), "evidence": evidence}


def _maas_role_name_matches(mintel_role_name: str, maas_role_name: str) -> bool:
    if not maas_role_name:
        return False
    mapping = {
        "DevOps Engineer": ["devops engineer", "continuous integration", "ci/cd", "devsecops"],
        "Cloud Platform Engineer": ["platform engineering", "cloud infrastructure engineer", "cloud automation engineer"],
        "Site Reliability / AIOps Engineer": ["site reliability engineer", "sre", "ai platform engineer", "aiops"],
        "Data Platform Engineer": ["data platform engineer", "database engineer", "database engineering"],
        "MLOps / AI Platform Engineer": ["machine learning engineer", "mlops", "ai platform engineer", "aiops"],
    }
    return any(item in maas_role_name for item in mapping.get(mintel_role_name, []))


def _role_match_terms(role: Any) -> list[str]:
    terms = [getattr(role, "name", "")]
    terms.extend(str(getattr(role, "aliases", "") or "").split(","))
    terms.extend(
        {
            "DevOps Engineer": ["devops", "build release", "release engineer", "ci/cd", "cicd"],
            "Cloud Platform Engineer": ["cloud platform", "platform engineer", "cloud engineer", "infrastructure engineer"],
            "Site Reliability / AIOps Engineer": ["site reliability", "sre", "aiops", "production support", "observability"],
            "Data Platform Engineer": ["data engineer", "data platform", "etl", "elt", "warehouse", "lakehouse"],
            "MLOps / AI Platform Engineer": ["mlops", "machine learning engineer", "ai platform", "model deployment"],
        }.get(getattr(role, "name", ""), [])
    )
    return [term.strip().lower() for term in terms if term.strip()]


def _domain_match_terms(domain: str) -> list[str]:
    mapping = {
        "Healthcare / Health Insurance": ["healthcare", "health insurance", "medical", "clinical", "hospital", "payer"],
        "Banking / Financial Services": ["bank", "banking", "financial", "fintech", "payment", "card", "lending"],
        "Retail / E-Commerce": ["retail", "ecommerce", "e-commerce", "commerce", "order", "customer"],
        "Insurance": ["insurance", "claims", "policy", "underwriting"],
        "Logistics / Transportation": ["logistics", "transportation", "fleet", "shipping", "warehouse", "supply chain"],
        "Manufacturing / Automotive / Industrial": ["manufacturing", "automotive", "industrial", "factory", "plant"],
        "Technology / SaaS / Enterprise Software": ["saas", "software", "platform", "enterprise"],
        "Energy / Utilities / Data Centers": ["energy", "utility", "utilities", "data center", "datacenter"],
        "Telecom / Media / Communications": ["telecom", "media", "communications", "subscriber", "network"],
    }
    return mapping.get(domain, [domain.lower()])


def _csv_int_set(value: str) -> set[int]:
    ids: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if item.isdigit():
            ids.add(int(item))
    return ids


def _plain_job_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", text).strip()


def _matched_job_terms(description: str, tools: list[str]) -> list[str]:
    lower = description.lower()
    return [tool for tool in tools if tool.lower() in lower][:8]


def _job_product_systems(description: str, applications: list[str], index: int) -> list[str]:
    lower = description.lower()
    matched = [item for item in applications if any(part and part.lower() in lower for part in re.split(r"[/&-]", item))]
    fallback = [applications[(index - 1) % len(applications)], applications[(index + 1) % len(applications)], applications[-1]]
    return _unique([*matched, *fallback])[:4]


def _jd_summary_from_job(job: dict[str, Any], role_name: str, domain: str) -> str:
    company = f" at {job['company']}" if job.get("company") else ""
    location = f" in {job['location']}" if job.get("location") else ""
    description = str(job.get("description", ""))
    lead = " ".join(description.split()[:45])
    return f"Real active MAAS JD for {role_name}{company}{location}, aligned to {domain}. {lead}".strip()


def _difficulty_from_job(job: dict[str, Any]) -> str:
    text = f"{job.get('title', '')} {job.get('description', '')} {job.get('experienceLevel', '')}".lower()
    if any(term in text for term in ["senior", "lead", "principal", "architect", "7+", "8+", "10+"]):
        return "Advanced"
    if any(term in text for term in ["junior", "entry", "associate", "0-2", "1-2"]):
        return "Foundation"
    return "Market-ready"


def _responsibilities_from_job(
    job: dict[str, Any],
    role_name: str,
    domain: str,
    focus: list[str],
    product_systems: list[str],
    selected_use_cases: list[str],
) -> list[str]:
    description = str(job.get("description", ""))
    sentence_candidates = [item.strip(" -•\t") for item in re.split(r"[\n.;]", description) if len(item.split()) >= 5]
    jd_lines = sentence_candidates[:2]
    defaults = [
        f"Connect the active JD to {', '.join(product_systems[:3])} and related {domain} systems.",
        f"Prepare explainable use cases: {', '.join(selected_use_cases[:3])}.",
    ]
    return _unique(
        [
            *jd_lines,
            *defaults,
            f"Explain {role_name} ownership around {', '.join(focus[:4]).lower()} without overclaiming product or application-team ownership.",
            "Produce architecture diagrams, workflow diagrams, validation evidence, runbooks, and project stories tied to the JD.",
        ]
    )[:5]


def _jd_mandatory_skills(role_name: str, domain: str, focus: list[str], tools: list[str], index: int) -> list[str]:
    base = _unique([*focus[:5], *tools[:5]])[:7]
    role_specific = {
        "DevOps Engineer": ["Git workflow and pull requests", "CI/CD validation", "rollback and release evidence", "production support runbooks"],
        "Cloud Platform Engineer": ["cloud networking", "IAM/RBAC", "Terraform modules", "private connectivity", "cost and policy controls"],
        "Site Reliability / AIOps Engineer": ["SLO/SLI", "metrics logs traces", "incident response", "RCA", "alert correlation"],
        "Data Platform Engineer": ["SQL", "data modeling", "ETL/ELT", "data quality", "orchestration", "warehouse/lakehouse"],
        "MLOps / AI Platform Engineer": ["ML pipeline", "model registry", "batch or real-time inference", "model monitoring", "drift checks"],
    }.get(role_name, [])
    domain_specific = {
        "Healthcare / Health Insurance": ["regulated data handling", "audit evidence"],
        "Banking / Financial Services": ["financial data controls", "reconciliation"],
        "Insurance": ["policy/claims data controls", "regulated reporting"],
        "Logistics / Transportation": ["event and telemetry data", "partner integration reliability"],
        "Retail / E-Commerce": ["customer/order data flow", "high-volume transaction support"],
        "Manufacturing / Automotive / Industrial": ["industrial telemetry", "ERP/MES integration"],
        "Technology / SaaS / Enterprise Software": ["multi-tenant operations", "usage telemetry"],
        "Energy / Utilities / Data Centers": ["mission-critical telemetry", "regulatory operations"],
        "Telecom / Media / Communications": ["subscriber/service assurance data", "high-volume billing or activation workflows"],
    }.get(domain, [])
    return _unique([*base, *role_specific, *domain_specific])[:10]


def _jd_preferred_skills(role_name: str, domain: str, index: int) -> list[str]:
    provider = ["AWS reference architecture", "Azure reference architecture", "Google Cloud reference architecture"][index % 3]
    role_specific = {
        "DevOps Engineer": ["Kubernetes", "Helm", "image scanning", provider],
        "Cloud Platform Engineer": ["landing zones", "hybrid connectivity", "policy as code", provider],
        "Site Reliability / AIOps Engineer": ["Datadog/New Relic/Splunk", "OpenTelemetry", "synthetic monitoring", provider],
        "Data Platform Engineer": ["Airflow", "Spark/Flink", "dbt", "Athena/Presto", provider],
        "MLOps / AI Platform Engineer": ["SageMaker/Azure ML/Vertex AI", "MLflow", "feature store", provider],
    }.get(role_name, [provider])
    return role_specific[:6]


def _full_responsibilities(role_name: str, domain: str, base: list[str]) -> list[str]:
    if role_name == "DevOps Engineer" and domain == "Healthcare / Health Insurance":
        return [
            "Built reusable GitHub Actions workflows for Java, .NET, Python, and frontend healthcare applications.",
            "Containerized member, claims, provider, patient portal, and care delivery services using Docker.",
            "Created Helm charts and Kubernetes manifests for dev, QA, stage, and production deployments.",
            "Supported Terraform-based provisioning for cloud infrastructure, Kubernetes namespaces, storage, secrets, and monitoring resources.",
            "Implemented approval-based production deployment workflows with rollback and smoke testing.",
            "Integrated image scanning, dependency scanning, and code quality checks into CI/CD pipelines.",
            "Supported production releases for claims, eligibility, patient portal, provider portal, and appointment scheduling applications.",
            "Created deployment dashboards, runbooks, and post-deployment validation steps.",
        ]
    applications = DOMAIN_PROFILES[domain].applications
    focus = ROLE_OWNERSHIP[role_name]["focus"]
    return [
        *base,
        f"Mapped {domain} product flows across {applications[0]}, {applications[1]}, {applications[2]}, and connected support systems.",
        f"Standardized {focus[0]} and {focus[1]} evidence so incidents and releases could be reviewed by application, platform, QA, security, and operations teams.",
        f"Built delivery artifacts for {applications[0]} including current-state workflow notes, smoke-test output, rollback decision criteria, resolver-group routing, and runbook updates.",
        f"Supported production issues across {applications[1]} and {applications[-1]} with logs, metrics, alerts, tickets, RCA notes, and runbook updates.",
        f"Improved cross-team operating model for {domain} systems by documenting boundaries between product owners, developers, infrastructure, data, database, security, and service desk teams.",
        f"Prepared project evidence for interviews using realistic {domain} scenarios, implementation artifacts, incident examples, architecture notes, and measurable support outcomes.",
    ]


def _architecture_layers(role_name: str, domain: str, applications: list[str], focus: list[str], tools: list[str]) -> list[dict[str, Any]]:
    return [
        {"layer": "Users and channels", "purpose": f"Entry points for {domain} users and support teams.", "components": [applications[0], applications[1], applications[2]], "roleView": f"{role_name} starts by identifying which user or business workflow is affected."},
        {"layer": "Edge and access", "purpose": "Routes and protects traffic before it reaches application services.", "components": ["DNS", "CDN", "WAF", "API Gateway", "Load Balancer", "TLS certificates"], "roleView": f"{role_name} checks routing, certificates, gateway status, and access signals when traffic is unhealthy."},
        {"layer": "Application runtime", "purpose": "Runs APIs, microservices, jobs, workers, and integration services.", "components": ["Kubernetes", "Containers", "Helm charts", "Namespaces", "Ingress"], "roleView": f"{role_name} connects {', '.join(focus[:3]).lower()} to runtime health and deployment safety."},
        {"layer": "Data and integration", "purpose": "Stores records, moves events, integrates systems, and supports analytics.", "components": ["Managed databases", "Object storage", "Event queues", "Data warehouse", applications[-1]], "roleView": f"{role_name} knows which dependency signal proves whether {applications[0]} is healthy or degraded."},
        {"layer": "Operations and observability", "purpose": "Shows health, latency, failures, ownership, and recovery evidence.", "components": _unique(["Prometheus", "Grafana", "OpenTelemetry", *tools[:5], "ServiceNow", "Runbooks"]), "roleView": f"{role_name} uses this layer to identify impact, correlate symptoms, reduce noise, and route work to the right owner."},
    ]


def _architecture_flows(role_name: str, domain: str, applications: list[str], focus: list[str]) -> list[dict[str, str]]:
    return [
        {"name": "User request flow", "diagram": f"User -> DNS/CDN/WAF -> API Gateway -> Load Balancer -> Kubernetes service -> {applications[0]} API -> Database/event queue -> Response", "explanation": f"{domain} user traffic reaches backend services through edge, routing, runtime, data, and integration layers. Latency, 5xx errors, routing issues, and dependency failures can be isolated by layer.", "whatToSay": f"{role_name} traces the request path, identifies the signal-producing layer, and routes the fix to the correct owner."},
        {"name": "Release flow", "diagram": "Developer PR -> CI validation -> Artifact/image -> Helm values -> Stage deployment -> Approval -> Production rollout -> Smoke checks -> Evidence", "explanation": "Code changes move to production through validation, artifact creation, environment configuration, approval, rollout, rollback readiness, and post-release checks.", "whatToSay": f"{role_name} owns or supports {', '.join(focus[:4]).lower()}; product feature logic stays with the application team."},
        {"name": "Observability and incident flow", "diagram": "Metrics/logs/traces/events -> Alert rules -> Correlation/anomaly detection -> Incident ticket -> Owner routing -> Recovery validation -> RCA/runbook update", "explanation": "Raw telemetry becomes support evidence through alert rules, correlation, incident routing, owner assignment, recovery validation, and RCA updates.", "whatToSay": f"{role_name} connects alerts to business impact, recent changes, unhealthy dependencies, and the responsible resolver group."},
        {"name": "Data and dependency flow", "diagram": f"{applications[0]} service -> Event queue/API integration -> {applications[1]} -> Data platform/reporting -> Support dashboard", "explanation": f"{domain} failures often cross service, queue, database, partner API, and reporting boundaries. The dependency path separates the visible symptom from the actual owner.", "whatToSay": "Symptom, dependency, owner, evidence, and recovery step are handled separately."},
    ]


def _architecture_mindmap(role_name: str, domain: str, applications: list[str], focus: list[str], tools: list[str]) -> dict[str, Any]:
    return {
        "root": f"{role_name} - {domain} Architecture",
        "branches": [
            {"title": "Product systems", "items": applications},
            {"title": "Edge and access", "items": ["DNS", "CDN", "WAF", "API Gateway", "Load Balancer"]},
            {"title": "Runtime", "items": ["Kubernetes", "Containers", "Helm", "Namespaces", "Ingress"]},
            {"title": "Data and integrations", "items": ["Managed databases", "Event queue", "Object storage", applications[-1]]},
            {"title": "Observability and support", "items": _unique(["Metrics", "Logs", "Traces", "Alerts", *tools[:4], "ServiceNow"])},
            {"title": f"{role_name} ownership", "items": focus[:6]},
        ],
    }


def _role_architecture_ownership(role_name: str, domain: str, applications: list[str], focus: list[str]) -> list[dict[str, str]]:
    return [
        {"area": focus[0], "ownership": f"Own the design/configuration change, lower-environment result, and support note for {focus[0].lower()} across {applications[0]} and connected systems.", "boundary": "Does not own product requirements or application business logic."},
        {"area": focus[1], "ownership": f"Use {focus[1].lower()} to make delivery and operations visible through rerunnable jobs, dashboard signals, ticket context, and named resolver groups.", "boundary": "Coordinate with developers, QA, security, and operations when the signal points outside the owned activity."},
        {"area": focus[2], "ownership": f"Create standards, evidence, dashboards, configuration, or runbooks so teams can operate {applications[0]} consistently.", "boundary": "Does not bypass enterprise approvals, access controls, or change windows."},
        {"area": "Production support", "ownership": "Triage impact, collect evidence, identify owner, validate recovery, and update RCA or runbook documentation.", "boundary": "Application code fixes stay with application owners; infrastructure, database, security, and data issues go to their resolver groups."},
    ]


def _component_responsibilities(role_name: str, domain: str, applications: list[str], focus: list[str], tools: list[str]) -> list[dict[str, str]]:
    rows = [
        ("DNS/CDN/WAF", "Routes and protects external traffic.", "Check routing, certificates, WAF blocks, and edge latency."),
        ("API Gateway/Load Balancer", "Routes requests to backend services.", "Review 4xx/5xx, target health, routing rules, and dependency errors."),
        ("Kubernetes/Runtime", "Runs services, jobs, workers, and platform workloads.", "Review pod health, restarts, deployment version, resource pressure, and rollout status."),
        ("Managed Databases/Event Queues", "Stores records and moves asynchronous events.", "Review connection errors, queue lag, slow queries, replica lag, and failed consumers."),
        ("Observability Stack", "Collects metrics, logs, traces, alerts, and incident records.", f"Read {', '.join(tools[:4])} signals to connect symptom, time window, recent change, suspected dependency, and owner/action."),
        ("ServiceNow/Runbooks", "Turns technical signals into support workflow.", "Attach evidence, assign resolver group, document RCA, and update support procedures."),
    ]
    return [{"component": component, "purpose": purpose, "roleResponsibility": responsibility} for component, purpose, responsibility in rows]


def _architecture_interview_explanation(role_name: str, domain: str, applications: list[str], focus: list[str], tools: list[str]) -> list[str]:
    return [
        f"User action enters {applications[0]}, passes through edge controls, reaches API/runtime services, depends on data or integration systems, and produces observability signals.",
        f"{role_name} ownership covers {', '.join(focus[:4]).lower()}; product owners, developers, QA, security, database, and operations teams own their respective layers.",
        f"{', '.join(tools[:3])} produced alerts, dashboards, logs, status records, deployment/configuration evidence, and runbook updates.",
        "Support flow: detect impact, correlate signals, check recent change, identify unhealthy dependency, route to owner, validate recovery, and update RCA/runbook notes.",
    ]


def _consultant_project_context(role_name: str, domain: str, applications: list[str], focus: list[str]) -> str:
    product_flow = ", ".join(applications[:4])
    adjacent_flow = ", ".join(applications[4:8])
    technical_ownership = ", ".join(focus[:5]).lower()
    return (
        f"Enterprise delivery context for {domain} technology systems with {role_name} responsibilities. "
        f"The reference operating model uses enterprise scale: about 600 IT employees, roughly 200 infrastructure/platform engineers, 300 application developers, and 100 operations, support, and service desk users. "
        f"The application landscape represents about 100 applications supported through 10 major technology teams. The product flow centered on {product_flow}; the technical ownership centered on {technical_ownership}. "
        f"This context represents large enterprise delivery rather than a small isolated project. A shared platform and infrastructure group served many product teams at the same time. The organization had business-facing applications, internal operations applications, integration services, analytics workloads, batch jobs, APIs, databases, file exchanges, event streams, monitoring tools, ticketing workflows, and several lower environments used for development, QA, staging, performance testing, and production readiness. Because the enterprise application portfolio was broad, no single engineer owned everything end to end. The practical value of the {role_name} was to turn the technical layer into named system paths, approved change steps, rerunnable automation, readable health signals, and recovery notes that application teams, QA teams, security reviewers, release managers, and operations teams could use without depending on tribal knowledge. "
        f"From a business point of view, the main application journey started in {product_flow}. These systems represented the visible product capability: users, operators, partners, support teams, or downstream business groups depended on them to complete daily work. Around that core journey, related systems such as {adjacent_flow} provided supporting capabilities for intake, validation, workflow status, documents, reporting, operational handoff, customer or member communication, and exception handling. I did not own the business rules of those applications. Product owners and business analysts owned priorities, process definitions, acceptance rules, and business outcomes. Application developers owned feature code, API behavior, UI changes, and service logic. My role was to make sure the platform, deployment path, runtime foundation, observability, security boundary, data movement, or recovery mechanism allowed those product capabilities to work reliably across environments. "
        f"The delivery model followed enterprise agile execution. Work came through Jira epics, features, stories, defects, operational requests, production support follow-ups, audit findings, architecture decisions, and release-readiness actions. A typical sprint contained small implementation stories, validation work, documentation updates, environment coordination, incident follow-up, or automation improvements. Every five or six related stories became a project use case with a business trigger, affected systems, implementation artifact, lower-environment result, rollback or recovery option, and production support note. For example, one use case might start with a business problem in {applications[0]}, identify dependency risk in {applications[1]}, define which {role_name} activities were in scope, implement a platform or operational change, validate the behavior in lower environment, prepare a rollback or recovery option, and attach the logs, dashboard, ticket, command output, or PR evidence that support teams could review. This is why the training material explains use cases as delivered enterprise scenarios instead of tool-only lessons. "
        f"Architecturally, I explain the assignment in layers. The first layer was the business capability and user journey. The second layer was the application and integration path across {product_flow}. The third layer was the shared technical platform: {technical_ownership}. The fourth layer was governance, including access, approvals, audit records, configuration standards, secrets handling, tagging, documentation, change windows, and support ownership. The fifth layer was operability: logs, metrics, traces, alerts, dashboards, runbooks, incident tickets, escalation routes, recovery steps, and post-change validation. I explain how these layers connected without claiming ownership of every layer. That distinction matters in interviews because senior interviewers listen for role clarity, realistic enterprise boundaries, and evidence of production thinking. "
        f"As a {role_name}, the technical work was usually not a single heroic build. It was a sequence of practical improvements that made the enterprise easier to run. I mapped current-state flows, reviewed environment differences, clarified system owners, helped define target-state architecture, created or updated configuration, automation, platform controls, dashboards, alerts, policies, pipelines, network paths, runtime settings, data movement checks, or runbooks, and then validated the change with concrete evidence. Evidence could include pull requests, pipeline output, Terraform or deployment plans, cloud console screenshots, Kubernetes rollout status, dashboard panels, alert rules, log queries, API smoke tests, access-control records, backup or restore proof, incident timelines, RCA notes, and release handoff summaries. The key was not collecting artifacts for decoration; the key was proving that the business workflow could be supported after the change. "
        f"The operating model also included frequent collaboration. Product owners explained why the capability mattered. Business analysts clarified process flow, inputs, outputs, exception paths, and KPIs. Architects reviewed design choices, tradeoffs, non-functional requirements, and integration patterns. Developers explained application dependencies and code behavior. QA teams verified happy path, failure path, regression risk, and release gates. Security teams reviewed IAM, secrets, encryption, policy exceptions, data handling, audit trails, and compliance controls. Operations and service desk teams needed first checks, symptom definitions, severity language, escalation owners, and recovery guidance. Project managers tracked scope, milestones, dependencies, risks, approvals, and status communication. My strongest contribution was connecting these views into one coherent implementation story. "
        f"This context explains real enterprise delivery. My story is not a lab exercise where one person created every component. It is practical delivery inside a large {domain} environment where I owned a clear technical slice, worked with many teams, understood the business flow, and kept evidence that survived production review. I start with the business capability, name the systems involved, explain the platform or operational problem, state which {role_name} activities I owned, walk through the implementation, describe validation and failure handling, and close with the production result. The most credible version of the story is functional: what business flow improved, what system behavior changed, which log/query/dashboard/pipeline/policy result proved it, what risk was reduced, and how the support team could operate it after handoff."
    )


def _consultant_project_context_brief(role_name: str, domain: str, applications: list[str], focus: list[str]) -> dict[str, Any]:
    product_flow = applications[:4]
    adjacent_flow = applications[4:8]
    technical_ownership = focus[:5]
    return {
        "headline": f"{domain} enterprise delivery context for {role_name}",
        "summary": (
            f"Enterprise scale reference model: about 600 IT employees, 100 applications, and 10 major technology teams across product, platform, application, QA, security, operations, data, support, and service desk functions. "
            f"The role improves the technical layer around {', '.join(technical_ownership).lower()} while product, application, QA, security, operations, and data teams retain their own ownership."
        ),
        "scale": [
            "Enterprise reference scale: about 600 IT employees",
            "About 200 infrastructure, platform, cloud, security, and operations engineers",
            "About 300 application developers owning product services and APIs",
            "About 100 operations, support, and service desk users",
            "About 100 applications across 10 major technology teams",
        ],
        "businessFlow": [
            f"Core product flow: {', '.join(product_flow)}.",
            f"Adjacent systems: {', '.join(adjacent_flow)}.",
            "Business value came from reliable user, operations, partner, support, reporting, and exception-handling workflows.",
        ],
        "roleBoundary": [
            f"{role_name} owned the technical enablement layer: {', '.join(technical_ownership).lower()}.",
            "Product owners and business analysts owned business priority, process rules, acceptance language, and KPIs.",
            "Application teams owned feature code, APIs, UI behavior, and service logic.",
            "QA, security, operations, data, and PM teams owned validation, controls, support, reporting, and delivery governance.",
        ],
        "deliveryModel": [
            "Work arrived through Jira epics, stories, defects, operational requests, audit findings, release-readiness actions, and production support follow-ups.",
            "Every five or six related stories became a delivered use case with current-state flow, target design, implementation artifact, lower-environment result, release note, runbook change, and support ticket trail.",
            "The story is explained as repeated enterprise delivery, not a single lab build.",
        ],
        "architectureView": [
            "Layer 1: business capability and user or operations journey.",
            f"Layer 2: application and integration path across {', '.join(product_flow)}.",
            f"Layer 3: shared technical platform around {', '.join(technical_ownership).lower()}.",
            "Layer 4: governance through access, approvals, audit trail, secrets, documentation, and change windows.",
            "Layer 5: operability through logs, metrics, traces, alerts, dashboards, runbooks, incidents, and recovery evidence.",
        ],
        "evidenceModel": [
            "Pull requests, pipeline output, Terraform or deployment plans, cloud console evidence, Kubernetes rollout status, dashboard panels, alert rules, log queries, smoke tests, access-control records, backup/restore proof, RCA notes, and release handoff summaries.",
            "Evidence proved that the business workflow could be validated, supported, recovered, and explained after delivery.",
        ],
        "interviewFrame": [
            "Start with the business capability and systems involved.",
            f"Explain the platform or operational problem and the {role_name} ownership boundary.",
            "Walk through implementation, validation, failure handling, resolver-group routing, runbook update, and measurable result.",
            "Keep the story functional: what business flow improved, what system behavior changed, what proof existed, and what risk was reduced.",
        ],
    }


def _enterprise_operating_model(role_name: str, domain: str, applications: list[str]) -> dict[str, Any]:
    return {
        "scale": [
            "Enterprise reference scale: about 600 IT employees",
            "About 200 infrastructure, platform, cloud, database, security operations, DevOps, GitOps, DataOps, MLOps, AIOps, and SRE engineers",
            "About 300 application developers across Java, .NET, Python, frontend, mobile, integration, and API teams",
            "About 100 customer support, operations, and service desk users",
            "About 100 supported applications owned by 10 major technology teams",
        ],
        "technologyTeams": [
            "Application development",
            "API and integration",
            "Cloud infrastructure",
            "DevOps and release engineering",
            "Platform engineering",
            "SRE and observability",
            "Data engineering and DataOps",
            "MLOps and analytics engineering",
            "Database engineering",
            "Security operations and service desk",
        ],
        "consultantPlacement": (
            f"The role sits inside a shared platform/infrastructure operating model, works with multiple application teams, and supports {domain} systems such as {', '.join(applications[:5])}. "
            f"{role_name} ownership covers technical enablement and support deliverables, while product owners own business requirements and application teams own feature code."
        ),
    }


def _role_product_explanation(role_name: str, domain: str, applications: list[str], focus: list[str]) -> str:
    return (
        f"From a product point of view, the {role_name} does not own the business application features directly. "
        f"They own the enablement, reliability, automation, data, platform, or operational layer that lets product teams ship and support {domain} capabilities safely. "
        f"Every tool maps to a product outcome: faster release, fewer failed deployments, reliable {applications[0].lower()}, clearer support ownership, stronger audit evidence, or better incident recovery."
    )


def _product_glossary(role_name: str, domain: str, profile: DomainProfile, focus: list[str]) -> list[dict[str, Any]]:
    items = []
    role_terms = list(dict.fromkeys(ROLE_TERMS.get(role_name, focus)))[:50]
    domain_terms = _domain_glossary_terms(domain, profile)[:50]
    for index, (term, source_type) in enumerate([(term, "role") for term in role_terms], start=1):
        source_item = _glossary_source_item(role_name, term)
        kind = _consultant_glossary_kind(term, index)
        meaning = _training_glossary_meaning(kind, term, role_name, domain, profile, focus)
        answer_bullets = _glossary_answer_bullets(kind, term, role_name, domain, profile, focus)
        boundary_bullets = _glossary_boundary_bullets(kind, term, role_name, profile)
        items.append(
            {
                "sourceId": source_item["id"] if source_item else "",
                "sourceType": source_type,
                "term": term,
                "category": source_item["category"] if source_item else _training_glossary_category(kind),
                "productMeaning": meaning,
                "consultantTalkTrack": " ".join(answer_bullets),
                "consultantTalkTrackBullets": answer_bullets,
                "boundary": " ".join(boundary_bullets),
                "boundaryBullets": boundary_bullets,
            }
        )
    for offset, term in enumerate(domain_terms, start=len(items) + 1):
        kind = _consultant_glossary_kind(term, offset)
        meaning = _training_glossary_meaning(kind, term, role_name, domain, profile, focus)
        answer_bullets = _glossary_answer_bullets(kind, term, role_name, domain, profile, focus)
        boundary_bullets = _glossary_boundary_bullets(kind, term, role_name, profile)
        items.append(
            {
                "sourceId": "",
                "sourceType": "domain",
                "term": term,
                "category": _training_glossary_category(kind),
                "productMeaning": meaning,
                "consultantTalkTrack": " ".join(answer_bullets),
                "consultantTalkTrackBullets": answer_bullets,
                "boundary": " ".join(boundary_bullets),
                "boundaryBullets": boundary_bullets,
            }
        )
    return items


def _domain_glossary_terms(domain: str, profile: DomainProfile) -> list[str]:
    terms: list[str] = []
    for lob in profile.lines_of_business:
        terms.append(str(lob.get("name", "")))
        terms.extend(str(item) for item in _training_as_list(lob.get("systems")))
        terms.extend(str(item) for item in _training_as_list(lob.get("jobSignals")))
    terms.extend(profile.applications)
    terms.extend(profile.platform_signals)
    terms.extend(_domain_specific_terms(domain))
    terms.extend(_domain_operational_terms(profile))
    return [term for term in dict.fromkeys(item.strip() for item in terms if item and item.strip()) if term][:50]


def _training_as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    return [value]


def _domain_specific_terms(domain: str) -> list[str]:
    lookup = {
        "Healthcare / Health Insurance": [
            "Member Eligibility", "Claims Adjudication", "Prior Authorization", "Provider Directory", "Care Coordination",
            "HIPAA Controls", "PHI Audit Trail", "FHIR API", "HL7 Interface", "EHR Integration",
            "Lab Order Result", "Pharmacy Benefit", "Encounter Data", "Patient Consent", "Clinical Document",
            "Claims SLA", "Eligibility Verification", "Provider Credentialing", "Referral Workflow", "Telehealth Session",
        ],
        "Banking / Financial Services": [
            "ACH Transfer", "Wire Transfer", "Card Authorization", "Payment Settlement", "Core Banking Ledger",
            "KYC Verification", "AML Screening", "OFAC Check", "Fraud Score", "Transaction Monitoring",
            "Account Balance", "Payment Reversal", "Dispute Case", "Loan Application", "Credit Bureau Pull",
            "Treasury Operations", "Reconciliation File", "Statement Cycle", "SOX Evidence", "PCI Controls",
        ],
        "Retail / E-Commerce": [
            "Product Catalog", "Shopping Cart", "Checkout Flow", "Order Capture", "Inventory Reservation",
            "Payment Authorization", "Promotion Code", "Loyalty Points", "Recommendation Signal", "Warehouse Pick Pack",
            "Shipping Label", "Return Merchandise Authorization", "Customer Profile", "Price Rule", "Abandoned Cart",
            "Fulfillment SLA", "Stockout Signal", "Order Cancellation", "Refund Workflow", "Customer Analytics",
        ],
        "Insurance": [
            "Policy Quote", "Policy Bind", "Underwriting Rule", "Premium Billing", "Claims Intake",
            "Claims Adjudication", "Adjuster Assignment", "FNOL", "Coverage Validation", "Risk Score",
            "Agent Portal", "Document Intake", "Loss Reserve", "Subrogation", "Regulatory Filing",
            "Fraud Referral", "Endorsement", "Renewal Notice", "Policy Cancellation", "Claims Payment",
        ],
        "Logistics / Transportation": [
            "Shipment Tracking", "Route Optimization", "Fleet Dispatch", "Driver Assignment", "Warehouse Pick",
            "Delivery Scan", "Proof of Delivery", "Partner Carrier API", "ETA Prediction", "Load Planning",
            "Inventory Movement", "IoT Telemetry", "Cold Chain Signal", "Exception Management", "Freight Billing",
            "Dock Scheduling", "Last Mile Delivery", "Vehicle Maintenance", "GPS Event", "Customer Tracking Portal",
        ],
        "Manufacturing / Automotive / Industrial": [
            "MES Workflow", "Plant Floor Integration", "PLC Signal", "SCADA Alarm", "Production Line",
            "Quality Inspection", "Defect Traceability", "Work Order", "Bill of Materials", "ERP Integration",
            "Supplier EDI", "Predictive Maintenance", "Machine Telemetry", "OEE Dashboard", "Robotics Cell",
            "Safety Interlock", "Part Serialization", "Assembly Station", "Warranty Claim", "Industrial IoT",
        ],
        "Technology / SaaS / Enterprise Software": [
            "Tenant Provisioning", "Subscription Billing", "Feature Entitlement", "Usage Metering", "Customer Onboarding",
            "SaaS Control Plane", "Identity Federation", "SSO Flow", "API Rate Limit", "Tenant Isolation",
            "Product Analytics", "Experiment Flag", "Customer Success Signal", "Support Escalation", "Audit Log",
            "License Management", "Service Tier", "Multi-Region Failover", "Release Ring", "Data Residency",
        ],
        "Energy / Utilities / Data Centers": [
            "Outage Management", "Grid Telemetry", "Smart Meter", "Asset Monitoring", "SCADA Integration",
            "Demand Forecast", "Work Crew Dispatch", "Transformer Health", "Renewable Generation", "Substation Alert",
            "Energy Trading", "Billing Meter Read", "Data Center Capacity", "Power Usage Effectiveness", "Cooling Telemetry",
            "Generator Failover", "Load Shedding", "Inspection Work Order", "Regulatory Reporting", "Asset Maintenance",
        ],
        "Telecom / Media / Communications": [
            "Subscriber Account", "Plan Management", "Device Activation", "SIM Provisioning", "Telecom Billing",
            "Network Provisioning", "Service Assurance", "Revenue Assurance", "Number Portability", "Roaming Event",
            "5G Core", "Network Slice", "Call Detail Record", "Usage Mediation", "Content Entitlement",
            "Streaming Session", "Field Service Dispatch", "Customer Trouble Ticket", "Coverage Map", "Activation Failure",
        ],
    }
    return lookup.get(domain, [])


def _domain_operational_terms(profile: DomainProfile) -> list[str]:
    base_terms = []
    for app in profile.applications[:10]:
        base_terms.extend(
            [
                f"{app} availability",
                f"{app} error rate",
                f"{app} latency",
                f"{app} audit evidence",
                f"{app} support workflow",
            ]
        )
    return base_terms


def _glossary_source_item(role_name: str, term: str) -> dict[str, Any] | None:
    for item in MARKETING_ROLE_GLOSSARY:
        if item["roles"] == role_name and item["term"] == term:
            return item
    return None


def _training_glossary_category(kind: str) -> str:
    return {
        "consultant_delivery": "Delivery",
        "consultant_automation": "Automation",
        "consultant_infrastructure": "Cloud Infrastructure",
        "consultant_security": "Security",
        "consultant_platform": "Platform",
        "consultant_reliability": "Reliability",
        "consultant_dataops": "DataOps",
        "consultant_mlops": "MLOps",
        "consultant_aiops": "AIOps",
        "consultant_database": "Cloud Database",
        "consultant_governance": "Governance",
        "consultant_support": "Reliability",
    }.get(kind, "Core Term")


def _consultant_glossary_kind(term: str, index: int) -> str:
    lower = term.lower()
    if any(word in lower for word in ["pipeline", "deploy", "release", "build", "artifact", "rollback", "promotion", "sync", "reconciliation"]):
        return "consultant_delivery"
    if any(word in lower for word in ["terraform", "automation", "provision", "module", "script", "state", "backend", "idempotency"]):
        return "consultant_automation"
    if any(word in lower for word in ["vpc", "vnet", "subnet", "network", "route", "load balancer", "dns", "gateway", "endpoint", "firewall"]):
        return "consultant_infrastructure"
    if any(word in lower for word in ["iam", "rbac", "secret", "vault", "encryption", "certificate", "policy", "guardrail", "access"]):
        return "consultant_security"
    if any(word in lower for word in ["kubernetes", "helm", "cluster", "namespace", "container", "docker", "registry", "ingress", "mesh"]):
        return "consultant_platform"
    if any(word in lower for word in ["slo", "sli", "sla", "incident", "alert", "monitor", "logging", "tracing", "observability", "mttr", "rca", "runbook", "postmortem"]):
        return "consultant_reliability"
    if any(word in lower for word in ["data", "airflow", "dag", "dbt", "spark", "kafka", "warehouse", "lake", "schema", "quality", "lineage"]):
        return "consultant_dataops"
    if any(word in lower for word in ["ml", "model", "inference", "feature", "drift", "registry", "experiment", "prediction"]):
        return "consultant_mlops"
    if any(word in lower for word in ["aiops", "anomaly", "correlation", "noise", "event", "ticket", "topology", "chatops", "remediation"]):
        return "consultant_aiops"
    if any(word in lower for word in ["database", "sql", "rds", "aurora", "replica", "backup", "restore", "query", "index", "replication"]):
        return "consultant_database"
    if index % 5 == 0:
        return "consultant_governance"
    if index % 3 == 0:
        return "consultant_support"
    return "consultant_core"


def _training_glossary_meaning(kind: str, term: str, role_name: str, domain: str, profile: DomainProfile, focus: list[str]) -> str:
    primary_app = profile.applications[0]
    secondary_app = profile.applications[1]
    signal = profile.platform_signals[0]
    meanings = {
        "consultant_delivery": f"{term} belongs to the release and delivery path for {primary_app}, where code, configuration, approvals, validation, and rollback evidence move toward production.",
        "consultant_automation": f"{term} converts repeatable {domain} engineering work into reusable automation so environments, operational tasks, and support checks do not depend on manual steps.",
        "consultant_infrastructure": f"{term} is part of the cloud or network foundation that lets {primary_app}, {secondary_app}, and connected services run securely and reliably.",
        "consultant_security": f"{term} protects access, secrets, certificates, policy controls, or audit evidence for regulated {domain} workflows such as {signal}.",
        "consultant_platform": f"{term} is part of the shared runtime or platform layer used by application teams to deploy, operate, observe, and support {domain} services.",
        "consultant_reliability": f"{term} is an operational reliability term used to detect impact, explain health, reduce recovery time, and prove production readiness for {primary_app}.",
        "consultant_dataops": f"{term} supports data movement, validation, orchestration, freshness, or reporting accuracy across {domain} source systems and analytics workflows.",
        "consultant_mlops": f"{term} supports model delivery, inference, monitoring, version control, or drift management for analytics and prediction workflows in {domain}.",
        "consultant_aiops": f"{term} improves incident intelligence by connecting alerts, logs, metrics, traces, tickets, topology, and recent changes for {domain} operations.",
        "consultant_database": f"{term} supports database availability, performance, backup, replication, security, or migration work behind {primary_app} and connected systems.",
        "consultant_governance": f"{term} provides change control, auditability, ownership clarity, and support evidence for {role_name} work across {domain} applications.",
        "consultant_support": f"{term} appears during production support when teams need impact evidence, owner routing, recovery validation, and runbook updates.",
    }
    return meanings.get(kind, f"{term} is a core {role_name} term that connects {', '.join(focus[:3]).lower()} to delivered work in the {domain} application landscape.")


def _glossary_answer_bullets(kind: str, term: str, role_name: str, domain: str, profile: DomainProfile, focus: list[str]) -> list[str]:
    app_one = profile.applications[0]
    app_two = profile.applications[1]
    app_three = profile.applications[2]
    signal_one = profile.platform_signals[0]
    signal_two = profile.platform_signals[1]
    role_focus = ", ".join(focus[:3]).lower()
    if kind == "consultant_delivery":
        return [
            f"{term} was tied to the delivery path for {app_one}: source change, validation, approval, rollout, smoke check, and rollback readiness.",
            f"Evidence included pipeline run, deployment version, approval record, failed-stage log, release marker, and post-release validation.",
            f"The business value was safer releases across {domain} systems without confusing product code ownership with platform or release ownership.",
        ]
    if kind == "consultant_automation":
        return [
            f"{term} removed repeated manual steps from environment, configuration, provisioning, validation, or support workflows.",
            f"The implementation pattern was inputs, reusable logic, review gate, lower-environment test, production-safe execution, and saved evidence.",
            f"Output was a repeatable artifact teams could reuse across {app_one}, {app_two}, and other {domain} applications.",
        ]
    if kind == "consultant_infrastructure":
        return [
            f"{term} was explained as an infrastructure dependency behind the product flow, not as an isolated cloud keyword.",
            f"Signals checked were connectivity, routing, target health, capacity, latency, access path, and recent infrastructure change.",
            f"Outcome was clearer isolation between application defect, platform issue, network issue, and dependency failure.",
        ]
    if kind == "consultant_security":
        return [
            f"{term} mattered because {domain} systems needed controlled access, protected credentials, traceable approvals, and audit-ready evidence.",
            f"The work handled implementation or validation evidence while policy ownership stayed with security, risk, audit, or governance teams.",
            f"Proof included access request, configuration diff, scan or policy result, secret reference, certificate status, and support handoff.",
        ]
    if kind == "consultant_platform":
        return [
            f"{term} belonged to the shared platform used by multiple application teams, including services around {app_one} and {app_two}.",
            f"Work focused on reusable standards: naming, namespace or runtime setup, deployment convention, observability, health checks, and onboarding notes.",
            f"The value was consistency across teams, fewer one-off fixes, and faster support when a product flow became unhealthy.",
        ]
    if kind == "consultant_reliability":
        return [
            f"{term} converted production behavior into evidence: impact, symptom, owner, recent change, recovery step, and validation.",
            f"Signals came from dashboards, alerts, logs, traces, tickets, deployment markers, or runbook checks tied to {app_one}.",
            f"Result was faster triage, cleaner escalation, better RCA notes, and less confusion during support calls.",
        ]
    if kind == "consultant_dataops":
        return [
            f"{term} was connected to data freshness, schema safety, pipeline reliability, reporting accuracy, or downstream analytics for {domain}.",
            f"Evidence included run history, failed task, data quality result, row count check, schema diff, SLA status, and backfill or replay notes.",
            f"Boundary was clear: data workflow reliability was handled, while business definitions and source-system feature logic stayed with owning teams.",
        ]
    if kind == "consultant_mlops":
        return [
            f"{term} supported controlled movement of model or feature work from development into monitored production usage.",
            f"Evidence included model version, feature pipeline status, deployment record, inference health, drift signal, access control, and rollback option.",
            f"The product link was prediction quality, service latency, secure model access, and stable analytics behavior for {domain}.",
        ]
    if kind == "consultant_aiops":
        return [
            f"{term} improved incident intelligence by joining signals from alerts, logs, metrics, traces, tickets, topology, and deployment events.",
            f"Output was correlation group, anomaly window, enriched ticket, suspected owner, runbook link, or RCA signal for {app_one}.",
            f"The operational result was less alert noise, faster triage, cleaner escalation, and better recovery evidence.",
        ]
    if kind == "consultant_database":
        return [
            f"{term} was treated as a data-store dependency behind product transactions, reporting, search, cache, or operational workflows.",
            f"Evidence included connection health, slow query signal, replica lag, backup status, storage trend, access issue, or migration validation.",
            f"Application teams owned data rules and feature code; database ownership focused on reliability, performance, recoverability, and operational safety.",
        ]
    if kind == "consultant_governance":
        return [
            f"{term} gave the work a reviewable trail: request, approval, implementation, validation, support handoff, and final evidence.",
            f"The term was used to explain how {role_name} work stayed aligned with enterprise standards without slowing delivery unnecessarily.",
            f"Evidence mattered because {domain} systems needed traceability across product, application, platform, security, and operations teams.",
        ]
    if kind == "consultant_support":
        return [
            f"{term} showed up when production support needed a clean path from symptom to owner to recovery validation.",
            f"My answer names the impacted product step, signal reviewed, owner contacted, action taken, and prevention note added afterward.",
            f"That made the support story concrete across {app_one}, {app_two}, and connected {domain} systems.",
        ]
    if kind == "consultant_core":
        return [
            f"{term} connected daily engineering execution to {domain} product delivery across {app_one}, {app_two}, and supporting systems.",
            f"Evidence included the Jira item, owner, implementation note, validation result, support impact, and handoff artifact.",
            f"The term was explained through delivered work: problem, action, artifact, validation, and production outcome.",
        ]
    if kind == "primary_application":
        return [
            f"{term} was the primary product workflow for the {domain} flow, so incidents were explained through user impact first: login, transaction, request status, page error, or service delay.",
            f"{role_name} ownership was to connect service health, recent changes, alerts, logs, and support tickets so the team could separate application defects from platform or dependency failures.",
            f"Useful {term} evidence: request error trend, latency chart, release marker, incident ticket, support timeline, and recovery validation after the user flow returned to normal.",
        ]
    if kind == "dependent_application":
        return [
            f"{term} was treated as a downstream dependency for {app_one}; a failure here could make the primary workflow look broken even when the front-end service was healthy.",
            f"{role_name} work focused on dependency visibility: health checks, timeout patterns, queue or API errors, escalation path, and runbook steps for owner routing.",
            f"{term} answer includes the dependency signal, affected product step, resolver group, and validation used after recovery.",
        ]
    if kind == "system_of_record":
        return [
            f"{term} held durable business state for {domain}, so every change required stronger traceability than a simple stateless service deployment.",
            f"{role_name} contribution was around release evidence, access assumptions, configuration safety, monitoring signals, and rollback or recovery coordination.",
            f"Strong {term} artifact trail: change request, approval, configuration diff, lower-environment validation, production verification, and support handoff.",
        ]
    if kind == "compliance_signal":
        return [
            f"{term} mattered because technical delivery had to protect regulated product workflows, not only pass a build or deployment check.",
            f"{role_name} evidence included access control review, secrets handling, logging visibility, deployment approval, scan result, or audit-friendly runbook notes.",
            f"The impressive {term} answer links compliance to engineering behavior: who approved, what was checked, what evidence was saved, and how production risk was reduced.",
        ]
    if kind == "operational_signal":
        return [
            f"{term} was converted into visible operational evidence for leads, support teams, and service owners.",
            f"{role_name} work connected dashboards, alert rules, runbook checks, release validation, or incident notes to the actual {domain} product flow.",
            f"The output was not a basic dashboard; it showed service health, ownership, recent change, impact, and next action for {app_one} or {app_two}.",
        ]
    if kind == "primary_role_skill":
        return [
            f"{term} was the main {role_name} ownership area for this program.",
            f"{term} inputs included product impact, service telemetry, repository or configuration changes, support tickets, and team handoff gaps.",
            f"Outputs included implementation evidence, validation result, dashboard or log proof, runbook update, and clearer ownership across {app_one}, {app_two}, and {app_three}.",
        ]
    if kind == "implementation_skill":
        return [
            f"{term} was handled through delivery activity and evidence.",
            f"{term} work produced concrete artifacts: pull request, configuration, automation workflow, dashboard, alert policy, deployment record, data check, model record, database change, or runbook depending on the role.",
            f"Validation connected the artifact back to {domain} product behavior and support readiness.",
        ]
    if kind == "standardization_skill":
        return [
            f"{term} became a repeatable standard across teams rather than a one-time fix for one application.",
            f"The {term} standard covered naming, environment separation, approval flow, evidence capture, monitoring, and handoff expectations.",
            f"That made the {term} story stronger because it affected multiple teams and applications, not only one isolated ticket.",
        ]
    if kind == "support_skill":
        return [
            f"{term} was important during production support because it defined how the team acted when a release, alert, dependency, or operational process failed.",
            f"{role_name} responsibility was to collect evidence, identify impact, route to the right owner, validate recovery, and update the runbook or RCA notes.",
            f"{term} answer includes a failure example, the signal reviewed, the decision made, and the prevention step added afterward.",
        ]
    return [
        f"{term} was the senior-level discussion area for tradeoffs across speed, reliability, governance, cost, and support ownership.",
        f"{role_name} explanation connects {role_focus} to product risk in {domain}, with clear evidence and a realistic decision path.",
        f"The best {term} answer names the tradeoff, selected option, rejected option, validation method, and operational outcome.",
    ]


def _glossary_boundary_bullets(kind: str, term: str, role_name: str, profile: DomainProfile) -> list[str]:
    if kind.startswith("consultant_"):
        if kind in {"consultant_security", "consultant_governance"}:
            return [
                f"{role_name} owned implementation evidence, validation notes, and operational handoff for {term}.",
                f"Security, risk, audit, or change governance teams owned policy approval and final control interpretation.",
                f"Application and product teams owned feature behavior, customer rules, and business acceptance.",
            ]
        if kind in {"consultant_infrastructure", "consultant_platform", "consultant_database"}:
            return [
                f"{role_name} owned the technical layer evidence for {term}: configuration, health signal, validation, and runbook notes.",
                "Application teams owned service code and business logic above the platform or database layer.",
                "Cloud, database, security, vendor, or network teams joined when the signal pointed to their resolver boundary.",
            ]
        if kind in {"consultant_reliability", "consultant_aiops", "consultant_support"}:
            return [
                f"{role_name} owned triage evidence, signal correlation, owner routing, recovery validation, and RCA/runbook updates for {term}.",
                "Resolver teams owned the actual code, infrastructure, database, data, security, or vendor fix once evidence identified the failing layer.",
                "Service desk and operations teams owned intake, communication workflow, and ticket lifecycle coordination.",
            ]
        if kind in {"consultant_dataops", "consultant_mlops"}:
            return [
                f"{role_name} owned pipeline, model, validation, monitoring, deployment, or support evidence for {term}.",
                "Data owners, data science teams, and product teams owned definitions, model intent, business rules, and acceptance criteria.",
                "Platform, security, and application teams joined when runtime, access, or integration boundaries were affected.",
            ]
        return [
            f"{role_name} owned implementation, validation, documentation, and support handoff for {term}.",
            "Product owners and application teams owned business requirements, feature logic, and acceptance priority.",
            "Escalation moved to the correct resolver group when evidence showed the issue outside the role boundary.",
        ]
    if kind in {"primary_application", "dependent_application", "system_of_record"}:
        return [
            f"{role_name} owned platform, delivery, reliability, data, automation, or support evidence around {term}.",
            f"Product owners owned business priority and acceptance criteria for {term}.",
            f"Application teams owned {term} feature code and domain rules.",
        ]
    if kind in {"compliance_signal", "operational_signal"}:
        return [
            f"{role_name} owned technical evidence and operational visibility for {term}.",
            f"Security, risk, audit, or governance teams owned final {term} policy approval.",
            "Support teams used the evidence for triage and escalation.",
        ]
    return [
        f"{role_name} owned implementation, validation, documentation, and support handoff for {term}.",
        f"Business logic and product decisions related to {term} stayed with product and application teams.",
        f"Escalation moved to database, data, security, infrastructure, or vendor teams when signals pointed outside the {role_name} boundary.",
    ]


def _use_case_boundaries(role_name: str, domain: str, profile: DomainProfile, applications: list[str], focus: list[str], is_full: bool) -> list[dict[str, str]]:
    primary_app = applications[0]
    secondary_app = applications[1]
    analytics_app = applications[-1]
    cases = [
        {
            "name": f"Release enablement for {primary_app}",
            "businessGoal": f"Help the product team release changes to {primary_app} without breaking customer-facing {domain} workflows.",
            "inScope": f"{focus[0]}, {focus[1]}, environment configuration, validation checks, rollback notes, release evidence, and coordination with QA/security.",
            "outOfScope": "Changing core application business logic, approving business requirements, or bypassing client change governance.",
            "consultantOwnership": f"As the {role_name}, I owned the enablement layer and made sure teams had repeatable implementation and support steps.",
            "implementationEvidence": "Pull request, pipeline run, configuration diff, deployment screenshot, smoke-test result, rollback note, and release summary.",
            "interviewPositioning": "Main project story: business context, technical execution, validation, and communication.",
        },
        {
            "name": f"Production support workflow for {secondary_app}",
            "businessGoal": f"Reduce downtime, confusion, and handoff delay when {secondary_app} has a failed release, latency issue, or data/configuration problem.",
            "inScope": f"Logs, metrics, traces, alerts, runbook steps, incident notes, RCA support, and ownership routing for {', '.join(focus[:3]).lower()}.",
            "outOfScope": "Blaming another team without evidence, making direct production changes without approval, or ignoring customer/business impact.",
            "consultantOwnership": "Symptoms were checked against signals, escalated with evidence, validated after recovery, and documented in the runbook.",
            "implementationEvidence": "Incident timeline, dashboard screenshot, alert rule, log query, RCA notes, and before/after metric.",
            "interviewPositioning": "Production support story: impact check, evidence collection, owner routing, recovery validation, and prevention.",
        },
        {
            "name": f"Operational visibility for {analytics_app}",
            "businessGoal": f"Give managers and support teams visibility into product health, delivery progress, and operational risk across the {domain} application landscape.",
            "inScope": f"Dashboards, service health indicators, deployment markers, SLA/SLO or freshness checks, alert routing, and weekly status artifacts.",
            "outOfScope": "Replacing enterprise BI, inventing business KPIs without stakeholder approval, or exposing sensitive data in dashboards.",
            "consultantOwnership": f"I built the technical visibility layer and translated tool output into a client-readable explanation.",
            "implementationEvidence": "Dashboard, alert policy, sample status report, runbook, and metric explanation.",
            "interviewPositioning": "Senior-level story: tool outputs connected to product decisions, stakeholder communication, and operational risk.",
        },
    ]
    if is_full:
        cases.append(
            {
                "name": f"Audit-ready delivery package for {domain}",
                "businessGoal": "Show that every implementation has evidence, approval, validation, and support documentation.",
                "inScope": "Architecture notes, access assumptions, deployment output, test results, security scan result, rollback plan, owner route, and runbook update.",
                "outOfScope": "Claiming compliance ownership or legal approval; the role supports evidence collection and engineering controls.",
                "consultantOwnership": f"I packaged the {role_name} work so a lead, client manager, or interviewer could understand what was built and how it was operated.",
                "implementationEvidence": "Final project folder, screenshots, command outputs, diagrams, glossary, and story bank.",
                "interviewPositioning": "Delivery story: design decision, implementation artifact, lower-environment result, release approval, rollback path, and support ticket/runbook trail.",
            }
        )
    return cases


def _delivered_use_cases(role_name: str, domain: str, profile: DomainProfile, applications: list[str], focus: list[str], is_full: bool) -> list[dict[str, Any]]:
    delivered = [
        {
            "title": f"Standardized release path for {applications[0]} and related {domain} services",
            "businessProblem": f"Multiple teams were releasing {domain} changes differently, which created inconsistent validation, rollback gaps, and support confusion across {applications[0]}, {applications[1]}, and {applications[2]}.",
            "deliveredScope": [
                f"Mapped the release workflow across dev, QA, stage, and production for {applications[0]}.",
                f"Implemented the {role_name} portion around {focus[0]}, {focus[1]}, and {focus[2]}.",
                "Defined approval gate, smoke-test result, rollback decision, release note, owner route, and runbook update.",
                "Documented the boundary between product owner, application developer, platform engineer, security, QA, and operations responsibilities.",
            ],
            "roleBoundary": f"I owned the {role_name} enablement layer for {applications[0]}. For {applications[0]}, I did not own business requirements or feature code, but I made the delivery path visible through rerunnable pipeline steps, smoke checks, rollback criteria, and support notes.",
            "systemsTouched": applications[:5],
            "evidenceToExplain": ["Architecture note naming systems and owners", "Pull request or configuration diff", "Pipeline run or deployment record", "Smoke-test result", "Rollback decision note and runbook step"],
            "interviewStory": f"Delivery standardization improved release safety for {domain} changes across a large enterprise application landscape.",
        },
        {
            "title": f"Production support and incident recovery model for {applications[1]}",
            "businessProblem": f"When {applications[1]} had failures, teams needed faster triage, named resolver groups, and logs/metrics/ticket context before escalating to developers, platform, database, data, or security teams.",
            "deliveredScope": [
                "Defined symptoms, dashboards, logs, traces, alert routing, and escalation paths.",
                f"Created role-specific checks for {', '.join(focus[:4]).lower()}.",
                "Built runbook steps for common failures, rollback decisions, and post-incident documentation.",
                "Connected support desk language to engineering signals so non-engineering users could report issues clearly.",
            ],
            "roleBoundary": f"I handled triage evidence, platform signals, and recovery coordination as the {role_name}; application fixes stayed with app owners and business priority stayed with product owners.",
            "systemsTouched": [applications[1], applications[3], applications[-1]],
            "evidenceToExplain": ["Incident timeline", "Dashboard/log examples", "Runbook", "RCA notes", "Before/after support workflow"],
            "interviewStory": f"Production maturity for {applications[1]} covered impact check, signal review, ownership routing, recovery validation, and prevention.",
        },
        {
            "title": f"Enterprise visibility and governance for {domain} technology teams",
            "businessProblem": f"{domain} leads and managers needed a consistent view of release status, open operational risks, service owners, alert coverage, rollback readiness, and support contacts across many teams and applications.",
            "deliveredScope": [
                "Created a shared view of application ownership, service health, deployment status, operational risks, and support contacts.",
                f"Added {role_name} deliverables into the operating model so teams knew where {', '.join(focus[:3]).lower()} fit.",
                "Defined weekly reviews of deployment records, alert quality, runbook gaps, owner routing, and release-readiness checkpoints.",
                "Aligned glossary and product terminology so systems could be discussed in business language.",
            ],
            "roleBoundary": f"I did not replace {domain} enterprise reporting or product KPIs; I created engineering views showing ownership, deployment state, alert coverage, open risks, and support contacts for the technology organization.",
            "systemsTouched": [applications[0], applications[4], applications[-1]],
            "evidenceToExplain": ["Ownership matrix by application and resolver group", "Dashboard with service health and release markers", "Delivery checklist with approval and smoke-test status", "Support contact map", "Glossary and workflow diagram"],
            "interviewStory": f"Cross-team operating-model improvement connected {domain} service ownership, release health, support readiness, and engineering evidence.",
        },
    ]
    if is_full:
        delivered.append(
            {
                "title": f"Audit-ready project evidence package for {domain} releases",
                "businessProblem": "The client needed better traceability from change request to implementation artifact, lower-environment result, production release, rollback decision, and support ticket/runbook trail.",
                "deliveredScope": [
                    "Created a delivery folder structure for design note, PR/config diff, validation output, approval record, release note, rollback path, and runbook update.",
                    "Mapped technical evidence to product impact and operational risk.",
                    "Prepared concise delivery stories covering project scope, ownership boundary, artifacts, validation, and outcomes.",
                    "Connected role-specific work to domain applications and enterprise teams.",
                ],
                "roleBoundary": f"I supported evidence, implementation, and technical documentation as the {role_name}; compliance approval remained with client governance teams.",
                "systemsTouched": applications[:6],
                "evidenceToExplain": ["Design diagram with system owners", "PR/config diff or change ticket", "Smoke-test screenshots or command output", "Runbook section with first checks", "Interview story bank"],
                "interviewStory": "Traceable delivery connected change request, implementation artifact, validation output, production release, rollback path, and support ticket/runbook trail.",
            }
        )
    provider_doc_backlog = _sprint_use_case_backlog(role_name, domain, applications, focus)
    consultant_ready_cases = [
        *provider_doc_backlog[:10],
        *_enterprise_lifecycle_use_cases(role_name, domain, applications, focus),
        *provider_doc_backlog[10:],
        *delivered,
    ][:13]
    return _enrich_delivered_use_cases(consultant_ready_cases, role_name, domain, profile, applications, focus)


def _enterprise_lifecycle_use_cases(role_name: str, domain: str, applications: list[str], focus: list[str]) -> list[dict[str, Any]]:
    primary = applications[0]
    secondary = applications[1] if len(applications) > 1 else applications[0]
    analytics = applications[-1]
    role_focus = ", ".join(focus[:4]).lower()
    return [
        {
            "title": f"Enterprise application lifecycle: sunrise, sunset, modernization, cutover, and operational transition for {primary}",
            "businessProblem": f"{domain} teams needed a controlled way to onboard new applications, modernize or migrate legacy workloads, cut over production traffic, retire older workflows, and transition support without losing ownership, routing, data retention, monitoring, support, cost, or audit evidence.",
            "deliveredScope": [
                "Created sunrise readiness: owner, environment, DNS/API route, IAM, secrets, monitoring, backup, runbook, CMDB/ServiceNow record, release gate, and support model.",
                "Created sunset readiness: dependency map, traffic drain, data archival/retention, alert removal, pipeline decommissioning, access cleanup, cost cleanup, and business signoff.",
                "Mapped modernization and migration readiness: data center exit, cloud migration, re-platforming, API gateway migration, database/schema migration, vendor integration onboarding, and third-party API failure handling.",
                "Prepared cutover controls: environment promotion, blue-green deployment, canary rollout, feature flag rollout, CAB approval, rollback/roll-forward, performance testing, multi-region readiness, and production readiness review.",
                "Validated resilience and governance: backup and restore validation, disaster recovery drill, certificate rotation, secrets rotation, IAM access review, firewall/network rule change, compliance audit evidence, SLO/SLA definition, capacity planning, and cost optimization.",
                "Closed the transition loop with observability onboarding, alert tuning/noise reduction, runbook creation, incident simulation, postmortem/RCA, CMDB/ServiceNow ownership update, operational handoff, knowledge transfer, tenant onboarding where relevant, and support model transition.",
                f"Mapped the {role_name} boundary around {role_focus}; product owners approved business retirement and application teams owned feature behavior.",
            ],
            "roleBoundary": f"I owned the {role_name} technical enablement and evidence path for sunrise/sunset; business decommission approval, product priority, and application feature logic stayed with their owners.",
            "systemsTouched": [primary, secondary, applications[2 % len(applications)], analytics],
            "evidenceToExplain": ["application inventory", "dependency map", "DNS/API cutover evidence", "migration runbook", "backup/restore proof", "DR test evidence", "CAB/change approval", "rollback plan", "ServiceNow/CMDB update", "access review", "cost cleanup report", "RCA/postmortem", "knowledge transfer note"],
            "interviewStory": "Enterprise lifecycle work shows maturity because it connects onboarding, modernization, migration, release cutover, recovery, governance cleanup, support transition, decommissioning, and production proof.",
        },
    ]


def _use_case_evidence_items(title: str, role_name: str, domain: str, primary: str, secondary: str, artifact: str, outcome: str) -> list[str]:
    artifact_parts = [part.strip() for part in artifact.split(",") if part.strip()]
    role_action = role_name.lower()
    return _unique(
        [
            "Jira story group showing analysis, design, implementation, validation, and handoff work as one delivered use case",
            f"Design note showing how {primary} connects to {secondary} and where {role_name} ownership starts and ends",
            *artifact_parts[:8],
            f"Validation proof showing the changed {primary} behavior, dashboard/job/query/pipeline result, or policy outcome after {title}",
            f"Operational handoff showing symptom, first check, escalation owner, rollback/rerun/retry decision, and support note for {secondary}",
            f"{domain} outcome proof connecting the {role_action} change to {outcome}",
        ]
    )


def _sprint_use_case_backlog(role_name: str, domain: str, applications: list[str], focus: list[str]) -> list[dict[str, Any]]:
    app = lambda i: applications[i % len(applications)]
    themes = _role_sprint_themes(role_name, domain, applications, focus)
    cases = []
    for index, item in enumerate(themes, start=1):
        theme = item["theme"]
        primary = item["primary"]
        secondary = item["secondary"]
        problem = item["problem"]
        artifact = item["artifact"]
        outcome = item["outcome"]
        focus_slice = ", ".join(focus[index % len(focus):][:3] or focus[:3]).lower()
        cases.append(
            {
                "title": f"{theme} for {primary}",
                "businessProblem": f"{problem} The affected {domain} systems included {primary}, {secondary}, and shared platform/support services.",
                "deliveredScope": [
                    f"Grouped 5-6 Jira stories into one use case: analysis, design, implementation, validation, production/support, and documentation.",
                    f"Delivered {role_name} work around {focus_slice} for {primary} and connected {domain} systems.",
                    f"Created sprint artifacts: {artifact}.",
                    f"Reviewed the outcome with application, platform, QA, security, operations, and service desk stakeholders: {outcome}.",
                ],
                "roleBoundary": f"I owned the {role_name} engineering scope for {primary}; product priority, feature code, final business acceptance, and policy approval stayed with the owning teams.",
                "systemsTouched": _unique([primary, secondary, app(index + 2), app(-1)]),
                "evidenceToExplain": _use_case_evidence_items(title=theme, role_name=role_name, domain=domain, primary=primary, secondary=secondary, artifact=artifact, outcome=outcome),
                "productionOutcome": outcome,
                "interviewStory": f"{theme} delivered a sprint-sized use case for {primary}, with clear Jira story grouping, implementation evidence, validation, and production support outcome.",
            }
        )
    return cases


def _role_sprint_themes(role_name: str, domain: str, applications: list[str], focus: list[str]) -> list[dict[str, str]]:
    app = lambda i: applications[i % len(applications)]
    role_themes = {
        ("Data Platform Engineer", "Banking / Financial Services"): [
            ("Payment transaction data lake ingestion", app(2), app(0), "Payment, card, and core banking events needed reliable ingestion from multiple source systems into a governed cloud data lake.", "S3/ADLS landing path, ingestion job, schema contract, partition layout, data quality checks, and row-count reconciliation", "transaction data became queryable and auditable for downstream analytics"),
            ("Card authorization streaming pipeline", app(3), app(2), "High-volume card authorization events needed low-latency processing for operations, fraud, dispute, and reporting consumers.", "Kafka/Kinesis/Pub/Sub topic design, Flink/Spark job, checkpointing, lag dashboard, replay note, and failed-event handling", "streaming card events became observable and recoverable"),
            ("Core banking ledger reporting model", app(0), app(-1), "Ledger and account-balance reporting required controlled transformations, reconciliation, and clear definitions for analysts.", "dbt model, SQL transformation, ledger reconciliation query, data dictionary entry, and reporting model documentation", "ledger reporting became consistent across analytics and operations users"),
            ("Presto and Athena query optimization", app(-1), app(1), "Analytical queries over large banking datasets slowed because file layout, partitions, joins, and scan volume were not tuned.", "Parquet conversion, partition strategy, Athena/Presto query plan, workgroup setting, before/after runtime, and cost evidence", "ad hoc and dashboard queries became faster and cheaper"),
            ("Airflow DAG reliability and retry model", app(-1), app(2), "Daily pipelines needed clear ownership for retries, late-arriving files, dependency failures, alert routing, and backfills.", "Airflow DAG, task dependency graph, retry policy, SLA alert, backfill command, and runbook", "pipeline failures became easier to diagnose and recover"),
            ("Spark or Flink job on Kubernetes", app(-1), app(3), "Large transformations and streaming jobs needed scalable execution with resource control, logs, metrics, and restart behavior.", "Kubernetes job spec, Spark/Flink configuration, resource request, job log, metrics dashboard, and failure recovery note", "data processing jobs became scalable and supportable"),
            ("Terraform-managed data infrastructure", app(-1), app(4), "Data platform infrastructure needed repeatable provisioning for storage, IAM, compute, catalog, warehouse, monitoring, and network access.", "Terraform module, plan/apply output, IAM policy, environment variables, state review, and tagging evidence", "data infrastructure became repeatable across environments"),
            ("Financial data security and access controls", app(-1), app(0), "Banking datasets needed strong access controls for customer, card, payment, and account data across analysts and engineering users.", "Lake/warehouse permissions, row or column controls, IAM/RBAC policy, audit log, exception record, and access test", "sensitive financial data access became governed and reviewable"),
            ("Settlement and reconciliation reporting", app(2), app(5), "Payment settlement and reconciliation reports needed consistent source-to-target validation across transactions, files, and warehouse tables.", "control totals, row counts, exception table, reconciliation SQL, failed-record handling, and stakeholder report", "settlement reporting became easier to trust and support"),
        ],
        ("Data Platform Engineer", "Healthcare / Health Insurance"): [
            ("PostgreSQL multi-schema healthcare application model", app(0), app(2), "Healthcare application modules needed clean relational boundaries across operational tables, reference data, audit tables, and analytics-facing structures.", "PostgreSQL schema map, entity relationships, constraints, indexes, migration notes, and data dictionary entries", "application and analytics teams could understand the data model consistently"),
            ("ORM migration and schema review workflow", app(0), app(1), "Backend developers needed a controlled way to evolve schemas without breaking downstream pipelines, reporting models, or healthcare audit expectations.", "Prisma or ORM migration review, pull request checklist, rollback note, test migration output, and downstream impact review", "schema changes became safer for application and data consumers"),
            ("Application data contract for healthcare modules", app(1), app(3), "Application modules needed explicit data contracts so APIs, ETL jobs, analysts, and reporting models used the same access patterns and field definitions.", "data contract document, source-to-target mapping, interface expectations, example payloads, and owner signoff", "backend and data teams had fewer misunderstandings about data ownership and usage"),
            ("FHIR HL7 R4 transformation pipeline", app(2), app(-1), "Clinical and operational data needed transformation into healthcare-friendly structures for application consumption, integration, and analytics.", "FHIR resource mapping, transformation job, validation rules, rejected-record handling, sample output, and clinical-data quality notes", "FHIR-style output became cleaner, validated, and easier to consume"),
            ("Synthetic PHI-safe test data generation", app(0), app(4), "Development and QA teams needed realistic data for testing without exposing production PHI.", "synthetic data generation script, masking rules, referential integrity checks, QA dataset, and PHI review checklist", "teams could test workflows safely without production patient data exposure"),
            ("Azure Data Factory versus code-based ETL decision", app(-1), app(0), "The team needed a durable ETL tooling decision for legacy ingestion, application-adjacent transforms, monitoring, retry behavior, and maintainability.", "decision matrix, ADF pipeline prototype, code-based ETL prototype, operational tradeoff notes, and implementation recommendation", "pipeline ownership and tooling decisions became easier to defend"),
            ("Pipeline reliability, retry, and runbook model", app(-1), app(1), "Healthcare pipelines needed clear monitoring, alerting, retry behavior, failure classification, and support runbooks for downstream application and analytics users.", "pipeline monitor, alert route, retry policy, failure table, backfill command, and support runbook", "pipeline failures became recoverable without one person holding all context"),
            ("Data dictionary and model documentation standard", app(-1), app(2), "Analysts, backend engineers, and product users needed shared definitions for clinical, operational, and reporting data elements.", "data dictionary, table/column descriptions, ownership metadata, glossary terms, lineage notes, and review cadence", "data definitions became reusable across engineering and analytics conversations"),
            ("Audit trail and history-table pattern", app(0), app(5), "Healthcare systems needed traceable changes for operational support, reporting, and HIPAA/HITRUST-aligned audit evidence.", "history table design, audit columns, change capture rule, retention note, query examples, and access review evidence", "data changes became traceable for support and governance needs"),
            ("HIPAA/HITRUST access and column-level controls", app(-1), app(3), "Sensitive healthcare data required role-based access, least privilege, column-level controls, and auditable exceptions across application and analytics users.", "RBAC matrix, column-level security rule, masked view, access test, audit log, and exception approval record", "sensitive data access became governed and explainable"),
        ],
        ("Data Platform Engineer", "Manufacturing / Automotive / Industrial"): [
            ("Manufacturing quality data pipeline", app(0), app(2), "Production, equipment, inspection, and product quality data needed reliable ingestion into trusted tables so quality engineers could analyze defects, yield, and station performance.", "MES extract, equipment telemetry feed, inspection source mapping, SQL/Python transform, data quality checks, and reconciliation output", "manufacturing quality data became trusted for operational analysis"),
            ("MES data validation and production control checks", app(0), app(1), "Manufacturing execution records needed validation for work order, station, serial number, operator action, test result, defect code, rework, pass/fail, and timestamp completeness.", "MES validation rules, rejected-record table, pass/fail reconciliation, missing-field report, and validation signoff", "production control data matched business expectations before dashboards consumed it"),
            ("Defect prediction feature pipeline", app(2), app(-1), "Quality teams needed early signals for likely defects based on process parameters, equipment behavior, historical failures, inspection results, and field-product feedback.", "Python/PySpark feature job, training dataset profile, correlation summary, outlier checks, defect label definition, and model-input validation", "defect analysis moved from after-the-fact reporting toward predictive quality insights"),
            ("Advanced statistics and quality trend analysis", app(3), app(0), "Engineers needed statistical evidence for failure trends, process drift, outliers, control-limit breaches, and root-cause hypotheses.", "statistical notebook, distribution and trend charts, control-limit calculation, outlier report, and root-cause evidence summary", "quality investigations had statistical support instead of dashboard observation alone"),
            ("Live quality dashboard for factory operations", app(-1), app(3), "Factory and quality teams needed live visualization for yield, defect rate, station performance, equipment downtime, product metric drift, and alert conditions.", "React or BI dashboard, live metric feed, refresh SLA, defect drilldown, station filter, and alert evidence", "quality assurance teams could see production risk while work was still in progress"),
            ("REST API and WebSocket quality event integration", app(1), app(4), "Factory applications and equipment feeds exposed quality events through APIs and near-real-time channels that needed reliable ingestion and monitoring.", "REST API contract, WebSocket event sample, schema validation, retry handling, event latency metric, and failure replay note", "near-real-time quality signals became usable in pipelines and dashboards"),
            ("Equipment and product field data failure analysis", app(4), app(2), "Field product signals and equipment logs needed to be connected to production history to understand failure modes and customer-impact patterns.", "source-to-target mapping, serial-number join logic, failure grouping query, dashboard view, and investigation note", "product quality and customer experience analysis used connected factory and field data"),
            ("Quality data warehouse model for engineering users", app(-1), app(0), "Quality engineers needed curated warehouse tables with stable definitions for defects, yields, inspections, stations, equipment, products, and field feedback.", "conceptual/logical model, fact and dimension table design, data dictionary, dbt or SQL model, and consumer signoff", "engineers and analysts could use common definitions for quality reporting"),
            ("Pipeline monitoring for manufacturing quality SLAs", app(-1), app(1), "Quality dashboards and automated production controls needed reliable pipeline execution with clear freshness, completeness, and failure alerts.", "pipeline SLA, freshness monitor, row-count check, failed-job alert, retry policy, and runbook", "manufacturing quality data issues became visible before decisions were made on stale data"),
            ("Cross-functional quality engineering analysis workflow", app(0), app(-1), "Data engineers, quality engineers, manufacturing engineers, and operations users needed one process for requirements, analysis, validation, dashboard review, and production support.", "requirements notes, engineering question log, analysis notebook, validation checklist, dashboard review, and support handoff", "data work connected directly to production quality decisions"),
        ],
        ("Data Platform Engineer", "Telecom / Media / Communications"): [
            ("Broadband customer and service data warehouse", app(0), app(2), "Customer, billing, subscription, service-order, outage, ticketing, network, and field-service data needed governed ingestion into warehouse and lake layers.", "ADF/Databricks or Airflow pipeline, source mapping, warehouse model, row-count reconciliation, and dashboard-ready dataset", "business and IT teams had trusted broadband operational datasets"),
            ("MDM customer and service-address golden record", app(0), app(1), "Customer, account, service address, product subscription, device, and network asset records needed standard matching, deduplication, survivorship, and stewardship rules.", "MDM rules, match/merge logic, golden-record table, survivorship decision, exception queue, and steward review", "master data became more consistent across billing, CRM, network, and reporting systems"),
            ("Master data repository standards and governance", app(-1), app(0), "The organization needed repeatable MDM standards for identifiers, ownership, definitions, quality rules, access, and lifecycle management.", "master-data standard, data dictionary, ownership matrix, quality threshold, lineage note, and governance review evidence", "MDM became an operating practice rather than a one-time cleanup"),
            ("Legacy billing and CRM data migration", app(1), app(3), "Legacy system data needed migration into new platforms without losing customer, account, service, invoice, payment, ticket, or device history.", "migration mapping, transformation rule, reconciliation count, rejected-record report, cutover checklist, and rollback plan", "legacy migration became auditable and safer for business operations"),
            ("Conceptual and logical broadband data model", app(-1), app(2), "Analysts and engineers needed visual models for customer, account, subscription, service location, plan, invoice, payment, modem/device, outage, ticket, and technician visit.", "conceptual model, logical model, ERD, flowchart, entity definitions, and stakeholder signoff", "business and IT teams could discuss data structure with the same vocabulary"),
            ("Digital media audience event pipeline", app(0), app(2), "A digital media portfolio needed scalable pipelines for page views, sessions, content engagement, newsletter clicks, video events, and reader behavior across high-traffic brands.", "frontend event schema, ingestion job, warehouse table, sessionization query, freshness monitor, and audience dashboard", "audience behavior became available for product, editorial, and analytics teams"),
            ("Advertising and affiliate revenue analytics model", app(1), app(-1), "Advertising, affiliate sales, and editorial stakeholders needed trusted datasets connecting content, traffic, campaign, partner, click, conversion, and revenue signals.", "source-to-target mapping, revenue fact table, attribution rule, dbt/SQL model, BI metric definition, and reconciliation output", "monetization reporting became easier to trust across business teams"),
            ("React and TypeScript frontend data capture contract", app(0), app(3), "Data-generating frontend applications needed clean event contracts so React and TypeScript instrumentation produced reliable analytics events.", "tracking plan, TypeScript event interface, payload example, validation rule, missing-event check, and stakeholder signoff", "frontend-generated data became easier to validate before it entered analytics pipelines"),
            ("Machine-learning-ready audience insight dataset", app(-1), app(5), "Personalization, recommendation, audience segmentation, and revenue optimization work needed clean features from reader behavior, content metadata, campaign data, and affiliate activity.", "feature dataset, entity grain definition, data quality checks, training snapshot, and ML consumer handoff", "ML and analytics teams received more reliable audience insight data"),
            ("Data quality improvement for customer operations", app(3), app(0), "Operational reporting suffered from duplicate customers, invalid addresses, missing device identifiers, stale service status, and inconsistent product codes.", "duplicate check, address validation rule, null check, freshness monitor, invalid-code report, and data quality dashboard", "customer and service reporting became more reliable"),
            ("Data orchestration and MDM workflow monitoring", app(-1), app(4), "Pipeline and MDM workflows needed scheduling, dependency management, alerts, retries, and clear failure ownership.", "orchestration DAG, MDM workflow status, retry policy, alert route, failure ticket, and runbook", "workflow failures became easier to detect and recover"),
            ("Customer segmentation and data mining dataset", app(2), app(-1), "Marketing, operations, and leadership needed segmentation for customer value, churn risk, service adoption, outage impact, and broadband product usage.", "segmentation query, feature dataset, cohort definition, BI output, and stakeholder explanation", "customer analytics became actionable without bypassing governance"),
            ("BI semantic layer for telecom reporting", app(-1), app(5), "Power BI, Tableau, Qlik, or Looker users needed stable measures and governed datasets for customer, revenue, outage, service, and field operations reporting.", "semantic model, metric definition, dashboard extract, access rule, and refresh validation", "BI users received trusted metrics from governed data products"),
            ("Enterprise data strategy and documentation pack", app(-1), app(0), "Business and IT teams needed a documented data strategy aligned to business processes, warehouse standards, integration patterns, and ownership boundaries.", "data strategy document, flowchart, model standard, pipeline documentation, governance cadence, and roadmap", "data platform work became easier to maintain and communicate remotely"),
            ("Editorial content performance warehouse mart", app(2), app(0), "Editors and business teams needed fast reporting on article performance, topic trends, referral channels, search traffic, engagement, and monetization impact.", "content dimension, engagement fact table, channel mapping, query optimization evidence, and dashboard extract", "editorial decisions could use consistent performance data"),
            ("Large-scale media warehouse performance tuning", app(-1), app(4), "Large audience, content, advertising, and affiliate datasets caused slow dashboards and expensive queries when storage layout and transformations were not optimized.", "BigQuery/Redshift/Snowflake query plan, clustering or partitioning note, incremental model, before/after runtime, and cost evidence", "warehouse queries became faster and more cost efficient"),
            ("Cross-functional media data requirements workflow", app(3), app(-1), "Engineering, editorial, advertising, affiliate, product, and analytics teams needed a shared process for requirements, definitions, dashboard changes, and data availability.", "requirements brief, metric definition, dashboard enhancement request, data availability note, and communication summary", "technical data work translated into business-facing media insights"),
            ("Media data governance and reliability controls", app(-1), app(1), "Audience and revenue reporting needed monitoring, validation, access controls, and governance so data remained accurate and secure for internal decision-making.", "data quality monitor, schema validation, access policy, lineage note, incident runbook, and governance review", "data products became more reliable for advertising, affiliate, and editorial stakeholders"),
        ],
        ("Data Platform Engineer", "Technology / SaaS / Enterprise Software"): [
            ("Production-grade customer data pipeline service", app(0), app(1), "A customer-facing SaaS platform needed reliable ingestion, transformation, validation, and publication of large datasets for product workflows and analytics.", "Python service, SQL transform, pipeline job, validation checks, retry logic, monitoring, and deployment record", "customer-facing data delivery became reliable and supportable"),
            ("AWS PySpark distributed processing pipeline", app(-1), app(2), "Large SaaS datasets needed scalable processing with PySpark, partitioning, efficient joins, job metrics, and recoverable failures on AWS.", "PySpark job, S3 layout, Glue/EMR or Databricks run, partition strategy, Spark UI metric, and failure recovery note", "large transformations became faster and easier to operate"),
            ("Data observability monitors for freshness and volume", app(-1), app(0), "Customers and internal teams needed early warning when data was stale, missing, duplicated, schema-shifted, or anomalous.", "freshness monitor, volume threshold, schema check, null/duplicate test, anomaly alert, and incident note", "data reliability issues were detected before customers reported them"),
            ("Schema and lineage incident triage workflow", app(1), app(3), "Downstream consumers broke when source schema changed or lineage was unclear across pipeline, warehouse, and customer-facing outputs.", "schema diff, lineage view, impacted-consumer list, rollback or compatibility note, and communication format", "schema incidents became easier to diagnose and communicate"),
            ("Backend service for pipeline execution metadata", app(0), app(-1), "Product and engineering teams needed APIs or services to trigger jobs, track run status, expose metadata, and show pipeline results to users.", "API contract, job metadata table, run-status endpoint, error payload, auth rule, and service log", "pipeline execution became a product-supported backend workflow"),
            ("ML platform dataset and feature pipeline support", app(2), app(4), "ML workflows needed validated feature datasets, batch inference inputs, model output tables, and reproducible training data snapshots.", "feature pipeline, input dataset validation, model-output table, batch run log, and reproducibility note", "ML platform teams had cleaner, production-ready data inputs"),
            ("Customer-facing data incident runbook", app(1), app(-1), "Data delays or bad outputs affected customers and required urgent triage, impact communication, fix validation, and prevention.", "incident timeline, freshness/volume evidence, affected customer scope, mitigation note, validation output, and post-incident action", "customer-impacting data issues were handled with evidence and urgency"),
            ("Simple flexible architecture scaling path", app(-1), app(0), "A fast-moving startup needed an architecture that started simple but could scale as data volume, customer count, and product use cases grew.", "architecture decision record, service boundary, storage layout, orchestration choice, scaling trigger, and tradeoff note", "the platform could ship quickly without blocking future scale"),
            ("Python SQL code quality and deployment workflow", app(3), app(2), "Pipeline and backend service changes needed review, tests, version control, and production deployment evidence.", "Git pull request, unit/data test, SQL review, CI output, deployment note, and rollback plan", "data code shipped faster while preserving production confidence"),
            ("Cost-aware AWS data platform optimization", app(-1), app(5), "Growing AWS data workloads needed tuning for storage layout, compute choice, query scan cost, retries, and job runtime.", "cost baseline, S3 partitioning note, Spark tuning change, warehouse query metric, and before/after evidence", "pipeline performance and platform cost became measurable"),
        ],
        ("DevOps Engineer", "Healthcare / Health Insurance"): [
            ("Azure Databricks notebook and job promotion pipeline", app(-1), app(0), "Healthcare analytics teams needed a controlled Azure DevOps pipeline to promote Databricks notebooks, jobs, and workflows across development, staging, and production without exposing PHI or breaking clinical reporting schedules.", "Azure DevOps pipeline YAML, Databricks Repos to Azure Repos Git integration, Databricks CLI or REST API deployment task, notebook promotion checklist, job run log, and rollback note", "Databricks analytics releases became repeatable, auditable, and safer for clinical and care-management data workflows"),
            ("AI-assisted deployment diagnosis and canary rollback", app(0), app(1), "Healthcare DevOps teams needed safer release diagnosis when Azure DevOps pipelines, Databricks jobs, Kubernetes workloads, application metrics, and rollback signals disagreed during production change windows.", "canary deployment plan, baseline versus canary metric comparison, Azure DevOps run log, agent diagnosis note, rollback trigger, pipeline block reason, human approval record, and support summary", "release failures became easier to diagnose and roll back without hiding accountability"),
            ("Agentic DevOps guardrails and approval workflow", app(1), app(3), "AI-assisted operations needed strict healthcare-safe boundaries so an agent could inspect logs, pipelines, runbooks, and deployment state without exposing PHI or changing production outside approval.", "agent action policy, allowed read-only commands, denied destructive actions, service-account RBAC, secret redaction rule, approval workflow, audit log sample, and escalation runbook", "AI-assisted operations became safer because the agent could investigate broadly but production changes stayed controlled"),
            ("Databricks secrets and Key Vault release controls", app(-1), app(1), "Databricks jobs needed secure access to tokens, storage paths, service principals, and environment configuration without hardcoding secrets in notebooks or pipeline variables.", "Azure Key Vault reference, Databricks secret scope, DevOps variable group, managed identity or service principal note, access test, and audit log", "secret handling became consistent across healthcare data platform releases"),
            ("PySpark notebook validation and multi-environment testing", app(-1), app(2), "PySpark and R notebooks needed validation before promotion so schema changes, missing parameters, package drift, or bad source data did not interrupt healthcare analytics jobs.", "unit or smoke test run, sample notebook execution, parameter file, schema validation output, failed-run example, and QA signoff", "notebook deployments had evidence before production promotion"),
            ("Databricks job failure troubleshooting runbook", app(-1), app(3), "Pipeline failures, failed notebook cells, cluster startup issues, permission errors, and deployment inconsistencies needed a clear troubleshooting path for development, QA, and production environments.", "Databricks job run output, Azure DevOps pipeline log, cluster event log, workspace permission check, retry decision, and support runbook", "support teams could triage Databricks failures without guessing ownership"),
            ("Healthcare data platform DevOps operating model", app(0), app(-1), "DevOps work in the healthcare data platform needed clean collaboration with Data Engineers, Platform teams, security, and analytics users while avoiding direct exposure to production PHI.", "ownership matrix, environment map, PHI-safe validation rule, release approval, data engineer handoff note, and production support contact map", "the team had a defensible operating model for Azure Databricks delivery"),
            ("Azure DevOps release evidence for regulated analytics", app(-1), app(4), "Clinical, claims, and care-management analytics workflows needed deployment evidence, approvals, run history, and configuration traceability for regulated healthcare operations.", "pull request, Azure Repos branch policy, pipeline run, approval record, Databricks workflow version, Key Vault access note, and audit-ready release summary", "analytics releases had stronger traceability for internal review and support"),
        ],
        ("DevOps Engineer", "Technology / SaaS / Enterprise Software"): [
            ("AI-assisted deployment diagnosis and canary rollback", app(0), app(1), "A high-scale SaaS platform needed faster diagnosis when CI/CD, canary metrics, Kubernetes events, logs, and customer-impact signals showed subtle regression during rollout.", "canary deployment plan, baseline versus canary metric comparison, pipeline log, agent diagnosis note, rollback trigger, customer-impact check, human approval record, and incident summary", "release failures became easier to diagnose and roll back without hiding accountability"),
            ("Agentic DevOps guardrails and approval workflow", app(1), app(3), "Autonomous operations could help a global platform, but agents needed policy boundaries before touching Kubernetes, Terraform, secrets, cloud APIs, or production deployment workflows.", "agent action policy, allowed read-only commands, denied destructive actions, service-account RBAC, approval workflow, audit log sample, and escalation runbook", "AI-assisted operations became safer because the agent could investigate broadly but production changes stayed controlled"),
            ("Hybrid AWS GCP and data center operations model", app(0), app(-1), "A global SaaS platform needed one operating model across AWS, GCP, and data center workloads so production incidents, deployments, capacity changes, and support escalations could be handled consistently.", "hybrid architecture map, environment inventory, ownership matrix, network dependency notes, deployment path, and escalation runbook", "hybrid operations became easier to explain and support across cloud and data center boundaries"),
            ("Global autoscaling and fleet automation", app(0), app(2), "The platform ran thousands of machines collecting, processing, and querying customer data across many regions and needed automated scaling without manual intervention.", "autoscaling policy, provisioning script, Terraform or Ansible run, capacity dashboard, scale event log, and rollback note", "global capacity changes became measurable, repeatable, and safer during traffic growth"),
            ("In-house monitoring and maintenance software", app(1), app(0), "Operations teams needed internal tools to monitor performance, detect data-availability issues, deploy code, provision machines, and reduce manual production checks.", "monitoring service design, health-check output, maintenance job log, alert route, dashboard screenshot, and support handoff", "production visibility improved beyond basic infrastructure dashboards"),
            ("Big-data SaaS production troubleshooting", app(-1), app(1), "A data-heavy SaaS platform needed fast diagnosis when data collection, crunching, querying, MySQL, NoSQL, or customer-facing performance degraded.", "Linux diagnostic output, query or database metric, processing lag dashboard, incident timeline, root-cause note, and recovery validation", "data availability and performance incidents had a clearer evidence path"),
            ("Fortune 500 customer-impact support readiness", app(3), app(4), "Enterprise customers depended on the platform for business-critical content and SEO workflows, so support needed impact language, uptime evidence, and fast resolver routing.", "customer-impact matrix, uptime/SLA dashboard, support escalation notes, incident communication format, and post-incident action list", "customer-facing incidents became easier to communicate and prevent"),
            ("SaaS release and provisioning toolchain", app(2), app(5), "Engineering needed reliable tools to deploy code, provision machines, automate maintenance, and validate releases for a continuously delivered SaaS platform.", "CI/CD pipeline run, deploy script, provisioning playbook, validation output, change record, and rollback evidence", "release and provisioning work became repeatable across product teams"),
        ],
        ("Cloud Platform Engineer", "Technology / SaaS / Enterprise Software"): [
            ("Agentic AI platform engineering workflow", app(0), app(-1), "Platform engineering teams needed an agent-aware workflow that could listen to CI/CD events, read logs and quality signals, reason about failures, propose remediation, and open a fix pull request or escalate with structured context while preserving enterprise governance.", "agent-aware platform architecture, GitHub Actions event trigger, LLM tool boundary, reasoning log, quality-gate report, fix PR example, escalation format, and governance checklist", "AI-assisted platform operations reduced manual diagnosis while keeping human oversight and auditability"),
            ("AI-powered CI/CD diagnosis loop", app(2), app(1), "Pipeline failures consumed engineering time because logs, tests, dependency errors, security scan output, and deployment context were reviewed manually across multiple tools.", "pipeline event payload, failure context bundle, log summary, root-cause hypothesis, remediation proposal, and human approval record", "CI/CD troubleshooting became faster and more structured without giving the agent unlimited production authority"),
            ("Intelligent release readiness quality gate", app(0), app(3), "Release decisions needed more than a pass/fail pipeline because teams also cared about test coverage, performance, security, reliability, cost, and rollback readiness.", "multi-signal quality gate, test coverage signal, performance metric, security scan result, cost note, release rationale report, and approval evidence", "release decisions became more explainable and audit-ready"),
            ("Self-healing versus escalation boundary", app(1), app(4), "Autonomous remediation needed strict boundaries so the platform could safely retry, revert, or propose fixes while escalating risky changes to humans.", "action policy, allowed self-heal list, denied action list, escalation rule, rollback guardrail, incident ticket example, and runbook", "agentic workflows became safer because the team knew which actions required human approval"),
            ("Agent runtime tool and memory governance", app(-1), app(2), "AI agents needed controlled access to tools, APIs, repositories, logs, tickets, and memory so they could reason with context without leaking secrets or changing systems outside policy.", "tool registry, API permission scope, memory retention rule, secret redaction rule, audit log, and review checklist", "agent context became useful for diagnosis while remaining governable"),
            ("Developer productivity platform agent rollout", app(5), app(0), "Platform teams needed a practical rollout path for an agent that helps developers understand failures, deployment readiness, and next actions without replacing engineering ownership.", "pilot plan, onboarding guide, developer feedback, adoption metric, failure examples, support model, and improvement backlog", "developers received faster platform feedback while platform engineers retained control of standards and guardrails"),
        ],
        "AIOps Engineer": [
            ("Alert correlation rollout", app(1), app(3), "Support teams received separate alerts for application errors, infrastructure symptoms, deployment events, and duplicate tickets.", "correlation rules, alert grouping evidence, incident ticket examples, suppression logic, and before/after alert count", "alert noise reduced and incident ownership became clearer"),
            ("Anomaly detection baseline", app(0), app(-1), "Teams needed early detection for abnormal latency, error spikes, traffic drops, queue lag, and unusual support ticket volume.", "baseline thresholds, anomaly model settings, dashboard panels, false-positive review, and escalation notes", "abnormal behavior was detected before every issue became a major incident"),
            ("Deployment-event correlation", app(0), app(1), "Incidents were hard to connect to recent releases, feature flags, configuration changes, or infrastructure events.", "release markers, event timeline, deployment tags, incident comparison notes, and RCA evidence", "incident review moved from guessing to event-backed investigation"),
            ("ServiceNow ticket enrichment", app(1), app(3), "Tickets lacked logs, dashboard links, owners, dependency status, and recent-change context.", "ticket enrichment fields, ServiceNow examples, owner mapping, runbook links, and resolver-group routing", "support handoff became faster and more actionable"),
            ("Operational knowledge graph", app(2), app(-1), "Repeated incidents did not reuse known symptoms, resolver groups, application dependencies, or historical RCA notes.", "knowledge base entries, symptom-to-owner mapping, dependency references, RCA tags, and search examples", "repeat issues were routed with prior knowledge instead of starting from zero"),
            ("Noise reduction and alert governance", app(4), app(0), "Low-value alerts were hiding business-impacting symptoms during high-volume production windows.", "alert inventory, severity rules, dedupe policy, escalation matrix, and muted-alert review", "on-call teams saw fewer duplicate alerts and more business-relevant signals"),
        ],
        "DevOps Engineer": [
            ("AI-assisted deployment diagnosis and canary rollback", app(0), app(1), "Modern DevOps teams are beginning to use AI agents to reason over pipeline logs, Kubernetes events, release markers, metrics, and runbooks during risky deployments, but production rollback still needs guardrails and human-visible evidence.", "canary deployment plan, baseline versus canary metric comparison, agent diagnosis note, rollback trigger, pipeline block reason, Slack or ticket summary, and human approval record", "release failures became easier to diagnose and roll back without hiding accountability"),
            ("Agentic DevOps guardrails and approval workflow", app(1), app(3), "Autonomous operations can become risky when an agent has broad access to Terraform, Kubernetes, secrets, or CI/CD actions without policy boundaries, least privilege, audit logs, and approval gates.", "agent action policy, allowed read-only commands, denied destructive actions, service-account RBAC, approval workflow, audit log sample, and escalation runbook", "AI-assisted operations became safer because the agent could investigate broadly but change production only through controlled approval"),
            ("S3 event to Lambda processing workflow", app(0), app(1), "File uploads needed automatic validation and processing without a manual operator watching the bucket.", "S3 event rule, suffix filter, Lambda invocation log, failed-event retry note, and output validation", "file-driven processing became event-based and easy to prove"),
            ("SQS decoupled deployment worker", app(2), app(0), "A bursty API or batch process needed reliable downstream processing without overloading the worker service.", "SQS queue configuration, dead-letter queue, Lambda/worker log, retry evidence, and queue depth dashboard", "spiky workloads became safer and easier to recover"),
            ("Secrets and identity rotation path", app(3), app(0), "Applications had credentials or configuration spread across environments, which created release failures and audit risk.", "Secrets Manager or Key Vault reference, rotation notes, environment matrix, least-privilege policy, and validation output", "secret handling became repeatable across releases"),
            ("API Gateway and serverless release path", app(1), app(4), "Teams needed a controlled way to expose APIs, route requests, validate deployments, and roll back failed changes.", "API route map, deployment stage, smoke test result, CloudWatch or Azure Monitor log, and rollback note", "API releases had clearer promotion and recovery steps"),
            ("Container and Helm deployment standardization", app(0), app(3), "Kubernetes releases needed consistent images, charts, values, namespace settings, probes, and rollback steps.", "Dockerfile update, image scan, Helm values, release history, smoke test result, and rollback command", "deployment behavior became consistent across environments"),
            ("Backup disaster recovery and cloud roadmap evidence", app(4), app(-1), "Cloud DevOps work needed clearer operational administration, backup and restore expectations, disaster recovery validation, reference architecture decisions, and cloud technology roadmap language for application teams and client stakeholders.", "backup policy, restore test result, DR runbook, RTO/RPO note, reference architecture diagram, cloud roadmap decision log, and security/performance monitoring evidence", "backup, disaster recovery, and cloud architecture strategy became easier to defend in consulting and infrastructure interviews"),
            ("Production change evidence package", app(5), app(2), "Production changes needed approval gates, deployment output, validation, monitoring, and support handoff.", "change ticket, approval record, pipeline run, validation screenshot, monitoring link, and runbook update", "release confidence increased and post-release confusion dropped"),
        ],
        "Cloud Platform Engineer": [
            ("Private service access and endpoint design", app(0), app(1), "Workloads needed private access to storage, APIs, databases, and platform services without opening unnecessary internet paths.", "VPC/VNet route table, private endpoint or gateway endpoint, security group/NSG rule, connectivity test, and diagram", "network access became private, explainable, and supportable"),
            ("Multi-AZ application scaling foundation", app(2), app(0), "A customer-facing application needed resilient load balancing, autoscaling, and highly available database access during variable traffic.", "load balancer diagram, autoscaling policy, health check result, failover note, and capacity dashboard", "application infrastructure became more resilient under changing load"),
            ("Cloud governance and tagging controls", app(3), app(4), "Teams needed resource ownership, cost center, environment, and compliance reporting across subscriptions/accounts/projects.", "tag policy, Azure Policy or AWS Config rule, non-compliance report, remediation note, and cost report", "platform resources became easier to govern and report"),
            ("Managed identity and least-privilege platform access", app(1), app(-1), "Applications needed access to storage, queues, databases, and secrets without local credentials.", "IAM role or managed identity assignment, policy scope, access test, secret reference, and audit log", "application access became safer with less credential handling"),
            ("Hybrid migration network landing zone", app(4), app(2), "On-premises workloads needed a controlled path into cloud environments for data migration, application modernization, and operations.", "VPN/ExpressRoute/Direct Connect diagram, route validation, DNS note, firewall rule, and migration runbook", "migration work had a clear network and ownership boundary"),
            ("Cost and storage lifecycle guardrails", app(-1), app(5), "Storage and compute usage grew without lifecycle policies, right-sizing checks, or owner visibility.", "lifecycle policy, Cost Explorer or Azure cost report, storage tier decision, owner tag, and exception list", "cost control became part of the platform operating model"),
        ],
        "Data Platform Engineer": [
            ("Glue crawler and catalog onboarding", app(-1), app(0), "New source files and database tables needed to be discoverable daily without manually updating schemas for analysts.", "Glue crawler or Data Catalog change, IAM role, schedule, table schema, partition note, and query validation", "source onboarding became repeatable and queryable"),
            ("Lake governance with row-level access", app(-1), app(1), "Analysts needed country, department, or customer-segment restrictions while using a shared data lake.", "Lake Formation or warehouse row filter, access policy, test user result, exception list, and governance note", "data access became centralized without creating many duplicate tables"),
            ("Athena and BigQuery performance modernization", app(-1), app(2), "Dashboard and ad hoc queries slowed as CSV/raw files grew and common predicates were not optimized.", "Parquet conversion, compression choice, partition layout, workgroup/query setting, and before/after query evidence", "query cost and runtime improved with visible evidence"),
            ("Streaming analytics dashboard pipeline", app(0), app(-1), "Operations teams needed low-latency views of sensor, event, transaction, or application activity.", "Kinesis/Pub/Sub/Dataflow/Flink flow, sink table, dashboard, latency metric, and replay note", "near-real-time analytics became supportable"),
            ("Warehouse sharing and API access pattern", app(-1), app(3), "A BI or application team needed data from the warehouse without interrupting critical ETL workloads.", "Redshift data sharing/Data API or Synapse/BigQuery access pattern, consumer permission, query result, and workload isolation note", "analytics consumers received data without stressing producer workloads"),
            ("Incremental migration into the data lake", app(-1), app(4), "Large on-premises or operational datasets needed recurring incremental transfer into cloud storage with minimal downtime.", "DMS/DataSync/Storage Transfer job, schedule, change capture evidence, reconciliation count, and failure recovery note", "migration became automated and auditable"),
        ],
        "MLOps / AI Platform Engineer": [
            ("SageMaker or Vertex AI training pipeline", app(-1), app(0), "Model work needed repeatable data extraction, feature preparation, training, evaluation, approval, and model artifact creation.", "pipeline run, experiment record, metrics report, approval note, and model artifact", "model delivery became traceable from data to registry"),
            ("Feature parity and drift guardrail", app(-1), app(1), "Training and inference could diverge when features changed without freshness, lineage, and validation evidence.", "feature validation, offline/online parity note, freshness check, drift metric, and rollback plan", "feature risk became visible before prediction quality dropped"),
            ("Model registry and staged promotion", app(-1), app(2), "Teams needed controlled model version promotion across dev, stage, and production endpoints.", "registry version, stage transition, approval record, deployment evidence, champion/challenger note, and rollback model", "model promotion became auditable and reversible"),
            ("Batch inference SLA and output validation", app(-1), app(3), "Batch predictions needed SLA tracking, failed-run recovery, and output completeness checks.", "batch run log, output count check, SLA alert, failure ticket, and recovery note", "prediction delivery became supportable"),
            ("Real-time inference endpoint operations", app(0), app(-1), "Online prediction endpoints needed secure access, autoscaling, latency monitoring, and controlled rollback.", "endpoint config, autoscaling setting, latency chart, IAM/access policy, canary test, and rollback evidence", "real-time inference became production-ready"),
            ("Model monitoring and retraining trigger", app(-1), app(4), "Model quality needed monitoring after release, not only during training.", "data drift metric, prediction drift dashboard, alert policy, RCA note, and retraining trigger", "model risk was detected after production deployment"),
        ],
        "Site Reliability / AIOps Engineer": [
            ("CloudWatch or Azure Monitor alert strategy", app(0), app(1), "Teams needed actionable alerts from logs, metrics, system events, and security signals instead of noisy dashboards.", "alert rule, query, action group or notification route, dashboard screenshot, and before/after alert review", "alerts became tied to real support action"),
            ("SRE/AIOps production control tower", app(0), app(1), "Production support needed one operating view that connected CI/CD status, Kubernetes health, logs, traces, incidents, security signals, recent releases, cloud cost anomalies, and rollback or scale actions instead of forcing responders to jump across isolated tools during an outage.", "control-tower dashboard layout, SLO panel, Kubernetes workload health, deployment marker, log and trace links, incident ticket, security alert context, rollback or scale decision note, and RCA action item", "production failures became easier to understand, route, recover, and explain in interviews"),
            ("Agent-assisted incident triage with human-approved remediation", app(0), app(1), "SRE teams needed faster triage when alerts, traces, pod health, recent deployments, and runbooks were scattered across tools, but remediation still required approval for production-impacting action.", "agent triage transcript, RAG runbook reference, kubectl/log query output, suspected root-cause note, recommended action, approval record, validation metric, and incident summary", "incident responders received faster context while keeping rollback, restart, secret rotation, and traffic changes under human control"),
            ("Network path troubleshooting workflow", app(1), app(3), "Hybrid and cloud workloads had intermittent connectivity problems that required packet, route, firewall, and endpoint evidence.", "Network Watcher/IP flow or VPC reachability result, route table, NSG/security group, packet decision, and escalation note", "network incidents became evidence-based"),
            ("SLO and user-journey reliability dashboard", app(0), app(2), "Teams needed reliability targets tied to user journeys rather than only raw infrastructure metrics.", "SLI query, SLO target, error budget dashboard, alert rule, and review note", "reliability discussions became measurable"),
            ("Autoscaling and load-balancer health response", app(2), app(4), "Variable traffic caused failures when scaling signals, load balancer health checks, or database read capacity were not aligned.", "autoscaling policy, load balancer target health, capacity chart, failover note, and incident timeline", "capacity incidents had clearer diagnosis and prevention"),
            ("Incident response and RCA modernization", app(1), app(-1), "Incident calls lacked consistent roles, timeline, impact statement, evidence collection, and recovery validation.", "incident format, timeline, owner assignments, recovery checklist, RCA, and runbook update", "incident handling became calmer and faster"),
            ("OpenTelemetry and dependency trace rollout", app(0), app(5), "Cross-service failures were hard to debug without request-level traces and dependency visibility.", "trace instrumentation, span examples, latency breakdown, dashboard, and troubleshooting runbook", "dependency bottlenecks became visible"),
        ],
    }
    default = [
        (f"{focus[0].title()} implementation", app(0), app(1), f"Teams needed practical delivery around {focus[0]} for {domain} systems.", f"{focus[0]} design notes, implementation evidence, validation output, and support handoff", f"{focus[0]} became repeatable across teams"),
        (f"{focus[1].title()} standardization", app(1), app(2), f"{focus[1]} differed across environments and created support gaps.", f"{focus[1]} standard, config evidence, review notes, and exception list", f"{focus[1]} became consistent across environments"),
        (f"{focus[2].title()} rollout", app(2), app(3), f"Multiple teams needed a controlled rollout for {focus[2]}.", f"{focus[2]} rollout plan, Jira group, test evidence, and readiness checklist", f"{focus[2]} adoption expanded without losing governance"),
        (f"{focus[3].title()} support model", app(3), app(4), f"Support teams needed clear steps when {focus[3]} failed.", f"{focus[3]} runbook, alert examples, owner map, and RCA notes", f"{focus[3]} support became easier to route"),
        (f"{focus[4].title()} governance", app(4), app(-1), f"Leads needed evidence and approval around {focus[4]}.", f"{focus[4]} approval evidence, dashboard, validation notes, and audit trail", f"{focus[4]} became reviewable"),
        (f"{role_name} cross-team rollout", app(0), app(-1), f"The {role_name} work needed adoption across multiple {domain} teams.", "onboarding notes, examples, glossary, support guide, and sprint evidence", "teams could reuse the pattern without waiting for one person"),
    ]
    selected = role_themes.get((role_name, domain), role_themes.get(role_name, default))
    automation = _automation_reference_use_cases(role_name, domain, applications)
    shared = [
        ("Sprint evidence packaging", app(0), app(-1), f"{domain} delivery needed clear grouping from Jira story to use case to interview evidence.", "Jira group, acceptance criteria, design note, implementation artifact, validation record, and support handoff", "sprint work became easy to explain as delivered project work"),
        ("Cross-team ownership matrix", app(2), app(10), "Incidents bounced between application, platform, data, database, network, security, and vendor teams without a clear resolver path.", "ownership matrix, escalation route, support contact map, and resolver notes", "handoffs became evidence-based"),
        ("Production readiness review", app(5), app(2), "Release candidates needed consistent readiness review before production change windows.", "readiness checklist, risk notes, approval record, validation evidence, and rollback criteria", "production changes had clearer go/no-go decisions"),
    ]
    rows = [*selected, *automation, *shared]
    return [
        {"theme": theme, "primary": primary, "secondary": secondary, "problem": problem, "artifact": artifact, "outcome": outcome}
        for theme, primary, secondary, problem, artifact, outcome in rows
    ]


def _automation_reference_use_cases(role_name: str, domain: str, applications: list[str]) -> list[tuple[str, str, str, str, str, str]]:
    def app(index: int) -> str:
        if not applications:
            return f"{domain} platform"
        return applications[index % len(applications)]

    if role_name == "DevOps Engineer":
        return [
            ("Shell and Python release automation toolkit", app(0), app(1), "Release teams needed reusable automation for Jenkins triggers, Kubernetes image updates, Terraform plan/apply wrappers, API smoke tests, rollback scripts, and release evidence collection.", "shell scripts, Python helper script, Jenkins trigger output, kubectl rollout status, Terraform plan log, API status check, rollback note, and runbook", "routine release operations became repeatable, explainable, and easier to validate"),
            ("Security and cleanup automation for delivery pipelines", app(1), app(2), "Pipelines needed lightweight checks for container image vulnerabilities, disk usage, stale Docker resources, log cleanup, SSL renewal, and open-port visibility before production handoff.", "Trivy scan result, disk usage output, docker prune report, log-rotation result, certificate renewal note, open-port check, and exception ticket", "deployment hygiene improved before issues reached production"),
        ]
    if role_name == "Cloud Platform Engineer":
        return [
            ("Cloud operations automation script pack", app(0), app(1), "Platform teams needed practical automation for EC2 or VM provisioning, DNS updates, S3/file transfer, CloudFormation or Terraform execution, backup verification, and server lifecycle operations.", "AWS CLI or Azure/GCP CLI output, DNS change record, S3 upload/download proof, IaC run log, backup verification output, and platform runbook", "cloud operations became repeatable with clear evidence and ownership"),
            ("Cost and resource hygiene automation", app(2), app(3), "Shared cloud platforms needed scheduled checks for unused containers/images, disk growth, scaling signals, backup age, resource tags, and daily health reports.", "cleanup script output, disk alert, autoscaling metric, backup age report, cost/tag evidence, and health-report summary", "platform hygiene and cost visibility improved without manual inspection every time"),
        ]
    if role_name == "Site Reliability / AIOps Engineer":
        return [
            ("Scripted health checks and alert evidence", app(0), app(1), "SRE teams needed small scripts for CPU, memory, disk, endpoint health, database service status, log error detection, and Slack or email alerts before building heavier observability automation.", "CPU/disk output, endpoint HTTP status, database service check, grep error result, Slack/email alert example, and incident note", "basic monitoring signals became easier to explain and convert into runbooks"),
            ("Automated log collection and incident report generation", app(1), app(2), "Incident responders needed a repeatable way to collect logs from services or servers, archive them safely, attach them to tickets, and generate a short health report.", "log archive, S3 or storage upload proof, server health report, timestamped incident folder, sanitized evidence note, and RCA attachment", "incident evidence collection became faster and more consistent"),
        ]
    if role_name == "Data Platform Engineer":
        return [
            ("Python data validation and transformation starter kit", app(0), app(1), "Data teams needed beginner automation for file reads, JSON/YAML parsing, SQL row checks, pandas transformations, database connection tests, and migration execution.", "Python script, JSON/YAML sample, SQL row-count/null-check output, pandas before/after file, Alembic or migration log, and data validation note", "data checks became scriptable and easier to discuss in interviews"),
            ("Backup, migration, and data-quality evidence automation", app(2), app(3), "Data pipelines and databases needed repeatable backup verification, schema migration evidence, failed-record checks, and reportable validation output.", "backup integrity result, migration command output, rejected-record sample, freshness check, reconciliation count, and runbook", "data reliability work produced concrete evidence instead of informal claims"),
        ]
    if role_name == "MLOps / AI Platform Engineer":
        return [
            ("Python automation for model service health checks", app(0), app(1), "MLOps teams needed lightweight automation for API health checks, rate-limited requests, container health, Docker Compose startup, and endpoint validation around model services.", "Python requests script, rate-limit handling result, Docker health status, compose up/down output, endpoint response sample, and validation note", "model service checks became repeatable before deeper monitoring and drift work"),
            ("Automation evidence for ML platform operations", app(1), app(2), "AI platform workflows needed practical scripts for reading config, logging results, checking resources, moving artifacts to object storage, and creating simple scheduled jobs.", "config parser output, structured log, psutil resource sample, object storage upload/download proof, scheduled job output, and handoff note", "ML platform operations became easier to prove with safe beginner automation evidence"),
        ]
    return []


def _sprint_delivery_model(role_name: str, domain: str, applications: list[str], focus: list[str]) -> dict[str, Any]:
    return {
        "cadence": "Two-week agile sprints across the delivery lifecycle",
        "summary": [
            "A long-running delivery lifecycle organized through repeated agile sprints.",
            "Typical sprint output: 2-3 completed Jira stories for the role-owned scope.",
            "Every 5-6 related Jira stories forms one use case with design, build, validation, release/support, and evidence.",
            "Training readiness focuses on 10-12 use cases selected from the larger delivery history.",
            "Expected delivery volume: multiple Jira story groups, application tracks, release cycles, and production support cycles.",
        ],
        "projectTracks": [
            {"track": "Application release modernization", "systems": [applications[0], applications[1], applications[2]], "output": f"{focus[0]}, release validation, rollback, and support handoff stories"},
            {"track": "Production support and incident intelligence", "systems": [applications[1], applications[3], applications[-1]], "output": f"{focus[1]}, {focus[2]}, RCA notes, alert/ticket evidence, and runbook stories"},
            {"track": "Operational visibility and governance", "systems": [applications[0], applications[4], applications[-1]], "output": f"{focus[3]}, dashboards, ownership matrix, audit evidence, and readiness review stories"},
            {"track": "Dependency and resilience improvements", "systems": [applications[2], applications[5 % len(applications)], applications[-1]], "output": f"{focus[4]}, dependency checks, failure routing, capacity, and recovery validation stories"},
            {"track": "Knowledge base and cross-team rollout", "systems": [applications[6 % len(applications)], applications[7 % len(applications)], applications[-1]], "output": "documentation, onboarding, glossary, support playbook, and interview evidence stories"},
        ],
    }


def _enrich_delivered_use_cases(use_cases: list[dict[str, Any]], role_name: str, domain: str, profile: DomainProfile, applications: list[str], focus: list[str]) -> list[dict[str, Any]]:
    return [_use_case_detail(use_case, index, role_name, domain, profile, applications, focus) for index, use_case in enumerate(use_cases, start=1)]


def _use_case_detail(use_case: dict[str, Any], index: int, role_name: str, domain: str, profile: DomainProfile, applications: list[str], focus: list[str]) -> dict[str, Any]:
    title = use_case["title"]
    systems = use_case["systemsTouched"]
    primary_system = systems[0] if systems else applications[0]
    secondary_system = systems[1] if len(systems) > 1 else applications[1]
    role_focus = ", ".join(focus[:5]).lower()
    explanation_sections = _textbook_explanation_sections(use_case, role_name, domain, systems, primary_system, role_focus)
    explanation = " ".join(" ".join(section["bullets"]) for section in explanation_sections)
    business_lens = _business_analyst_lens(use_case, index, role_name, domain, profile, primary_system, secondary_system)
    project_manager_lens = _project_manager_lens(use_case, role_name, domain, primary_system, secondary_system, focus)
    qa_test_lens = _qa_test_lens(use_case, role_name, domain, primary_system, secondary_system)
    production_support_lens = _production_support_lens(use_case, role_name, domain, primary_system, secondary_system)
    security_compliance_lens = _security_compliance_lens(use_case, role_name, domain, primary_system, secondary_system)
    data_reporting_lens = _data_reporting_lens(use_case, role_name, domain, primary_system, secondary_system)
    product_owner_lens = _product_owner_lens(use_case, role_name, domain, primary_system, secondary_system)
    implementation_scenario = _implementation_scenario(use_case, index, role_name, domain, primary_system, secondary_system, focus)
    architect_lens = _architect_lens(use_case, role_name, domain, primary_system, secondary_system, focus)
    return {
        **use_case,
        "slug": _slug(title),
        "businessAnalystLens": business_lens,
        "projectManagerLens": project_manager_lens,
        "qaTestLens": qa_test_lens,
        "productionSupportLens": production_support_lens,
        "securityComplianceLens": security_compliance_lens,
        "dataReportingLens": data_reporting_lens,
        "productOwnerLens": product_owner_lens,
        **implementation_scenario,
        "architectLens": architect_lens,
        "textbookExplanation": explanation,
        "textbookSections": explanation_sections,
        "whatToRemember": _use_case_memory_points(use_case, role_name, domain, primary_system, secondary_system, role_focus),
        "jiraStories": _jira_stories(title, role_name, domain, systems, focus),
        "interviewQuestionSet": _use_case_questions(title, role_name, domain, systems, focus, "interview", 10),
        "architectureQuestionSet": _use_case_questions(title, role_name, domain, systems, focus, "architecture", 5),
        "systemDesignQuestionSet": _use_case_questions(title, role_name, domain, systems, focus, "system design", 5),
        "scenarioQuestionSet": _use_case_questions(title, role_name, domain, systems, focus, "scenario", 5),
        "troubleshootingQuestionSet": _use_case_questions(title, role_name, domain, systems, focus, "troubleshooting", 5),
        "workflow": _use_case_workflow(title, role_name, domain, primary_system, secondary_system, focus),
    }


def _use_case_memory_points(use_case: dict[str, Any], role_name: str, domain: str, primary_system: str, secondary_system: str, role_focus: str) -> list[str]:
    title = str(use_case.get("title") or "the use case")
    outcome = str(use_case.get("productionOutcome") or use_case.get("outcome") or use_case.get("interviewStory") or "the workflow had a named owner route, validation result, and recovery note").rstrip(".")
    evidence = _training_as_list(use_case.get("evidenceToExplain"))
    evidence_text = ", ".join(evidence[:3]) if evidence else "implementation evidence, validation output, and support handoff"
    return [
        f"{title} is a {domain} workflow story, not a tool list: {primary_system} is the business-facing anchor and {secondary_system} is the dependency or handoff that proves the flow is enterprise-scale.",
        f"{role_name} ownership sits around {role_focus}; product priority, business rules, feature code, security approval, QA signoff, and production operations remain with the teams named in the owner matrix.",
        f"The functional result is {outcome}. The proof is not a claim; it is visible through {evidence_text}.",
        "A senior explanation connects business trigger, system path, implementation artifact, validation signal, failure behavior, recovery path, and resolver group in one consistent story.",
    ]


def _qa_test_lens(use_case: dict[str, Any], role_name: str, domain: str, primary_system: str, secondary_system: str) -> dict[str, Any]:
    title = use_case["title"]
    evidence = _training_as_list(use_case.get("evidenceToExplain"))
    return {
        "testStrategy": f"QA reads {title} as a business-flow validation problem: prove the normal path, the failed path, the recovery path, and the evidence path across {primary_system} and {secondary_system}.",
        "coverage": [
            f"Happy path: {primary_system} produces the expected system behavior and downstream handoff.",
            f"Failure path: dependency, config, data, permission, deployment, or runtime error is visible and explainable.",
            "Regression path: the change does not break the existing business workflow, dashboard, alert, job, or release path.",
        ],
        "releaseGates": _unique([
            "lower-environment validation",
            "smoke test result",
            "failure-path proof",
            "rollback or recovery evidence",
            *evidence[:3],
        ]),
        "qaAcceptanceCriteria": [
            f"The {domain} flow is testable from trigger to outcome.",
            f"The {role_name} owned change has clear pass/fail evidence.",
            "Known exception paths have screenshots, logs, query output, pipeline output, or monitoring proof.",
        ],
    }


def _production_support_lens(use_case: dict[str, Any], role_name: str, domain: str, primary_system: str, secondary_system: str) -> dict[str, Any]:
    title = use_case["title"]
    evidence = _training_as_list(use_case.get("evidenceToExplain"))
    return {
        "supportModel": f"Production support reads {title} through symptoms, impact, ownership, first checks, escalation route, and recovery proof for {primary_system}.",
        "incidentFlow": [
            "Confirm user, business, job, release, data, or infrastructure impact before changing the system.",
            f"Check {primary_system}, {secondary_system}, recent deployments, logs, metrics, alerts, and dependency status.",
            "Route to application, platform, data, security, vendor, or operations owner using evidence rather than guesswork.",
        ],
        "opsHandoff": _unique([
            "symptom-to-owner mapping",
            "first five checks",
            "rollback/rerun/retry/escalation path",
            "post-change validation signal",
            *evidence[:3],
        ]),
        "supportAcceptanceCriteria": [
            "A support engineer can identify whether the issue is application, platform, data, security, dependency, or release related.",
            "The runbook names the signal, command or dashboard, expected result, next action, and escalation owner.",
        ],
    }


def _security_compliance_lens(use_case: dict[str, Any], role_name: str, domain: str, primary_system: str, secondary_system: str) -> dict[str, Any]:
    evidence = _training_as_list(use_case.get("evidenceToExplain"))
    return {
        "securityControls": [
            f"Access to {primary_system} and {secondary_system} follows least privilege, environment separation, and approval evidence.",
            "Secrets, tokens, keys, service accounts, certificates, and sensitive values are stored and rotated through approved controls.",
            f"{domain} audit expectations are supported through change history, policy results, exception records, and evidence retention.",
        ],
        "complianceEvidence": _unique([
            "IAM/RBAC rule",
            "secret or key reference",
            "policy/scan result",
            "approval or exception record",
            "audit trail",
            *evidence[:3],
        ]),
        "securityAcceptanceCriteria": [
            f"The {role_name} story explains what was protected, who could access it, and how the control was verified.",
            "The use case avoids exposing sensitive data while still preserving enough evidence for troubleshooting and audit review.",
        ],
    }


def _data_reporting_lens(use_case: dict[str, Any], role_name: str, domain: str, primary_system: str, secondary_system: str) -> dict[str, Any]:
    evidence = _training_as_list(use_case.get("evidenceToExplain"))
    return {
        "dataFlow": f"Data/reporting reads the use case as a path from source event or operational state in {primary_system} to evidence consumed through {secondary_system}, dashboard, report, alert, or audit view.",
        "dataQualityChecks": [
            "Source, owner, timestamp, status, and expected count or state are identifiable.",
            "Freshness, completeness, duplicate handling, schema compatibility, or reconciliation is checked where the workflow depends on data.",
            "Reporting output matches the business definition used by BA, product, operations, and leadership stakeholders.",
        ],
        "reportingSignals": _unique([
            "source-of-truth note",
            "lineage or dependency map",
            "freshness/count check",
            "dashboard or report reference",
            "reconciliation evidence",
            *evidence[:3],
        ]),
        "dataAcceptanceCriteria": [
            f"The {domain} story shows where the status, metric, alert, or report value comes from.",
            "A data consumer can trust the evidence because ownership, timing, quality checks, and exception handling are visible.",
        ],
    }


def _product_owner_lens(use_case: dict[str, Any], role_name: str, domain: str, primary_system: str, secondary_system: str) -> dict[str, Any]:
    title = use_case["title"]
    outcome = use_case.get("productionOutcome") or use_case.get("outcome") or f"{primary_system} had a visible validation result, owner route, and recovery note"
    return {
        "productValue": f"Product reads {title} as value delivered to the {domain} capability: {outcome}.",
        "roadmapFit": [
            f"The work protects or improves the user journey, operational journey, support journey, or platform journey around {primary_system}.",
            f"The {role_name} scope is important because it gives the capability a release path, health signal, recovery option, audit/control record, or operations note.",
            f"{secondary_system} is part of the product story because business value depends on the handoff, not only the first application.",
        ],
        "productAcceptanceCriteria": [
            "The result is visible in a KPI, support signal, release metric, incident reduction, audit result, or customer/internal user impact.",
            "The delivered behavior can be described without tool jargon and without changing the engineering facts.",
            "Product ownership shows why this item mattered compared with competing backlog work.",
        ],
        "adoptionSignals": _unique([
            "KPI or user-impact statement",
            "release readiness note",
            "support or user impact signal",
            "KPI/SLA/report evidence",
            "stakeholder signoff",
        ]),
    }


def _project_manager_lens(use_case: dict[str, Any], role_name: str, domain: str, primary_system: str, secondary_system: str, focus: list[str]) -> dict[str, Any]:
    title = use_case["title"]
    evidence = _training_as_list(use_case.get("evidenceToExplain"))
    delivered_scope = _training_as_list(use_case.get("deliveredScope"))
    systems = _training_as_list(use_case.get("systemsTouched"))
    role_focus = ", ".join(focus[:4]).lower()
    return {
        "projectObjective": f"Deliver {title} for {domain} systems so {primary_system} and {secondary_system} have a named owner path, lower-environment result, runbook update, and measurable signal.",
        "scope": [
            f"In scope: role-owned {role_focus}, workflow documentation, validation output, implementation artifact, release note, owner routing, and operational follow-up.",
            "Out of scope: business prioritization, product feature ownership, unrelated application code changes, enterprise security approval ownership, and production operations outside the named owner path.",
        ],
        "stakeholders": _unique([
            "product owner",
            "business analyst",
            "application development lead",
            role_name,
            "QA lead",
            "security reviewer",
            "operations or SRE owner",
            "service desk/support owner",
            "release manager",
        ]),
        "dependencies": _unique([
            primary_system,
            secondary_system,
            *systems[:4],
            "environment access",
            "test data or sample event",
            "approval window",
            "monitoring/logging access",
            "runbook owner",
        ]),
        "milestones": [
            "Scope confirmed with business problem, affected systems, role-owned activity, expected output, and owner path.",
            "Current-state flow, dependencies, risks, and acceptance criteria documented.",
            "Role-owned implementation change completed in lower environment.",
            "Happy-path, failure-path, rollback/recovery, and evidence checks completed.",
            "Release note completed with runbook section, owner routing, and post-change validation result.",
        ],
        "deliveryRisks": [
            {"risk": "Dependency delay", "impact": "Validation or release slips because access, data, downstream owner, or approval is unavailable.", "mitigation": "Track dependency owner, due date, fallback plan, and escalation path."},
            {"risk": "Scope creep", "impact": "The role-owned change expands into product, application, data, security, or operations ownership.", "mitigation": "Maintain explicit in-scope/out-of-scope language and change-control notes."},
            {"risk": "Proof gap", "impact": "The team cannot prove completion, recovery, or user/system impact.", "mitigation": "Define required artifacts before implementation: PR/config diff, job output, dashboard/query result, ticket note, and runbook change."},
        ],
        "statusReporting": [
            f"Status: {title} is tracked by business problem, affected systems, role-owned change, dependency status, risk, validation output, release note, and owner route.",
            "Green means implementation and validation output are complete; yellow means dependency, access, approval, or proof artifact is at risk; red means business flow, release, recovery, or owner route is blocked.",
            "The status update names completed milestone, current blocker, owner, ETA, risk, mitigation, and next proof artifact.",
        ],
        "pmAcceptanceCriteria": [
            "Business objective, affected systems, stakeholders, and role-owned activity are documented.",
            "Dependencies, risks, mitigations, and escalation owners are visible.",
            "Implementation output and validation result match the agreed scope.",
            "Release note includes runbook, rollback or recovery path, owner routing, and post-change proof.",
            "Outcome can be summarized in business, architecture, and engineering language without changing the facts.",
        ],
        "deliveryEvidence": _unique([
            "project scope note",
            "stakeholder/owner matrix",
            "dependency tracker",
            "risk and mitigation log",
            "milestone status note",
            *evidence[:4],
            *delivered_scope[:2],
            "release note and support ticket trail",
        ]),
    }


def _business_analyst_lens(use_case: dict[str, Any], index: int, role_name: str, domain: str, profile: DomainProfile, primary_system: str, secondary_system: str) -> dict[str, Any]:
    lines = profile.lines_of_business or []
    matched_lob = next(
        (
            lob
            for lob in lines
            if primary_system in _training_as_list(lob.get("systems"))
            or secondary_system in _training_as_list(lob.get("systems"))
        ),
        lines[(index - 1) % len(lines)] if lines else {"name": domain, "description": profile.context, "systems": [primary_system, secondary_system], "jobSignals": profile.platform_signals},
    )
    lob_name = str(matched_lob.get("name") or domain)
    lob_description = str(matched_lob.get("description") or profile.context)
    lob_systems = _training_as_list(matched_lob.get("systems")) or [primary_system, secondary_system]
    signals = _unique([*profile.platform_signals[:4], *_training_as_list(matched_lob.get("jobSignals"))[:4]])
    title = use_case["title"]
    evidence = _training_as_list(use_case.get("evidenceToExplain"))
    return {
        "lineOfBusiness": lob_name,
        "businessCapability": f"{lob_name}: {lob_description}",
        "businessActors": _domain_business_actors(domain, lob_name),
        "businessWorkflow": [
            f"A user, operator, partner, or downstream team triggers work in {primary_system}.",
            f"{primary_system} validates the request, event, file, case, transaction, or operational signal against the {lob_name} business process.",
            f"The flow touches {secondary_system} or connected systems for decision, fulfillment, reporting, support, or exception handling.",
            f"The implemented use case changes how {title} is seen, controlled, recovered, or routed for support.",
            "The result is visible through SLA, KPI, report, dashboard, ticket, reconciliation, or audit record.",
        ],
        "businessRules": [
            f"{domain} business rules require accurate status, named owners, secure access, and traceable handoff across the flow.",
            "Exceptions need an owner, reason code or failure signal, retry/recovery path, and communication or support note.",
            "Approval, audit, retention, privacy, or compliance controls apply when the flow affects regulated data, financial impact, customer experience, operational SLA, or production change.",
        ],
        "kpisAndReports": _domain_kpis(domain, lob_name, signals),
        "systemTouchpoints": _unique([primary_system, secondary_system, *lob_systems[:6]]),
        "baAcceptanceCriteria": [
            f"The {lob_name} flow can be described from business trigger to final outcome.",
            "Actors, inputs, outputs, exception paths, and ownership handoffs are identifiable.",
            "The implemented change maps to at least one KPI, SLA, report, audit signal, or operational dashboard.",
            "The business problem, system touchpoints, validation result, and support impact are consistent across the use-case story.",
        ],
        "domainVocabulary": _unique([lob_name, *signals[:6], "business capability", "exception path", "SLA", "KPI", "audit record", "owner route"]),
        "businessQuestions": [
            f"Which {lob_name} business capability is affected by this use case?",
            "Who starts the workflow and who consumes the result?",
            "What is the normal path, and what exception path matters most?",
            "Which KPI, SLA, report, or audit signal proves business value?",
            "Which system owns the status, data, or operational truth at each handoff?",
        ],
        "businessEvidence": _unique([*evidence[:4], *signals[:4], "business process map", "KPI/SLA note", "exception path note", "report/dashboard reference"]),
    }


def _domain_business_actors(domain: str, lob_name: str) -> list[str]:
    actors_by_domain = {
        "Healthcare / Health Insurance": ["member", "patient", "provider", "claims examiner", "care coordinator", "support agent", "compliance reviewer"],
        "Banking / Financial Services": ["customer", "cardholder", "banker", "payment operations analyst", "fraud analyst", "risk analyst", "compliance reviewer"],
        "Insurance": ["policyholder", "agent", "underwriter", "claims adjuster", "billing specialist", "fraud analyst", "regulatory analyst"],
        "Logistics / Transportation": ["shipper", "dispatcher", "driver", "warehouse operator", "customer support agent", "partner carrier", "operations manager"],
        "Retail / E-Commerce": ["shopper", "merchandiser", "store operations user", "fulfillment associate", "customer support agent", "marketing analyst", "finance analyst"],
        "Manufacturing / Automotive / Industrial": ["plant operator", "quality engineer", "production planner", "supplier manager", "maintenance technician", "warehouse operator", "operations manager"],
        "Technology / SaaS / Enterprise Software": ["tenant admin", "end user", "customer success manager", "support engineer", "product manager", "security admin", "finance operations user"],
        "Energy / Utilities / Data Centers": ["customer", "field technician", "grid operator", "billing analyst", "control room operator", "compliance analyst", "operations manager"],
        "Telecom / Media / Communications": ["subscriber", "field technician", "network operations user", "billing analyst", "customer care agent", "media operations user", "service assurance analyst"],
    }
    base = actors_by_domain.get(domain, ["business user", "operations user", "support agent", "analyst", "manager", "compliance reviewer"])
    return _unique([*base, f"{lob_name} owner"])[:8]


def _domain_kpis(domain: str, lob_name: str, signals: list[str]) -> list[str]:
    domain_kpis = {
        "Healthcare / Health Insurance": ["eligibility response time", "claims SLA", "prior authorization turnaround", "portal availability", "PHI audit exception count"],
        "Banking / Financial Services": ["payment success rate", "fraud decision latency", "transaction reconciliation accuracy", "login success rate", "audit exception count"],
        "Insurance": ["claim cycle time", "policy issuance SLA", "billing accuracy", "document processing backlog", "regulatory report timeliness"],
        "Logistics / Transportation": ["shipment status freshness", "on-time delivery rate", "route update latency", "partner API success rate", "exception resolution time"],
        "Retail / E-Commerce": ["checkout conversion", "payment authorization success", "inventory accuracy", "order fulfillment SLA", "promotion error rate"],
        "Manufacturing / Automotive / Industrial": ["plant uptime", "production order completion", "quality exception rate", "telemetry freshness", "ERP/MES integration success"],
        "Technology / SaaS / Enterprise Software": ["tenant availability", "API latency", "deployment success rate", "support ticket MTTR", "usage telemetry freshness"],
        "Energy / Utilities / Data Centers": ["service availability", "meter read success", "billing accuracy", "incident response time", "regulatory report completeness"],
        "Telecom / Media / Communications": ["activation success rate", "service assurance SLA", "billing event accuracy", "network incident MTTR", "subscriber experience score"],
    }
    return _unique([*(domain_kpis.get(domain, [])[:5]), *signals[:4], f"{lob_name} operational dashboard"])


def _architect_lens(use_case: dict[str, Any], role_name: str, domain: str, primary_system: str, secondary_system: str, focus: list[str]) -> dict[str, Any]:
    title = use_case["title"]
    evidence = _training_as_list(use_case.get("evidenceToExplain"))
    systems = _training_as_list(use_case.get("systemsTouched"))
    delivered_scope = _training_as_list(use_case.get("deliveredScope"))
    role_focus = ", ".join(focus[:4]).lower()
    return {
        "decisionRationale": [
            f"The architecture favors a {role_name} operating pattern with versioned artifacts, environment checks, and owner routing because {primary_system} and {secondary_system} need predictable behavior across releases and incidents.",
            f"The selected approach keeps {role_focus} visible as role-owned behavior while product requirements, feature logic, data ownership, security approval, and production operations remain separated.",
            f"The use case is credible because the design connects business trigger, system path, implementation artifact, validation signal, failure handling, and operations record.",
        ],
        "architecturalTradeoffs": [
            "Standardization gives teams the same release gates, naming, rollback checks, and dashboard fields, but the design still leaves room for product-specific checks where a one-size-fits-all workflow would hide business risk.",
            "Automation reduces manual handoffs, but approvals, rollback criteria, audit trail, and exception handling remain explicit control points.",
            "More telemetry improves diagnosis, but alert quality, sensitive data handling, log volume, and cost controls must be managed intentionally.",
        ],
        "constraints": [
            f"{domain} systems require named owners, controlled access, audit-ready records, and support paths that do not expose sensitive operational data.",
            f"{primary_system} and {secondary_system} can fail through upstream dependency, runtime, data, network, identity, deployment, or external partner behavior.",
            "The role-owned implementation must fit existing change windows, environment promotion rules, approval gates, service ownership, and incident routing.",
        ],
        "nfrsAndControls": [
            "Availability and recovery: rollback, rerun, failover, retry, or escalation path is defined before production dependency risk is accepted.",
            "Security and compliance: IAM, secrets, encryption, audit trail, policy checks, and data handling are visible in the flow.",
            "Operability: logs, metrics, traces, dashboards, runbooks, alerts, and ownership routing show how the system is supported after delivery.",
            "Performance and cost: latency, throughput, capacity, data freshness, job runtime, or cloud spend has a measurable signal where relevant.",
        ],
        "riskRegister": [
            {"risk": "Hidden owner gap", "impact": "Incidents bounce across product, application, platform, data, security, and operations teams.", "mitigation": "Use ownership matrix, escalation path, and log/dashboard/ticket-based routing."},
            {"risk": "Validation gap", "impact": "A change appears complete but fails under real product flow or dependency behavior.", "mitigation": "Capture happy-path, failure-path, rollback/recovery, and post-change evidence."},
            {"risk": "Tool-only story", "impact": "The use case sounds like tool exposure instead of architecture and product understanding.", "mitigation": "Anchor explanation to business capability, system behavior, NFRs, controls, and evidence."},
        ],
        "seniorExplanation": (
            f"Architecturally, {title} is not just a task list. It is a controlled change to the {primary_system} / {secondary_system} flow. "
            f"The important design question is whether {role_name} ownership gives the flow versioned change steps, visible health signals, and a recovery path without taking ownership away from product, application, security, data, or operations teams."
        ),
        "reviewQuestions": [
            f"Which business capability in {domain} does this use case protect or improve?",
            f"Where does {primary_system} hand off to {secondary_system}, and what evidence proves that handoff is healthy?",
            "What failure mode would expose the weakest part of this design?",
            "Which NFR matters most here: availability, security, performance, cost, auditability, data quality, or operability?",
            f"What part is truly owned by {role_name}, and what part must remain with another team?",
        ],
        "artifactEvidence": _unique([
            *evidence[:5],
            *delivered_scope[:2],
            "architecture decision note",
            "NFR/control checklist",
            "risk and mitigation note",
            "operational readiness evidence",
            *systems[:3],
        ]),
    }


def _implementation_scenario(use_case: dict[str, Any], index: int, role_name: str, domain: str, primary_system: str, secondary_system: str, focus: list[str]) -> dict[str, Any]:
    title = use_case["title"]
    evidence = _training_as_list(use_case.get("evidenceToExplain"))
    delivered_scope = _training_as_list(use_case.get("deliveredScope"))
    systems = _training_as_list(use_case.get("systemsTouched"))
    role_focus = ", ".join(focus[:4]).lower()
    activity_base = [
        (
            "Current-state system flow",
            f"The existing {primary_system} flow includes owners, repositories/jobs, environments, dependency points, and support gaps that shape the change.",
            ["current-state diagram", "owner matrix", "known failure list"],
        ),
        (
            "Target-state behavior",
            f"The target {title} workflow adds role-owned {role_focus}, environment path, approval point, rollback/recovery path, and owner route.",
            ["target-state diagram", "implementation plan", "rollback note"],
        ),
        (
            "Role-owned implementation change",
            delivered_scope[0] if delivered_scope else f"The {role_name} owned configuration, automation, platform, data, reliability, or deployment change is the implementation center of this scenario.",
            ["pull request or config change", "pipeline/job run", "review/approval evidence"],
        ),
        (
            "Validation behavior",
            f"Happy-path and failure-path checks for {primary_system} and {secondary_system} produce logs, metrics, job output, policy result, or deployment status.",
            ["validation output", "failed-case proof", "fix or exception note"],
        ),
        (
            "Operational handoff behavior",
            f"Operations, application, security, data, QA, and support teams see what changed, which symptom matters, and how recovery works if the flow fails.",
            ["operational checklist", "runbook", "support ticket update"],
        ),
        (
            "Project narrative evidence",
            "The scenario is explainable through the business problem, system flow, role-owned change, validation signal, failure behavior, and result.",
            ["project narrative", "evidence folder", "interview answer"],
        ),
    ]
    acceptance = [
        f"{primary_system} flow is drawn from business trigger to validation result.",
        f"{role_name} ownership is separated from product, application, QA, security, data, and operations ownership.",
        "At least one lower-environment implementation artifact exists and can be explained.",
        "Happy-path validation and one failure-path validation are documented.",
        "Runbook or support ticket note includes symptom, check, proof artifact, next action, and escalation owner.",
        "The scenario is repeatable from the artifact pack without relying on vague notes.",
    ]
    replication_lab = [
        f"A sandbox version of the {primary_system} workflow can be represented with a small repo, sample config, mock service, sample dataset, or local container.",
        f"The smallest functional slice is the role-owned activity: {delivered_scope[0] if delivered_scope else role_focus}.",
        "Validation output shows whether the command, job, policy, deployment, data movement, or service check behaved correctly.",
        "A controlled failure can come from image tag, secret name, endpoint, query, policy, threshold, schema, or config value.",
        "Recovery is visible when the runbook action changes the failed state back to healthy state.",
        "The scenario narrative connects problem, activity, proof artifact, result, and owner boundary.",
    ]
    artifact_pack = _unique([
        "current-state diagram",
        "target-state diagram",
        "Jira story group with acceptance criteria",
        "implementation PR/config/job output",
        *evidence[:5],
        "validation output",
        "runbook",
        "rollback or recovery note",
        "project narrative",
    ])
    consultant_story = (
        f"On the {domain} project, this scenario centers on {primary_system} and {secondary_system}. "
        f"The work sequence is concrete: map the current flow, build or configure the {role_name} owned part, validate the result, document failure handling, and attach logs, dashboard output, ticket notes, or runbook updates for support and interviews."
    )
    return {
        "projectScenario": consultant_story,
        "implementationActivities": [
            {"phase": phase, "activity": activity, "artifacts": artifacts}
            for phase, activity, artifacts in activity_base
        ],
        "replicationLab": replication_lab,
        "implementationAcceptanceCriteria": acceptance,
        "scenarioArtifactPack": artifact_pack,
        "consultantCanSay": [
            f"I implemented the {role_name} owned portion of {title} by mapping the existing {primary_system} flow, making the role-owned change, validating it, and preparing support evidence.",
            f"The systems involved were {', '.join(systems[:4])}; the boundary was {use_case.get('roleBoundary')}.",
            f"The artifact set included {', '.join(artifact_pack[:5])}.",
            "The result was ready for handoff because the activity, validation output, failure handling, owner route, and escalation path were documented.",
        ],
    }


def _textbook_explanation_sections(use_case: dict[str, Any], role_name: str, domain: str, systems: list[str], primary_system: str, role_focus: str) -> list[dict[str, list[str]]]:
    systems_text = ", ".join(systems)
    return [
        {
            "title": "Enterprise Context",
            "bullets": [
                f"Enterprise context uses a large {domain} technology organization model with about 100 applications across 10 major technology teams.",
                f"{role_name} work had to fit an enterprise operating model with product owners, application developers, QA, security, platform teams, operations, and service desk users.",
                f"The goal was not only to change {primary_system}; the goal was to make the workflow visible through versioned change steps, lower-environment checks, health signals, and recovery notes.",
                f"{primary_system} was the main system used to explain the story because it connected business impact, technical delivery, and production support.",
            ],
        },
        {
            "title": "Business Problem",
            "bullets": [
                use_case["businessProblem"],
                f"The affected {domain} product area included {systems_text}; these systems were handled together because enterprise incidents and releases rarely stop at one application boundary.",
                f"The practical problem around {primary_system} was owner confusion: product requirement, code, platform change, validation, and production support had to be separated.",
                f"Without a clear {role_name} activity model, releases created delays, support teams lacked logs/dashboard/ticket context, and engineers spent extra time proving where the issue belonged.",
            ],
        },
        {
            "title": f"{role_name} Ownership",
            "bullets": [
                f"My ownership stayed on the engineering layer around {role_focus}.",
                f"I did not claim ownership of {primary_system} product requirements, business rules, or application feature logic.",
                f"Product owners defined {domain} business priority, developers owned feature code, QA validated functional behavior, security reviewed guardrails, and operations handled service desk intake.",
                f"My contribution was to make the {primary_system} delivery or support path visible through workflow notes, automation output, dashboard/log signals, and runbook updates.",
            ],
        },
        {
            "title": "Implementation Approach",
            "bullets": [
                f"Mapped the current {primary_system} workflow before changing anything: request source, repositories, environments, approvals, expected validation output, support contacts, and known failure points.",
                f"Designed the target workflow with clear {role_name} in-scope and out-of-scope boundaries so the team could avoid confusion during delivery or incidents.",
                f"Created the role-owned artifact for {primary_system}: configuration, automation, dashboard, alert, runbook, validation checklist, deployment record, or support document.",
                f"Validated the {domain} workflow in lower environments first, then prepared production rollout, rollback, owner route, and runbook notes.",
            ],
        },
        {
            "title": "Completion Criteria",
            "bullets": [
                f"{primary_system} product flow could be explained from user impact to backend/platform dependency.",
                f"The {role_name} delivery or support process could run without depending on one person.",
                f"The {domain} result could be validated using logs, metrics, alerts, deployment history, dashboard screenshots, pull requests, or runbook steps.",
                f"{primary_system} failure scenarios had a documented troubleshooting path and clear escalation ownership.",
            ],
        },
        {
            "title": "Interview Answer Points",
            "bullets": [
                f"Business context: {primary_system} workflow, affected users, and operational risk.",
                f"Systems touched: {systems_text}, platform dependencies, data paths, tools, and support owner routes.",
                f"Role ownership: {role_name} work stayed on {role_focus}; product requirements and feature code stayed with product and application teams.",
                f"Outcome: fewer release surprises, clearer resolver routing, stronger validation output, and cleaner production support for {domain}.",
            ],
        },
    ]


def _jira_stories(title: str, role_name: str, domain: str, systems: list[str], focus: list[str]) -> list[dict[str, Any]]:
    primary_system = systems[0] if systems else f"{domain} application"
    return [
        {
            "key": f"JIRA-{index}",
            "title": story_title,
            "story": story,
            "acceptanceCriteria": criteria,
            "implementationNotes": notes,
        }
        for index, (story_title, story, criteria, notes) in enumerate(
            [
                (
                    f"Map current-state workflow for {primary_system}",
                    f"As a {role_name}, I need to document the current {domain} workflow so the team understands systems, owners, environments, approvals, and support gaps before implementation.",
                    ["Current workflow is documented", "Application and platform owners are identified", "Known failure points are listed", "Interview explanation can describe the before state"],
                    f"Focus on {focus[0]}, {focus[1]}, ownership boundaries, and the handoff between developers, platform, QA, security, and operations.",
                ),
                (
                    f"Design target-state implementation for {title}",
                    f"As a {role_name}, I need to define the target workflow so delivery, validation, rollback, monitoring, and support responsibilities are clear.",
                    ["Target workflow is reviewed", "In-scope and out-of-scope items are clear", "Validation and rollback approach is documented", "Architecture assumptions are captured"],
                    f"Technical boundary: {', '.join(focus[:4]).lower()}. Every tool connects to a product or support outcome.",
                ),
                (
                    f"Implement role-owned changes for {primary_system}",
                    f"As a {role_name}, I need to implement the role-owned configuration, automation, platform, data, reliability, or support changes required by the use case.",
                    ["Implementation artifact exists", "Peer review or approval is complete", "Lower environment validation is complete", "Evidence is ready for interview discussion"],
                    "Evidence can include pull request, pipeline run, dashboard, query, alert, runbook, configuration screenshot, deployment record, or RCA note.",
                ),
                (
                    f"Validate production readiness for {domain} teams",
                    f"As a {role_name}, I need to validate the use case before production rollout so the application team, operations team, and support desk can trust the process.",
                    ["Smoke checks are defined", "Monitoring or support signals are visible", "Rollback or recovery path is documented", "Support handoff is complete"],
                    "Validation evidence covers successful flow, failure signals, escalation path, and recovery steps.",
                ),
                (
                    f"Create delivery evidence package for {title}",
                    f"As a {role_name}, I need to package the delivered use case with product context, exact ownership, artifacts, issues, decisions, and outcome.",
                    ["Two-minute summary is written", "Deep-dive explanation is written", "Five technical artifacts are listed", "Scenario and troubleshooting answers are prepared"],
                    "Final story: real enterprise delivery, clear ownership boundary, specific artifacts, no tool-only explanation.",
                ),
            ],
            start=1,
        )
    ]


def _use_case_questions(title: str, role_name: str, domain: str, systems: list[str], focus: list[str], category: str, count: int) -> list[dict[str, Any]]:
    primary_system = systems[0] if systems else f"{domain} platform"
    secondary_system = systems[1] if len(systems) > 1 else "downstream services"
    base_questions = {
        "interview": [
            f"Walk me through the {title} use case from business problem to production outcome.",
            f"What exactly did you own as a {role_name}, and what was owned by developers or product owners?",
            f"Which {domain} applications were touched, and why did {primary_system} matter to the business?",
            "How did you gather requirements and confirm the current-state workflow?",
            f"Which tools did you use for {focus[0]} and {focus[1]}, and what output did each tool produce?",
            "How did you validate the change before production?",
            "How did you document rollback, support handoff, or recovery steps?",
            "What issue came up during implementation, and how did you resolve it?",
            "How did you communicate progress and risk to leads or support teams?",
            "What measurable improvement or operational benefit came from the use case?",
        ],
        "architecture": [
            f"Draw the high-level architecture for {primary_system} in this use case.",
            "Where do DNS, WAF, API gateway, load balancer, compute, database, queue, secrets, and observability fit?",
            f"Which components were inside your {role_name} boundary and which were outside it?",
            "How did the architecture support dev, QA, stage, and production separation?",
            "What architecture risk would you call out to a lead engineer?",
        ],
        "system design": [
            f"How would you design this use case if {primary_system} had to support more teams and applications?",
            "How would you make the workflow reusable across multiple product teams?",
            "How would you design access control, secrets, audit evidence, and approval flow?",
            "How would you design observability so support teams can understand user impact?",
            f"How would you handle dependency failure between {primary_system} and {secondary_system}?",
        ],
        "scenario": [
            "A release is approved but smoke tests fail in stage. What do you do?",
            "The product owner wants an urgent production change. How do you protect the process?",
            "A developer says the issue is infrastructure, but the dashboard is inconclusive. How do you investigate?",
            "Support reports customer impact but there is no active alert. How do you respond?",
            "A manager asks for status in the middle of an incident. What do you communicate?",
        ],
        "troubleshooting": [
            "A deployment succeeded but users still see errors. What are your first checks?",
            "Pods or services restart after rollout. What signals do you review?",
            "A pipeline, data job, sync, model, database, or automation workflow failed. How do you isolate the cause?",
            "Latency increased after a change. How do you separate application, platform, network, and database causes?",
            "Rollback did not fully restore behavior. What do you check next?",
        ],
    }
    questions = base_questions[category][:count]
    rows = []
    for question in questions:
        answer = _interview_style_answer(question, category, role_name, domain, primary_system, focus)
        rows.append({"question": question, "answer": answer, "answerBullets": _answer_bullets(answer)})
    return rows


def _answer_bullets(answer: str) -> list[str]:
    return [part.strip() for part in answer.split(". ") if part.strip()]


def _interview_style_answer(question: str, category: str, role_name: str, domain: str, primary_system: str, focus: list[str]) -> str:
    tools = ROLE_OWNERSHIP[role_name]["tools"]
    tool_one = tools[0] if tools else focus[0]
    tool_two = tools[1] if len(tools) > 1 else focus[1]
    tool_three = tools[2] if len(tools) > 2 else focus[2]
    q = question.lower()
    if "which tools" in q or "what output" in q:
        return (
            f"{tool_one} handled {focus[0].lower()}; {tool_two} supported {focus[1].lower()}; {tool_three} was used for {focus[2].lower()} around {primary_system}. "
            f"For {primary_system}, the outputs were correlated alerts or execution results, dashboards, logs, status records, configuration changes, and runbook updates. "
            f"Those outputs helped identify the affected service, recent change, unhealthy dependency, and next owning team during {domain} production issues."
        )
    if "walk me through" in q:
        return (
            f"The problem was inconsistent delivery and support flow around {primary_system}. I mapped {primary_system} business impact, application owners, platform dependencies, and support handoff gaps. "
            f"My {role_name} work for {primary_system} covered {', '.join(focus[:4]).lower()}: implementation artifacts, lower-environment validation, rollout notes, and recovery evidence. "
            f"The outcome was cleaner ownership, stronger release validation, and faster production support for the {domain} workflow."
        )
    if "exactly did you own" in q:
        return (
            f"I owned the {role_name} layer, specifically {', '.join(focus[:5]).lower()}. For {primary_system}, product owners owned business priority, developers owned feature code, QA owned functional testing, and security reviewed controls. "
            f"My work was to make the engineering process around {primary_system} repeatable and supportable. I handled {role_name} configuration, automation, observability, documentation, validation evidence, or support workflow, then coordinated with other teams when the signal moved outside my boundary."
        )
    if "applications were touched" in q or "why did" in q:
        return (
            f"The main application was {primary_system}, and it mattered because it was part of the {domain} product flow where business users and support teams felt the impact quickly. "
            f"The {primary_system} work also touched connected services such as APIs, platform components, data stores, monitoring tools, and operational handoff points. I explained the {domain} system as a chain: user action, application service, platform dependency, data or integration layer, monitoring signal, and support response."
        )
    if "gather requirements" in q or "current-state" in q:
        return (
            f"I gathered {primary_system} requirements by talking to application owners, platform engineers, QA, security, and support users. I reviewed tickets, existing runbooks, deployment history, dashboards, and failure examples for {primary_system}. "
            f"Then I documented the {domain} current-state workflow: who raised the request, who approved it, which environment was used, what validation existed, where handoff failed, and what evidence was missing. That gave the {role_name} work a clear baseline before implementation."
        )
    if "validate" in q and "production" in q:
        return (
            f"I validated the {primary_system} change in lower environments first by checking the expected workflow, logs, metrics, deployment or execution output, and support signals. For {primary_system}, I made sure the team had a smoke-check path and rollback or recovery notes. "
            f"Before production, I confirmed {domain} approvals, reviewed the evidence package, and made sure operations knew what to monitor after rollout."
        )
    if "rollback" in q or "support handoff" in q or "recovery" in q:
        return (
            f"For {primary_system}, {role_name} rollback and support handoff were part of the delivery package, not an afterthought. The {primary_system} {role_name} notes included rollback trigger, reverse steps, dashboards to watch, known dependencies, owner contacts, and validation after recovery. "
            f"{role_name} support handoff translated {domain} technical signals into clear instructions: symptom to check, evidence to collect, and escalation path to application, platform, database, data, or security teams."
        )
    if "issue came up" in q:
        return (
            f"One issue was that the signals from {primary_system} were not enough to identify the owner quickly. The {primary_system} alert or failure showed symptoms, but it did not clearly separate application, platform, dependency, or configuration causes. "
            f"I resolved it for {primary_system} by adding clearer checks, documenting the decision path, and linking the evidence back to the correct team. After that, {domain} support could triage faster instead of escalating with incomplete information."
        )
    if "communicate" in q:
        return (
            f"{role_name} status for {primary_system} was communicated in three parts: current impact, verified evidence, and next action. Example for {primary_system}: affected workflow, recent change reviewed, dashboard signal confirmed, and next owner identified. "
            f"For managers, the message stayed business-readable; for engineers, it included enough {role_name} detail to act."
        )
    if "measurable" in q or "benefit" in q:
        return (
            f"The main benefit for {primary_system} was operational clarity. The {domain} team had a repeatable workflow, better evidence during support, clearer ownership, and less time spent guessing where an issue belonged. "
            f"For {domain}, that mattered because product teams, platform teams, and support users could move from incident symptom to responsible owner more quickly, with validation and recovery steps already documented."
        )
    if category == "troubleshooting":
        if "deployment succeeded" in q:
            return (
                f"First checks for {primary_system}: confirm the deployed version, compare error rate before and after rollout, review ingress/API gateway status, check pod or service health, and validate downstream dependencies. "
                f"As a {role_name}, the decision was rollback, configuration correction, or escalation based on evidence, not only deployment status."
            )
        if "restart" in q:
            return (
                f"Restarts after rollout were checked through pod events, resource pressure, liveness/readiness probes, image version, configuration changes, and recent traffic pattern for {primary_system}. "
                f"The useful {primary_system} answer separates bad code, bad config, capacity issue, dependency timeout, and platform scheduling issue."
            )
        if "workflow failed" in q or "isolate" in q:
            return (
                f"{primary_system} failure isolation started with the failing stage: trigger, input, permission, environment variable, dependency, schema, artifact, or execution log. "
                f"For {role_name}, the evidence had to show whether {', '.join(focus[:3]).lower()} failed inside the owned layer or needed handoff to app, data, database, security, or vendor teams."
            )
        if "latency" in q:
            return (
                f"{primary_system} latency was split by layer: edge timing, API gateway, application runtime, database, queue, network, and third-party dependency. "
                f"For {primary_system}, traces and dashboard panels had to show whether the slowdown came from code path, platform capacity, database query, network route, or downstream service."
            )
        if "rollback" in q:
            return (
                f"When rollback did not restore behavior, the next checks were data state, cache, feature flag, database migration, queued messages, dependency version, and client-side cached response. "
                f"That prevented a false assumption that reverting the deployment always resets the full {domain} workflow."
            )
        return (
            f"Troubleshooting for {primary_system} started with impact and evidence. I confirmed whether users were affected, checked recent changes, and reviewed logs, metrics, traces, deployment history, configuration, dependencies, and alerts. "
            f"As a {role_name}, I focused on {', '.join(focus[:4]).lower()}, involved the right owner when the signal pointed outside my boundary, validated recovery, and updated the runbook or RCA notes."
        )
    if category == "scenario":
        if "smoke tests fail" in q:
            return (
                f"Stage smoke-test failure stopped the release for {primary_system}. I reviewed {primary_system} failed check, recent change, logs, deployment version, configuration, and dependency health before deciding rerun, fix-forward in stage, or rollback of the candidate build. "
                f"Production stayed blocked until evidence showed the {domain} workflow was safe."
            )
        if "urgent production change" in q:
            return (
                f"Urgency did not remove governance. I clarified user impact, approval owner, risk of waiting, rollback option, validation path, and monitoring plan for {primary_system}. "
                f"If approved, the {primary_system} change used the emergency path with documented evidence and post-change review."
            )
        if "dashboard is inconclusive" in q:
            return (
                f"Inconclusive {primary_system} dashboard meant more evidence was needed before blaming infrastructure. I compared {primary_system} application logs, platform metrics, traces, deployment history, gateway errors, database signals, and dependency status. "
                f"The {domain} escalation included the signal that pointed to the next owner, not a vague statement."
            )
        if "no active alert" in q:
            return (
                f"Customer impact without an alert was treated as a monitoring gap. I validated the report, checked synthetic/user-flow evidence, reviewed logs and traces for {primary_system}, and opened an incident if impact was real. "
                f"After {primary_system} recovery, the alert rule or dashboard threshold was adjusted so the same issue was visible next time."
            )
        if "manager asks" in q:
            return (
                f"Incident status was given as impact, evidence, action, owner, and next update time. For {primary_system}: what is affected, what has been ruled out, who is working, whether rollback is being considered, and when the next checkpoint will be shared. "
                f"That keeps leadership informed without slowing the technical recovery."
            )
        return (
            f"For {primary_system}, I avoided guessing and first clarified the business impact. I identified the decision owner, collected evidence from the relevant tools, and explained the risk in simple language. "
            f"Then I used the approved {domain} workflow to validate, rollback, escalate, or proceed, while keeping my {role_name} responsibility clear."
        )
    if category == "architecture":
        if "draw the high-level" in q:
            return (
                f"The high-level architecture for {primary_system} starts with users or business systems, then DNS/CDN/WAF, API gateway, load balancer, runtime service, database or queue, observability, and support workflow. "
                f"{role_name} work is marked on the layers where {', '.join(focus[:3]).lower()} changed reliability, release safety, or recovery."
            )
        if "where do dns" in q:
            return (
                f"For {primary_system}, DNS resolves the entry point, WAF protects traffic, API gateway applies routing and policy, load balancer distributes requests, compute runs the service, database and queue hold state or async work, secrets protect access, and observability proves health. "
                f"For {primary_system}, each component has a signal: status code, latency, target health, pod health, query/queue metric, secret access, or alert."
            )
        if "inside your" in q:
            return (
                f"Inside the {role_name} boundary: {', '.join(focus[:5]).lower()}, validation evidence, runbooks, dashboards, and support routing. "
                f"Outside the {primary_system} boundary: product priority, feature code, final security approval, database engine tuning, and business acceptance unless the signal required coordination."
            )
        if "environment separation" in q:
            return (
                f"Dev, QA, stage, and production were separated by configuration, secrets, approval gates, deployment targets, data access, and monitoring labels. "
                f"For {primary_system}, this prevented lower-environment shortcuts from leaking into production and made release evidence easier to trust."
            )
        if "risk" in q:
            return (
                f"The main architecture risk was hidden dependency failure: {primary_system} could look unhealthy because of gateway routing, database pressure, queue lag, secret access, or a connected service. "
                f"The mitigation for {primary_system} was dependency-level telemetry, release markers, clear owner routing, and rollback criteria."
            )
        return (
            f"The {primary_system} architecture has four paths: request path, deployment path, data path, and support path. {primary_system} core layers are edge controls, service layer, data layer, observability, secrets, and environment separation. "
            f"{role_name} influence is marked where the work improves reliability, delivery, security, or supportability."
        )
    if category == "system design":
        if "more teams" in q:
            return (
                f"To scale {primary_system} for more teams, standardize delivery patterns, ownership metadata, environment naming, approval flow, observability labels, runbook format, and escalation groups. "
                f"The design goal for {domain} teams is onboarding a new team without rebuilding the process from zero."
            )
        if "reusable" in q:
            return (
                f"Reusable {primary_system} workflow means one approved pattern with parameterized values for application, environment, owner, validation checks, rollback, and monitoring. "
                f"For {role_name}, reuse must still allow product-specific checks for {domain} systems instead of hiding every difference behind one shared pipeline."
            )
        if "access control" in q:
            return (
                f"{primary_system} access control design uses least privilege, separate service identities, secrets manager, approval gates, audit logs, and break-glass process. "
                f"For {primary_system}, evidence includes who approved, which identity executed, what secret or permission was used, and how access was reviewed."
            )
        if "observability" in q:
            return (
                f"Observability design starts with user impact: availability, latency, error rate, dependency health, release marker, and support ticket signal for {primary_system}. "
                f"{primary_system} dashboards must show owner, affected layer, recent change, and next action, not only raw CPU or memory."
            )
        if "dependency failure" in q:
            return (
                f"{primary_system} dependency failure is handled with timeout policy, retry/backoff, circuit breaker where applicable, queue lag visibility, owner routing, and fallback communication. "
                f"For {primary_system}, the design separates front-end symptom from downstream owner so the incident does not bounce between teams."
            )
        return (
            f"The {primary_system} design supports reuse across teams, not just one application. It includes ownership, delivery patterns and standards, validation gates, monitoring, access control, documentation, and support handoff. "
            f"The main tradeoff is speed versus governance because this is a {domain} enterprise environment."
        )
    return (
        f"{primary_system} was part of the {domain} application landscape. My {role_name} ownership covered {', '.join(focus[:4]).lower()}. "
        f"I created evidence, validated the workflow, documented support steps, and coordinated with application, platform, QA, security, and operations teams. The result was a clearer delivery and support process from business impact to technical outcome."
    )


def _use_case_workflow(title: str, role_name: str, domain: str, primary_system: str, secondary_system: str, focus: list[str]) -> dict[str, Any]:
    diagram, steps = _use_case_workflow_path(title, role_name, domain, primary_system, secondary_system, focus)
    return {
        "title": f"Workflow for {title}",
        "diagram": diagram,
        "steps": steps,
    }


def _use_case_workflow_path(title: str, role_name: str, domain: str, primary_system: str, secondary_system: str, focus: list[str]) -> tuple[str, list[str]]:
    lower = title.lower()
    focus_text = ", ".join(focus[:4]).lower()
    if any(token in lower for token in ("alert", "slo", "incident", "rca", "trace", "troubleshooting", "autoscaling", "load-balancer", "load balancer", "opentelemetry")):
        steps = [
            f"Detect user or service impact for {primary_system}.",
            "Confirm SLI/SLO, alert severity, recent deployment, and business priority.",
            "Correlate metrics, logs, traces, capacity, network path, and dependency health.",
            f"Route the evidence to the correct owner while {role_name} coordinates mitigation.",
            "Validate recovery with before/after signals and user-flow checks.",
            "Update incident timeline, RCA action, alert tuning, and runbook evidence.",
        ]
        return (
            f"Impact signal -> SLO/alert check -> Metrics/logs/traces -> Dependency isolation -> Mitigation/owner routing -> Recovery validation -> RCA/runbook",
            steps,
        )
    if any(token in lower for token in ("glue", "catalog", "lake", "athena", "presto", "bigquery", "streaming", "warehouse", "data lake", "incremental migration", "airflow", "dag", "spark", "flink", "dbt", "ledger", "reconciliation", "settlement", "transaction", "data infrastructure", "data security", "access controls", "postgresql", "schema", "migration", "data contract", "fhir", "hl7", "synthetic", "phi", "etl decision", "runbook model", "data dictionary", "audit trail", "history-table", "column-level")):
        steps = [
            f"Identify source data, consumer, freshness, access, and reporting need for {primary_system}.",
            "Ingest source data into raw, curated, streaming, or warehouse layers.",
            "Catalog schema, partitioning, lineage, quality rules, and access controls.",
            "Transform, validate, reconcile row counts, and publish to analytics consumers.",
            "Monitor freshness, failed runs, bad records, query cost, and replay/backfill behavior.",
            "Document data contract, evidence, consumer communication, and recovery runbook.",
        ]
        return (
            f"Source data -> Ingestion -> Catalog/governance -> Transform/validate -> Serve analytics -> Freshness/recovery evidence",
            steps,
        )
    if any(token in lower for token in ("model", "sagemaker", "vertex", "feature", "inference", "drift", "registry", "training pipeline")):
        steps = [
            f"Identify model use case, input data, prediction consumer, and risk for {primary_system}.",
            "Prepare feature pipeline, training data, validation data, and reproducible environment.",
            "Run training/evaluation and register the approved model artifact.",
            "Promote model through stage gates to batch or real-time inference.",
            "Monitor latency, errors, data drift, model drift, output quality, and access controls.",
            "Decide rollback, retraining, or owner escalation using model and platform evidence.",
        ]
        return (
            f"Data/features -> Train/evaluate -> Registry approval -> Endpoint/batch deploy -> Drift/latency monitor -> Rollback or retrain",
            steps,
        )
    if any(token in lower for token in ("s3", "sqs", "lambda", "api gateway", "serverless", "secrets", "helm", "container", "production change", "release path")):
        steps = [
            f"Start from code, file, API, queue, or deployment trigger for {primary_system}.",
            "Run CI validation, tests, scans, artifact creation, and approval gates.",
            "Deploy runtime, serverless, container, secret, or queue changes through controlled environments.",
            "Validate invocation logs, queue/DLQ depth, release history, smoke tests, and rollback criteria.",
            "Monitor production signal and attach evidence to change or support tickets.",
            "Update runbook, release note, and delivery story.",
        ]
        return (
            f"Trigger/change -> CI validation -> Artifact/config -> Deploy/runtime event -> Smoke/log/queue validation -> Rollback/support evidence",
            steps,
        )
    if any(token in lower for token in ("private service", "endpoint", "multi-az", "landing zone", "governance", "tagging", "identity", "least-privilege", "migration", "cost", "storage lifecycle")):
        steps = [
            f"Identify connectivity, security, resilience, or governance need for {primary_system}.",
            f"Map request path between {primary_system}, {secondary_system}, network, identity, data, and platform services.",
            "Design private access, IAM/RBAC, routing, health checks, lifecycle, or tagging controls.",
            "Validate with connectivity tests, policy checks, failover checks, and cost or compliance evidence.",
            "Document ownership boundaries for application, platform, network, security, and operations teams.",
            "Package architecture diagram, runbook, validation output, and interview explanation.",
        ]
        return (
            f"Platform need -> Network/IAM design -> Private/service access -> Policy or health validation -> Owner handoff -> Architecture evidence",
            steps,
        )
    steps = [
        f"Confirm business problem and user impact for {primary_system}.",
        f"Map dependencies between {primary_system}, {secondary_system}, platform services, data stores, and support teams.",
        f"Design the {role_name} portion around {focus_text}.",
        "Implement the change with reviewable evidence.",
        "Validate in lower environments and document rollback or recovery.",
        "Release through the approved enterprise process and monitor production signals.",
    ]
    return (
        f"Business request -> Current-state review -> {role_name} design -> Implementation -> Validation -> Production evidence -> Support handoff",
        steps,
    )


def _workflow_diagrams(role_name: str, domain: str, applications: list[str], focus: list[str]) -> list[dict[str, Any]]:
    app_one = applications[0]
    app_two = applications[1]
    workflows = [
        {
            "name": "Product-to-platform delivery workflow",
            "purpose": "Shows how a business request becomes a controlled technical change.",
            "steps": [
                f"Product owner raises change for {app_one}",
                f"{role_name} reviews {focus[0]} and environment impact",
                "Developer opens pull request with configuration or service change",
                "Pipeline validates build, tests, scan, artifact, and deployment package",
                "QA/security/lead approval confirms release readiness",
                "Stage deployment runs smoke test and operational checks",
                "Production deployment follows change window, rollback plan, and post-release validation",
            ],
            "diagram": f"Product request -> Pull request -> CI validation -> Artifact -> Stage deploy -> Approval -> Production deploy -> Smoke test -> Runbook evidence",
            "interviewCue": f"{role_name} ownership appears at environment impact review, validation, release readiness, post-release checks, and runbook evidence.",
        },
        {
            "name": "Incident triage workflow",
            "purpose": "Shows the production support path from symptom to validated recovery.",
            "steps": [
                f"Alert or user report indicates issue in {app_two}",
                "Confirm customer/business impact and recent deployment history",
                "Check logs, metrics, traces, configuration, capacity, and dependency health",
                "Identify likely owner and coordinate with application, platform, data, security, or vendor teams",
                "Apply approved fix or rollback, then validate recovery",
                "Document timeline, root cause, prevention, and runbook update",
            ],
            "diagram": "Alert -> Impact check -> Signal review -> Owner routing -> Fix or rollback -> Validation -> RCA and runbook",
            "interviewCue": "Troubleshooting flow: confirm impact, review signals, route owner, validate recovery, document RCA.",
        },
        {
            "name": f"{role_name} ownership boundary map",
            "purpose": "Keeps application, platform, data, security, and support ownership boundaries clear.",
            "steps": [
                "Business team owns product requirements and acceptance priorities",
                "Application team owns feature code and domain logic",
                f"{role_name} owns the practical layer around {', '.join(focus[:4]).lower()}",
                "Security and infrastructure teams own guardrails, access, network, and compliance review",
                "Operations team owns service desk, escalation process, and production handoff standards",
            ],
            "diagram": f"Business requirements -> Application code -> {role_name} enablement -> Security/infrastructure controls -> Operations handoff",
            "interviewCue": f"{role_name} owns the enablement layer; product, application, security, infrastructure, and operations teams keep their own boundaries.",
        },
    ]
    if role_name == "Site Reliability / AIOps Engineer":
        workflows.insert(
            1,
            {
                "name": "Datadog-style incident investigation workflow",
                "purpose": "Shows how SRE work moves from user-impact signal to Datadog evidence, owner routing, mitigation, and prevention.",
                "steps": [
                    f"Monitor or SLO burn alert fires for {app_two}",
                    "Open service dashboard and confirm user impact using latency, error rate, traffic, and saturation",
                    "Jump from service map to APM trace, logs, deployment events, node/pod health, and dependency status",
                    "Correlate the failing path with recent release, capacity, downstream service, or infrastructure change",
                    "Mitigate through rollback, scale-out, traffic isolation, feature flag, or dependency escalation",
                    "Capture Datadog dashboard screenshot, trace/log evidence, incident timeline, RCA, and runbook update",
                ],
                "diagram": "SLO alert -> Service dashboard -> APM trace -> Logs and events -> Kubernetes health -> Mitigation -> RCA evidence",
                "interviewCue": "Datadog-style SRE answer: alert, impact, correlated evidence, owner, mitigation, validation, RCA, prevention.",
            },
        )
    return workflows


def _datadog_inline_diagrams(role_name: str, domain: str, applications: list[str]) -> list[dict[str, Any]]:
    if role_name != "Site Reliability / AIOps Engineer":
        return []
    app_one = applications[0]
    app_two = applications[1]
    return [
        {
            "title": "Kubernetes Monitoring With Datadog Distribution",
            "sourceName": "Datadog Architecture Center",
            "sourceUrl": "https://www.datadoghq.com/architecture/monitoring-kubernetes-with-datadog-distribution/",
            "imageUrl": "https://corp.dd-static.net/img/architecture/monitoring-kubernetes-with-datadog-distribution/monitoring-kubernetes-with-datadog-distribution-v2.png?auto=format&fit=max&w=847&dpr=2",
            "whereItFits": f"This fits the explanation of how {app_one} workloads, pods, nodes, containers, and cluster-level health are monitored in production.",
            "beginnerExplanation": "The picture teaches that Kubernetes reliability is not one dashboard. The SRE needs workload health, cluster health, logs, metrics, traces, and deployment context connected together.",
            "whatToSay": "I start with the affected service, then check pod and node health, recent deployment events, saturation, errors, and dependency signals before choosing rollback, scale, or escalation.",
            "evidenceToCollect": ["Kubernetes service dashboard", "pod or node health screenshot", "APM trace linked to logs", "monitor alert", "incident timeline"],
        },
        {
            "title": "Observability Pipelines Kubernetes Deployment",
            "sourceName": "Datadog Architecture Center",
            "sourceUrl": "https://www.datadoghq.com/architecture/observability-pipelines-kubernetes-deployment/",
            "imageUrl": "https://corp.dd-static.net/img/architecture/observability-pipelines-kubernetes-deployment/observability-pipelines-kubernetes-deployment.png?auto=format&fit=max&w=847&dpr=2",
            "whereItFits": f"This fits telemetry control for {domain.lower()} systems where logs can be high volume, sensitive, or expensive.",
            "beginnerExplanation": "The picture teaches that logs and events should be routed with intent. The SRE can filter noise, redact sensitive fields, route critical logs, and keep cost under control.",
            "whatToSay": "I treat telemetry as an engineering pipeline: collect, filter, enrich, redact, route, store, and prove that important incident evidence is still available.",
            "evidenceToCollect": ["pipeline route note", "filter or redaction rule", "before/after log volume", "critical log sample", "runbook update"],
        },
        {
            "title": "Efficient Kubernetes Monitoring With The Datadog Cluster Agent",
            "sourceName": "Datadog Architecture Center",
            "sourceUrl": "https://www.datadoghq.com/architecture/efficient-kubernetes-monitoring-with-the-datadog-cluster-agent/",
            "imageUrl": "https://corp.dd-static.net/img/architecture/efficient-kubernetes-monitoring-with-the-datadog-cluster-agent/efficient-kubernetes-monitoring-with-the-datadog-cluster-agent-1.png?auto=format&fit=max&w=847&dpr=2",
            "whereItFits": f"This fits shared cluster observability for {app_two} and other services running on the same platform.",
            "beginnerExplanation": "The picture teaches why a cluster-level component matters. It reduces duplicate work and gives the SRE a cleaner view of service discovery, checks, metadata, and workload health.",
            "whatToSay": "For platform-scale monitoring I separate node-level collection from cluster-level coordination, so service discovery and workload metadata stay consistent across teams.",
            "evidenceToCollect": ["cluster agent deployment view", "service discovery output", "workload metadata example", "monitor configuration", "ownership note"],
        },
    ]


def _project_delivery_plan(role_name: str, domain: str, applications: list[str], focus: list[str], is_full: bool) -> dict[str, Any]:
    phases = [
        ("Project onboarding and domain discovery", f"Map {domain} applications, users, product flows, technology teams, ownership boundaries, support contacts, and sensitive data paths."),
        ("Current-state architecture and operating model", f"Document how {applications[0]}, {applications[1]}, and related systems move through API, platform, data, security, support, and release processes."),
        ("Use-case design and implementation", f"Deliver role-specific use cases around {', '.join(focus[:5]).lower()} with realistic configuration, automation, validation, and operational evidence."),
        ("Production support and reliability improvement", "Use incidents, alerts, logs, dashboards, runbooks, release history, and RCA notes to show how issues were investigated and prevented."),
        ("Cross-team rollout and documentation", "Create architecture diagrams, workflow maps, ownership matrices, glossary, use-case boundaries, deliverables, and support handoff notes."),
        ("Interview and resume conversion", "Convert delivered work into project explanations, STAR stories, troubleshooting answers, resume bullets, and role-specific interview deep dives."),
    ]
    if is_full:
        phases[2] = (phases[2][0], phases[2][1] + " Add before/after metrics and audit-ready release evidence.")
    final_evidence = [
        "Architecture diagram",
        "Workflow diagram",
        "Use-case boundary sheet",
        "Tool configuration notes",
        "Screenshots or command outputs",
        "Runbook and incident simulation",
        "Interview story bank",
        "Resume bullets and project summary",
    ]
    if role_name == "Site Reliability / AIOps Engineer":
        final_evidence = [
            "Official Datadog architecture reference diagram connected to the project explanation",
            "Datadog-style service health dashboard with latency, errors, traffic, saturation, and dependency signals",
            "APM trace linked to logs, infrastructure or Kubernetes health, and deployment event evidence",
            "SLO or monitor configuration with alert threshold and escalation path",
            "Observability pipeline note for filtering, redaction, routing, and retention decisions",
            *final_evidence,
        ]
    return {
        "enterpriseContext": "Enterprise project narrative across a large IT organization supporting a broad application portfolio through multiple technology teams.",
        "phases": [{"phase": phase, "focus": focus_text} for phase, focus_text in phases],
        "finalEvidence": final_evidence,
    }


def _interview_talk_tracks(role_name: str, domain: str, applications: list[str], focus: list[str]) -> list[dict[str, str]]:
    return [
        {
            "prompt": "Two-minute project introduction",
            "answer": (
                f"{domain} platform delivery as a {role_name}. Business systems included {', '.join(applications[:5])}. "
                f"Role focus: {', '.join(focus[:5]).lower()}. {role_name} answer flow: {applications[0]} product flow, owned technical boundary, implementation example, support example."
            ),
        },
        {
            "prompt": "What exactly did you build?",
            "answer": (
                f"I built the repeatable engineering layer around {', '.join(focus[:4]).lower()}. "
                f"For {applications[0]}, that meant configuration, automation, validation, documentation, and support evidence that allowed application teams to release and operate safely."
            ),
        },
        {
            "prompt": "How do you prove it was real work?",
            "answer": (
                f"{applications[0]} artifact trail: request, design note, pull request or configuration, validation result, dashboard or log evidence, runbook, incident notes, and final status update. "
                f"The {role_name} story reads as implementation work because it includes decisions, evidence, validation, issue handling, and outcome."
            ),
        },
    ]


def _deliverables(role_name: str, domain: str, focus: list[str]) -> list[str]:
    return [
        f"{domain} application architecture map",
        f"{role_name} ownership matrix",
        *[f"{item.title()} standard" for item in focus[:5]],
        "Production support runbooks",
        "Deployment and rollback evidence",
        "Monitoring and alerting dashboard",
        "Interview story bank",
        "Resume project summary",
    ]


def _timeline(role_name: str, domain: str, applications: list[str], focus: list[str], is_full: bool) -> dict[str, list[str]]:
    app_one = applications[0]
    app_two = applications[1]
    app_three = applications[2]
    app_four = applications[3] if len(applications) > 3 else applications[0]
    app_five = applications[4] if len(applications) > 4 else applications[-1]
    analytics_app = applications[-1]
    project_context = [
        f"Single project scope: supported a {domain} enterprise platform across {app_one}, {app_two}, {app_three}, {app_four}, and {analytics_app}.",
        f"Business flow: mapped how users, product systems, operational teams, and reporting or support workflows depended on {app_one}.",
        f"Ownership boundary: documented product owner, application team, platform, data, database, security, QA, operations, and service desk responsibilities.",
        f"Environment map: captured dev, QA, stage, production, access model, approval path, support contacts, and operational gaps.",
        f"Architecture context: documented request path, release path, data/dependency path, support path, and ownership boundary for {app_one}.",
        f"Dependency view: showed how failures in {app_two}, {app_three}, queues, APIs, databases, or partner systems affected the main product workflow.",
        f"Risk view: identified hidden dependency risks involving {analytics_app}, databases, queues, APIs, and security controls.",
        f"Business outcome target: make the project easier to operate, validate, support, explain, and improve across teams.",
    ]
    implemented_use_cases = [
        f"Delivered baseline {focus[0]} and {focus[1]} artifacts for {app_one} with evidence that could be reviewed by leads.",
        f"Connected dashboards, logs, alerts, deployment history, tickets, and runbook references for {app_two}.",
        f"Created validation checklist, rollback trigger, smoke-check path, and production handoff format for {app_one}.",
        f"Converted manual handoffs into repeatable steps for request intake, implementation, validation, release, and support.",
        f"Applied the operating pattern to additional {domain} systems including {app_three}, {app_four}, and {app_five}.",
        f"Owned repeatable {role_name} deliverables across multiple {domain} application teams, not only {app_one}.",
        f"Improved {focus[2]} and {focus[3]} standards with review gates, reusable standards, and documented exceptions.",
        f"Added release evidence, access assumptions, approval records, and audit-friendly support notes for {app_one} and {app_two}.",
        f"Created or refined views for service health, owner, recent change, incident status, and next action.",
        f"Aligned {focus[0]}, {focus[1]}, {focus[2]}, and {focus[3]} across dev, QA, stage, and production.",
        f"Extended the mature operating pattern to more teams supporting {app_four}, {app_five}, and {analytics_app}.",
        f"Presented design tradeoffs around speed versus governance, central standards versus team flexibility, and alert volume versus actionable signals.",
    ]
    evidence_and_interview = [
        f"Handled recurring failure patterns around {app_two}, captured incident timelines, and documented owner-routing decisions.",
        "Maintained pull request/configuration records, screenshots, dashboard links, ticket notes, RCA notes, and support artifacts.",
        "Reduced ambiguity during support by giving teams a single path for impact check, signal review, owner routing, and recovery validation.",
        "Improved signal grouping across alerts, deployment events, infrastructure symptoms, dependency failures, and support tickets.",
        "Handled support scenarios using logs, metrics, traces, alerts, tickets, RCA notes, and runbooks.",
        "Converted repeated incidents into improved alert rules, validation checks, knowledge-base entries, or runbook updates.",
        "Gave leads concise status using impact, evidence, owner, action, risk, and next update time.",
        f"Onboarded application/support teams to the workflow with examples from {app_one}, {app_two}, and {analytics_app}.",
        "Created reusable troubleshooting entries, system glossary, ownership matrix, incident patterns, and evidence examples.",
        "Handled complex scenarios where symptoms crossed application, platform, data, database, network, and security boundaries.",
        "Organized architecture diagrams, workflow maps, Jira stories, runbooks, dashboards, RCA examples, and resume-ready project summaries.",
        f"Prepared deep-dive stories for {role_name} covering design, implementation, failure recovery, tradeoffs, validation, and business impact.",
    ]
    if is_full:
        project_context.append(f"Full-content track: added screenshots, architecture notes, and troubleshooting notes for each {domain} implementation artifact.")
        implemented_use_cases.append(f"Full-content track: documented before/after evidence showing reduced manual work or faster recovery for {app_one}.")
        evidence_and_interview.append(f"Full-content track: prepared senior-level stories covering design, failure recovery, audit evidence, and business impact for {role_name}.")
    return {
        "Project Context": project_context,
        "Implemented Use Cases": implemented_use_cases,
        "Evidence And Interview Stories": evidence_and_interview,
    }


def _interview_story(role_name: str, domain: str, applications: list[str], focus: list[str], is_full: bool) -> str:
    depth = " I also prepared audit-ready evidence, post-deployment validation, rollback notes, and production support runbooks." if is_full else ""
    return (
        f"{role_name} work supported {domain} applications such as {', '.join(applications[:5])}. "
        f"Responsibilities focused on {', '.join(focus[:6]).lower()}. Collaboration for {applications[0]} included application, security, infrastructure, QA, and operations teams across dev, QA, stage, and production. "
        f"{role_name} production issues were handled through logs, metrics, traces, deployment history, and runbooks to isolate cause, coordinate the fix, validate recovery, and document the result.{depth}"
    )


def _resume_summary(role_name: str, domain: str, applications: list[str], focus: list[str]) -> str:
    return (
        f"Supported a {domain} enterprise platform as a {role_name}, working across {', '.join(applications[:6])}. "
        f"Delivered {', '.join(focus[:5]).lower()}, production support, monitoring, documentation, and release readiness improvements using domain-aware cloud and platform engineering practices."
    )


def _scenarios(role_name: str, incidents: list[str]) -> list[str]:
    if role_name == "Site Reliability Engineer":
        return [*incidents, "Alert noise hid a customer-impacting symptom until SLO burn-rate alerting was tuned."]
    if role_name == "Data Platform Engineer":
        return [*incidents, "Data freshness dashboard showed stale warehouse tables after upstream schema drift."]
    if role_name == "Cloud Database Engineer":
        return [*incidents, "Database read replica lag increased after a reporting workload spiked."]
    return incidents


def _interview_questions(role_name: str, domain: str, focus: list[str]) -> list[str]:
    return [
        f"How did you apply {focus[0]} in a {domain} project?",
        f"Which {domain} applications did your {role_name} work support?",
        "How did you handle production deployment approvals and validation?",
        "How did you troubleshoot a failed production release or incident?",
        "How did you protect secrets, access, audit evidence, and regulated data?",
        f"How did you measure success for {focus[1]} or {focus[2]} improvements?",
        "How did you coordinate with developers, QA, security, infrastructure, and business teams?",
        "What would you improve if you joined a similar client project today?",
    ]


def _seed_role(role_name: str) -> SeedMarketingRole:
    ownership = ROLE_OWNERSHIP[role_name]
    return SeedMarketingRole(name=role_name, code=_slug(role_name), description=f"Seed role for {role_name}", covers=", ".join(ownership["focus"]), common_tools=", ".join(ownership["tools"]), aliases=role_name, keywords=role_name.lower())


def _unique(items: list[str]) -> list[str]:
    result = []
    seen = set()
    for item in items:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result


def _slug(value: str) -> str:
    return value.lower().replace(" / ", "-").replace(" & ", "-").replace(" ", "-").replace("/", "-")
