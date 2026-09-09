'''Application composition for one-process, deterministic, or external modes.'''

from dataclasses import dataclass

from app.agents.knowledge import KnowledgeAgent
from app.agents.resolution import ResolutionAgent
from app.agents.triage import TriageAgent
from app.config.settings import RemediationMode, Settings
from app.database.repository import InMemoryRepository, PostgresRepository, Repository
from app.database.seed import build_seed_repository, seed_postgres
from app.graph.workflow import InvestigationWorkflow, WorkflowOutput
from app.kubernetes.service import KubernetesService
from app.mcp.server import MCPToolClient, MCPToolServer
from app.ml.classifier import build_classifier
from app.providers.llm import HTTPLLMProvider, RuleBasedLLMProvider
from app.rag.service import RAGService
from app.remediation.service import RemediationAgent, SimulatedActionExecutor


@dataclass
class ApplicationRuntime:
    settings: Settings
    repository: Repository
    workflow: InvestigationWorkflow
    ready: bool
    readiness_message: str
    kubernetes: KubernetesService | None = None

    async def analyze(self, description: str) -> WorkflowOutput:
        from app.models.schemas import Incident
        return await self.workflow.analyze(Incident(description=description))


async def build_runtime(settings: Settings | None = None) -> ApplicationRuntime:
    settings = settings or Settings.from_env()
    configuration_errors = settings.configuration_errors()
    if configuration_errors:
        return ApplicationRuntime(
            settings,
            InMemoryRepository(),
            _empty_workflow(),
            False,
            '; '.join(configuration_errors),
            None,
        )
    if settings.database_url.startswith('memory://'):
        repository: Repository = await build_seed_repository()
        readiness = 'memory repository ready'
    else:
        repository = PostgresRepository(settings.database_url)
        try:
            if settings.app_env not in {'cloud', 'production'}:
                await repository.initialize()
            ready, readiness = await repository.health()
            if not ready:
                return ApplicationRuntime(settings, repository, _empty_workflow(), False, readiness, None)
            if settings.app_env not in {'cloud', 'production'}:
                await seed_postgres(repository)
        # Initialization is an infrastructure boundary; expose only a safe
        # readiness state and let the request layer remain available.
        except Exception:  # noqa: BLE001
            return ApplicationRuntime(settings, repository, _empty_workflow(), False, 'database unavailable', None)
    rag = RAGService(repository)
    kubernetes = KubernetesService(
        mode=settings.kubernetes_mode,
        namespace=settings.kubernetes_namespace,
        workload=settings.kubernetes_workload,
    )
    remediation_executor = kubernetes if settings.kubernetes_mode == 'execute' and settings.remediation_mode is RemediationMode.EXECUTE else SimulatedActionExecutor()
    mcp_server = MCPToolServer(rag, remediation_executor=remediation_executor, kubernetes=kubernetes)
    mcp_client = MCPToolClient(server=mcp_server) if not settings.mcp_server_url else MCPToolClient(server_url=settings.mcp_server_url)
    classifier = build_classifier(settings.lora_adapter_path)
    triage = TriageAgent(classifier)
    knowledge = KnowledgeAgent(mcp_client)
    if settings.llm_api_key and settings.llm_base_url:
        provider = HTTPLLMProvider(
            settings.llm_base_url,
            settings.llm_api_key.get_secret_value(),
            settings.llm_model,
            settings.provider_timeout_seconds,
        )
    else:
        provider = RuleBasedLLMProvider()
    remediation = RemediationAgent(mcp_client)
    workflow = InvestigationWorkflow(
        triage,
        knowledge,
        ResolutionAgent(provider),
        repository,
        remediation_agent=remediation,
        remediation_enabled=settings.remediation_mode is not RemediationMode.DISABLED,
    )
    return ApplicationRuntime(settings, repository, workflow, True, readiness, kubernetes)


def _empty_workflow() -> InvestigationWorkflow:
    repository = InMemoryRepository()
    rag = RAGService(repository)
    mcp = MCPToolClient(server=MCPToolServer(rag, remediation_executor=SimulatedActionExecutor()))
    return InvestigationWorkflow(
        TriageAgent(),
        KnowledgeAgent(mcp),
        ResolutionAgent(),
        repository,
        remediation_agent=RemediationAgent(mcp),
        remediation_enabled=False,
    )
