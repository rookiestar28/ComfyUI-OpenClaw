"""
Connector Router (F29 Remediation).
Dispatches parsed commands to handlers with AST argument parsing.
"""

from typing import TYPE_CHECKING

from .config import ConnectorConfig
from .contract import CommandRequest as CommandRequest
from .contract import CommandResponse as CommandResponse
from .llm_client import LLMClient
from .openclaw_client import OpenClawClient
from .router_admin_handlers import RouterAdminMixin
from .router_chat_handlers import RouterChatMixin
from .router_dispatch import RouterDispatchMixin
from .router_execution_handlers import RouterExecutionMixin
from .state import ConnectorState

if TYPE_CHECKING:
    from .results_poller import ResultsPoller

from .command_firewall import CommandFirewall
from .rate_limiter import RateLimiter
from .semantic_guard import SemanticGuard


class CommandRouter(
    RouterDispatchMixin,
    RouterExecutionMixin,
    RouterAdminMixin,
    RouterChatMixin,
):
    def _build_llm_client(self) -> LLMClient:
        """Resolve the facade dependency at call time to preserve patch seams."""
        return LLMClient(self.client)

    def __init__(
        self,
        config: ConnectorConfig,
        client: OpenClawClient,
        poller: "ResultsPoller" = None,
    ):
        self.config = config
        self.client = client
        self.poller = poller
        self.state = ConnectorState(path=self.config.state_path)
        self._template_meta_cache: dict[str, dict[str, object]] = {}
        # F32 WP2: Rate limiter
        self._rate_limiter = RateLimiter(
            user_rpm=self.config.rate_limit_user_rpm,
            channel_rpm=self.config.rate_limit_channel_rpm,
        )
        # S44/R97: Semantic Guards
        self.semantic_guard = SemanticGuard()
        self.command_firewall = CommandFirewall()
