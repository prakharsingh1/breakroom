"""Breakroom: crash tests for action-taking support agents."""
from .models import (AgentCancelled, AgentContext, AgentResult, BudgetExceeded,
                     SupportTools, TaskEnvelope, ToolError)

__version__ = "0.2.0"
__all__ = ["AgentContext", "AgentResult", "SupportTools", "TaskEnvelope", "ToolError",
           "BudgetExceeded", "AgentCancelled"]
