import json
import re
from pathlib import Path

from app.services.training_programs import (
    ALL_DOMAINS_LABEL,
    ALL_MARKETING_ROLES_LABEL,
    INDUSTRY_DOMAINS,
    MARKETING_ROLE_NAMES,
    filter_training_seed_records,
    training_program_seed_records,
)

from app.web.router import _pdf_provider_architecture_commands, _simple_text_pdf, _training_basics_14_day_plan, _training_basics_course_overview, _training_basics_devops_visual_reference, _training_basics_five_six_year_interview_questions, _training_basics_master_architecture, _training_basics_pdf_blocks, _training_basics_preparation_modules, _training_basics_topic_architecture_focus, _training_basics_topic_assessment, _training_basics_topic_sections, _training_cicd_security_pipeline_reference, _training_concept_coverage_map, _training_onboarding_assessment, _training_weekly_plan
from app.services.marketing_glossary import ROLE_TERMS
from app.services.product_systems import (
    BANKING_PRODUCT_SYSTEM_NAMES,
    DOMAIN_PRODUCT_SYSTEM_NAMES,
    banking_product_system_detail,
    product_system_detail,
    product_system_link_map,
    product_system_slug_lookup,
)
from app.web.router import _training_provider_usecase_sources
from app.web.router import _training_document_diagram_workbook, _training_program_pdf_blocks
from app.web.templates import templates
from types import SimpleNamespace


def _training_question_word_count(value):
    return len(re.findall(r"[A-Za-z0-9`/.-]+", value or ""))


def test_training_seed_supports_all_role_domain_combinations():
    records = training_program_seed_records()
    assert len(records) == len(MARKETING_ROLE_NAMES) * len(INDUSTRY_DOMAINS)
    assert {record["marketingRole"] for record in records} == set(MARKETING_ROLE_NAMES)
    assert {record["industryDomain"] for record in records} == set(INDUSTRY_DOMAINS)
    assert all(record["applicationLandscape"] for record in records)
    assert all(record["projectResponsibilities"] for record in records)
    assert all(record["interviewStory"] for record in records)


def test_filter_training_programs_all_filters():
    records = training_program_seed_records()
    assert len(filter_training_seed_records(records, ALL_MARKETING_ROLES_LABEL, ALL_DOMAINS_LABEL, "")) == len(MARKETING_ROLE_NAMES) * len(INDUSTRY_DOMAINS)


def test_filter_training_programs_by_role_only():
    records = training_program_seed_records()
    result = filter_training_seed_records(records, "DevOps Engineer", ALL_DOMAINS_LABEL, "")
    assert len(result) == len(INDUSTRY_DOMAINS)
    assert all(record["marketingRole"] == "DevOps Engineer" for record in result)


def test_filter_training_programs_by_domain_only():
    records = training_program_seed_records()
    result = filter_training_seed_records(records, ALL_MARKETING_ROLES_LABEL, "Healthcare / Health Insurance", "")
    assert len(result) == len(MARKETING_ROLE_NAMES)
    assert all(record["industryDomain"] == "Healthcare / Health Insurance" for record in result)


def test_filter_training_programs_by_role_domain_and_search():
    records = training_program_seed_records()
    result = filter_training_seed_records(records, "DevOps Engineer", "Healthcare / Health Insurance", "claims")
    assert len(result) == 1
    assert result[0]["marketingRole"] == "DevOps Engineer"
    assert result[0]["industryDomain"] == "Healthcare / Health Insurance"


def test_details_record_has_required_sections():
    record = filter_training_seed_records(training_program_seed_records(), "Cloud Platform Engineer", "Retail / E-Commerce", "")[0]
    assert record["cloudArchitecture"]["coreComponents"]
    assert len(record["cloudArchitecture"]["linesOfBusiness"]) == 3
    assert all(item["systems"] and item["jobSignals"] for item in record["cloudArchitecture"]["linesOfBusiness"])
    assert all(item["rolePlatformView"] and item["consultantExplanation"] and item["evidenceModel"] for item in record["cloudArchitecture"]["linesOfBusiness"])
    assert record["cloudArchitecture"]["roleDomainPlatform"]["guideShape"] == "Platform-foundation guide"
    assert record["cloudArchitecture"]["projectNarrativeReadingGuide"]["title"] == "How I Say This In An Interview"
    product_system_branch = next(item for item in record["cloudArchitecture"]["architectureMindmap"]["branches"] if item["title"] == "Product systems")
    assert product_system_branch["items"] == record["applicationLandscape"]
    workstreams = record["threeYearDeliveryTimeline"]
    assert list(workstreams) == ["Project Context", "Implemented Use Cases", "Evidence And Interview Stories"]
    assert len(workstreams["Project Context"]) >= 8
    assert len(workstreams["Implemented Use Cases"]) >= 12
    assert len(workstreams["Evidence And Interview Stories"]) >= 12
    workstream_text = " ".join(item for items in workstreams.values() for item in items)
    assert "Single project scope" in workstream_text
    assert "repeatable" in workstream_text.lower()
    assert all(marker not in workstream_text for marker in ["Q1", "Q2", "Q3", "Q4", "Year 1", "Year 2", "Year 3"])
    assert record["productionSupportScenarios"]
    assert record["resumeProjectSummary"]


def test_all_role_domain_programs_have_natural_project_narrative_reading_guide():
    records = training_program_seed_records()
    assert len(records) == len(MARKETING_ROLE_NAMES) * len(INDUSTRY_DOMAINS)
    for record in records:
        guide = record["cloudArchitecture"]["projectNarrativeReadingGuide"]
        label = f"{record['marketingRole']} / {record['industryDomain']}"
        assert guide["title"] == "How I Say This In An Interview", label
        assert len(guide["quickRead"]) == 5, label
        assert len(guide["readingOrder"]) == 5, label
        assert len(guide["sayThis"]) >= 5, label
        assert "I supported" in " ".join(guide["sayThis"]), label
        assert "I did not own business rules or feature code" in guide["anchorSentence"], label
        assert record["marketingRole"] in guide["anchorSentence"], label
        assert record["industryDomain"] in " ".join(item[1] for item in guide["quickRead"]), label
        assert "I owned the whole application." in guide["avoidSaying"], label


def test_details_record_has_consultant_quality_material():
    record = filter_training_seed_records(training_program_seed_records(), "DevOps Engineer", "Healthcare / Health Insurance", "claims")[0]
    architecture = record["cloudArchitecture"]

    assert "600 IT employees" in architecture["consultantProjectContext"]
    assert "100 applications" in architecture["consultantProjectContext"]
    assert architecture["enterpriseOperatingModel"]["scale"]
    assert len(architecture["architectureLayers"]) >= 5
    assert len(architecture["architectureFlows"]) >= 4
    assert architecture["architectureMindmap"]["root"]
    assert len(architecture["architectureMindmap"]["branches"]) >= 6
    assert len(architecture["roleArchitectureOwnership"]) >= 4
    assert len(architecture["componentResponsibilities"]) >= 6
    assert len(architecture["architectureInterviewExplanation"]) >= 4
    assert architecture["roleProductExplanation"]
    assert len(architecture["productGlossary"]) == 100
    assert [item["term"] for item in architecture["productGlossary"][:50]] == ROLE_TERMS["DevOps Engineer"][:50]
    assert all(item["sourceType"] == "role" for item in architecture["productGlossary"][:50])
    assert all(item["sourceType"] == "domain" for item in architecture["productGlossary"][50:])
    assert all(item["productMeaning"] and item["consultantTalkTrack"] and item["boundary"] for item in architecture["productGlossary"])
    assert all(item["consultantTalkTrackBullets"] and item["boundaryBullets"] for item in architecture["productGlossary"])
    assert len(architecture["useCaseBoundaries"]) >= 4
    assert all(item["inScope"] and item["outOfScope"] and item["implementationEvidence"] for item in architecture["useCaseBoundaries"])
    assert len(architecture["deliveredUseCases"]) >= 12
    assert all(item["businessProblem"] and item["deliveredScope"] and item["roleBoundary"] for item in architecture["deliveredUseCases"])
    assert architecture["sprintDeliveryModel"]["projectTracks"]
    assert "multiple Jira story groups" in " ".join(architecture["sprintDeliveryModel"]["summary"])
    assert architecture["maasInterviewBenchmark"]["coreQuestions"]
    assert architecture["maasInterviewBenchmark"]["followUpProbes"]
    assert architecture["maasInterviewBenchmark"]["pressureChecks"]
    assert architecture["maasInterviewBenchmark"]["evaluationFocus"]
    assert "MAAS" in architecture["maasInterviewBenchmark"]["source"]
    assert architecture["maasInterviewBenchmark"]["questionBank"]
    assert all(item["answerResponse"] and item["answerBullets"] and item["evidenceToMention"] for item in architecture["maasInterviewBenchmark"]["questionBank"])
    data_record = filter_training_seed_records(training_program_seed_records(), "Data Platform Engineer", "Telecom / Media / Communications", "")[0]
    data_benchmark = data_record["cloudArchitecture"]["maasInterviewBenchmark"]
    data_answer = next(
        item["answerResponse"]
        for item in data_benchmark["questionBank"]
        if any(token in item["question"].lower() for token in ["data", "schema", "backfill", "dashboard"])
    )
    assert "I explain a data issue" in data_answer
    assert "source to consumer" in data_answer
    assert "Trace source-to-consumer flow" not in data_answer
    assert "Protect consumers with" not in data_answer
    assert "Recovery evidence includes" not in data_answer
    assert len(architecture["maasInterviewBenchmark"]["readinessMatrix"]) >= 5
    assert all(item["mustKnow"] and item["programEvidence"] and item["mockCheck"] for item in architecture["maasInterviewBenchmark"]["readinessMatrix"])
    first_usecase = architecture["deliveredUseCases"][0]
    assert first_usecase["businessAnalystLens"]["businessCapability"]
    assert first_usecase["businessAnalystLens"]["businessActors"]
    assert first_usecase["businessAnalystLens"]["businessWorkflow"]
    assert first_usecase["businessAnalystLens"]["businessRules"]
    assert first_usecase["businessAnalystLens"]["kpisAndReports"]
    assert first_usecase["businessAnalystLens"]["baAcceptanceCriteria"]
    assert first_usecase["projectManagerLens"]["projectObjective"]
    assert first_usecase["projectManagerLens"]["scope"]
    assert first_usecase["projectManagerLens"]["stakeholders"]
    assert first_usecase["projectManagerLens"]["dependencies"]
    assert first_usecase["projectManagerLens"]["milestones"]
    assert first_usecase["projectManagerLens"]["deliveryRisks"]
    assert first_usecase["projectManagerLens"]["statusReporting"]
    assert first_usecase["projectManagerLens"]["pmAcceptanceCriteria"]
    assert first_usecase["qaTestLens"]["testStrategy"]
    assert first_usecase["qaTestLens"]["coverage"]
    assert first_usecase["qaTestLens"]["releaseGates"]
    assert first_usecase["productionSupportLens"]["supportModel"]
    assert first_usecase["productionSupportLens"]["incidentFlow"]
    assert first_usecase["productionSupportLens"]["opsHandoff"]
    assert first_usecase["securityComplianceLens"]["securityControls"]
    assert first_usecase["securityComplianceLens"]["complianceEvidence"]
    assert first_usecase["dataReportingLens"]["dataFlow"]
    assert first_usecase["dataReportingLens"]["dataQualityChecks"]
    assert first_usecase["dataReportingLens"]["reportingSignals"]
    assert first_usecase["productOwnerLens"]["productValue"]
    assert first_usecase["productOwnerLens"]["roadmapFit"]
    assert first_usecase["productOwnerLens"]["productAcceptanceCriteria"]
    pm_text = " ".join(
        [
            first_usecase["projectManagerLens"]["projectObjective"],
            " ".join(first_usecase["projectManagerLens"]["scope"]),
            " ".join(first_usecase["projectManagerLens"]["stakeholders"]),
            " ".join(first_usecase["projectManagerLens"]["dependencies"]),
            " ".join(first_usecase["projectManagerLens"]["milestones"]),
            " ".join(first_usecase["projectManagerLens"]["statusReporting"]),
        ]
    )
    assert "stakeholder" in pm_text.lower() or "product owner" in pm_text.lower()
    assert "dependency" in pm_text.lower()
    assert "milestone" in pm_text.lower()
    ba_text = " ".join(
        [
            first_usecase["businessAnalystLens"]["businessCapability"],
            " ".join(first_usecase["businessAnalystLens"]["businessActors"]),
            " ".join(first_usecase["businessAnalystLens"]["businessWorkflow"]),
            " ".join(first_usecase["businessAnalystLens"]["businessRules"]),
            " ".join(first_usecase["businessAnalystLens"]["kpisAndReports"]),
        ]
    )
    assert "SLA" in ba_text or "KPI" in ba_text or "dashboard" in ba_text
    assert "flow" in ba_text.lower()
    assert first_usecase["architectLens"]["decisionRationale"]
    assert first_usecase["architectLens"]["architecturalTradeoffs"]
    assert first_usecase["architectLens"]["constraints"]
    assert first_usecase["architectLens"]["nfrsAndControls"]
    assert first_usecase["architectLens"]["riskRegister"]
    architect_text = " ".join(
        [
            first_usecase["architectLens"]["seniorExplanation"],
            " ".join(first_usecase["architectLens"]["decisionRationale"]),
            " ".join(first_usecase["architectLens"]["architecturalTradeoffs"]),
            " ".join(first_usecase["architectLens"]["nfrsAndControls"]),
        ]
    )
    assert "Architecturally" in architect_text
    assert "versioned change steps" in architect_text
    assert "visible health signals" in architect_text
    assert "Security and compliance" in architect_text
    lens_text = " ".join(
        [
            first_usecase["qaTestLens"]["testStrategy"],
            first_usecase["productionSupportLens"]["supportModel"],
            " ".join(first_usecase["securityComplianceLens"]["securityControls"]),
            first_usecase["dataReportingLens"]["dataFlow"],
            first_usecase["productOwnerLens"]["productValue"],
        ]
    )
    assert "QA" in lens_text
    assert "Production support" in lens_text
    assert "least privilege" in lens_text
    assert "Data/reporting" in lens_text
    assert "Product reads" in lens_text
    assert len(first_usecase["textbookExplanation"].split()) >= 250
    assert "This use case describes" not in first_usecase["textbookExplanation"]
    assert len(first_usecase["textbookSections"]) >= 6
    assert all(section["title"] and section["bullets"] for section in first_usecase["textbookSections"])
    assert len(first_usecase["jiraStories"]) == 5
    assert [story["key"] for story in first_usecase["jiraStories"]] == ["JIRA-1", "JIRA-2", "JIRA-3", "JIRA-4", "JIRA-5"]
    assert len(first_usecase["interviewQuestionSet"]) == 10
    answer_text = " ".join(item["answer"] for item in first_usecase["interviewQuestionSet"])
    assert "I would answer" not in answer_text
    assert "In my project" not in answer_text
    assert all(item["answerBullets"] for item in first_usecase["interviewQuestionSet"])
    assert "the outputs were" in answer_text
    assert len(first_usecase["architectureQuestionSet"]) == 5
    assert len(first_usecase["systemDesignQuestionSet"]) == 5
    assert len(first_usecase["scenarioQuestionSet"]) == 5
    assert len(first_usecase["troubleshootingQuestionSet"]) == 5
    assert first_usecase["workflow"]["steps"]
    assert len(architecture["workflowDiagrams"]) >= 3
    assert all("->" in item["diagram"] and item["steps"] for item in architecture["workflowDiagrams"])
    assert "Enterprise project narrative" in architecture["projectDeliveryPlan"]["enterpriseContext"]
    assert all("duration" not in item for item in architecture["projectDeliveryPlan"]["phases"])
    assert architecture["interviewTalkTracks"]


def test_training_content_avoids_coaching_language():
    banned_phrases = [
        "This use case describes",
        "I would answer",
        "In my project",
        "Use this",
        "How to explain",
        "Interview-style answer",
        "Interview positioning",
        "Interview cue",
        "consultant should",
        "the consultant should",
        "the answer should",
        "answer should",
        "consultant explains",
        "Trainer should",
        "strong answer",
        "strong answers",
        "answer bullets",
        "How To Explain",
        "avoid vague",
        "interview-ready",
        "Use these as",
    ]
    records = training_program_seed_records()
    sample_records = [
        records[0],
        next(record for record in records if record["marketingRole"] == "Site Reliability / AIOps Engineer" and record["industryDomain"] == "Banking / Financial Services"),
        next(record for record in records if record["marketingRole"] == "MLOps / AI Platform Engineer" and record["industryDomain"] == "Retail / E-Commerce"),
    ]
    chunks = []
    for record in sample_records:
        chunks.extend(
            [
            record["shortDescription"],
            record["interviewStory"],
            record["resumeProjectSummary"],
            record["cloudArchitecture"]["roleProductExplanation"],
            record["cloudArchitecture"]["consultantProjectContext"],
            json.dumps(record["cloudArchitecture"]["maasInterviewBenchmark"]["marketSignals"]),
            json.dumps(record["cloudArchitecture"]["maasInterviewBenchmark"]["questionBank"]),
            " ".join(item["consultantTalkTrack"] for item in record["cloudArchitecture"]["productGlossary"]),
            " ".join(item["textbookExplanation"] for item in record["cloudArchitecture"]["deliveredUseCases"][:3]),
            " ".join(question["answer"] for item in record["cloudArchitecture"]["deliveredUseCases"][:2] for question in item["interviewQuestionSet"]),
            ]
        )
    content = " ".join(chunks)
    for phrase in banned_phrases:
        assert phrase.lower() not in content.lower()


def test_training_program_role_domain_content_avoids_template_language():
    banned = [
        "learner",
        "participant",
        "worksheet",
        "rubric",
        "template style",
        "generic",
        "answer standard",
        "response shape",
        "coaching",
        "practice standard",
        "readiness standard",
        "should be able",
        "can explain",
        "must know",
        "the goal is",
        "use these",
        "trainer",
        "classroom",
        "copied definitions",
        "memorized",
        "vague confidence",
        "disconnected tool",
        "the consultant",
        "can the",
    ]
    visible_keys = {
        "title",
        "shortDescription",
        "enterpriseContext",
        "cloudArchitecture",
        "projectResponsibilities",
        "threeYearDeliveryTimeline",
        "keyDeliverables",
        "interviewStory",
        "resumeProjectSummary",
        "productionSupportScenarios",
        "interviewQuestions",
    }

    def flatten(value):
        if isinstance(value, dict):
            for nested in value.values():
                yield from flatten(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from flatten(nested)
        elif isinstance(value, str):
            yield value

    for record in training_program_seed_records():
        text = " ".join(
            segment
            for key in visible_keys
            for segment in flatten(record.get(key))
        ).lower()
        for phrase in banned:
            assert phrase not in text


def test_generated_use_cases_replace_filler_evidence_with_functional_content():
    filler_phrases = {
        "Validation evidence",
        "Runbook/RCA/support handoff",
        "Jira story group",
        "Design note",
    }
    records = training_program_seed_records()
    sample_records = [
        records[0],
        next(record for record in records if record["marketingRole"] == "DevOps Engineer" and record["industryDomain"] == "Healthcare / Health Insurance"),
        next(record for record in records if record["marketingRole"] == "Cloud Platform Engineer" and record["industryDomain"] == "Logistics / Transportation"),
        next(record for record in records if record["marketingRole"] == "Data Platform Engineer" and record["industryDomain"] == "Banking / Financial Services"),
        next(record for record in records if record["marketingRole"] == "Site Reliability / AIOps Engineer" and record["industryDomain"] == "Insurance"),
        next(record for record in records if record["marketingRole"] == "MLOps / AI Platform Engineer" and record["industryDomain"] == "Retail / E-Commerce"),
    ]
    for record in sample_records:
        use_cases = record["cloudArchitecture"]["deliveredUseCases"][:10]
        assert use_cases
        for use_case in use_cases:
            evidence = use_case["evidenceToExplain"]
            assert not any(item in filler_phrases for item in evidence)
            assert any("showing" in item or "proof" in item or "outcome" in item for item in evidence)
            memory_points = use_case["whatToRemember"]
            assert len(memory_points) == 4
            combined = " ".join(memory_points)
            assert record["industryDomain"] in combined
            assert record["marketingRole"] in combined
            assert "not a tool list" in combined
            assert "business trigger" in combined


def test_provider_document_use_cases_are_prioritized_by_role():
    expected = {
        "Cloud Platform Engineer": "Private service access",
        "Data Platform Engineer": "Glue crawler",
        "DevOps Engineer": "Azure Databricks notebook",
        "MLOps / AI Platform Engineer": "SageMaker or Vertex AI",
        "Site Reliability / AIOps Engineer": "CloudWatch or Azure Monitor",
    }
    records = training_program_seed_records()
    for role, first_title in expected.items():
        domain = "Insurance" if role == "Data Platform Engineer" else "Healthcare / Health Insurance"
        record = next(item for item in records if item["marketingRole"] == role and item["industryDomain"] == domain)
        assert first_title in record["cloudArchitecture"]["deliveredUseCases"][0]["title"]


def test_healthcare_devops_training_includes_databricks_jd_gap_use_case():
    record = next(
        item
        for item in training_program_seed_records()
        if item["marketingRole"] == "DevOps Engineer"
        and item["industryDomain"] == "Healthcare / Health Insurance"
    )
    titles = [item["title"] for item in record["cloudArchitecture"]["deliveredUseCases"]]
    content = " ".join(
        [
            " ".join(titles),
            " ".join(item["businessProblem"] for item in record["cloudArchitecture"]["deliveredUseCases"][:6]),
            " ".join(" ".join(item["evidenceToExplain"]) for item in record["cloudArchitecture"]["deliveredUseCases"][:6]),
        ]
    )
    assert any("Azure Databricks notebook" in title for title in titles)
    assert "Azure DevOps pipeline" in content
    assert "Databricks CLI or REST API" in content
    assert "Azure Key Vault" in content
    assert "PHI" in content


def test_saas_devops_training_includes_brightedge_jd_gap_use_case():
    record = next(
        item
        for item in training_program_seed_records()
        if item["marketingRole"] == "DevOps Engineer"
        and item["industryDomain"] == "Technology / SaaS / Enterprise Software"
    )
    titles = [item["title"] for item in record["cloudArchitecture"]["deliveredUseCases"]]
    content = " ".join(
        [
            " ".join(titles),
            " ".join(item["businessProblem"] for item in record["cloudArchitecture"]["deliveredUseCases"][:6]),
            " ".join(" ".join(item["evidenceToExplain"]) for item in record["cloudArchitecture"]["deliveredUseCases"][:6]),
        ]
    )
    assert any("Hybrid AWS GCP and data center operations" in title for title in titles)
    assert "thousands of machines" in content
    assert "MySQL" in content
    assert "NoSQL" in content
    assert "Fortune 500" in content


def test_data_platform_training_includes_latest_jd_gap_use_cases():
    records = training_program_seed_records()
    manufacturing = next(
        item
        for item in records
        if item["marketingRole"] == "Data Platform Engineer"
        and item["industryDomain"] == "Manufacturing / Automotive / Industrial"
    )
    telecom = next(
        item
        for item in records
        if item["marketingRole"] == "Data Platform Engineer"
        and item["industryDomain"] == "Telecom / Media / Communications"
    )
    saas = next(
        item
        for item in records
        if item["marketingRole"] == "Data Platform Engineer"
        and item["industryDomain"] == "Technology / SaaS / Enterprise Software"
    )

    manufacturing_text = " ".join(
        [
            " ".join(item["title"] for item in manufacturing["cloudArchitecture"]["deliveredUseCases"]),
            " ".join(item["businessProblem"] for item in manufacturing["cloudArchitecture"]["deliveredUseCases"][:8]),
            " ".join(" ".join(item["evidenceToExplain"]) for item in manufacturing["cloudArchitecture"]["deliveredUseCases"][:8]),
        ]
    )
    assert "Manufacturing quality data pipeline" in manufacturing_text
    assert "MES data validation" in manufacturing_text
    assert "Defect prediction" in manufacturing_text
    assert "Live quality dashboard" in manufacturing_text
    assert "REST API and WebSocket" in manufacturing_text

    telecom_text = " ".join(
        [
            " ".join(item["title"] for item in telecom["cloudArchitecture"]["deliveredUseCases"]),
            " ".join(item["businessProblem"] for item in telecom["cloudArchitecture"]["deliveredUseCases"][:8]),
            " ".join(" ".join(item["evidenceToExplain"]) for item in telecom["cloudArchitecture"]["deliveredUseCases"][:8]),
        ]
    )
    assert "MDM customer and service-address golden record" in telecom_text
    assert "Legacy billing and CRM data migration" in telecom_text
    assert "Conceptual and logical broadband data model" in telecom_text
    assert "Digital media audience event pipeline" in telecom_text
    assert "Advertising and affiliate revenue analytics model" in telecom_text
    assert "React and TypeScript frontend data capture contract" in telecom_text
    assert "Machine-learning-ready audience insight dataset" in telecom_text
    assert "customer" in telecom_text
    assert "service address" in telecom_text

    saas_text = " ".join(
        [
            " ".join(item["title"] for item in saas["cloudArchitecture"]["deliveredUseCases"]),
            " ".join(item["businessProblem"] for item in saas["cloudArchitecture"]["deliveredUseCases"][:8]),
            " ".join(" ".join(item["evidenceToExplain"]) for item in saas["cloudArchitecture"]["deliveredUseCases"][:8]),
        ]
    )
    assert "Production-grade customer data pipeline service" in saas_text
    assert "AWS PySpark distributed processing pipeline" in saas_text
    assert "Data observability monitors" in saas_text
    assert "Backend service for pipeline execution metadata" in saas_text
    assert "ML platform dataset" in saas_text


def test_saas_cloud_platform_training_includes_agentic_ai_use_case():
    record = next(
        item
        for item in training_program_seed_records()
        if item["marketingRole"] == "Cloud Platform Engineer"
        and item["industryDomain"] == "Technology / SaaS / Enterprise Software"
    )
    titles = [item["title"] for item in record["cloudArchitecture"]["deliveredUseCases"]]
    content = " ".join(
        [
            " ".join(titles),
            " ".join(item["businessProblem"] for item in record["cloudArchitecture"]["deliveredUseCases"][:6]),
            " ".join(" ".join(item["evidenceToExplain"]) for item in record["cloudArchitecture"]["deliveredUseCases"][:6]),
            " ".join(item["textbookExplanation"] for item in record["cloudArchitecture"]["deliveredUseCases"][:2]),
        ]
    )
    assert any("Agentic AI platform engineering workflow" in title for title in titles)
    assert "GitHub Actions event trigger" in content
    assert "quality-gate report" in content
    assert "fix PR example" in content
    assert "Self-healing versus escalation boundary" in content
    assert "tool registry" in content
    assert "memory retention rule" in content


def test_generic_devops_training_includes_backup_dr_and_cloud_roadmap_language():
    record = next(
        item
        for item in training_program_seed_records()
        if item["marketingRole"] == "DevOps Engineer"
        and item["industryDomain"] == "Insurance"
    )
    content = " ".join(
        [
            " ".join(item["title"] for item in record["cloudArchitecture"]["deliveredUseCases"]),
            " ".join(item["businessProblem"] for item in record["cloudArchitecture"]["deliveredUseCases"]),
            " ".join(" ".join(item["evidenceToExplain"]) for item in record["cloudArchitecture"]["deliveredUseCases"]),
        ]
    )
    assert "Backup disaster recovery" in content
    assert "restore test" in content
    assert "RTO/RPO" in content
    assert "reference architecture diagram" in content
    assert "cloud roadmap decision log" in content


def test_training_basics_include_agile_and_any_role_readiness_gate():
    modules = _training_basics_preparation_modules()
    titles = [module["title"] for module in modules]
    assert any("Agile" in title and "Jira" in title and "Evidence" in title for title in titles)
    assert any("APIs" in title and "SQL" in title and "Evidence" in title for title in titles)
    terminal = modules[0]
    terminal_text = " ".join([terminal["title"], terminal["why"], " ".join(terminal["concepts"]), " ".join(terminal["commands"]), terminal["drill"], terminal["interview"]])
    assert "Linux" in terminal_text
    assert "SSH" in terminal_text
    assert "DNS" in terminal_text
    assert "HTTP" in terminal_text
    cicd = next(module for module in modules if "CI/CD" in module["title"])
    cicd_text = " ".join([cicd["why"], " ".join(cicd["concepts"]), " ".join(cicd["commands"]), cicd["drill"], cicd["interview"]])
    assert "artifact repository" in cicd_text
    assert "container registry" in cicd_text
    assert "plan, code, commit, build, test, artifact, deploy, run, monitor" in cicd_text
    bridge = modules[-1]
    assert "Enterprise Lifecycle" in bridge["title"]
    assert "Readiness Exam" in bridge["title"]
    combined = " ".join(
        [
            bridge["why"],
            " ".join(bridge["concepts"]),
            " ".join(bridge["commands"]),
            bridge["drill"],
            bridge["interview"],
        ]
    )
    assert "role/domain training" in combined


def test_training_includes_enterprise_application_lifecycle_concepts():
    modules = _training_basics_preparation_modules()
    basics_text = " ".join(
        [
            module["title"]
            + " "
            + module["why"]
            + " "
            + " ".join(module["concepts"])
            + " "
            + module["interview"]
            for module in modules
        ]
    )
    for concept in [
        "application sunrise",
        "application sunset",
        "legacy migration",
        "DNS cutover",
        "database migration",
        "data archival and retention",
        "CMDB / ServiceNow ownership update",
        "production readiness review",
        "support model transition",
    ]:
        assert concept in basics_text

    record = next(item for item in training_program_seed_records() if item["marketingRole"] == "Cloud Platform Engineer")
    use_case_text = " ".join(
        [
            " ".join(item["title"] for item in record["cloudArchitecture"]["deliveredUseCases"]),
            " ".join(item["businessProblem"] for item in record["cloudArchitecture"]["deliveredUseCases"]),
            " ".join(" ".join(item["deliveredScope"]) for item in record["cloudArchitecture"]["deliveredUseCases"]),
            " ".join(" ".join(item["evidenceToExplain"]) for item in record["cloudArchitecture"]["deliveredUseCases"]),
        ]
    )
    assert "Enterprise application lifecycle" in use_case_text
    assert "sunrise" in use_case_text
    assert "sunset" in use_case_text
    assert "modernization" in use_case_text
    assert "cutover" in use_case_text
    assert "operational transition" in use_case_text
    assert "backup/restore proof" in use_case_text
    assert "DR test evidence" in use_case_text
    assert "ServiceNow" in use_case_text
    automation = next(module for module in modules if "Shell And Python Automation" in module["title"])
    automation_text = " ".join([automation["why"], " ".join(automation["concepts"]), " ".join(automation["commands"]), automation["drill"], automation["interview"], " ".join(automation["interview_examples"])])
    assert "Shell scripting" in automation_text
    assert "Python automation" in automation_text
    assert "Jenkins trigger" in automation_text
    assert "Kubernetes deployment" in automation_text
    assert "Terraform apply" in automation_text
    assert "image scanning" in automation_text
    assert "health-check script" in automation_text


def test_training_basics_include_devops_visual_reference_notes():
    reference = _training_basics_devops_visual_reference()
    combined = " ".join(
        [
            reference["title"],
            reference["summary"],
            " ".join(reference["loop"]),
            " ".join(reference["pipeline"]),
            " ".join(reference["platform"]),
            " ".join(f"{item['title']} {item['caption']}" for item in reference["image_panels"]),
            " ".join(reference["notes"]),
            " ".join(reference["interview_notes"]),
        ]
    )
    assert "Development To Operations Loop" in combined
    assert "Code Build Test Release Deploy Operate Monitor" in combined
    assert "Source control CI CD Deployment Monitoring" in combined
    assert "AWS / Azure / GCP" in combined
    assert "Docker / Kubernetes" in combined
    assert "faster delivery" in combined.lower()
    assert "higher reliability" in combined.lower()
    assert "safe delivery, reliable operations, observability, security, and recovery" in combined
    assert "Develop Code, build, test" in combined
    assert "Release Move validated artifacts" in combined
    assert "Operate Run, monitor, secure" in combined
    pdf_text = " ".join(block["text"] for block in _training_basics_pdf_blocks())
    assert "DevOps Visual Reference" in pdf_text
    assert "Visual flow" in pdf_text
    assert "Image panels" in pdf_text
    assert "Interview notes" in pdf_text


def test_training_includes_cicd_security_pipeline_image_reference():
    reference = _training_cicd_security_pipeline_reference()
    assert reference["imageUrl"] == "/static/training/devops-cicd-security-pipeline.gif"
    combined = " ".join([reference["title"], reference["whereItFits"], " ".join(reference["flow"]), " ".join(reference["interviewNotes"])])
    assert "Jenkins" in combined
    assert "OWASP" in combined
    assert "SonarQube" in combined
    assert "Trivy" in combined
    assert "Argo CD" in combined
    assert "Kubernetes" in combined
    assert "Prometheus" in combined
    assert "Grafana" in combined
    pdf_text = " ".join(block["text"] for block in _training_basics_pdf_blocks())
    assert "CI/CD Security Pipeline Visual" in pdf_text
    assert reference["imageUrl"] in pdf_text


def test_training_basics_sections_include_theory_and_common_interview_examples():
    modules = _training_basics_preparation_modules()
    assert all(module["theory"] for module in modules)
    assert all(module["interview_examples"] for module in modules)
    assert all(len(module["flowchart"]) >= 6 for module in modules)
    combined = " ".join(
        " ".join(module["interview_examples"] + module["flowchart"]) for module in modules
    )
    assert "CrashLoopBackOff" in combined
    assert "pipeline failed" in combined
    assert "API returns 500" in combined
    assert "Terraform plan" in combined
    assert "access denied" in combined
    assert "row count" in combined
    assert "Jira story" in combined
    pdf_text = " ".join(block["text"] for block in _training_basics_pdf_blocks())
    assert "Fundamentals checklist" in pdf_text
    assert "Interview examples" in pdf_text
    assert "Flowchart" in pdf_text
    assert "Project scenario" in pdf_text
    assert "Natural screening answer" in pdf_text
    assert "Likely interview questions" in pdf_text
    assert "Commit -> Build -> Test/scan -> Artifact -> Deploy -> Monitor/rollback" in pdf_text


def test_training_basics_include_linux_git_and_docker_command_maps():
    modules = _training_basics_preparation_modules()
    linux_module = next(module for module in modules if module["title"].startswith("1. RHEL"))
    git_module = next(module for module in modules if module["title"].startswith("2. Git"))
    docker_module = next(module for module in modules if module["title"].startswith("3. Docker"))
    linux_text = " ".join(
        group["group"] + " " + group["context"] + " " + " ".join(command["command"] + " " + command["meaning"] for command in group["commands"])
        for group in linux_module["command_groups"]
    )
    git_text = " ".join(
        group["group"] + " " + group["context"] + " " + " ".join(command["command"] + " " + command["meaning"] for command in group["commands"])
        for group in git_module["command_groups"]
    )
    docker_text = " ".join(
        group["group"] + " " + group["context"] + " " + " ".join(command["command"] + " " + command["meaning"] for command in group["commands"])
        for group in docker_module["command_groups"]
    )
    assert "RHEL Login Context And File Navigation" in linux_text
    assert "Daily Linux File And Directory Work" in linux_text
    assert "Reading Application And Service Logs" in linux_text
    assert "Simple Shell Automation" in linux_text
    assert "SSH And Remote Server Access" in linux_text
    assert "Environment Variables And Runtime Config" in linux_text
    assert "Packages And DevOps Tools On Linux" in linux_text
    assert "User-Facing Permission Problems" in linux_text
    assert "pwd" in linux_text
    assert "cp <source> <target>" in linux_text
    assert "mv <source> <target>" in linux_text
    assert "rm <file>" in linux_text
    assert "head -n 20 <file>" in linux_text
    assert "tail -f /var/log/payment-api/app.log" in linux_text
    assert "chmod +x check-payment-api.sh" in linux_text
    assert "ssh -i key.pem user@host" in linux_text
    assert "scp <file> user@host:/path" in linux_text
    assert "telnet <host> <port>" in linux_text
    assert "nc -vz <host> <port>" in linux_text
    assert "dig <host>" in linux_text
    assert "traceroute <host>" in linux_text
    assert "echo $JAVA_HOME" in linux_text
    assert "grep -n \"DATABASE_URL\" .env" in linux_text
    assert "kubectl version --client" in linux_text
    assert "terraform version" in linux_text
    assert "chmod +x deploy.sh" in linux_text
    assert "sudo systemctl status payment-api" in linux_text
    assert "sudo journalctl -u payment-api -n 50 --no-pager" in linux_text
    assert "sudo firewall-cmd --list-ports" in linux_text
    assert "getenforce" in linux_text
    assert "ss -tulpn" in linux_text
    assert "Start A Working Area" in git_text
    assert "git bisect" in git_text
    assert "git fetch" in git_text
    assert "Common Container Workflow" in docker_text
    assert "docker run <image>" in docker_text
    assert "docker --help" in docker_text
    pdf_text = " ".join(block["text"] for block in _training_basics_pdf_blocks())
    assert "RHEL Login Context And File Navigation" in pdf_text
    assert "Daily Linux File And Directory Work" in pdf_text
    assert "Environment Variables And Runtime Config" in pdf_text
    assert "grep -n \"DATABASE_URL\" .env" in pdf_text
    assert "kubectl version --client" in pdf_text
    assert "chmod +x deploy.sh" in pdf_text
    assert "sudo journalctl -u payment-api -n 50 --no-pager" in pdf_text
    assert "git rebase <base>" in pdf_text
    assert "docker stats" in pdf_text
    assert "docker <command> --help" in pdf_text


def test_training_basics_include_kubernetes_command_map_from_reference():
    modules = _training_basics_preparation_modules()
    kubernetes_module = next(module for module in modules if module["title"].startswith("4. Kubernetes"))
    kubernetes_text = " ".join(
        group["group"] + " " + group["context"] + " " + " ".join(command["command"] + " " + command["meaning"] for command in group["commands"])
        for group in kubernetes_module["command_groups"]
    )
    assert "Cluster And Context" in kubernetes_text
    assert "kubectl cluster-info" in kubernetes_text
    assert "kubectl config get-contexts" in kubernetes_text
    assert "kubectl scale deployment <deployment_name> --replicas=<number> -n <namespace>" in kubernetes_text
    assert "kubectl top nodes" in kubernetes_text
    assert "ConfigMap" in kubernetes_text
    assert "Persistent Volume" in kubernetes_text
    pdf_text = " ".join(block["text"] for block in _training_basics_pdf_blocks())
    assert "kubectl rollout restart deployment <deployment_name> -n <namespace>" in pdf_text
    assert "kubectl get pvc -n <namespace>" in pdf_text


def test_training_basics_all_topics_have_tool_command_maps():
    modules = _training_basics_preparation_modules()
    expected_terms = {
        "5. Cloud": ["aws sts get-caller-identity", "az account show", "gcloud config list"],
        "6. CI/CD": ["gh run view <run-id> --log", "docker push <registry>/app:${GIT_SHA}", "kubectl rollout status deploy/app -n app"],
        "7. Observability": ["curl -w", "aws logs tail", "az monitor metrics list"],
        "8. Terraform": ["terraform init", "terraform plan", "terraform state list"],
        "9. Security": ["aws secretsmanager list-secrets", "az keyvault secret list", "kubectl get secrets -n app"],
        "10. Agile": ["curl -i https://example.com/health", "jq . sample.json", "psql -c"],
        "11. Ansible": ["ansible all -i inventory.ini -m ping", "ansible-playbook -i inventory.ini playbook.yml", "python3 scripts/call_api.py"],
        "12. Enterprise": ["dig app.example.com", "curl -i https://app.example.com/health", "Application inventory row"],
    }
    assert all(module["command_groups"] for module in modules)
    for title_start, terms in expected_terms.items():
        module = next(item for item in modules if item["title"].startswith(title_start))
        text = " ".join(
            group["group"] + " " + group["context"] + " " + " ".join(command["command"] + " " + command["meaning"] for command in group["commands"])
            for group in module["command_groups"]
        )
        for term in terms:
            assert term in text


def test_training_basics_topic_sections_have_expanded_detail_pages_and_answers():
    modules = _training_basics_preparation_modules()
    expected_labels = [
        "Core Concept Model",
        "Scenario Practice",
        "Failure Patterns",
        "Evidence Package",
        "Mostly Asked Interview Examples",
        "Checkpoint Questions",
        "Readiness Check",
    ]
    for topic_number, module in enumerate(modules, start=1):
        sections = _training_basics_topic_sections(module, topic_number)
        assert [section["label"] for section in sections] == expected_labels
        assert all(section["detail_url"].startswith(f"/training-basics/topics/{topic_number}/sections/") for section in sections)
        assert all(len(section["preview"]) <= 4 for section in sections)
        assert all(section["expanded_material"] for section in sections)
        assert all(10 <= len(section["qa"]) <= 15 for section in sections)
        assert all(item["question"] and item["answer"] and item["faangDepth"] for section in sections for item in section["qa"])
        assert all(item["faangDepth"]["commands"] and item["faangDepth"]["expectedSignal"] and item["faangDepth"]["failureSignal"] for section in sections for item in section["qa"])


def test_training_basics_cicd_core_section_has_visual_and_specific_answers():
    module = _training_basics_preparation_modules()[5]
    section = _training_basics_topic_sections(module, 6)[0]
    assert section["key"] == "core-concept-model"
    visual_text = " ".join(
        [visual["title"] + " " + visual["caption"] for visual in section["visuals"]]
        + [node["label"] + " " + node["meta"] for visual in section["visuals"] for node in visual["nodes"]]
    )
    qa_text = " ".join(item["question"] + " " + item["answer"] for item in section["qa"])
    expanded_text = " ".join(
        [block["heading"] + " " + block["body"] for block in section["expanded_material"]]
        + [bullet for block in section["expanded_material"] for bullet in block["bullets"]]
    )
    assert "CI/CD Release Flow" in visual_text
    assert "Commit / PR" in visual_text
    assert "Artifact / Image" in visual_text
    assert "Smoke + Monitor" in visual_text
    assert "What is CI/CD in the Digital Banking Onboarding And Payments Platform project?" in qa_text
    assert "pipeline run ID" in qa_text
    assert "kubectl rollout status deploy/app -n app" in qa_text
    assert "CI means continuous integration" in expanded_text
    assert "Commands are useful only when they prove" in visual_text


def test_training_basics_expanded_sections_explain_instead_of_lab_instructions():
    module = _training_basics_preparation_modules()[0]
    sections = {section["key"]: section for section in _training_basics_topic_sections(module, 1)}
    scenario_text = " ".join(
        [block["body"] for block in sections["scenario-practice"]["expanded_material"]]
        + [bullet for block in sections["scenario-practice"]["expanded_material"] for bullet in block["bullets"]]
    )
    failure_text = " ".join(
        [block["body"] for block in sections["failure-patterns"]["expanded_material"]]
        + [bullet for block in sections["failure-patterns"]["expanded_material"] for bullet in block["bullets"]]
    )
    assert "Digital Banking Onboarding And Payments Platform" in scenario_text
    assert "usually visible as a small" not in scenario_text
    assert "visible operating need" not in scenario_text
    assert "service, file, process, resource, release, or integration" not in scenario_text
    assert "someone reading a checklist" not in scenario_text
    assert "project functionality" not in scenario_text.lower()
    assert "Run `pwd`" not in scenario_text
    assert "Create a lab folder" not in scenario_text
    assert "Memorizing commands without knowing" not in failure_text
    assert "RHEL-hosted app can fail" in failure_text
    assert "systemd" in failure_text
    assert "firewalld" in failure_text
    assert "SELinux" in failure_text


def test_training_basics_detailed_answers_are_interview_responses_not_meta_coaching():
    banned_phrases = [
        "the consultant should",
        "consultant should",
        "the answer should",
        "answer should",
        "use these as",
        "strong answer",
        "strong answers",
        "how to explain",
        "interview cue",
        "interview-ready",
        "consultant explains",
        "interview preparation",
        "scripted tool list",
        "i would",
        "answer pattern",
        "sound real",
        "what should",
        "the consultant",
        "technical interviewer",
        "usually visible as a small",
        "visible operating need",
        "someone reading a checklist",
        "project functionality",
        "service, file, process, resource, release, or integration",
        "same pattern repeatedly",
    ]
    modules = _training_basics_preparation_modules()
    for topic_number, module in enumerate(modules, start=1):
        topic_questions = []
        for section in _training_basics_topic_sections(module, topic_number):
            section_questions = [item["question"] for item in section["qa"]]
            assert len(section_questions) == len(set(section_questions))
            topic_questions.extend(section_questions)
            answers = " ".join(item["answer"] for item in section["qa"])
            depth_text = " ".join(
                " ".join(
                    [
                        item["faangDepth"]["heading"],
                        item["faangDepth"]["followUp"],
                        " ".join(item["faangDepth"]["commands"]),
                        item["faangDepth"]["expectedSignal"],
                        item["faangDepth"]["failureSignal"],
                        item["faangDepth"]["reasoning"],
                        item["faangDepth"]["tradeoff"],
                        item["faangDepth"]["prevention"],
                        item["faangDepth"]["sampleClose"],
                    ]
                )
                for item in section["qa"]
            )
            expanded_material = " ".join(
                [block["heading"] + " " + block["body"] for block in section["expanded_material"]]
                + [bullet for block in section["expanded_material"] for bullet in block["bullets"]]
                + [section["summary"]]
            )
            section_text = f"{answers} {depth_text} {expanded_material}"
            for phrase in banned_phrases:
                assert phrase not in section_text.lower()
            assert "use a concise answer:" not in section_text.lower()
            assert "I " in answers or "My " in answers
            assert all(item["faangDepth"]["expectedSignal"] for item in section["qa"])
            assert all(item["faangDepth"]["failureSignal"] for item in section["qa"])
            assert all(item["faangDepth"]["tradeoff"] for item in section["qa"])
            assert all(item["faangDepth"]["prevention"] for item in section["qa"])
        assert len(topic_questions) == len(set(topic_questions))
    first_sections = _training_basics_topic_sections(modules[0], 1)
    assert first_sections[0]["qa"][0]["question"] == "What is RHEL server in RHEL System Administration, Linux, Networking, And Troubleshooting?"
    assert first_sections[1]["qa"][0]["question"] == "What is the day-to-day RHEL admin practice for Topic 1 in the Digital Banking Onboarding And Payments Platform project?"
    assert first_sections[2]["qa"][0]["question"] == "What failure pattern do you commonly see on a RHEL application host?"


def test_training_path_is_twelve_topic_foundation_and_four_weeks_role_domain():
    plan = _training_basics_14_day_plan()
    assert len(plan) == 12
    assert plan[0]["day"] == "1"
    assert plan[-1]["day"] == "12"
    assert all(item["detailUrl"].startswith("/training-basics/topics/") for item in plan)
    assert all(item["learn"] and item["scenario"] and len(item["practice"]) >= 4 for item in plan)
    assert all(item["commands"] and item["screeningQuestions"] and item["screeningAnswer"] for item in plan)
    assert all(item["studyMode"] == "10-hour topic" for item in plan)
    assert all(item["hours"] == "10" for item in plan)
    assert all(item["projectName"] == "Digital Banking Onboarding And Payments Platform" for item in plan)
    assert all(item["courseSections"] and item["dailyPlan"] and item["labFocus"] for item in plan)
    assert "Technical Basics + DevOps Foundation" in plan[0]["courseTitle"]
    assert plan[4]["focus"] == "Cloud Foundation For Project Interviews"
    assert "MFA and budget alerts" in plan[4]["courseSections"]
    assert "Checkpoint Test" in plan[5]["focus"]
    assert "Terraform" in plan[7]["focus"]
    assert "APIs" in plan[9]["focus"]
    assert "Ansible" in plan[10]["focus"]
    assert "Final Exam" in plan[11]["focus"]
    overview = _training_basics_course_overview()
    assert len(overview["projects"]) == 4
    assert overview["courseTitle"] == "Merged 120-Hour Technical Basics + DevOps Foundation"
    assert any("120 hours" in item for item in overview["learning"])
    reference_text = json.dumps(overview["industryReferences"])
    assert "backbase.com/blog/digital-banking-platform" in reference_text
    assert "crassula.io/blog/digital-banking-architecture" in reference_text
    assert "bepeerless.co/blog/microservices-vs-monolithic-architecture" in reference_text
    assert "kms-technology.com/blog/core-banking-system" in reference_text
    assert "core banking" in reference_text
    assert "service and orchestration layer" in reference_text
    assert "fault isolation" in reference_text
    assert "targeted scaling" in reference_text
    assert "security and compliance by design" in reference_text
    assert "real-time event-driven processing" in reference_text
    assert "Customer Onboarding Diagram" in json.dumps(overview["visualDiagrams"])
    assert all("Digital Banking Onboarding And Payments Platform" in (overview["summary"] + " ".join(item["purpose"] for item in overview["projects"])) for _ in [None])
    pdf_text = " ".join(block["text"] for block in _training_basics_pdf_blocks())
    assert "12 topics at 10 hours per topic" in pdf_text
    assert "12-Topic / 120-Hour Preparation Schedule" in pdf_text
    assert "What this topic teaches" in pdf_text
    assert "Commands and artifacts" in pdf_text
    assert "Expected Interview Questions For 5-6 Years JDs" in pdf_text
    assert "10-hour study structure" in pdf_text
    assert "Course spine" in pdf_text
    assert "Digital Banking Onboarding And Payments Platform project flows" in pdf_text
    assert "Industry references" in pdf_text
    assert "backbase.com/blog/digital-banking-platform" in pdf_text
    assert "crassula.io/blog/digital-banking-architecture" in pdf_text
    assert "bepeerless.co/blog/microservices-vs-monolithic-architecture" in pdf_text
    assert "kms-technology.com/blog/core-banking-system" in pdf_text
    assert "Book Review App" not in pdf_text
    assert "8-10" not in pdf_text

    record = next(item for item in training_program_seed_records() if item["marketingRole"] == "DevOps Engineer")
    program = SimpleNamespace(
        marketing_role=SimpleNamespace(name=record["marketingRole"]),
        industry_domain=record["industryDomain"],
    )
    weekly = _training_weekly_plan(program)
    assert len(weekly) == 5
    assert weekly[0]["week"] == "Prerequisite Foundation"
    assert [item["week"] for item in weekly[1:]] == ["Role Week 1", "Role Week 2", "Role Week 3", "Role Week 4"]
    assert "12-topic, 120-hour foundation" in weekly[0]["goal"]
    assert "Digital Banking Onboarding And Payments Platform" in weekly[0]["goal"]
    assert "role/domain labs" in weekly[3]["goal"]


def test_training_basics_index_embeds_master_architecture_diagram():
    modules = _training_basics_preparation_modules()
    response = templates.TemplateResponse(
        "web/training_basics.html",
        {
            "request": SimpleNamespace(url=SimpleNamespace(path="/training-basics"), session={}),
            "user": SimpleNamespace(role="admin"),
            "selected_section": "overview",
            "section_tabs": [],
            "basics_modules": modules,
            "command_map_modules": [module for module in modules if module.get("command_groups")],
            "course_overview": _training_basics_course_overview(),
            "master_architecture": _training_basics_master_architecture(),
            "devops_visual_reference": _training_basics_devops_visual_reference(),
            "cicd_security_pipeline_reference": _training_cicd_security_pipeline_reference(),
            "basics_day_plan": _training_basics_14_day_plan(),
            "five_six_year_questions": _training_basics_five_six_year_interview_questions(),
            "consultant_profile": None,
            "assigned_training_program": None,
        },
    )
    body = response.body.decode()
    assert "Master Architecture: Digital Banking Onboarding And Payments Platform" in body
    assert "AKS/EKS Runtime" in body
    assert "payment-api deployment and pods" in body
    assert "Core banking ledger / system of record" in body
    assert "Core connectivity and API normalization" in body
    assert "Backbase: Digital banking platform architecture for the AI era" in body
    assert "Crassula: Digital banking architecture key elements and best practices" in body
    assert "Peerless: Microservices vs monolithic architecture for core banking" in body
    assert "KMS Technology: The future of core banking systems" in body
    assert "Real-Time Reference Signals" in body
    assert "Visual Diagrams Used In The Guide" in body
    assert "Payment Processing Diagram" in body
    assert "Terraform modules and Ansible runbooks" in body
    assert "How The 12 Topics Attach" in body


def test_role_domain_guides_are_concise_consultant_explanations_not_bulk_templates():
    records = training_program_seed_records()
    expected_shapes = {
        "DevOps Engineer": "Release-story guide",
        "Cloud Platform Engineer": "Platform-foundation guide",
        "Site Reliability / AIOps Engineer": "Operations-control-room guide",
        "Data Platform Engineer": "Source-to-consumer data guide",
        "MLOps / AI Platform Engineer": "Model-lifecycle guide",
    }
    shapes = {}
    for role, expected_shape in expected_shapes.items():
        record = next(item for item in records if item["marketingRole"] == role and item["industryDomain"] == "Banking / Financial Services")
        architecture = record["cloudArchitecture"]
        shapes[role] = architecture["roleDomainPlatform"]["guideShape"]
        assert shapes[role] == expected_shape
        assert all(role in lob["rolePlatformView"] for lob in architecture["linesOfBusiness"])
    assert len(set(shapes.values())) == len(expected_shapes)
    record = next(item for item in records if item["marketingRole"] == "Data Platform Engineer" and item["industryDomain"] == "Banking / Financial Services")
    program = SimpleNamespace(
        id=1,
        title=record["title"],
        short_description=record["shortDescription"],
        enterprise_context=record["enterpriseContext"],
        industry_domain=record["industryDomain"],
        application_landscape=record["applicationLandscape"],
        cloud_architecture=record["cloudArchitecture"],
        project_responsibilities=record["projectResponsibilities"],
        key_deliverables=record["keyDeliverables"],
        tools_and_technologies=record["toolsAndTechnologies"],
        interview_story=record["interviewStory"],
        resume_project_summary=record["resumeProjectSummary"],
        production_support_scenarios=record["productionSupportScenarios"],
        three_year_delivery_timeline=record["threeYearDeliveryTimeline"],
        marketing_role=SimpleNamespace(name=record["marketingRole"], common_tools=", ".join(record["toolsAndTechnologies"])),
    )
    text = " ".join(block["text"] for block in _training_program_pdf_blocks(program, include_diagrams=True))
    assert "600-Page Role And Domain Scenario Workbook" not in text
    assert "Consultant explanation" in text
    assert "Business analyst view" in text
    assert "Senior architect view" in text
    assert "Full Project Narrative - Readable Version" in text
    assert "Real interview questions" in text
    assert "What exactly did you contribute" in text
    assert "Enterprise delivery context for Banking / Financial Services technology systems" not in text
    assert len(text) < 450000


def test_training_basics_topic_page_embeds_command_mindmap():
    modules = _training_basics_preparation_modules()
    topic_number = 4
    module = modules[topic_number - 1]
    response = templates.TemplateResponse(
        "web/training_basics_topic.html",
        {
            "request": SimpleNamespace(url=SimpleNamespace(path="/training-basics/topics/4"), session={}),
            "user": SimpleNamespace(role="admin"),
            "module": module,
            "day": _training_basics_14_day_plan()[topic_number - 1],
            "topic_number": topic_number,
            "topic_total": len(modules),
            "previous_topic": topic_number - 1,
            "next_topic": topic_number + 1,
            "topic_sections": _training_basics_topic_sections(module, topic_number),
            "architecture_focus": _training_basics_topic_architecture_focus(topic_number),
        },
    )
    body = response.body.decode()
    assert "Topic Mindmap" in body
    assert "Architecture Layer" in body
    assert "Commands And Proof" in body
    assert "Project Evidence" in body
    assert "kubectl get" in body


def test_onboarding_assessment_uses_only_basic_prep_and_selected_role_domain_material():
    record = filter_training_seed_records(
        training_program_seed_records(),
        "DevOps Engineer",
        "Healthcare / Health Insurance",
        "",
    )[0]
    program = SimpleNamespace(
        marketing_role=SimpleNamespace(
            name=record["marketingRole"],
            common_tools=", ".join(record["toolsAndTechnologies"]),
            description="DevOps Engineer delivery and support workflow",
        ),
        industry_domain=record["industryDomain"],
        application_landscape=record["applicationLandscape"],
        cloud_architecture=record["cloudArchitecture"],
        project_responsibilities=record["projectResponsibilities"],
        key_deliverables=record["keyDeliverables"],
        tools_and_technologies=record["toolsAndTechnologies"],
        production_support_scenarios=record["productionSupportScenarios"],
    )

    assessment = _training_onboarding_assessment(program)
    questions = assessment["questions"]
    assert assessment["totalQuestions"] == 30
    assert assessment["allowedTypes"] == ["single_choice", "multi_select"]
    assert assessment["sections"][0]["questionCount"] == 18
    assert assessment["sections"][1]["questionCount"] == 12
    assert "Technical Basics + DevOps Foundation" in assessment["sourceRule"]
    assert "selected marketing role/domain" in assessment["sourceRule"]
    assert {question["sourceType"] for question in questions} == {"basic_prep", "marketing_role_domain"}
    assert all(question["type"] in {"single_choice", "multi_select"} for question in questions)
    assert all(question["correctAnswers"] for question in questions)
    assert all(len(question["options"]) >= 4 for question in questions)
    assert all(set(question["correctAnswers"]).issubset({option["key"] for option in question["options"]}) for question in questions)
    combined = " ".join(
        [assessment["subtitle"]]
        + [question["prompt"] for question in questions]
        + [option["text"] for question in questions for option in question["options"]]
        + [question["sourceTitle"] for question in questions]
    )
    assert "DevOps Engineer" in combined
    assert "Healthcare / Health Insurance" in combined
    assert "Kubernetes" in combined
    assert "Terraform" in combined
    assert "Ansible" in combined
    assert "short answer" not in combined.lower()


def test_each_training_basics_topic_has_dynamic_fifteen_question_test():
    modules = _training_basics_preparation_modules()
    assert len(modules) == 12
    for topic_number, module in enumerate(modules, start=1):
        assessment = _training_basics_topic_assessment(module, topic_number)
        questions = assessment["questions"]
        assert assessment["totalQuestions"] == 15
        assert assessment["allowedTypes"] == ["single_choice", "multi_select"]
        assert assessment["sourceRule"] == "Questions are generated only from this Technical Basics + DevOps Foundation topic and its Digital Banking Onboarding And Payments Platform project material."
        assert len(questions) == 15
        assert [question["section"] for question in questions].count("Foundation Theory") == 4
        assert [question["section"] for question in questions].count("Intermediate Project Scenario") == 6
        assert [question["section"] for question in questions].count("Evidence And Failure") == 3
        assert [question["section"] for question in questions].count("Interview Readiness") == 2
        assert {question["sourceType"] for question in questions} == {"basic_prep_topic"}
        assert {question["sourceTitle"] for question in questions} == {module["title"]}
        assert all(question["id"].startswith(f"T{topic_number:02d}-Q") for question in questions)
        assert all(question["type"] in {"single_choice", "multi_select"} for question in questions)
        assert all(question["correctAnswers"] for question in questions)
        assert all(len(question["options"]) >= 4 for question in questions)
        assert all(set(question["correctAnswers"]).issubset({option["key"] for option in question["options"]}) for question in questions)
        assert len({question["prompt"] for question in questions}) == 15
        assert len({question["id"] for question in questions}) == 15


def test_training_basics_questions_are_not_short_template_prompts():
    minimum_words = 12
    modules = _training_basics_preparation_modules()
    for topic_number, module in enumerate(modules, start=1):
        for section in _training_basics_topic_sections(module, topic_number):
            assert all(_training_question_word_count(item["question"]) >= minimum_words for item in section["qa"])
        assessment = _training_basics_topic_assessment(module, topic_number)
        assert all(_training_question_word_count(item["prompt"]) >= minimum_words for item in assessment["questions"])


def test_training_program_questions_are_at_least_twelve_words():
    def question_like_strings(value, path=""):
        if isinstance(value, dict):
            for key, nested in value.items():
                nested_path = f"{path}.{key}" if path else str(key)
                yield from question_like_strings(nested, nested_path)
        elif isinstance(value, list):
            for index, nested in enumerate(value):
                yield from question_like_strings(nested, f"{path}[{index}]")
        elif isinstance(value, str):
            leaf = path.rsplit(".", 1)[-1].lower()
            if value.strip().endswith("?") and any(token in leaf for token in ("question", "prompt")):
                yield path, value

    short_questions = []
    for record in training_program_seed_records():
        label = f"{record['marketingRole']} / {record['industryDomain']}"
        for path, text in question_like_strings(record):
            if _training_question_word_count(text) < 12:
                short_questions.append((label, path, text))

    assert short_questions == []


def test_training_basics_section_page_does_not_show_generated_answer_template():
    template = Path("templates/web/training_basics_topic_section.html").read_text()
    assert "Detailed Questions And Answers" not in template
    assert "faangDepth" not in template
    assert "Project-based interview response" not in template
    assert "Answer Standard" not in template
    assert "Practice Prompts" in template


def test_training_basics_visible_section_material_avoids_template_language():
    banned = [
        "Interview Answer Standard",
        "Good definition",
        "Good project example",
        "Good command use",
        "Weak answer",
        "Enterprise Response Shape",
        "Checkpoint Standard",
        "Stable Interview Response",
        "the consultant",
        "Can the consultant",
    ]
    for topic_number, module in enumerate(_training_basics_preparation_modules(), start=1):
        for section in _training_basics_topic_sections(module, topic_number):
            visible_parts: list[str] = [
                section["label"],
                section["summary"],
                *section.get("practice_prompts", []),
            ]
            for block in section.get("expanded_material", []):
                visible_parts.append(block.get("heading", ""))
                visible_parts.append(block.get("body", ""))
                visible_parts.extend(block.get("bullets", []))
            for solution in section.get("practice_solutions", []):
                visible_parts.append(solution.get("prompt", ""))
                visible_parts.append(solution.get("answer", ""))
                visible_parts.extend(solution.get("steps", []))
                visible_parts.append(solution.get("close", ""))
            visible_text = " ".join(visible_parts)
            for phrase in banned:
                assert phrase not in visible_text


def test_training_basics_topic_section_template_renders_core_concept():
    modules = _training_basics_preparation_modules()
    module = modules[0]
    sections = _training_basics_topic_sections(module, 1)
    section = sections[0]
    response = templates.TemplateResponse(
        "web/training_basics_topic_section.html",
        {
            "request": SimpleNamespace(url=SimpleNamespace(path="/training-basics/topics/1/sections/core-concept-model"), session={}),
            "user": SimpleNamespace(role="admin"),
            "module": module,
            "topic_number": 1,
            "topic_total": len(modules),
            "section": section,
            "topic_sections": sections,
        },
    )
    body = response.body.decode()
    assert "Practice Prompts" in body
    assert "Internal Server Error" not in body


def test_training_basics_practice_solutions_render_for_rhel_scenario():
    modules = _training_basics_preparation_modules()
    module = modules[0]
    sections = _training_basics_topic_sections(module, 1)
    section = next(item for item in sections if item["key"] == "scenario-practice")
    assert section["practice_solutions_url"].endswith("/practice-solutions")
    assert len(section["practice_solutions"]) == 5
    response = templates.TemplateResponse(
        "web/training_basics_practice_solutions.html",
        {
            "request": SimpleNamespace(url=SimpleNamespace(path=section["practice_solutions_url"]), session={}),
            "user": SimpleNamespace(role="admin"),
            "module": module,
            "topic_number": 1,
            "topic_total": len(modules),
            "section": section,
            "topic_sections": sections,
        },
    )
    body = response.body.decode()
    assert "Practice Solutions" in body
    assert "Evidence Path" in body
    assert "systemctl status payment-api" in body
    assert "journalctl -u payment-api" in body
    assert "firewall-cmd --list-ports" in body


def test_training_basics_core_concepts_have_practice_solutions_for_all_topics():
    modules = _training_basics_preparation_modules()
    for topic_number, module in enumerate(modules, start=1):
        section = _training_basics_topic_sections(module, topic_number)[0]
        assert section["key"] == "core-concept-model"
        assert section["practice_solutions_url"].endswith("/practice-solutions")
        assert section["practice_solutions"]


def test_training_basics_topics_include_official_product_references():
    modules = _training_basics_preparation_modules()
    assert all(module["official_references"] for module in modules)
    combined = " ".join(
        reference["url"]
        for module in modules
        for reference in module["official_references"]
    )
    assert "docs.redhat.com" in combined
    assert "git-scm.com" in combined
    assert "docs.github.com" in combined
    assert "docs.docker.com" in combined
    assert "kubernetes.io/docs" in combined
    assert "developer.hashicorp.com/terraform" in combined
    assert "docs.ansible.com" in combined
    assert "opentelemetry.io/docs" in combined


def test_training_basics_practice_prompts_are_not_internal_rubrics():
    banned = ["can the consultant", "the consultant", "trainer can use to judge", "rubric"]
    for topic_number, module in enumerate(_training_basics_preparation_modules(), start=1):
        for section in _training_basics_topic_sections(module, topic_number):
            prompt_text = " ".join(section.get("practice_prompts", []))
            for phrase in banned:
                assert phrase not in prompt_text.lower()


def test_training_basics_topics_are_project_based_and_interview_ready():
    modules = _training_basics_preparation_modules()
    for module in modules:
        assert module["study_hours"] == "10 hours"
        assert module["course_level"] == "Technical Basics + DevOps Foundation"
        assert module["continuous_project"] == "Digital Banking Onboarding And Payments Platform on AKS/EKS"
        project = module["project_thread"]
        assert project["name"] == "Digital Banking Onboarding And Payments Platform"
        assert project["scenario"] in module["mini_project"][0]
        assert project["evidence"][0] in module["evidence_checklist"][0]
        assert project["intermediate_questions"][0] in module["practice_questions"][0]
        assert project["interview_questions"][0] in module["interview_examples"][0]


def test_training_basics_includes_twenty_questions_for_five_six_year_jds():
    groups = _training_basics_five_six_year_interview_questions()
    questions = [question for group in groups for question in group["questions"]]
    assert len(questions) == 20
    combined = " ".join(questions + [group["answer_model"] for group in groups])
    assert "CI/CD pipeline" in combined
    assert "Terraform plan" in combined
    assert "CrashLoopBackOff" in combined
    assert "logs, metrics, traces" in combined
    assert "Shell versus Python" in combined
    pdf_text = " ".join(block["text"] for block in _training_basics_pdf_blocks())
    assert "Expected Interview Questions For 5-6 Years JDs" in pdf_text
    assert "These 20 questions" in pdf_text


def test_role_program_maps_concepts_to_basics_or_company_context():
    record = next(item for item in training_program_seed_records() if item["marketingRole"] == "DevOps Engineer")
    program = SimpleNamespace(
        marketing_role=SimpleNamespace(name=record["marketingRole"]),
        industry_domain=record["industryDomain"],
        application_landscape=record["applicationLandscape"],
        tools_and_technologies=record["toolsAndTechnologies"],
        cloud_architecture=record["cloudArchitecture"],
    )
    coverage = _training_concept_coverage_map(program)
    areas = [item["area"] for item in coverage]
    assert "Basics Prep Fundamentals" in areas
    assert "Company Structure Context" in areas
    assert "Domain Product System Context" in areas
    assert "Marketing Role Implementation Context" in areas
    assert "Use-Case Scenario Practice" in areas
    assert "Enterprise Application Lifecycle Concepts" in areas
    combined = " ".join(
        [
            " ".join(item["concepts"][:50])
            + " "
            + item["where"]
            + " "
            + item["practice"]
            + " "
            + item["proof"]
            for item in coverage
        ]
    )
    assert "Kubernetes" in combined
    assert "CI/CD" in combined or "source trigger" in combined
    assert "product owners" in combined
    assert "application developers" in combined
    assert "service desk" in combined
    assert record["applicationLandscape"][0] in combined
    assert "project scenario" in combined
    assert "validation checks" in combined
    assert "application sunrise" in combined
    assert "application sunset" in combined
    assert "production readiness review" in combined
    role_blocks = _training_program_pdf_blocks(
        SimpleNamespace(
            id=1,
            title=record["title"],
            short_description=record["shortDescription"],
            enterprise_context=record["enterpriseContext"],
            industry_domain=record["industryDomain"],
            application_landscape=record["applicationLandscape"],
            cloud_architecture=record["cloudArchitecture"],
            project_responsibilities=record["projectResponsibilities"],
            key_deliverables=record["keyDeliverables"],
            tools_and_technologies=record["toolsAndTechnologies"],
            interview_story=record["interviewStory"],
            resume_project_summary=record["resumeProjectSummary"],
            production_support_scenarios=record["productionSupportScenarios"],
            three_year_delivery_timeline=record["threeYearDeliveryTimeline"],
            marketing_role=SimpleNamespace(name=record["marketingRole"], common_tools=", ".join(record["toolsAndTechnologies"])),
        ),
        include_diagrams=True,
    )
    role_text = " ".join(block["text"] for block in role_blocks)
    assert "Concept Coverage Map" in role_text
    assert "How I Say This In An Interview" in role_text
    assert "Say it naturally" in role_text
    assert "Full Project Narrative - Readable Version" in role_text
    assert "I did not own business rules or feature code" in role_text
    assert "Enterprise delivery context for Healthcare / Health Insurance technology systems" not in role_text
    assert "Every concept is explained either in Basics Prep or in the role/domain company context" in role_text
    assert "Architect reading lens" in role_text
    assert "Decision Rationale" in role_text
    assert "NFRs And Controls" in role_text
    assert "Business/domain reading lens" in role_text
    assert "BA And Domain View" in role_text
    assert "KPIs And Reports" in role_text
    assert "Project Manager Delivery View" in role_text
    assert "Status Reporting" in role_text
    assert "PM Acceptance Criteria" in role_text


def test_all_role_domain_programs_use_functionality_first_language():
    records = training_program_seed_records()
    assert records
    for record in records:
        program = SimpleNamespace(
            marketing_role=SimpleNamespace(name=record["marketingRole"]),
            industry_domain=record["industryDomain"],
            application_landscape=record["applicationLandscape"],
            tools_and_technologies=record["toolsAndTechnologies"],
            cloud_architecture=record["cloudArchitecture"],
        )
        coverage = _training_concept_coverage_map(program)
        combined = " ".join(
            [
                item["where"]
                + " "
                + item["practice"]
                + " "
                + item["proof"]
                + " "
                + " ".join(item["concepts"][:20])
                for item in coverage
            ]
        )
        assert "Shows" in combined or "Explains" in combined
        assert "project scenario" in combined
        assert "validation" in combined


def test_basics_export_prioritizes_useful_content_over_page_count():
    basics_blocks = _training_basics_pdf_blocks()
    basics_text = " ".join(block["text"] for block in basics_blocks)
    assert "Basics Interview Response Bank" in basics_text
    assert "Master evidence checklist" in basics_text
    assert "Answer workspace" not in basics_text
    assert "Evidence note format" not in basics_text
    assert "Skill gate" not in basics_text
    assert "Functional focus" not in basics_text
    assert "Natural screening answer" in basics_text
    assert "Deeper follow-up answer" in basics_text
    basics_pdf = _simple_text_pdf("Mintel Basics Preparation Command Workbook", basics_blocks)
    basics_pages = int(re.search(rb"/Count (\d+)", basics_pdf).group(1))
    assert 120 <= basics_pages <= 240

    record = next(item for item in training_program_seed_records() if item["marketingRole"] == "DevOps Engineer")
    program = SimpleNamespace(
        id=1,
        title=record["title"],
        short_description=record["shortDescription"],
        enterprise_context=record["enterpriseContext"],
        industry_domain=record["industryDomain"],
        application_landscape=record["applicationLandscape"],
        cloud_architecture=record["cloudArchitecture"],
        project_responsibilities=record["projectResponsibilities"],
        key_deliverables=record["keyDeliverables"],
        tools_and_technologies=record["toolsAndTechnologies"],
        interview_story=record["interviewStory"],
        resume_project_summary=record["resumeProjectSummary"],
        production_support_scenarios=record["productionSupportScenarios"],
        three_year_delivery_timeline=record["threeYearDeliveryTimeline"],
        marketing_role=SimpleNamespace(name=record["marketingRole"], common_tools=", ".join(record["toolsAndTechnologies"])),
    )
    role_blocks = _training_program_pdf_blocks(program, include_diagrams=True)
    role_text = " ".join(block["text"] for block in role_blocks)
    assert "600-Page Role And Domain Scenario Workbook" not in role_text
    assert "Consultant explanation" in role_text
    assert "Real interview questions" in role_text
    assert "QA / Test Lead View" in role_text
    assert "Production Support / Operations View" in role_text
    assert "Security / Compliance View" in role_text
    assert "Data / Reporting View" in role_text
    assert "Product Owner View" in role_text
    role_pdf = _simple_text_pdf("Mintel Consultant Training Book", role_blocks, program=program)
    role_pages = int(re.search(rb"/Count (\d+)", role_pdf).group(1))
    assert 180 <= role_pages <= 350


def test_sre_training_includes_inline_datadog_diagrams():
    record = next(
        item
        for item in training_program_seed_records()
        if item["marketingRole"] == "Site Reliability / AIOps Engineer"
        and item["industryDomain"] == "Banking / Financial Services"
    )
    diagrams = record["cloudArchitecture"]["datadogInlineDiagrams"]
    assert len(diagrams) >= 3
    assert all("datadoghq.com/architecture/" in item["sourceUrl"] for item in diagrams)
    assert all("corp.dd-static.net/img/architecture/" in item["imageUrl"] for item in diagrams)
    assert any("Observability Pipelines" in item["title"] for item in diagrams)
    assert "Datadog-style incident investigation workflow" in [
        item["name"] for item in record["cloudArchitecture"]["workflowDiagrams"]
    ]
    evidence = " ".join(record["cloudArchitecture"]["projectDeliveryPlan"]["finalEvidence"])
    assert "Official Datadog architecture reference diagram" in evidence
    assert "APM trace linked to logs" in evidence


def test_sre_training_includes_production_control_tower_use_case():
    record = next(
        item
        for item in training_program_seed_records()
        if item["marketingRole"] == "Site Reliability / AIOps Engineer"
    )
    use_cases = record["cloudArchitecture"]["deliveredUseCases"]
    titles = [item["title"] for item in use_cases]
    assert any(title.startswith("SRE/AIOps production control tower") for title in titles)
    control_tower = next(item for item in use_cases if item["title"].startswith("SRE/AIOps production control tower"))
    combined = " ".join(
        [
            control_tower["businessProblem"],
            " ".join(control_tower["deliveredScope"]),
            " ".join(control_tower["evidenceToExplain"]),
            control_tower["textbookExplanation"],
        ]
    )
    assert "CI/CD status" in combined
    assert "Kubernetes health" in combined
    assert "logs" in combined
    assert "traces" in combined
    assert "rollback" in combined
    assert "RCA" in combined


def test_devops_and_sre_training_include_agentic_operations_use_cases():
    records = training_program_seed_records()
    devops = next(item for item in records if item["marketingRole"] == "DevOps Engineer")
    devops_text = " ".join(
        [
            " ".join(item["title"] for item in devops["cloudArchitecture"]["deliveredUseCases"]),
            " ".join(item["businessProblem"] for item in devops["cloudArchitecture"]["deliveredUseCases"]),
            " ".join(" ".join(item["evidenceToExplain"]) for item in devops["cloudArchitecture"]["deliveredUseCases"]),
        ]
    )
    assert "AI-assisted deployment diagnosis and canary rollback" in devops_text
    assert "Agentic DevOps guardrails and approval workflow" in devops_text
    assert "canary" in devops_text
    assert "human approval" in devops_text
    assert "audit log" in devops_text

    sre = next(item for item in records if item["marketingRole"] == "Site Reliability / AIOps Engineer")
    sre_text = " ".join(
        [
            " ".join(item["title"] for item in sre["cloudArchitecture"]["deliveredUseCases"]),
            " ".join(item["businessProblem"] for item in sre["cloudArchitecture"]["deliveredUseCases"]),
            " ".join(" ".join(item["evidenceToExplain"]) for item in sre["cloudArchitecture"]["deliveredUseCases"]),
        ]
    )
    assert "Agent-assisted incident triage with human-approved remediation" in sre_text
    assert "RAG runbook reference" in sre_text
    assert "approval record" in sre_text
    assert "validation metric" in sre_text


def test_pdf_automation_reference_updates_all_marketing_roles():
    records = training_program_seed_records()
    expected = {
        "DevOps Engineer": ["Shell and Python release automation toolkit", "Trivy scan result", "Jenkins trigger output"],
        "Cloud Platform Engineer": ["Cloud operations automation script pack", "DNS change record", "backup verification output"],
        "Site Reliability / AIOps Engineer": ["Scripted health checks and alert evidence", "Automated log collection", "Slack/email alert example"],
        "Data Platform Engineer": ["Python data validation and transformation starter kit", "SQL row-count/null-check output", "backup integrity result"],
        "MLOps / AI Platform Engineer": ["Python automation for model service health checks", "Docker health status", "rate-limit handling result"],
    }
    for role, snippets in expected.items():
        record = next(item for item in records if item["marketingRole"] == role)
        text = " ".join(
            [
                " ".join(item["title"] for item in record["cloudArchitecture"]["deliveredUseCases"]),
                " ".join(item["businessProblem"] for item in record["cloudArchitecture"]["deliveredUseCases"]),
                " ".join(" ".join(item["evidenceToExplain"]) for item in record["cloudArchitecture"]["deliveredUseCases"]),
            ]
        )
        for snippet in snippets:
            assert snippet in text


def test_provider_document_source_pack_exists_for_each_mintel_role():
    records = training_program_seed_records()
    for role in MARKETING_ROLE_NAMES:
        record = next(item for item in records if item["marketingRole"] == role)
        program = SimpleNamespace(
            marketing_role=SimpleNamespace(name=role),
            industry_domain=record["industryDomain"],
            cloud_architecture=record["cloudArchitecture"],
        )
        sources = _training_provider_usecase_sources(program)
        assert sources
        assert all(item["use_cases"] and item["evidence"] for item in sources)


def test_full_training_pdf_includes_curated_visual_diagrams():
    record = next(
        item
        for item in training_program_seed_records()
        if item["marketingRole"] == "Site Reliability / AIOps Engineer"
        and item["industryDomain"] == "Healthcare / Health Insurance"
    )
    program = SimpleNamespace(
        id=1,
        title=record["title"],
        short_description=record["shortDescription"],
        enterprise_context=record["enterpriseContext"],
        industry_domain=record["industryDomain"],
        application_landscape=record["applicationLandscape"],
        cloud_architecture=record["cloudArchitecture"],
        project_responsibilities=record["projectResponsibilities"],
        key_deliverables=record["keyDeliverables"],
        tools_and_technologies=record["toolsAndTechnologies"],
        interview_story=record["interviewStory"],
        resume_project_summary=record["resumeProjectSummary"],
        production_support_scenarios=record["productionSupportScenarios"],
        three_year_delivery_timeline=record["threeYearDeliveryTimeline"],
        marketing_role=SimpleNamespace(name=record["marketingRole"], common_tools=", ".join(record["toolsAndTechnologies"])),
    )
    workbook = _training_document_diagram_workbook(program)
    assert len(workbook) >= 25
    assert any("Azure Monitor" in item["title"] for item in workbook)
    assert any("Datadog APM" in item["title"] for item in workbook)
    assert all(item["nodes"] for item in workbook)
    blocks = _training_program_pdf_blocks(program, include_diagrams=True)
    workbook_titles = {item["title"] for item in workbook}
    rendered_titles = {block["text"].split("||", 1)[0] for block in blocks if block.get("style") == "visual_flow"}
    assert 10 <= len(workbook_titles & rendered_titles) <= 16
    provider_diagrams = [block for block in blocks if block.get("style") == "provider_arch" and any(title in block["text"] for title in workbook_titles)]
    assert 10 <= len(provider_diagrams) <= 16
    datadog_reference_diagrams = [
        block
        for block in blocks
        if block.get("style") == "provider_arch"
        and "Datadog" in block["text"]
        and "Visual Reference Flow" in block["text"]
    ]
    assert len(datadog_reference_diagrams) >= 3


def test_provider_architecture_diagram_wraps_panel_labels_inside_boxes():
    payload = {
        "title": "Customer Journey To Business Outcome",
        "provider": "Microsoft Cloud",
        "nodes": [
            {"label": "Audience or user", "items": ["business trigger", "user or system impact", "priority"], "kind": "problem"},
            {"label": "Member enrollment source system", "items": ["source system"], "kind": ""},
            {"label": "Engagement metric", "items": ["metric"], "kind": ""},
            {"label": "Conversion or analysis workflow", "items": ["analysis"], "kind": "role"},
            {"label": "Measured outcome for Cloud Platform Engineer", "items": ["Cloud Platform Engineer"], "kind": "role"},
            {"label": "Validation evidence", "items": ["evidence", "runbook", "interview proof"], "kind": "support"},
        ],
        "evidence": [
            "Explain the diagram as a flow from Audience or user to Validation evidence, not as isolated boxes.",
            "Connect the business outcome to Healthcare / Health Insurance: member workflow is measurable.",
            "State the Cloud Platform Engineer boundary around Conversion or analysis workflow; product and application teams keep ownership.",
            "Use validation proof from the final step: screenshot, dashboard, log/query output, pipeline result, runbook, incident note, or interview story.",
        ],
    }
    commands = "\n".join(_pdf_provider_architecture_commands(58, 640, json.dumps(payload)))
    assert "(Validation evidence)" not in commands
    assert "(Measured outcome for Cloud Platform Engineer)" not in commands
    assert "(Validation) Tj" in commands
    assert "(evidence) Tj" in commands
    assert "(Measured outcome) Tj" in commands


def test_document_diagram_evidence_explains_instead_of_repeating_labels():
    record = next(item for item in training_program_seed_records() if item["marketingRole"] == "Cloud Platform Engineer")
    program = SimpleNamespace(
        title=record["title"],
        industry_domain=record["industryDomain"],
        application_landscape=record["applicationLandscape"],
        cloud_architecture=record["cloudArchitecture"],
        marketing_role=SimpleNamespace(name=record["marketingRole"], common_tools=", ".join(record["toolsAndTechnologies"])),
    )
    workbook = _training_document_diagram_workbook(program)
    evidence = workbook[0]["evidence"]
    assert evidence != ["diagram explanation", "business outcome", "role boundary", "validation proof"]
    assert all(len(item.split()) >= 8 for item in evidence)
    combined = " ".join(evidence)
    assert "flow from" in combined
    assert "business outcome" in combined
    assert "boundary" in combined
    assert "validation proof" in combined


def test_document_diagram_workbooks_are_role_and_domain_specific():
    records = training_program_seed_records()
    samples = {
        "Cloud Platform Engineer": "Banking / Financial Services",
        "DevOps Engineer": "Healthcare / Health Insurance",
        "Data Platform Engineer": "Telecom / Media / Communications",
        "MLOps / AI Platform Engineer": "Technology / SaaS / Enterprise Software",
    }
    first_titles = {}
    for role, domain in samples.items():
        record = next(item for item in records if item["marketingRole"] == role and item["industryDomain"] == domain)
        program = SimpleNamespace(
            title=record["title"],
            industry_domain=record["industryDomain"],
            application_landscape=record["applicationLandscape"],
            cloud_architecture=record["cloudArchitecture"],
            marketing_role=SimpleNamespace(name=record["marketingRole"], common_tools=", ".join(record["toolsAndTechnologies"])),
        )
        workbook = _training_document_diagram_workbook(program)
        assert len(workbook) == 25
        combined = " ".join(item["title"] + " " + item["purpose"] for item in workbook)
        first_titles[role] = workbook[0]["title"]
        assert domain in combined
        assert record["applicationLandscape"][0] in combined
        assert all(item.get("layout") for item in workbook)

    assert len(set(first_titles.values())) == len(first_titles)


def test_banking_cloud_platform_document_diagrams_are_not_marketing_flow_pack():
    record = next(
        item
        for item in training_program_seed_records()
        if item["marketingRole"] == "Cloud Platform Engineer"
        and item["industryDomain"] == "Banking / Financial Services"
    )
    program = SimpleNamespace(
        title=record["title"],
        industry_domain=record["industryDomain"],
        application_landscape=record["applicationLandscape"],
        cloud_architecture=record["cloudArchitecture"],
        marketing_role=SimpleNamespace(name=record["marketingRole"], common_tools=", ".join(record["toolsAndTechnologies"])),
    )
    workbook = _training_document_diagram_workbook(program)
    titles = " ".join(item["title"] for item in workbook)
    assert "Customer Journey" not in titles
    assert "Campaign Planning" not in titles
    assert "Lead Lifecycle" not in titles
    assert "Landing Zone" in titles
    assert "Hub-Spoke VPC/VNet" in titles
    assert "Private Endpoint" in titles


def test_banking_product_system_pages_exist_for_all_marketing_roles():
    banking_records = filter_training_seed_records(
        training_program_seed_records(),
        ALL_MARKETING_ROLES_LABEL,
        "Banking / Financial Services",
        "",
    )
    assert len(banking_records) == len(MARKETING_ROLE_NAMES)

    for record in banking_records:
        links = product_system_link_map(record["applicationLandscape"])
        slug_lookup = product_system_slug_lookup(record["applicationLandscape"])
        assert set(BANKING_PRODUCT_SYSTEM_NAMES) <= set(links)
        for system_name in BANKING_PRODUCT_SYSTEM_NAMES:
            assert slug_lookup[system_name.lower()] == links[system_name]
            detail = banking_product_system_detail(links[system_name], record["marketingRole"])
            assert detail
            assert detail["name"] == system_name
            assert 1000 <= detail["word_count"] <= 1500
            assert record["marketingRole"] in detail["sections"][7]["title"]
            assert detail["capabilities"]
            assert detail["systems"]
            assert detail["operational_signals"]


def test_product_system_pages_exist_for_all_marketing_roles_by_domain():
    records = training_program_seed_records()
    assert len(records) == len(MARKETING_ROLE_NAMES) * len(INDUSTRY_DOMAINS)

    for record in records:
        expected_systems = DOMAIN_PRODUCT_SYSTEM_NAMES[record["industryDomain"]]
        links = product_system_link_map(record["applicationLandscape"])
        slug_lookup = product_system_slug_lookup(record["applicationLandscape"])
        assert set(expected_systems) <= set(links)
        for system_name in expected_systems:
            assert slug_lookup[system_name.lower()] == links[system_name]
            detail = product_system_detail(links[system_name], record["marketingRole"], record["industryDomain"])
            assert detail
            assert detail["name"] == system_name
            assert record["marketingRole"] in detail["sections"][7]["title"]
            assert detail["capabilities"]
            assert detail["systems"]
            assert detail["operational_signals"]
