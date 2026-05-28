from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ProductGlossaryItem(BaseModel):
    term: str
    productMeaning: str
    consultantTalkTrack: str
    boundary: str


class UseCaseBoundary(BaseModel):
    name: str
    businessGoal: str
    inScope: str
    outOfScope: str
    consultantOwnership: str
    implementationEvidence: str
    interviewPositioning: str


class WorkflowDiagram(BaseModel):
    name: str
    purpose: str
    steps: list[str]
    diagram: str
    interviewCue: str


class ProjectDeliveryPhase(BaseModel):
    phase: str
    focus: str


class ProjectDeliveryPlan(BaseModel):
    enterpriseContext: str
    phases: list[ProjectDeliveryPhase]
    finalEvidence: list[str]


class DeliveredUseCase(BaseModel):
    title: str
    businessProblem: str
    deliveredScope: list[str]
    roleBoundary: str
    systemsTouched: list[str]
    evidenceToExplain: list[str]
    interviewStory: str
    slug: str = ""
    textbookExplanation: str = ""
    textbookSections: list[dict] = Field(default_factory=list)
    jiraStories: list[dict] = Field(default_factory=list)
    interviewQuestionSet: list[dict] = Field(default_factory=list)
    architectureQuestionSet: list[dict] = Field(default_factory=list)
    systemDesignQuestionSet: list[dict] = Field(default_factory=list)
    scenarioQuestionSet: list[dict] = Field(default_factory=list)
    troubleshootingQuestionSet: list[dict] = Field(default_factory=list)
    workflow: dict = Field(default_factory=dict)


class EnterpriseOperatingModel(BaseModel):
    scale: list[str]
    technologyTeams: list[str]
    consultantPlacement: str


class InterviewTalkTrack(BaseModel):
    prompt: str
    answer: str


class CloudArchitecture(BaseModel):
    cloudProviderOptions: list[str]
    architectureSummary: str
    coreComponents: list[str]
    architectureLayers: list[dict] = Field(default_factory=list)
    architectureFlows: list[dict] = Field(default_factory=list)
    architectureMindmap: dict = Field(default_factory=dict)
    roleArchitectureOwnership: list[dict] = Field(default_factory=list)
    componentResponsibilities: list[dict] = Field(default_factory=list)
    architectureInterviewExplanation: list[str] = Field(default_factory=list)
    consultantProjectContext: str = ""
    enterpriseOperatingModel: Optional[EnterpriseOperatingModel] = None
    roleProductExplanation: str = ""
    productGlossary: list[ProductGlossaryItem] = Field(default_factory=list)
    useCaseBoundaries: list[UseCaseBoundary] = Field(default_factory=list)
    deliveredUseCases: list[DeliveredUseCase] = Field(default_factory=list)
    workflowDiagrams: list[WorkflowDiagram] = Field(default_factory=list)
    projectDeliveryPlan: Optional[ProjectDeliveryPlan] = None
    interviewTalkTracks: list[InterviewTalkTrack] = Field(default_factory=list)


class ThreeYearDeliveryTimeline(BaseModel):
    year1: list[str]
    year2: list[str]
    year3: list[str]


class TrainingProgramRead(BaseModel):
    id: int
    marketingRole: str
    industryDomain: str
    title: str
    shortDescription: str
    enterpriseContext: str
    applicationLandscape: list[str]
    cloudArchitecture: CloudArchitecture
    projectResponsibilities: list[str]
    threeYearDeliveryTimeline: ThreeYearDeliveryTimeline
    keyDeliverables: list[str]
    toolsAndTechnologies: list[str]
    interviewStory: str
    resumeProjectSummary: str
    productionSupportScenarios: list[str]
    interviewQuestions: list[str]
    displayOrder: int
    isActive: bool


class TrainingProgramList(BaseModel):
    items: list[TrainingProgramRead]
    total: int
    filters: dict[str, list[str]]
